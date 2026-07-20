"""空域与电子围栏域（flyWorkZone 非图斑类：禁飞区/限飞区/限高区/限速区/警告区）。

- 围栏数据与图斑同表（fly_work_zone），zoneType 区分；
- 冲突检测在本侧做（航线折线 × 围栏多边形求交，geo.py 纯几何）；
- create/delete 为高危写（confirm_token 人在环）；平台围栏**无过期时间字段**，
  临时管制区的到期语义记录在 zoneConfig.expireAt + 备注中，需人工到期删除。
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from uav_mcp import approval, config, geo
from uav_mcp import routes as routes_core
from uav_mcp.drone_manage import DroneManageError, get_client, wkt_polygon_to_geojson

logger = logging.getLogger(__name__)

# 关注的围栏类型（图斑之外的全部管控区类型；平台为自由字符串，此处为白名单）
ZONE_TYPES = ["禁飞区", "限飞区", "限高区", "限速区", "警告区", "特殊区域", "工作区", "作业区"]
# 硬约束类型：航线穿越即 fail（限高区单独按高度判断）
NO_FLY_TYPES = {"禁飞区", "限飞区"}

HYDRATE_TTL_S = 30.0
_zones_cache: list[dict[str, Any]] = []
_last_hydrate = 0.0


def _hydrate() -> list[dict[str, Any]]:
    global _zones_cache, _last_hydrate
    if _zones_cache and time.time() - _last_hydrate < HYDRATE_TTL_S:
        return _zones_cache
    raw = get_client().list_zones(ZONE_TYPES)
    zones = []
    for z in raw:
        geometry = z.get("zoneGeometryJson") or wkt_polygon_to_geojson(z.get("zoneGeometry") or "")
        if not geometry or geometry.get("type") not in ("Polygon", "MultiPolygon"):
            continue  # 点/线状围栏暂不参与面冲突检测
        rings = (
            [geometry["coordinates"][0]]
            if geometry["type"] == "Polygon"
            else [poly[0] for poly in geometry["coordinates"]]
        )
        # 去掉 POLYGON Z 的第三维，统一 [lon, lat]
        rings = [[[pt[0], pt[1]] for pt in ring] for ring in rings]
        zones.append(
            {
                "zone_id": z.get("zoneId"),
                "zone_name": z.get("zoneName") or "-",
                "zone_type": z.get("zoneType"),
                "status": "启用" if z.get("status") == 1 else "停用",
                "enabled": z.get("status") == 1,
                "limit_height_m": z.get("limitHeight"),
                "region": z.get("areaName") or "-",
                "area_code": z.get("areaCode") or "-",
                "source": z.get("zoneSource") or "-",
                "created_at": (z.get("createTime") or "")[:16],
                "rings": rings,
                "geometry": {"type": "Polygon", "coordinates": [rings[0]]},
                "expire_at": (z.get("zoneConfig") or {}).get("expireAt"),
            }
        )
    _zones_cache = zones
    _last_hydrate = time.time()
    return zones


def invalidate_cache() -> None:
    global _last_hydrate
    _last_hydrate = 0.0


def _zone_view(z: dict[str, Any], include_geometry: bool) -> dict[str, Any]:
    v = {k: z[k] for k in ("zone_id", "zone_name", "zone_type", "status",
                           "limit_height_m", "region", "area_code", "source",
                           "created_at", "expire_at")}
    if include_geometry:
        v["geometry"] = z["geometry"]
    return v


def list_zones(zone_type: str | None = None, region: str | None = None,
               include_geometry: bool = False) -> dict[str, Any]:
    try:
        zones = _hydrate()
    except DroneManageError as exc:
        return {"error": f"无人机平台不可达：{exc}", "zones": [], "count": 0}
    if zone_type:
        zones = [z for z in zones if zone_type in z["zone_type"]]
    if region:
        zones = [z for z in zones if region.replace("区", "") in z["region"]
                 or str(z["area_code"]).startswith(region)]
    return {
        "count": len(zones),
        "zones": [_zone_view(z, include_geometry) for z in zones[:50]],
    }


def check_route_conflict(route_id: str, altitude_m: float | None = None) -> dict[str, Any]:
    """航线 × 围栏冲突检测。

    结论口径：穿越禁飞/限飞区 → fail；进入限高区且飞行高度超 limit_height → fail；
    进入警告区/限速区 → warn；无冲突 → pass。
    """
    r, rev = routes_core._rev(route_id)
    if not rev:
        return {"error": f"航线 {route_id} 不存在"}
    line = [[w["lon"], w["lat"]] for w in rev["waypoints"]]
    alt = altitude_m if altitude_m is not None else rev.get("altitude_m") or max(
        (w.get("alt_m") or 0) for w in rev["waypoints"]
    )
    try:
        zones = _hydrate()
    except DroneManageError as exc:
        return {
            "item": "空域许可",
            "status": "warn",
            "detail": f"围栏数据源不可达（{exc}），无法完成空域冲突检测，起飞前请人工核实",
            "conflicts": [],
        }
    conflicts = []
    for z in zones:
        if not z["enabled"]:
            continue
        hit_ring = next((ring for ring in z["rings"] if geo.polyline_crosses_ring(line, ring)), None)
        if hit_ring is None:
            continue
        if z["zone_type"] in NO_FLY_TYPES:
            level, reason = "fail", f"航线穿越{z['zone_type']}"
        elif z["zone_type"] == "限高区":
            limit = z.get("limit_height_m")
            if limit is not None and alt > float(limit):
                level, reason = "fail", f"飞行高度 {alt}m 超过限高 {limit}m（相对起飞点口径，需人工复核真高）"
            else:
                level, reason = "warn", f"航线进入限高区（限高 {limit}m，当前 {alt}m，未超限）"
        elif z["zone_type"] in ("警告区", "限速区", "特殊区域"):
            level, reason = "warn", f"航线进入{z['zone_type']}，注意作业规范"
        else:  # 工作区/作业区等中性区域不算冲突
            continue
        conflicts.append(
            {
                "level": level,
                "reason": reason,
                "zone_id": z["zone_id"],
                "zone_name": z["zone_name"],
                "zone_type": z["zone_type"],
                "limit_height_m": z.get("limit_height_m"),
                "geometry": z["geometry"],  # 冲突多边形，供前端落图
            }
        )
    status = "pass"
    if any(c["level"] == "fail" for c in conflicts):
        status = "fail"
    elif conflicts:
        status = "warn"
    detail = (
        "未检测到与禁飞区/限高区等电子围栏的冲突"
        if not conflicts
        else "；".join(f"{c['reason']}（{c['zone_name']}）" for c in conflicts)
    )
    return {
        "route_id": (r or {}).get("route_id") or route_id.upper(),
        "status": status,
        "altitude_m": alt,
        "checked_zones": len(zones),
        "conflicts": conflicts,
        "detail": detail,
    }


def create_zone(
    zone_type: str,
    zone_name: str,
    geometry: dict[str, Any],
    limit_height_m: float | None = None,
    expire_at: str | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """【高危】新建管控区（临时管制/禁飞区等）。人在环确认后落平台。"""
    if zone_type not in ZONE_TYPES or zone_type in ("工作区", "作业区"):
        return {"error": f"zone_type 须为管控区类型：{[t for t in ZONE_TYPES if t not in ('工作区', '作业区')]}"}
    coords = (geometry or {}).get("coordinates")
    if (geometry or {}).get("type") != "Polygon" or not coords:
        return {"error": "geometry 须为 GeoJSON Polygon（{type, coordinates}）"}
    if confirm_token is None:
        rows = [
            {"label": "类型", "value": zone_type},
            {"label": "名称", "value": zone_name},
            {"label": "面积", "value": f"{round(geo.polygon_area_m2(coords[0]) / 666.67, 1)} 亩"},
        ]
        if limit_height_m is not None:
            rows.append({"label": "限高", "value": f"{limit_height_m} m"})
        if expire_at:
            rows.append({"label": "到期", "value": f"{expire_at}（平台不自动失效，需到期人工删除）"})
        item = approval.create_pending_action(
            "create_zone",
            {"zone_type": zone_type, "zone_name": zone_name, "geometry": geometry,
             "limit_height_m": limit_height_m, "expire_at": expire_at},
            {"title": f"新建{zone_type} · {zone_name}", "rows": rows},
        )
        return {
            "status": "requires_confirmation",
            "action_id": item["action_id"],
            "action": "create_zone",
            "message": "高危操作：已生成待确认单，人工确认后才会在平台创建管控区",
        }
    item = approval.validate_and_consume("create_zone", confirm_token)
    if not item:
        return approval.refusal("create_zone")
    p = item["params"]
    ring = p["geometry"]["coordinates"][0]
    if ring[0] != ring[-1]:
        ring = ring + [ring[0]]
    wkt = "POLYGON ((" + ",".join(f"{pt[0]} {pt[1]}" for pt in ring) + "))"
    zone_id = uuid.uuid4().hex
    zone_config: dict[str, Any] = {"createdBy": "低空智察Agent"}
    if p.get("expire_at"):
        zone_config["expireAt"] = p["expire_at"]
    payload: dict[str, Any] = {
        "zoneId": zone_id,
        "zoneName": p["zone_name"],
        "zoneType": p["zone_type"],
        "zoneSource": "Agent创建",
        "zoneGeometry": wkt,
        "featureType": "polygon",
        "workspaceId": config.DRONE_WORKSPACE_ID,
        "status": 1,
        "zoneConfig": zone_config,
        "zoneArea": round(geo.polygon_area_m2(ring), 1),
    }
    if p.get("limit_height_m") is not None:
        payload["limitHeight"] = p["limit_height_m"]
    try:
        get_client().create_zone(payload)
    except DroneManageError as exc:
        return {"error": f"平台创建围栏失败：{exc}"}
    invalidate_cache()
    return {
        "status": "created",
        "zone_id": zone_id,
        "zone_type": p["zone_type"],
        "zone_name": p["zone_name"],
        "expire_at": p.get("expire_at"),
        "note": "围栏已在平台生效（平台记录层面）；推送到设备侧强制生效需围栏下发能力（规划中）",
    }


def delete_zone(zone_id: str, confirm_token: str | None = None) -> dict[str, Any]:
    """【高危】删除管控区。人在环确认。"""
    try:
        z = get_client().get_zone(zone_id)
    except DroneManageError as exc:
        return {"error": f"无人机平台不可达：{exc}"}
    if not z:
        return {"error": f"围栏 {zone_id} 不存在"}
    if (z.get("zoneType") or "").startswith("图斑"):
        return {"error": "该记录是核查图斑，不是管控围栏，禁止经本工具删除"}
    if confirm_token is None:
        item = approval.create_pending_action(
            "delete_zone",
            {"zone_id": zone_id},
            {"title": f"删除{z.get('zoneType')} · {z.get('zoneName')}",
             "rows": [{"label": "类型", "value": z.get("zoneType") or "-"},
                      {"label": "名称", "value": z.get("zoneName") or "-"},
                      {"label": "区域", "value": z.get("areaName") or "-"}]},
        )
        return {
            "status": "requires_confirmation",
            "action_id": item["action_id"],
            "action": "delete_zone",
            "message": "高危操作：已生成待确认单，人工确认后才会删除该管控区",
        }
    item = approval.validate_and_consume("delete_zone", confirm_token)
    if not item:
        return approval.refusal("delete_zone")
    try:
        get_client().delete_zone(item["params"]["zone_id"])
    except DroneManageError as exc:
        return {"error": f"平台删除围栏失败：{exc}"}
    invalidate_cache()
    return {"status": "deleted", "zone_id": item["params"]["zone_id"]}
