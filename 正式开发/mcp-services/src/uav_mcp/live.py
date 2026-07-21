"""直播与遥测回放域核心逻辑（uav-live-mcp，docs/05 §2.3 / docs/06 主线五）。

安全口径：开流/停流/切镜头是**中危写但免 confirm_token**（只动视频流不动飞行器，
docs/05 §2.3），全部入审计（audit 拦截器按工具名记录）。遥测/轨迹为纯读。
返回统一带 view 指令素材（拉流地址），GIS 前端 show_live 内嵌播放器用。
"""

from __future__ import annotations

import logging
from typing import Any

from uav_mcp import drones as drones_core
from uav_mcp.drone_manage import DroneManageError, get_client

logger = logging.getLogger(__name__)

_QUALITY = {"高清": 0, "标清": 1, "流畅": 2}
_CAMERA_TYPES = {"wide": "广角", "zoom": "变焦", "ir": "红外"}


def _find(drone_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """解析设备；失败返回 (None, error_dict)。"""
    drones_core.hydrate()
    d = drones_core.find(drone_id)
    if not d or not d.get("device_sn"):
        return None, {"error": f"设备 {drone_id} 不存在或无 SN"}
    return d, None


def get_live_capacity(drone_id: str) -> dict[str, Any]:
    d, err = _find(drone_id)
    if err:
        return err
    try:
        cap = get_client().live_capacity(d["device_sn"])
    except DroneManageError as exc:
        return {"error": f"直播能力查询失败：{exc}"}
    return {"drone_id": d["drone_id"], "device_sn": d["device_sn"],
            "capacity": cap or {}, "note": None if cap else "设备离线或不支持直播"}


def start_live(drone_id: str, source: str = "drone") -> dict[str, Any]:
    if source not in ("drone", "airport", "assist"):
        return {"error": "source 须为 drone（无人机镜头）/ airport（机场镜头）/ assist（辅助摄像）"}
    d, err = _find(drone_id)
    if err:
        return err
    try:
        data = get_client().live_start(d["device_sn"], source)
    except DroneManageError as exc:
        return {"error": f"开流失败：{exc}", "hint": "确认设备在线且具备直播能力（先查 get_live_capacity）"}
    return {
        "status": "streaming",
        "drone_id": d["drone_id"],
        "source": source,
        "stream": data or {},
        "view_directive": {"type": "show_live", "drone_id": d["drone_id"], "stream": data or {}},
        "note": "直播已开启（视频流操作，不影响飞行）。不用时请停流以释放通道。",
    }


def stop_live(drone_id: str) -> dict[str, Any]:
    d, err = _find(drone_id)
    if err:
        return err
    try:
        get_client().live_stop(d["device_sn"])
    except DroneManageError as exc:
        return {"error": f"停流失败：{exc}"}
    return {"status": "stopped", "drone_id": d["drone_id"]}


def switch_camera(drone_id: str, camera: str) -> dict[str, Any]:
    """camera：wide/zoom/ir（无人机镜头）或数字位（机场镜头位）。"""
    d, err = _find(drone_id)
    if err:
        return err
    try:
        if camera.isdigit():
            get_client().live_switch_dock_camera(d["device_sn"], int(camera))
        elif camera in _CAMERA_TYPES:
            get_client().live_switch_drone_camera(d["device_sn"], camera)
        else:
            return {"error": f"camera 须为 {list(_CAMERA_TYPES)}（无人机）或镜头位数字（机场）"}
    except DroneManageError as exc:
        return {"error": f"切镜头失败：{exc}", "hint": "需先开流"}
    return {"status": "switched", "drone_id": d["drone_id"],
            "camera": _CAMERA_TYPES.get(camera, f"机位{camera}")}


def set_live_quality(drone_id: str, quality: str) -> dict[str, Any]:
    if quality not in _QUALITY:
        return {"error": f"quality 须为：{list(_QUALITY)}"}
    d, err = _find(drone_id)
    if err:
        return err
    try:
        get_client().live_quality(d["device_sn"], _QUALITY[quality])
    except DroneManageError as exc:
        return {"error": f"设置画质失败：{exc}", "hint": "需先开流"}
    return {"status": "ok", "drone_id": d["drone_id"], "quality": quality}


def _osd_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "time": row.get("time") or row.get("createTime"),
        "lon": row.get("longitude"), "lat": row.get("latitude"),
        "height_m": row.get("height") or row.get("elevation"),
        "speed_ms": row.get("horizontalSpeed"),
        "battery_pct": row.get("batteryPercent") or row.get("capacityPercent"),
        "mode": row.get("modeCode"),
    }


def get_telemetry_history(drone_id: str, start_time: str, end_time: str,
                          limit: int = 100) -> dict[str, Any]:
    """时间格式 yyyy-MM-dd HH:mm:ss；返回按时间抽样最多 limit 条（省上下文）。"""
    d, err = _find(drone_id)
    if err:
        return err
    try:
        rows = get_client().osd_history(d["device_sn"], start_time, end_time)
    except DroneManageError as exc:
        return {"error": f"遥测历史查询失败：{exc}", "points": [], "count": 0}
    step = max(1, len(rows) // limit)
    sampled = [_osd_view(r) for r in rows[::step][:limit]]
    return {"drone_id": d["drone_id"], "range": [start_time, end_time],
            "total_raw": len(rows), "count": len(sampled), "points": sampled,
            "note": f"原始 {len(rows)} 点已抽样为 {len(sampled)} 点" if step > 1 else None}


def get_flight_trajectory(task_id: str | None = None, drone_id: str | None = None,
                          start_time: str | None = None, end_time: str | None = None) -> dict[str, Any]:
    """按任务（mission）或按设备+时间范围取轨迹，返回可落图折线。"""
    cli = get_client()
    try:
        if task_id:
            data = cli.trajectory_by_mission(task_id)
        elif drone_id and start_time and end_time:
            d, err = _find(drone_id)
            if err:
                return err
            data = cli.trajectory_by_device(d["device_sn"], start_time, end_time)
        else:
            return {"error": "需提供 task_id，或 drone_id + start_time + end_time"}
    except DroneManageError as exc:
        return {"error": f"轨迹查询失败：{exc}"}
    points = data if isinstance(data, list) else (data or {}).get("points") or []
    line = [[p.get("longitude"), p.get("latitude")] for p in points
            if isinstance(p, dict) and p.get("longitude") is not None][:2000]
    return {
        "task_id": task_id, "count": len(line),
        "trajectory": line,
        "view_directive": {"type": "show_trajectory", "line": line} if line else None,
        "note": None if line else "该任务/时段无轨迹数据",
    }
