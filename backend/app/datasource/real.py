"""drone-manage（若依无人机管理平台）HTTP 客户端与字段映射。

接口清单来自对模块源码的盘点与实测（2026-07-13，服务 192.168.101.21:10009）：
  图斑     POST /flyWorkZone/page（zoneType=图斑，WKT POLYGON Z）
  设备     POST /device/statistics/devices（domain=3 机场）
  OSD      GET  /drone/dock/osd/latest/{sn}（Java 侧读 Redis 后吐出）
  航线规划 POST /drone/route/planDynamicRoute（PLOT_INSPECTION，支持 MultiPolygon，
           与平台批量图斑调度器同一算法：每图斑边界 4 点对中拍照 + 中心高空拍摄）
  航线航点 GET  /drone/route/points/{routeId}
  航线删除 DELETE /drone/route/routeId/{routeId}
  建任务   POST /api/tasks/create（只建不下发；下发须走平台审核/调度）
  气象     POST /api/flight/weather/detect（服务端需配置和风天气 key）
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from app import config
from app.core import geo

logger = logging.getLogger(__name__)

CRUISE_MS = 8.0


class DroneManageError(RuntimeError):
    """真实数据源调用失败（core 捕获后回落 mock）。"""


def wkt_polygon_to_geojson(wkt: str) -> dict[str, Any] | None:
    """WKT POLYGON / POLYGON Z / MULTIPOLYGON (Z) → GeoJSON Polygon。

    解析所有最内层括号组为环；MULTIPOLYGON 取面积最大的外环作为图斑主面
    （核查图斑展示与规划以主面为准，孔洞/碎面忽略）。
    """
    if not wkt or "POLYGON" not in wkt.upper():
        return None
    rings: list[list[list[float]]] = []
    for group in re.findall(r"\(([^()]+)\)", wkt):
        pts = []
        for token in group.split(","):
            nums = token.split()
            if len(nums) >= 2:
                try:
                    pts.append([round(float(nums[0]), 8), round(float(nums[1]), 8)])
                except ValueError:
                    break
        if len(pts) >= 4:
            if pts[0] != pts[-1]:
                pts.append(pts[0])
            rings.append(pts)
    if not rings:
        return None
    if wkt.upper().lstrip().startswith("MULTIPOLYGON") and len(rings) > 1:
        rings = [max(rings, key=geo.polygon_area_m2)]
    return {"type": "Polygon", "coordinates": [rings[0]]}


class DroneManageClient:
    def __init__(self, base: str) -> None:
        self.base = base
        self.http = httpx.Client(base_url=base, timeout=25)

    def _call(self, method: str, path: str, **kw) -> Any:
        try:
            resp = self.http.request(method, path, **kw)
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise DroneManageError(f"{method} {path} 调用失败：{exc}") from exc
        # 响应包装不统一：AjaxResult{code,msg,data} / TableDataInfo{code,rows,total} / R{code,data}
        code = body.get("code")
        if code not in (200, 0):
            raise DroneManageError(f"{method} {path} 业务失败：code={code} msg={body.get('msg')}")
        return body

    # ── 图斑（FlyWorkZone，zoneType=图斑）───────────────────

    def list_plots(self, region: str | None = None, keyword: str | None = None) -> list[dict[str, Any]]:
        body = self._call(
            "POST", "/flyWorkZone/page",
            json={"pageNum": 1, "pageSize": 100, "zoneType": "图斑"},
        )
        records = (body.get("data") or {}).get("records") or body.get("rows") or []
        plots = []
        for z in records:
            if z.get("zoneType") != "图斑":
                continue
            geometry = wkt_polygon_to_geojson(z.get("zoneGeometry") or "")
            if not geometry:
                continue
            name = z.get("zoneName") or z.get("zoneId")
            if region and region.replace("区", "").replace("市", "").replace("县", "") not in name:
                continue
            if keyword and keyword not in name:
                continue
            ring = geometry["coordinates"][0]
            plots.append(
                {
                    "plot_id": name,  # 业务编号（如 汉川市-土地规委会-20260626-00001），可读且唯一
                    "platform_zone_id": z.get("zoneId"),
                    "plot_type": z.get("zoneSource") or "图斑",
                    "priority": "高" if "执法" in (z.get("zoneSource") or "") else "中",
                    "batch_no": z.get("zoneSource") or "-",
                    "region": name.split("-")[0] if "-" in name else "-",
                    "issued_at": (z.get("createTime") or "")[:10],
                    "status": "待核查",
                    "area_mu": round(geo.polygon_area_m2(ring) / 666.67, 1),
                    "centroid": [round(v, 6) for v in geo.centroid(ring)],
                    "geometry": geometry,
                }
            )
        return plots

    # ── 设备（device_registration，domain=3 机场）───────────

    def list_docks(self) -> list[dict[str, Any]]:
        body = self._call(
            "POST", "/device/statistics/devices",
            json={"pageNum": 1, "pageSize": 200},
        )
        rows = body.get("rows") or (body.get("data") or {}).get("records") or []
        docks = []
        for d in rows:
            if d.get("domain") != 3:
                continue
            if d.get("longitude") is None or d.get("latitude") is None:
                continue
            docks.append(
                {
                    "drone_id": d.get("nickname") or d.get("deviceName") or d.get("deviceSn"),
                    "device_sn": d.get("deviceSn"),
                    "model": d.get("modelName") or d.get("deviceName") or "-",
                    "payload": "机场内置无人机",
                    "status": "idle" if d.get("onlineStatus") == 1 else "offline",
                    "status_cn": "在线" if d.get("onlineStatus") == 1 else "离线",
                    "online": d.get("onlineStatus") == 1,
                    "unit": d.get("unitName") or "-",
                    "endurance_min": 28,  # 平台未提供，按机型典型值
                    "battery_pct": None,  # 由 OSD 补充
                    "location": {"type": "Point", "coordinates": [d["longitude"], d["latitude"]]},
                }
            )
        return docks

    def dock_osd(self, device_sn: str) -> dict[str, Any] | None:
        body = self._call("GET", f"/drone/dock/osd/latest/{device_sn}")
        return body.get("data") or None

    def drone_state(self, device_sn: str) -> dict[str, Any] | None:
        body = self._call("GET", f"/out/getDroneState/{device_sn}")
        return body.get("data") or None

    # ── 航线（planDynamicRoute · PLOT_INSPECTION）───────────

    def plan_plot_inspection_route(
        self,
        route_name: str,
        polygons: list[list[list[list[float]]]],
        photo_num: int = 4,
        altitude_m: float | None = None,
        overlap_rate: float | None = None,
    ) -> dict[str, Any]:
        """多图斑图斑巡检航线：MultiPolygon 一次规划（平台批量调度器同款算法）。

        polygons: GeoJSON MultiPolygon 的 coordinates。
        photo_num: 每个图斑边界均分的拍照点数（平台统一参数，非单图斑）。
        altitude_m: 全局飞行高度（平台会经安全高度下限校验，实际值以返回为准）。
        返回：{route_id, waypoints, length_km, duration_min, altitude_m(平台实际采用值)}
        """
        geojson: dict[str, Any] = (
            {"type": "Polygon", "coordinates": polygons[0]}
            if len(polygons) == 1
            else {"type": "MultiPolygon", "coordinates": polygons}
        )
        payload: dict[str, Any] = {
            "routeName": route_name,
            "planningMode": "PLOT_INSPECTION",
            "photoNum": photo_num,
            "centerPhoto": True,
            "targetAreaGeoJson": geojson,
        }
        if altitude_m is not None:
            payload["globalHeight"] = altitude_m
        if overlap_rate is not None:
            payload["overlapRate"] = overlap_rate
        body = self._call("POST", "/drone/route/planDynamicRoute", json=payload)
        route = body.get("data") or {}
        route_id = route.get("routeId")
        if not route_id:
            raise DroneManageError("planDynamicRoute 未返回 routeId")
        pts_body = self._call("GET", f"/drone/route/points/{route_id}")
        raw_pts = pts_body.get("data") or []
        raw_pts.sort(key=lambda p: p.get("pointIndex") or 0)
        waypoints = [
            {
                "seq": i + 1,
                "lon": round(p["longitude"], 6),
                "lat": round(p["latitude"], 6),
                "alt_m": p.get("height") or route.get("globalHeight") or 120,
                "speed_ms": route.get("autoFlightSpeed") or CRUISE_MS,
            }
            for i, p in enumerate(raw_pts)
        ]
        coords = [[w["lon"], w["lat"]] for w in waypoints]
        length_km = round(
            (route.get("estimatedDistance") or geo.path_len_m(coords)) / 1000, 1
        )
        # estimatedDuration 单位为分钟：距离/(速度×60) + 每航点 3s 悬停拍照
        duration = route.get("estimatedDuration")
        return {
            "platform_route_id": route_id,
            "platform_route_name": route.get("routeName"),
            "waypoints": waypoints,
            "length_km": length_km,
            "duration_min": round(duration) if duration else None,
            "speed_ms": route.get("autoFlightSpeed") or CRUISE_MS,
            "altitude_m": route.get("globalHeight") or 120,
        }

    def delete_route(self, platform_route_id: str) -> None:
        self._call("DELETE", f"/drone/route/routeId/{platform_route_id}")

    # ── 航线更新（编辑器回写）────────────────────────────────

    def update_route_waypoints(self, platform_route_id: str, waypoints: list[dict[str, Any]]) -> bool:
        """把编辑后的航点坐标回写平台（PUT /drone/route → updateRoutePoints）。

        按索引对位更新经纬度/高度，保留平台侧动作（拍照/云台等）；
        航点数量变化时不回写（动作无法推断），返回 False 由调用方标注。
        """
        pts_body = self._call("GET", f"/drone/route/points/{platform_route_id}")
        raw_pts = pts_body.get("data") or []
        raw_pts.sort(key=lambda p: p.get("pointIndex") or 0)
        if len(raw_pts) != len(waypoints):
            return False
        for p, w in zip(raw_pts, waypoints):
            p["longitude"] = w["lon"]
            p["latitude"] = w["lat"]
            if w.get("alt_m"):
                p["height"] = w["alt_m"]
        self._call("PUT", "/drone/route", json={"routeId": platform_route_id, "routePointList": raw_pts})
        return True

    # ── 飞行任务（创建 → 下发）────────────────────────────────
    # 真实起飞流程 = 创建 flighttask + 下发计划(publish)。仅创建不下发不会飞；
    # 下发（POST /api/tasks/publish/{taskId}）才把航线派到机场执行（immediate 模式
    # 立即起飞）。start/sync 不是起飞入口，不用。

    def create_flight_task(
        self, task_id: str, task_name: str, platform_route_id: str, device_sn: str, execution_mode: str = "immediate"
    ) -> dict[str, Any]:
        """POST /api/tasks 创建飞行任务（此步只建、不飞）。"""
        # 服务端创建时同步做禁飞区文件/KMZ 等重活，响应可达分钟级，单独放宽超时
        self._call(
            "POST", "/api/tasks",
            json={
                "taskId": task_id,
                "taskName": task_name,
                "routeId": platform_route_id,
                "deviceSn": device_sn,
                "workspaceId": config.DRONE_WORKSPACE_ID,
                "taskType": "PLOT_INSPECTION",
                "executionMode": execution_mode,
                "description": "低空智察智能体创建（人在环确认后）",
            },
            timeout=120,
        )
        created = self.get_flight_task(task_id)
        return {"taskId": task_id, "status": created.get("status"), "taskName": created.get("taskName")}

    def publish_flight_task(self, task_id: str) -> dict[str, Any]:
        """POST /api/tasks/publish/{taskId} 下发计划到机场执行（**真实起飞**，
        immediate 模式立即飞）。返回 {success, missionId/message}。"""
        body = self._call("POST", f"/api/tasks/publish/{task_id}", timeout=120)
        return body.get("data") or {}

    def get_flight_task(self, task_id: str) -> dict[str, Any]:
        body = self._call("GET", f"/api/tasks/{task_id}")
        return body.get("data") or {}

    def update_flight_task(self, task_id: str, patch: dict[str, Any]) -> None:
        """PUT /api/tasks 按数字主键更新（服务端 updateById），先查回 id 再合并提交。"""
        current = self.get_flight_task(task_id)
        if not current.get("id"):
            raise DroneManageError(f"任务 {task_id} 不存在，无法更新")
        self._call("PUT", "/api/tasks", json={**patch, "id": current["id"], "taskId": task_id})

    def cancel_flight_task(self, task_id: str) -> None:
        self._call("PUT", f"/api/tasks/cancel/{task_id}")

    # ── 气象 ─────────────────────────────────────────────────

    def weather_detect(self, lat: float, lon: float) -> dict[str, Any]:
        body = self._call(
            "POST", "/api/flight/weather/detect",
            json={"latitude": lat, "longitude": lon},
        )
        data = body.get("data")
        if not data:
            raise DroneManageError("气象接口无数据")
        return data
