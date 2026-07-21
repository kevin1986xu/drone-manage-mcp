"""drone-manage（若依无人机管理平台）HTTP 客户端与字段映射。

接口清单来自对模块源码的盘点与实测（2026-07，服务 192.168.101.21:10009）：
  图斑     POST /flyWorkZone/page（zoneType=图斑，WKT POLYGON Z）
  设备     POST /device/statistics/devices（domain=3 机场）
  OSD      GET  /drone/dock/osd/latest/{sn}（Java 侧读 Redis 后吐出）
  航线规划 POST /drone/route/planDynamicRoute（PLOT_INSPECTION，支持 MultiPolygon，
           与平台批量图斑调度器同一算法：每图斑边界拍照点对中 + 中心高空拍摄）
  航线航点 GET  /drone/route/points/{routeId}
  航线删除 DELETE /drone/route/routeId/{routeId}
  建任务   POST /api/tasks（只建不飞）；下发 POST /api/tasks/publish/{taskId}（真实起飞）
  气象     POST /api/flight/weather/detect（服务端需配置和风天气 key）
"""

from __future__ import annotations

import contextvars
import logging
import re
import threading
from functools import lru_cache
from typing import Any

import httpx

from uav_mcp import config, geo

logger = logging.getLogger(__name__)

CRUISE_MS = 8.0

# 回源身份透传（关三·P1，见 docs/07 §4.3）：拦截器/工具层把发起用户身份
# 存入此上下文变量，DroneManageClient._call 自动注入平台请求头，
# 平台据此做 dataScope 数据过滤。未设置则只带服务账号 token（或裸调）。
_current_user_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("uav_user_id", default=None)


def set_platform_identity(user_id: str | None) -> contextvars.Token:
    """设置当前调用链的平台用户身份（返回 token，用完 reset 复位）。"""
    return _current_user_id.set(user_id)


def reset_platform_identity(token: contextvars.Token) -> None:
    _current_user_id.reset(token)


