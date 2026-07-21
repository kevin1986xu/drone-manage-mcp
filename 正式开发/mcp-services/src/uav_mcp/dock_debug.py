"""机场调试与远程运维域核心逻辑（uav-dock-debug-mcp，docs/05 §2.8 / docs/06 主线七）。

顺序依赖（硬约束，docs/06）：**进 debug_mode → 操作 → 复位 → 退 debug**。
- debug_mode open 持有设备锁（category=debug，TTL 30 分钟），close 释放；
- 舱盖/推杆/无人机电源/充电/重启/电池保养等动作要求本进程已开调试模式
  （锁持有者=debug_mode），乱序调用直接拒绝（平台侧 jobs/* 也有 checkDebugCondition，
  这里是第一道闸）；
- **临近排期拒绝进调试**：未来 2 小时内该机场有排期任务则拒绝 debug_mode open。

平台两条下行路径：
- /api/dockDebug/* 固定 GET 路由（debug 开关/舱盖/无人机电源/充电/重启）；
- /control/api/v1/devices/{sn}/jobs/{service}（推杆/空调/补光灯/电池保养——
  固定路由没暴露的走这条）。
高危🔒全部 confirm_token 两阶段；空调/补光灯中危免 token 入审计。
真机依赖：全部动作需真机机场验证（docs/05 P1 依赖），失败返回如实提示。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from uav_mcp import approval, device_lock
from uav_mcp import drones as drones_core
from uav_mcp.drone_manage import DroneManageError, get_client

logger = logging.getLogger(__name__)

DEBUG_TTL_S = 1800  # 调试模式锁 30 分钟

# 高危动作 → (执行通道, 平台参数)；channel: fixed=/api/dockDebug 固定路由, job=jobs/{service}
_ACTIONS: dict[str, dict[str, Any]] = {
    "cover_open": {"channel": "fixed", "path": "dock/coverOpen", "label": "开舱盖"},
    "cover_close": {"channel": "fixed", "path": "dock/coverClose", "label": "关舱盖"},
    "cover_force_close": {"channel": "fixed", "path": "dock/forceCoverClose", "label": "强制关舱盖"},
    "putter_open": {"channel": "job", "service": "putter_open", "label": "推杆展开"},
    "putter_close": {"channel": "job", "service": "putter_close", "label": "推杆归中"},
    "drone_power_on": {"channel": "fixed", "path": "drone/open", "label": "舱内无人机开机"},
    "drone_power_off": {"channel": "fixed", "path": "drone/close", "label": "舱内无人机关机"},
    "charge_on": {"channel": "fixed", "path": "drone/chargeOpen", "label": "开始充电"},
    "charge_off": {"channel": "fixed", "path": "drone/chargeClose", "label": "停止充电"},
    "device_reboot": {"channel": "fixed", "path": "dock/reboot", "label": "重启机场"},
    "battery_maintenance_on": {"channel": "job", "service": "battery_maintenance_switch",
                               "param": {"action": 1}, "label": "开启电池保养"},
    "battery_maintenance_off": {"channel": "job", "service": "battery_maintenance_switch",
                                "param": {"action": 0}, "label": "关闭电池保养"},
}

_AIRCON_MODES = {"关闭": 0, "制冷": 1, "制热": 2, "除湿": 3}


def _find(dock_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    drones_core.hydrate()
    d = drones_core.find(dock_id)
    if not d or not d.get("device_sn"):
        return None, {"error": f"机场 {dock_id} 不存在或无 SN"}
    return d, None


def _upcoming_jobs(device_sn: str, hours: int = 2) -> list[str]:
    """未来 hours 小时内的排期任务名（进调试前检查）。"""
    now = datetime.now()
    try:
        rows = get_client().wayline_jobs_search({
            "pageNum": 1, "pageSize": 20, "deviceSn": device_sn,
            "beginTimeStart": now.strftime("%Y-%m-%d %H:%M:%S"),
            "beginTimeEnd": (now + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S"),
        })
        return [r.get("jobName") or str(r.get("jobId")) for r in rows]
    except DroneManageError:
        return []


def _debug_held(device_sn: str) -> bool:
    return device_lock.holder(device_sn, "debug") == "debug_mode"


def debug_mode(dock_id: str, open_: bool, confirm_token: str | None = None) -> dict[str, Any]:
    action = "debug_mode"
    label = "进入调试模式" if open_ else "退出调试模式"
    if confirm_token is None:
        d, err = _find(dock_id)
        if err:
            return err
        if open_:
            upcoming = _upcoming_jobs(d["device_sn"])
            if upcoming:
                return {"status": "rejected",
                        "reason": f"机场 {d['drone_id']} 未来 2 小时内有排期任务（{', '.join(upcoming[:3])}），"
                        "拒绝进入调试模式（调试会中断任务执行）。请先改排期或等任务窗口过后再试。"}
        item = approval.create_pending_action(
            action, {"dock_id": dock_id, "open": open_},
            {"rows": [["动作", label], ["机场", dock_id],
                      ["影响", "调试期间机场不可执行飞行任务" if open_ else "结束调试，机场恢复可用"]]},
        )
        return {"status": "requires_confirmation", "action_id": item["action_id"],
                "action": action, "summary": item["summary"],
                "message": f"{label}为高危操作，已生成确认单。"}
    item = approval.validate_and_consume(action, confirm_token)
    if not item:
        return approval.refusal(action)
    p = item["params"]
    d, err = _find(p["dock_id"])
    if err:
        return err
    sn = d["device_sn"]
    try:
        get_client().dock_debug(sn, "debug/open" if p["open"] else "debug/close")
    except DroneManageError as exc:
        return {"error": f"{label}失败：{exc}", "hint": "真机联调项：确认机场在线"}
    if p["open"]:
        device_lock.acquire(sn, "debug", "debug_mode", ttl_s=DEBUG_TTL_S)
        note = "已进入调试模式（锁 30 分钟）。操作完成后必须复位设备并退出调试模式。"
    else:
        device_lock.release(sn, "debug")
        note = "已退出调试模式，机场恢复可用。"
    return {"status": "debug_on" if p["open"] else "debug_off", "dock_id": d["drone_id"], "note": note}


def _debug_action(dock_id: str, action_key: str, confirm_token: str | None) -> dict[str, Any]:
    """高危调试动作统一两阶段：顺序前置（须已进调试）+ confirm_token。"""
    spec = _ACTIONS[action_key]
    if confirm_token is None:
        d, err = _find(dock_id)
        if err:
            return err
        if not _debug_held(d["device_sn"]):
            return {"status": "rejected",
                    "reason": f"{spec['label']}要求机场先进入调试模式（debug_mode open）——"
                    "顺序：进调试 → 操作 → 复位 → 退调试，禁止跳步。"}
        item = approval.create_pending_action(
            action_key, {"dock_id": dock_id},
            {"rows": [["动作", spec["label"]], ["机场", dock_id], ["前置", "调试模式已开启 ✓"]]},
        )
        return {"status": "requires_confirmation", "action_id": item["action_id"],
                "action": action_key, "summary": item["summary"],
                "message": f"{spec['label']}为高危操作，已生成确认单。"}
    item = approval.validate_and_consume(action_key, confirm_token)
    if not item:
        return approval.refusal(action_key)
    d, err = _find(item["params"]["dock_id"])
    if err:
        return err
    sn = d["device_sn"]
    if not _debug_held(sn):
        return {"status": "rejected", "reason": "调试模式已失效（锁过期或已退出），请重新进入调试模式"}
    cli = get_client()
    try:
        if spec["channel"] == "fixed":
            result = cli.dock_debug(sn, spec["path"])
        else:
            result = cli.dock_service_job(sn, spec["service"], spec.get("param"))
    except DroneManageError as exc:
        return {"error": f"{spec['label']}失败：{exc}", "hint": "真机联调项"}
    return {"status": "done", "action": action_key, "label": spec["label"],
            "dock_id": d["drone_id"], "platform_response": result}


def dock_cover(dock_id: str, op: str, confirm_token: str | None = None) -> dict[str, Any]:
    key = {"open": "cover_open", "close": "cover_close", "force_close": "cover_force_close"}.get(op)
    if not key:
        return {"error": "op 须为 open / close / force_close"}
    return _debug_action(dock_id, key, confirm_token)


def dock_putter(dock_id: str, op: str, confirm_token: str | None = None) -> dict[str, Any]:
    key = {"open": "putter_open", "close": "putter_close"}.get(op)
    if not key:
        return {"error": "op 须为 open / close"}
    return _debug_action(dock_id, key, confirm_token)


def drone_power(dock_id: str, on: bool, confirm_token: str | None = None) -> dict[str, Any]:
    return _debug_action(dock_id, "drone_power_on" if on else "drone_power_off", confirm_token)


def charge_control(dock_id: str, on: bool, confirm_token: str | None = None) -> dict[str, Any]:
    return _debug_action(dock_id, "charge_on" if on else "charge_off", confirm_token)


def device_reboot(dock_id: str, confirm_token: str | None = None) -> dict[str, Any]:
    return _debug_action(dock_id, "device_reboot", confirm_token)


def battery_maintenance(dock_id: str, on: bool, confirm_token: str | None = None) -> dict[str, Any]:
    return _debug_action(dock_id, "battery_maintenance_on" if on else "battery_maintenance_off",
                         confirm_token)


def air_conditioner(dock_id: str, mode: str) -> dict[str, Any]:
    """空调模式（中危写，免 token 入审计；不要求调试模式——温控是驻场常态操作）。"""
    if mode not in _AIRCON_MODES:
        return {"error": f"mode 须为：{list(_AIRCON_MODES)}"}
    d, err = _find(dock_id)
    if err:
        return err
    try:
        get_client().dock_service_job(d["device_sn"], "air_conditioner_mode_switch",
                                      {"action": _AIRCON_MODES[mode]})
    except DroneManageError as exc:
        return {"error": f"空调控制失败：{exc}", "hint": "真机联调项"}
    return {"status": "ok", "dock_id": d["drone_id"], "mode": mode}


def supplement_light(dock_id: str, on: bool) -> dict[str, Any]:
    """舱内补光灯（中危写，免 token 入审计）。"""
    d, err = _find(dock_id)
    if err:
        return err
    try:
        get_client().dock_service_job(
            d["device_sn"], "supplement_light_open" if on else "supplement_light_close")
    except DroneManageError as exc:
        return {"error": f"补光灯控制失败：{exc}", "hint": "真机联调项"}
    return {"status": "on" if on else "off", "dock_id": d["drone_id"]}


def get_dock_environment(dock_id: str) -> dict[str, Any]:
    """机场环境读数（纯读）：温湿度/风速/雨量/舱内状态——巡检体检第一步。"""
    d, err = _find(dock_id)
    if err:
        return err
    try:
        osd = get_client().dock_osd_latest(d["device_sn"])
    except DroneManageError as exc:
        return {"error": f"机场环境查询失败：{exc}"}
    if not osd:
        return {"dock_id": d["drone_id"], "error": "无 OSD 数据（机场可能离线）"}
    return {
        "dock_id": d["drone_id"],
        "environment": {
            "temperature_c": osd.get("temperature"),
            "humidity_pct": osd.get("humidity"),
            "wind_speed_ms": osd.get("windSpeed"),
            "rainfall": osd.get("rainfall"),
            "cover_state": osd.get("coverState"),
            "drone_in_dock": osd.get("droneInDock"),
            "battery_pct": osd.get("batteryPercent") or osd.get("capacityPercent"),
        },
        "debug_mode_held": _debug_held(d["device_sn"]),
        "raw_time": osd.get("time") or osd.get("createTime"),
    }
