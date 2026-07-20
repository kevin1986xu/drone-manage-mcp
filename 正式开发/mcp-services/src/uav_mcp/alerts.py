"""告警与设备健康域（DroneAlertController + HMS + OSD）。

枚举口径（以平台 DroneAlert 实体为准，2026-07 实测印证）：
  alertLevel：0低 / 1中 / 2高 / 3紧急
  alertStatus：0未处理 / 1已处理 / 2已忽略
  alertType：0设备 / 1飞行 / 2任务 / 3系统
处理/忽略是低危写：无需 confirm_token，但备注中记录操作来源。
"""

from __future__ import annotations

import logging
from typing import Any

from uav_mcp import drones as drones_core
from uav_mcp.drone_manage import DroneManageError, get_client

logger = logging.getLogger(__name__)

LEVEL_CN = {0: "低", 1: "中", 2: "高", 3: "紧急"}
STATUS_CN = {0: "未处理", 1: "已处理", 2: "已忽略"}
TYPE_CN = {0: "设备", 1: "飞行", 2: "任务", 3: "系统"}
STATUS_PARAM = {"未处理": 0, "已处理": 1, "已忽略": 2}
LEVEL_PARAM = {"低": 0, "中": 1, "高": 2, "紧急": 3}


def _alert_view(a: dict[str, Any]) -> dict[str, Any]:
    return {
        "alert_id": a.get("alertId"),
        "title": a.get("alertTitle") or "-",
        "content": a.get("alertContent") or a.get("alertReason") or "-",
        "type": TYPE_CN.get(a.get("alertType"), str(a.get("alertType"))),
        "level": LEVEL_CN.get(a.get("alertLevel"), str(a.get("alertLevel"))),
        "status": STATUS_CN.get(a.get("alertStatus"), str(a.get("alertStatus"))),
        "device": a.get("airportName") or a.get("deviceSn") or "-",
        "device_sn": a.get("deviceSn"),
        "alert_time": a.get("alertTime") or a.get("createTime"),
        "handled_by": a.get("handleBy"),
        "handle_result": a.get("handleResult"),
    }


def list_alerts(
    status: str | None = None,
    level: str | None = None,
    drone_id: str | None = None,
    date_range: list[str] | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    filters: dict[str, Any] = {"pageNum": 1, "pageSize": max(1, min(limit, 50))}
    if status:
        if status not in STATUS_PARAM:
            return {"error": f"status 须为：{list(STATUS_PARAM)}"}
        filters["alertStatus"] = STATUS_PARAM[status]
    if level:
        if level not in LEVEL_PARAM:
            return {"error": f"level 须为：{list(LEVEL_PARAM)}"}
        filters["alertLevel"] = LEVEL_PARAM[level]
    if drone_id:
        try:
            drones_core.hydrate()
            d = drones_core.find(drone_id)
        except DroneManageError:
            d = None
        filters["deviceSn"] = d["device_sn"] if d else drone_id
    if date_range and len(date_range) == 2:
        filters["beginTime"] = f"{date_range[0]} 00:00:00"
        filters["endTime"] = f"{date_range[1]} 23:59:59"
    try:
        result = get_client().list_alerts(filters)
    except DroneManageError as exc:
        return {"error": f"无人机平台不可达：{exc}", "alerts": [], "count": 0}
    return {
        "count": result["total"],
        "returned": len(result["rows"]),
        "alerts": [_alert_view(a) for a in result["rows"]],
    }


def get_alert_detail(alert_id: str) -> dict[str, Any]:
    try:
        a = get_client().get_alert(alert_id)
    except DroneManageError as exc:
        return {"error": f"无人机平台不可达：{exc}"}
    if not a:
        return {"error": f"告警 {alert_id} 不存在"}
    return _alert_view(a)


def handle_alert(alert_id: str, note: str) -> dict[str, Any]:
    """处理告警（低危写，审计经拦截器落盘）。note 为处置说明，必填。"""
    if not (note or "").strip():
        return {"error": "处置说明（note）必填：说明做了什么处置或确认了什么"}
    try:
        a = get_client().get_alert(alert_id)
        if not a:
            return {"error": f"告警 {alert_id} 不存在"}
        if a.get("alertStatus") != 0:
            return {"error": f"告警当前状态为「{STATUS_CN.get(a.get('alertStatus'))}」，仅未处理告警可操作"}
        get_client().handle_alert(alert_id, f"{note.strip()}（经低空智察Agent提交）")
    except DroneManageError as exc:
        return {"error": f"平台操作失败：{exc}"}
    return {"status": "handled", "alert_id": alert_id, "note": note.strip()}


def ignore_alert(alert_id: str, note: str | None = None) -> dict[str, Any]:
    try:
        a = get_client().get_alert(alert_id)
        if not a:
            return {"error": f"告警 {alert_id} 不存在"}
        if a.get("alertStatus") != 0:
            return {"error": f"告警当前状态为「{STATUS_CN.get(a.get('alertStatus'))}」，仅未处理告警可操作"}
        get_client().ignore_alert(alert_id)
    except DroneManageError as exc:
        return {"error": f"平台操作失败：{exc}"}
    return {"status": "ignored", "alert_id": alert_id, "note": note}


def get_unhandled_count() -> dict[str, Any]:
    try:
        n = get_client().alerts_unhandled_count()
    except DroneManageError as exc:
        return {"error": f"无人机平台不可达：{exc}"}
    return {"unhandled_count": n}


def get_device_health(drone_id: str) -> dict[str, Any]:
    """设备健康体检：在线状态 + 实时电量 + 未读 HMS 健康消息 + 未处理告警数。"""
    try:
        drones_core.hydrate()
    except DroneManageError as exc:
        return {"error": f"无人机平台不可达：{exc}"}
    d = drones_core.find(drone_id)
    if not d:
        return {"error": f"无人机 {drone_id} 不存在"}
    drones_core.enrich_battery(d)
    out: dict[str, Any] = {
        "drone_id": d["drone_id"],
        "device_sn": d.get("device_sn"),
        "model": d["model"],
        "online": d.get("online", False),
        "battery_pct": d.get("battery_pct"),
        "status": d.get("status_cn") or d.get("status"),
    }
    sn = d.get("device_sn")
    hms: list[dict[str, Any]] = []
    if sn:
        try:
            hms = get_client().device_hms_unread(sn)
        except DroneManageError as exc:
            out["hms_note"] = f"HMS 健康消息查询失败：{exc}"
    out["hms_unread"] = [
        {"level": h.get("level"), "message": h.get("messageZh") or h.get("key"),
         "time": h.get("createTime")}
        for h in hms[:10]
    ]
    out["hms_unread_count"] = len(hms)
    try:
        alerts = get_client().list_alerts(
            {"pageNum": 1, "pageSize": 1, "alertStatus": 0, "deviceSn": sn or drone_id}
        )
        out["unhandled_alerts"] = alerts["total"]
    except DroneManageError:
        out["unhandled_alerts"] = None
    healthy = out["online"] and not hms and not out.get("unhandled_alerts")
    out["conclusion"] = (
        "设备在线且无未读健康告警，状态良好" if healthy
        else "存在健康风险项，见 hms_unread / unhandled_alerts / online 字段"
    )
    return out