class DroneManageError(RuntimeError):
    """平台调用失败（调用方转为工具错误返回，不静默造数）。"""


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
        # 网关认证模式（配 DRONE_GATEWAY_BASE）vs 直连模式（现状，向后兼容）
        self.gateway = config.DRONE_GATEWAY_BASE
        self.prefix = config.DRONE_GATEWAY_PREFIX if self.gateway else ""
        self.base = self.gateway or base
        self._token: str | None = None
        self._token_lock = threading.Lock()
        # VPN 链路有阵发性丢包（2026-07-20 实测坏窗口新建连接失败率 ~25%）：
        # retries=2 只重试连接建立阶段（幂等安全），吸收 SYN 级瞬断；
        # keepalive 拉长到 60s，减少新建连接次数（httpx 默认 5s 一到就丢弃连接）
        self.http = httpx.Client(
            base_url=self.base,
            timeout=25,
            transport=httpx.HTTPTransport(retries=2),
            limits=httpx.Limits(max_keepalive_connections=10, keepalive_expiry=60),
        )

    def _login(self) -> str:
        """账号密码登录平台网关，返回 access_token（若依 Sa-Token JWT）。"""
        resp = self.http.post(
            config.DRONE_LOGIN_PATH,
            json={"username": config.DRONE_LOGIN_USERNAME, "password": config.DRONE_LOGIN_PASSWORD},
        )
        resp.raise_for_status()
        data = (resp.json() or {}).get("data") or {}
        tok = data.get("access_token") or data.get("token")
        if not tok:
            raise DroneManageError("平台登录未返回 access_token")
        return tok

    def _ensure_token(self, force: bool = False) -> str:
        with self._token_lock:
            if force or not self._token:
                self._token = self._login()
                logger.info("平台网关登录成功（账号 %s）", config.DRONE_LOGIN_USERNAME)
            return self._token

    def _auth_headers(self, relogin: bool = False) -> dict[str, str]:
        """回源鉴权头（关三，见 docs/07 §4.3）。

        网关模式：账号登录拿 JWT（缓存/401 重登）；否则回落静态 token。
        用户身份透传（P1）在两种模式下都注入。都没配则空头（直连裸调，向后兼容）。
        """
        headers: dict[str, str] = {}
        if self.gateway and config.DRONE_LOGIN_USERNAME:
            headers["Authorization"] = f"Bearer {self._ensure_token(force=relogin)}"
        elif config.DRONE_PLATFORM_TOKEN:
            headers["Authorization"] = f"Bearer {config.DRONE_PLATFORM_TOKEN}"
        if config.DRONE_USER_ID_HEADER:
            uid = _current_user_id.get()
            if uid:
                headers[config.DRONE_USER_ID_HEADER] = uid
        return headers

    def _call(self, method: str, path: str, **kw) -> Any:
        full = f"{self.prefix}{path}" if self.prefix else path
        user_kw = kw.pop("headers", None) or {}

        def _once(relogin: bool) -> httpx.Response:
            headers = {**self._auth_headers(relogin=relogin), **user_kw}
            return self.http.request(method, full, headers=headers or None, **kw)

        try:
            resp = _once(relogin=False)
            # 网关模式下 token 过期返回 401/403 → 重登重试一次
            if resp.status_code in (401, 403) and self.gateway and config.DRONE_LOGIN_USERNAME:
                logger.info("平台回源 %s，token 疑似过期，重登重试", resp.status_code)
                resp = _once(relogin=True)
            resp.raise_for_status()
            body = resp.json()
        except DroneManageError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DroneManageError(f"{method} {full} 调用失败：{exc}") from exc
        # 响应包装不统一：AjaxResult{code,msg,data} / TableDataInfo{code,rows,total} / R{code,data}
        code = body.get("code")
        if code not in (200, 0):
            raise DroneManageError(f"{method} {full} 业务失败：code={code} msg={body.get('msg')}")
        return body

    # ── 图斑（FlyWorkZone，zoneType=图斑）───────────────────

    def list_plots(self) -> list[dict[str, Any]]:
        # 图斑量已到数千级（2026-07-20 平台数据迁移后 3500+）。首选单页拉全：
        # 平台 SQL 仅按 create_time 排序，批量导入的同秒记录跨页顺序不稳定，
        # 分页会漏行/重行（实测 620 条漏 105）；分页仅作服务端限制 pageSize 时的兜底
        records: list[dict[str, Any]] = []
        for page in range(1, 11):
            body = self._call(
                "POST", "/flyWorkZone/page",
                json={"pageNum": page, "pageSize": 5000, "zoneType": "图斑"},
            )
            data = body.get("data") or {}
            batch = data.get("records") or body.get("rows") or []
            records.extend(batch)
            total = data.get("total")
            if not batch or (total is not None and len(records) >= total):
                break
        plots = []
        for z in records:
            # 前缀匹配兼容历史命名（"图斑调查"曾与"图斑"并存，平台已统一但防反复）
            if not (z.get("zoneType") or "").startswith("图斑"):
                continue
            geometry = wkt_polygon_to_geojson(z.get("zoneGeometry") or "")
            if not geometry:
                continue
            name = z.get("zoneName") or z.get("zoneId")
            ring = geometry["coordinates"][0]
            # 区域优先取平台 areaName/areaCode 字段（zoneName 可能是 UUID，拆名不可靠）；
            # /flyWorkZone/page 的 areaName/areaCode 查询参数线上未生效（实测 2026-07-20
            # 传任意值均返回全量），过滤仍在客户端做，平台重新部署后可下沉服务端
            region = z.get("areaName") or (name.split("-")[0] if "-" in name else "-")
            plots.append(
                {
                    "plot_id": name,  # 业务编号（如 汉川市-土地规委会-20260626-00001），可读且唯一
                    "platform_zone_id": z.get("zoneId"),
                    "plot_type": z.get("zoneSource") or "图斑",
                    "priority": "高" if "执法" in (z.get("zoneSource") or "") else "中",
                    "batch_no": z.get("zoneSource") or "-",
                    "region": region,
                    "area_code": z.get("areaCode") or "-",
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
        duration = route.get("estimatedDuration")  # 单位分钟
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
    # start/sync 不是起飞入口，不用。

    def create_flight_task(
        self, task_id: str, task_name: str, platform_route_id: str, device_sn: str, execution_mode: str = "immediate"
    ) -> dict[str, Any]:
        """POST /api/tasks 创建飞行任务（此步只建、不飞）。

        服务端创建时同步做禁飞区文件/KMZ 等重活，响应可达分钟级，单独放宽超时。
        """
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
        immediate 模式立即飞）。"""
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

    # ── 电子围栏（flyWorkZone，zoneType=禁飞区/限高区/…）──────

    def list_zones(self, type_list: list[str] | None = None) -> list[dict[str, Any]]:
        """非图斑类围栏全量（禁飞区/限飞区/限高区/限速区/警告区/特殊区域）。"""
        payload: dict[str, Any] = {"pageNum": 1, "pageSize": 2000}
        if type_list:
            payload["typeList"] = type_list
        body = self._call("POST", "/flyWorkZone/page", json=payload)
        records = (body.get("data") or {}).get("records") or body.get("rows") or []
        return [z for z in records if not (z.get("zoneType") or "").startswith("图斑")
                and (not type_list or z.get("zoneType") in type_list)]

    def create_zone(self, zone: dict[str, Any]) -> None:
        """POST /flyWorkZone。zone 为 FlyWorkZone 驼峰字段（zoneGeometry 传 WKT）。"""
        self._call("POST", "/flyWorkZone", json=zone)

    def get_zone(self, zone_id: str) -> dict[str, Any] | None:
        body = self._call("GET", f"/flyWorkZone/zoneId/{zone_id}")
        return body.get("data") or None

    def delete_zone(self, zone_id: str) -> None:
        self._call("DELETE", f"/flyWorkZone/zoneId/{zone_id}")

    # ── 告警与设备健康（DroneAlertController / HMS）──────────

    def list_alerts(self, filters: dict[str, Any]) -> dict[str, Any]:
        """POST /api/alerts/list（TableDataInfo：rows/total）。"""
        body = self._call("POST", "/api/alerts/list", json=filters)
        return {"rows": body.get("rows") or [], "total": body.get("total") or 0}

    def get_alert(self, alert_id: str) -> dict[str, Any] | None:
        body = self._call("GET", f"/api/alerts/{alert_id}")
        return body.get("data") or None

    def handle_alert(self, alert_id: str, result: str) -> None:
        self._call("PUT", f"/api/alerts/{alert_id}/handle", json={"handleResult": result})

    def ignore_alert(self, alert_id: str) -> None:
        self._call("PUT", f"/api/alerts/{alert_id}/ignore")

    def alerts_unhandled_count(self) -> int:
        body = self._call("GET", f"/api/alerts/unhandled/count/{config.DRONE_WORKSPACE_ID}")
        return int(body.get("data") or 0)

    def device_hms_unread(self, device_sn: str) -> list[dict[str, Any]]:
        """单设备未读 HMS 健康消息。"""
        body = self._call("GET", f"/devices/hms/devices/hms/{device_sn}")
        return body.get("data") or []

    # ── 媒体成果（MediaFileController / 覆盖计算 / WebODM / 飞行录像）──

    def media_page(self, filters: dict[str, Any]) -> dict[str, Any]:
        body = self._call("POST", "/media/page", json=filters)
        return {"rows": body.get("rows") or [], "total": body.get("total") or 0}

    def media_file_url(self, file_id: str) -> str | None:
        body = self._call("GET", f"/media/fileUrl/{file_id}")
        data = body.get("data")
        return data if isinstance(data, str) else (data or {}).get("url")

    def media_file_detail(self, file_id: str) -> dict[str, Any] | None:
        body = self._call("GET", f"/media/file/{file_id}")
        return body.get("data") or None

    def coverage_calculate_batch(self, requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        body = self._call("POST", "/media/coverage/calculate/batch", json=requests, timeout=60)
        return body.get("data") or []

    def webodm_start(self, flight_task_id: str, process_type: str | None = None) -> dict[str, Any]:
        params = {"processType": process_type} if process_type else None
        body = self._call("POST", f"/media/webodm/modeling/{flight_task_id}/start",
                          params=params, timeout=120)
        return body.get("data") or {}

    def flight_videos(self, mission_id: str) -> list[dict[str, Any]]:
        body = self._call("GET", f"/flight/video/list/{mission_id}")
        return body.get("data") or []

    # ── 任务排期与调度（FlightTaskController 扩展面）──────────

    def flight_tasks_query(self, filters: dict[str, Any]) -> dict[str, Any]:
        """POST /api/tasks/list（TableDataInfo：rows/total）。"""
        body = self._call("POST", "/api/tasks/list", json=filters)
        return {"rows": body.get("rows") or [], "total": body.get("total") or 0}

    def wayline_jobs_search(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        """POST /api/tasks/device/search 设备档期/作业查询（rows=WaylineJobDTO）。"""
        body = self._call("POST", "/api/tasks/device/search", json=filters)
        return body.get("rows") or []

    def plan_new_task(self, task_id: str) -> Any:
        """POST /api/tasks/planNewTask/{taskId} 平台自动重排期（不传时间）。"""
        body = self._call("POST", f"/api/tasks/planNewTask/{task_id}", timeout=60)
        return body.get("data")

    def fail_task_retry(self, job_id: str) -> Any:
        body = self._call("GET", f"/api/tasks/failTaskRetry/{job_id}", timeout=60)
        return body.get("data")

    def breakpoint_flight(self, job_id: str) -> Any:
        body = self._call("GET", f"/api/tasks/breakPointFlight/{job_id}", timeout=60)
        return body.get("data")

    def optimize_route(self, task_id: str, min_height_above_terrain: float = 120.0) -> dict[str, Any]:
        body = self._call("POST", f"/api/tasks/optimizeRoute/{task_id}",
                          params={"minHeightAboveTerrain": min_height_above_terrain}, timeout=120)
        return body.get("data") or {}

    def create_scheduled_flight_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /api/tasks 创建 scheduled/recurring 任务（payload 为 FlightTask 驼峰字段）。"""
        self._call("POST", "/api/tasks", json=payload, timeout=120)
        return self.get_flight_task(payload["taskId"])

    # ── 直播与遥测回放（DeviceLiveStreamController /device/live/* + InfluxDB OSD + 轨迹）──

    def live_capacity(self, device_sn: str) -> Any:
        body = self._call("GET", f"/device/live/capacity/{device_sn}")
        return body.get("data")

    def live_start(self, device_sn: str, source: str = "drone") -> Any:
        """source：drone（无人机镜头）/ airport（机场镜头）/ assist（无人机辅助摄像）。"""
        path = {"drone": f"/device/live/drone/start/{device_sn}",
                "airport": f"/device/live/airport/start/{device_sn}",
                "assist": f"/device/live/drone/assist/start/{device_sn}"}[source]
        body = self._call("POST", path, timeout=30)
        return body.get("data")

    def live_stop(self, device_sn: str) -> Any:
        body = self._call("POST", f"/device/live/stop/{device_sn}")
        return body.get("data")

    def live_quality(self, device_sn: str, quality: int) -> Any:
        """quality：0 高清 / 1 标清 / 2 流畅（平台口径）。"""
        body = self._call("POST", f"/device/live/quality/{device_sn}/{quality}")
        return body.get("data")

    def live_switch_dock_camera(self, device_sn: str, camera_position: int) -> Any:
        body = self._call("POST", f"/device/live/switchDock/{device_sn}/{camera_position}")
        return body.get("data")

    def live_switch_drone_camera(self, device_sn: str, video_type: str) -> Any:
        """video_type：wide 广角 / zoom 变焦 / ir 红外（平台 videoType 口径）。"""
        body = self._call("POST", f"/device/live/switchDrone/{device_sn}/{video_type}")
        return body.get("data")

    def osd_history(self, device_sn: str, start_time: str, end_time: str) -> list[dict[str, Any]]:
        """GET /drone/influxdb/query/osd/device/{sn}（时间格式 yyyy-MM-dd HH:mm:ss）。"""
        body = self._call("GET", f"/drone/influxdb/query/osd/device/{device_sn}",
                          params={"startTime": start_time, "endTime": end_time}, timeout=60)
        return body.get("data") or []

    def osd_latest(self, device_sn: str) -> dict[str, Any] | None:
        body = self._call("GET", f"/drone/influxdb/query/osd/latest/{device_sn}")
        return body.get("data")

    def trajectory_by_mission(self, mission_id: str) -> Any:
        body = self._call("GET", f"/api/trajectories/mission/{mission_id}", timeout=60)
        return body.get("data")

    def trajectory_by_device(self, device_sn: str, start_time: str, end_time: str) -> Any:
        body = self._call("GET", f"/api/trajectories/device/{device_sn}",
                          params={"startTime": start_time, "endTime": end_time}, timeout=60)
        return body.get("data")

    # ── 实时飞控（DockController jobs/* + DRC + wayline job 状态）──

    def dock_service_job(self, device_sn: str, service: str, param: dict[str, Any] | None = None) -> Any:
        """POST /control/api/v1/devices/{sn}/jobs/{service}——下行指令统一入口。
        service 见平台 RemoteDebugMethodEnum（return_home / debug_mode_open /
        putter_open / air_conditioner_mode_switch / battery_maintenance_switch…）。
        除 return_home(_cancel) 外其余 service 要求设备已进调试模式。"""
        body = self._call("POST", f"/control/api/v1/devices/{device_sn}/jobs/{service}",
                          json=param or {}, timeout=30)
        return body.get("data")

    def emergency_stop(self, device_sn: str) -> Any:
        """POST /control/api/v1/devices/{sn}/emergencyStop（独立端点，非 service 枚举）。"""
        body = self._call("POST", f"/control/api/v1/devices/{device_sn}/emergencyStop", timeout=30)
        return body.get("data")

    def update_wayline_job_status(self, job_id: str, status: int) -> Any:
        """PUT /wayline/api/v1/workspaces/{ws}/jobs/{job_id}——WaylineTaskStatusEnum
        按 ordinal 序列化：**0=PAUSE（flighttaskPause）、1=RESUME（flighttaskRecovery）**。
        平台无独立暂停/恢复路由；任务未在执行中时平台侧直接抛错。"""
        body = self._call("PUT",
                          f"/wayline/api/v1/workspaces/{config.DRONE_WORKSPACE_ID}/jobs/{job_id}",
                          json={"status": status}, timeout=30)
        return body.get("data")

    def grab_flight_authority(self, device_sn: str) -> Any:
        body = self._call("POST", f"/control/api/v1/devices/{device_sn}/authority/flight", timeout=30)
        return body.get("data")

    def fly_to_point(self, device_sn: str, param: dict[str, Any]) -> Any:
        body = self._call("POST", f"/control/api/v1/devices/{device_sn}/jobs/fly-to-point",
                          json=param, timeout=30)
        return body.get("data")

    def fly_to_point_stop(self, device_sn: str) -> Any:
        body = self._call("DELETE", f"/control/api/v1/devices/{device_sn}/jobs/fly-to-point", timeout=30)
        return body.get("data")

    def takeoff_to_point(self, device_sn: str, param: dict[str, Any]) -> Any:
        body = self._call("POST", f"/control/api/v1/devices/{device_sn}/jobs/takeoff-to-point",
                          json=param, timeout=60)
        return body.get("data")

    def drc_speaker(self, device_sn: str, action: str, param: dict[str, Any]) -> Any:
        """action：tts_set / play_tts / stop / volume_set（DRC 下行，需 DRC 通道）。"""
        body = self._call("POST", f"/control/api/v1/{device_sn}/drc/speaker_{action}",
                          json=param, timeout=30)
        return body.get("data")

    def drc_light(self, device_sn: str, action: str, param: dict[str, Any]) -> Any:
        """action：brightness_set / mode_set（探照灯 DRC 下行）。"""
        body = self._call("POST", f"/control/api/v1/{device_sn}/drc/drc_light_{action}",
                          json=param, timeout=30)
        return body.get("data")

    def set_drone_height_limit(self, device_sn: str, limit_m: int) -> Any:
        """GET /api/tasks/setDroneHeightLimit/{sn}?droneLeightLimit=N（参数名拼写为平台原样）。"""
        body = self._call("GET", f"/api/tasks/setDroneHeightLimit/{device_sn}",
                          params={"droneLeightLimit": limit_m}, timeout=30)
        return body.get("data")

    def takeover_no_fly_zone_check(self, lon: float, lat: float,
                                   altitude: float | None = None) -> Any:
        body = self._call("POST", "/api/tasks/takeover/no-fly-zone/check",
                          json={"workspaceId": config.DRONE_WORKSPACE_ID, "longitude": lon,
                                "latitude": lat, "currentAltitude": altitude})
        return body.get("data")

    def payload_command(self, device_sn: str, cmd: str, data: dict[str, Any] | None = None) -> Any:
        """POST /control/api/v1/devices/{sn}/payload/commands（相机等载荷指令，
        cmd 如 camera_photo_take；需先夺取负载控制权）。"""
        body = self._call("POST", f"/control/api/v1/devices/{device_sn}/payload/commands",
                          json={"cmd": cmd, "data": data or {}}, timeout=30)
        return body.get("data")

    # ── 机场调试（DebugManageController /api/dockDebug/* + DockOsd）──

    def dock_debug(self, device_sn: str, path: str) -> Any:
        """GET /api/dockDebug/{path}/{sn}——固定路由调试指令（debug/open、dock/coverOpen、
        drone/chargeOpen…）。putter/空调/补光灯/电池保养不在此控制器，走 dock_service_job。"""
        body = self._call("GET", f"/api/dockDebug/{path}/{device_sn}", timeout=30)
        return body.get("data")

    def dock_osd_latest(self, device_sn: str) -> dict[str, Any] | None:
        """GET /drone/dock/osd/latest/{sn}——机场环境读数（温湿度/风速/雨量/舱内状态）。"""
        body = self._call("GET", f"/drone/dock/osd/latest/{device_sn}")
        return body.get("data")

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


@lru_cache(maxsize=1)
def get_client() -> DroneManageClient:
    # 网关模式用 DRONE_GATEWAY_BASE，直连模式用 DRONE_API_BASE，至少配一个
    if not config.DRONE_GATEWAY_BASE and not config.DRONE_API_BASE:
        raise DroneManageError("未配置平台地址（DRONE_GATEWAY_BASE 或 DRONE_API_BASE），无法访问业务数据")
    return DroneManageClient(config.DRONE_API_BASE)
