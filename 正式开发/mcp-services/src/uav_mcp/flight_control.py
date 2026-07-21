"""实时飞控域核心逻辑（uav-flight-control-mcp，docs/05 §2.4 / docs/06 主线六）。

三档安全口径（docs/05 §4）：
- **紧急白名单⚡**（return_home / emergency_stop）：止损动作，免 confirm_token
  秒执行；防注入三件套——①前置条件：该机确有活动飞行才可调（地面机直接拒，
  防"告警备注里一句话就中断合法飞行"的注入 DoS）；②同机频率限制（冷却窗内
  重复调用拒绝）；③评测注入反向用例（平台数据里的指令≠用户指令）。
  执行即通知（返回 notify 字段供播报），事后需人工在平台关单确认。
- **高危🔒**（fly_to_point / takeoff_to_point / speaker_tts / set_height_limit /
  pause·resume_task）：confirm_token 两阶段（approval.py），拦截器名单登记。
- **中危写**（light_control / camera_take_photo）：免 token 入审计。

设备级操作锁（device_lock）：同机 flight 类写动作互斥；紧急动作**旁路锁**
（止损优先，不能被普通操作挡住）。

真机依赖：speaker_tts / light_control 走 DRC 下行需 DRC 通道已建立，
takeoff_to_point / fly_to_point 需飞行控制权——联调待真机环境（docs/05 P1 依赖）。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from uav_mcp import approval, device_lock
from uav_mcp import drones as drones_core
from uav_mcp.drone_manage import DroneManageError, get_client
from uav_mcp.state import STATE

logger = logging.getLogger(__name__)

# 紧急动作冷却窗（秒）：同机同动作窗口内重复调用拒绝（防注入重放/误触发连击）
EMERGENCY_COOLDOWN_S = 60
_emergency_calls: dict[tuple[str, str], float] = {}

# DJI mode_code 飞行中区间（3 manual…12 三桨叶降落）；0-2 待机/准备、13+ 升级/失联
_FLYING_MODES = set(range(3, 13))


def _find(drone_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    drones_core.hydrate()
    d = drones_core.find(drone_id)
    if not d or not d.get("device_sn"):
        return None, {"error": f"设备 {drone_id} 不存在或无 SN"}
    return d, None


def _active_flight_evidence(d: dict[str, Any]) -> str | None:
    """返回活动飞行证据描述；无证据返回 None（紧急动作前置条件）。"""
    if d.get("status") == "flying":
        return "本进程任务态=flying"
    for t in STATE.flight_tasks.values():
        if t.get("drone_id") == d["drone_id"] and t.get("status") == "flying":
            return f"进行中任务 {t.get('flight_task_id')}"
    try:
        osd = get_client().osd_latest(d["device_sn"])
        mode = (osd or {}).get("modeCode")
        if mode in _FLYING_MODES:
            return f"OSD modeCode={mode}（飞行中）"
    except DroneManageError:
        pass
    return None


def _emergency(drone_id: str, action: str, executor) -> dict[str, Any]:
    """紧急白名单动作统一入口：前置条件 + 冷却窗 + 旁路锁 + 强审计标记。"""
    d, err = _find(drone_id)
    if err:
        return err
    evidence = _active_flight_evidence(d)
    if not evidence:
        return {
            "status": "rejected",
            "reason": f"{d['drone_id']} 当前无活动飞行，{action} 不适用（紧急动作仅对飞行中设备开放；"
            "若确认设备在飞请稍后重试或人工处置）。",
        }
    key = (d["device_sn"], action)
    now = time.time()
    last = _emergency_calls.get(key, 0.0)
    if now - last < EMERGENCY_COOLDOWN_S:
        return {
            "status": "rejected",
            "reason": f"{action} 在 {int(now - last)} 秒前刚对该机执行过（冷却 {EMERGENCY_COOLDOWN_S}s 内拒绝重复），"
            "如首次未生效请人工介入。",
        }
    try:
        result = executor(d["device_sn"])
    except DroneManageError as exc:
        return {"error": f"{action} 下发失败：{exc}", "hint": "请立即改用平台人工操作"}
    _emergency_calls[key] = now
    logger.warning("⚡ 紧急动作已执行：%s drone=%s 依据=%s", action, d["drone_id"], evidence)
    return {
        "status": "executed",
        "action": action,
        "drone_id": d["drone_id"],
        "emergency": True,  # 审计强标记
        "evidence": evidence,
        "platform_response": result,
        "notify": f"⚡ 已对 {d['drone_id']} 执行{action}（免确认止损动作）。请立即在平台确认设备状态并关单。",
    }


def return_home(drone_id: str) -> dict[str, Any]:
    return _emergency(drone_id, "一键返航",
                      lambda sn: get_client().dock_service_job(sn, "return_home"))


def emergency_stop(drone_id: str) -> dict[str, Any]:
    return _emergency(drone_id, "紧急停止",
                      lambda sn: get_client().emergency_stop(sn))


# ── 高危🔒：confirm_token 两阶段 ─────────────────────────────

def _job_of(task_id: str) -> str | None:
    """flight_task_id → 平台 wayline jobId（暂停/恢复要 jobId）。"""
    try:
        rows = get_client().wayline_jobs_search({"pageNum": 1, "pageSize": 20, "taskId": task_id})
        for r in rows:
            if r.get("jobId"):
                return str(r["jobId"])
    except DroneManageError:
        pass
    return None


def _pause_resume(task_id: str, resume: bool, confirm_token: str | None) -> dict[str, Any]:
    action = "resume_task" if resume else "pause_task"
    label = "恢复" if resume else "暂停"
    if confirm_token is None:
        item = approval.create_pending_action(
            action, {"task_id": task_id},
            {"rows": [["动作", f"{label}任务"], ["任务", task_id],
                      ["影响", "飞行中航线立即" + ("续飞" if resume else "悬停暂停")]]},
        )
        return {"status": "requires_confirmation", "action_id": item["action_id"],
                "action": action, "summary": item["summary"],
                "message": f"{label}任务为高危操作，已生成确认单，请人工确认。"}
    item = approval.validate_and_consume(action, confirm_token)
    if not item:
        return approval.refusal(action)
    tid = item["params"]["task_id"]
    job_id = _job_of(tid)
    if not job_id:
        return {"error": f"任务 {tid} 未找到平台执行 job（未开始执行或已结束，无法{label}）"}
    try:
        get_client().update_wayline_job_status(job_id, 1 if resume else 0)
    except DroneManageError as exc:
        return {"error": f"{label}下发失败：{exc}"}
    return {"status": "resumed" if resume else "paused", "task_id": tid, "job_id": job_id}


def pause_task(task_id: str, confirm_token: str | None = None) -> dict[str, Any]:
    return _pause_resume(task_id, resume=False, confirm_token=confirm_token)


def resume_task(task_id: str, confirm_token: str | None = None) -> dict[str, Any]:
    return _pause_resume(task_id, resume=True, confirm_token=confirm_token)


def fly_to_point(drone_id: str, lon: float, lat: float, alt_m: float,
                 confirm_token: str | None = None) -> dict[str, Any]:
    if confirm_token is None:
        item = approval.create_pending_action(
            "fly_to_point", {"drone_id": drone_id, "lon": lon, "lat": lat, "alt_m": alt_m},
            {"rows": [["动作", "指点飞行"], ["设备", drone_id],
                      ["目标", f"({lon:.6f}, {lat:.6f}) 高度 {alt_m}m"],
                      ["前置", "需已夺取飞行控制权且无人机为手动模式"]]},
        )
        return {"status": "requires_confirmation", "action_id": item["action_id"],
                "action": "fly_to_point", "summary": item["summary"],
                "message": "指点飞行为高危操作，已生成确认单，请人工确认。"}
    item = approval.validate_and_consume("fly_to_point", confirm_token)
    if not item:
        return approval.refusal("fly_to_point")
    p = item["params"]
    d, err = _find(p["drone_id"])
    if err:
        return err
    ok, holding = device_lock.acquire(d["device_sn"], "flight", "fly_to_point", ttl_s=300)
    if not ok:
        return {"error": f"设备正被其他飞行操作占用（{holding}），拒绝并发指点飞行"}
    cli = get_client()
    try:
        cli.grab_flight_authority(d["device_sn"])
        result = cli.fly_to_point(d["device_sn"], {
            "max_speed": 14,
            "points": [{"longitude": p["lon"], "latitude": p["lat"], "height": p["alt_m"]}],
        })
    except DroneManageError as exc:
        device_lock.release(d["device_sn"], "flight")
        return {"error": f"指点飞行下发失败：{exc}",
                "hint": "需真机环境验证（控制权/手动模式前置），失败请人工接管"}
    return {"status": "flying_to_point", "drone_id": d["drone_id"],
            "target": [p["lon"], p["lat"], p["alt_m"]], "platform_response": result,
            "note": "到点后悬停；stop_fly_to_point 可中止（锁 5 分钟自动释放）"}


def stop_fly_to_point(drone_id: str) -> dict[str, Any]:
    """中止指点飞行（悬停）。低危止损口径：免 token 入审计。"""
    d, err = _find(drone_id)
    if err:
        return err
    try:
        get_client().fly_to_point_stop(d["device_sn"])
    except DroneManageError as exc:
        return {"error": f"中止指点飞行失败：{exc}"}
    device_lock.release(d["device_sn"], "flight")
    return {"status": "stopped", "drone_id": d["drone_id"], "note": "无人机就地悬停"}


def takeoff_to_point(drone_id: str, lon: float, lat: float, alt_m: float = 100.0,
                     confirm_token: str | None = None) -> dict[str, Any]:
    if confirm_token is None:
        item = approval.create_pending_action(
            "takeoff_to_point", {"drone_id": drone_id, "lon": lon, "lat": lat, "alt_m": alt_m},
            {"rows": [["动作", "一键起飞至点位"], ["设备", drone_id],
                      ["目标", f"({lon:.6f}, {lat:.6f}) 高度 {alt_m}m"],
                      ["提示", "应急响应第一动作：从机场直接起飞奔赴事发点"]]},
        )
        return {"status": "requires_confirmation", "action_id": item["action_id"],
                "action": "takeoff_to_point", "summary": item["summary"],
                "message": "一键起飞为高危操作，已生成确认单，请人工确认。"}
    item = approval.validate_and_consume("takeoff_to_point", confirm_token)
    if not item:
        return approval.refusal("takeoff_to_point")
    p = item["params"]
    d, err = _find(p["drone_id"])
    if err:
        return err
    ok, holding = device_lock.acquire(d["device_sn"], "flight", "takeoff_to_point", ttl_s=600)
    if not ok:
        return {"error": f"设备正被其他飞行操作占用（{holding}）"}
    alt = min(float(p["alt_m"]), 120.0)  # 法定 120m 硬上限
    try:
        result = get_client().takeoff_to_point(d["device_sn"], {
            "target_longitude": p["lon"], "target_latitude": p["lat"],
            "target_height": alt, "security_takeoff_height": max(alt, 60.0),
            "rth_altitude": max(alt, 100.0), "max_speed": 14,
            "rc_lost_action": 2,  # 失联返航
        })
    except DroneManageError as exc:
        device_lock.release(d["device_sn"], "flight")
        return {"error": f"一键起飞下发失败：{exc}", "hint": "真机联调项：确认设备为机场在位且具备起飞条件"}
    return {"status": "airborne_to_point", "drone_id": d["drone_id"],
            "target": [p["lon"], p["lat"], alt], "platform_response": result,
            "notify": f"{d['drone_id']} 已起飞奔赴事发点（预计目标高度 {alt}m）"}


def speaker_tts(drone_id: str, text: str, confirm_token: str | None = None) -> dict[str, Any]:
    if confirm_token is None:
        item = approval.create_pending_action(
            "speaker_tts", {"drone_id": drone_id, "text": text},
            {"rows": [["动作", "喊话器 TTS 播放"], ["设备", drone_id],
                      ["播放原文", text], ["红线", "喊话内容以本确认单原文为准，不得改写"]]},
        )
        return {"status": "requires_confirmation", "action_id": item["action_id"],
                "action": "speaker_tts", "summary": item["summary"],
                "message": "喊话为高危操作（对外发声），确认单已生成——请人工核准喊话原文。"}
    item = approval.validate_and_consume("speaker_tts", confirm_token)
    if not item:
        return approval.refusal("speaker_tts")
    p = item["params"]
    d, err = _find(p["drone_id"])
    if err:
        return err
    cli = get_client()
    try:
        cli.drc_speaker(d["device_sn"], "tts_set", {"text": p["text"]})
        cli.drc_speaker(d["device_sn"], "play_tts", {})
    except DroneManageError as exc:
        return {"error": f"喊话下发失败：{exc}", "hint": "真机联调项：需 DRC 通道已建立且挂载喊话器"}
    return {"status": "playing", "drone_id": d["drone_id"], "text": p["text"]}


def light_control(drone_id: str, on: bool, brightness: int | None = None) -> dict[str, Any]:
    """探照灯（中危写，免 token 入审计）。"""
    d, err = _find(drone_id)
    if err:
        return err
    cli = get_client()
    try:
        cli.drc_light(d["device_sn"], "mode_set", {"mode": 1 if on else 0})
        if on and brightness is not None:
            cli.drc_light(d["device_sn"], "brightness_set",
                          {"brightness": max(0, min(100, int(brightness)))})
    except DroneManageError as exc:
        return {"error": f"探照灯控制失败：{exc}", "hint": "真机联调项：需 DRC 通道且挂载探照灯"}
    return {"status": "on" if on else "off", "drone_id": d["drone_id"], "brightness": brightness}


def camera_take_photo(drone_id: str) -> dict[str, Any]:
    """应急现场单拍取证（写入审计；照片经媒体域 list_media 取回）。"""
    d, err = _find(drone_id)
    if err:
        return err
    try:
        result = get_client().payload_command(d["device_sn"], "camera_photo_take",
                                              {"payload_index": "auto"})
    except DroneManageError as exc:
        return {"error": f"拍照下发失败：{exc}", "hint": "真机联调项：需负载控制权"}
    return {"status": "photo_taken", "drone_id": d["drone_id"], "platform_response": result,
            "note": "照片回传后可在媒体域 list_media 查看"}


def set_height_limit(drone_id: str, limit_m: int, confirm_token: str | None = None) -> dict[str, Any]:
    if not 20 <= limit_m <= 120:
        return {"error": "限高须在 20-120m（法定上限 120m）"}
    if confirm_token is None:
        item = approval.create_pending_action(
            "set_height_limit", {"drone_id": drone_id, "limit_m": limit_m},
            {"rows": [["动作", "设置无人机限高"], ["设备", drone_id], ["限高", f"{limit_m}m"]]},
        )
        return {"status": "requires_confirmation", "action_id": item["action_id"],
                "action": "set_height_limit", "summary": item["summary"],
                "message": "改限高为高危操作，已生成确认单。"}
    item = approval.validate_and_consume("set_height_limit", confirm_token)
    if not item:
        return approval.refusal("set_height_limit")
    p = item["params"]
    d, err = _find(p["drone_id"])
    if err:
        return err
    try:
        get_client().set_drone_height_limit(d["device_sn"], int(p["limit_m"]))
    except DroneManageError as exc:
        return {"error": f"设置限高失败：{exc}"}
    return {"status": "ok", "drone_id": d["drone_id"], "limit_m": p["limit_m"]}


def check_takeover_no_fly_zone(lon: float, lat: float, altitude_m: float | None = None) -> dict[str, Any]:
    """接管前限飞检查（纯读）：人工/Agent 接管设备前查该位置限飞告警状态。"""
    try:
        data = get_client().takeover_no_fly_zone_check(lon, lat, altitude_m)
    except DroneManageError as exc:
        return {"error": f"限飞检查失败：{exc}"}
    return {"position": [lon, lat], "altitude_m": altitude_m, "result": data,
            "note": "结果为平台限飞区告警判定，接管操作前必查"}
