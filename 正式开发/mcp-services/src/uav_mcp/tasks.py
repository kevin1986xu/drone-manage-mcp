"""高危写操作（人在环）与飞行任务域。

dispatch_drone / take_off：无有效 confirm_token 时**只登记待确认单**，
绝不执行（安全红线）。token 由审批服务签发（Agent 之外）。
真实起飞 = 平台创建 flighttask + 下发计划(publish)，两级开关：
  UAV_CREATE_REAL_TASK：确认后在平台创建任务（只建不飞）
  UAV_REAL_PUBLISH：下发计划到机场执行（**真起飞**）——需前一开关也开
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from uav_mcp import approval, config
from uav_mcp import drones as drones_core
from uav_mcp import routes as routes_core
from uav_mcp.drone_manage import DroneManageError, get_client
from uav_mcp.state import STATE

logger = logging.getLogger(__name__)


def dispatch_drone(drone_id: str, task_type: str, plot_ids: list[str], confirm_token: str | None = None) -> dict[str, Any]:
    try:
        drones_core.hydrate()
    except DroneManageError as exc:
        return {"error": f"无人机平台不可达：{exc}"}
    d = drones_core.find(drone_id)
    if not d:
        return {"error": f"无人机 {drone_id} 不存在"}
    if confirm_token is None:
        summary = {
            "title": f"锁定无人机 {d['drone_id']}",
            "rows": [
                {"label": "执行无人机", "value": f"{d['drone_id']} · {d['model']}"},
                {"label": "任务类型", "value": task_type},
                {"label": "关联图斑", "value": " / ".join(i.upper() for i in plot_ids)},
                {"label": "当前电量", "value": f"{d['battery_pct']}%" if d.get("battery_pct") is not None else "待遥测"},
            ],
        }
        item = approval.create_pending_action(
            "dispatch_drone",
            {"drone_id": d["drone_id"], "task_type": task_type, "plot_ids": [i.upper() for i in plot_ids]},
            summary,
        )
        return {
            "status": "requires_confirmation",
            "action_id": item["action_id"],
            "action": "dispatch_drone",
            "summary": summary,
            "message": "高危操作：已生成待确认单，等待人工在界面上确认后执行",
        }
    item = approval.validate_and_consume("dispatch_drone", confirm_token)
    if not item:
        return approval.refusal("dispatch_drone")
    return drones_core.lock_drone(**item["params"])


def take_off(drone_id: str, route_id: str, confirm_token: str | None = None) -> dict[str, Any]:
    try:
        drones_core.hydrate()
    except DroneManageError as exc:
        return {"error": f"无人机平台不可达：{exc}"}
    d = drones_core.find(drone_id)
    if not d:
        return {"error": f"无人机 {drone_id} 不存在"}
    detail = routes_core.get_route_detail(route_id)
    if detail.get("error"):
        return detail
    if confirm_token is None:
        covered = " / ".join(c["plot_id"] for c in detail["covered_plots"])
        summary = {
            "title": f"起飞指令 take_off · {d['drone_id']}",
            "rows": [
                {"label": "执行无人机", "value": f"{d['drone_id']} · {d['model']}"},
                {"label": "航线", "value": f"{detail['route_id']} rev.{detail['version']} · {detail['length_km']} km"},
                {"label": "覆盖图斑", "value": covered},
                {"label": "预计时长", "value": f"{detail['duration_min']} min"},
            ],
        }
        item = approval.create_pending_action(
            "take_off", {"drone_id": d["drone_id"], "route_id": detail["route_id"]}, summary
        )
        return {
            "status": "requires_confirmation",
            "action_id": item["action_id"],
            "action": "take_off",
            "summary": summary,
            "message": "高危操作：起飞确认卡片已在界面弹出，等待人工点击确认。"
            "请勿再次调用 take_off、勿自行构造 confirm_token；确认后系统会给你带 token 的指令。",
        }
    item = approval.validate_and_consume("take_off", confirm_token)
    if not item:
        return approval.refusal("take_off")

    # 以确认单中锁定的参数为准执行，防止确认内容与实际执行不一致
    d = drones_core.find(item["params"]["drone_id"])
    detail = routes_core.get_route_detail(item["params"]["route_id"])
    task_id = STATE.next_id("T", 4)
    d["status"] = "flying"

    platform_task = None
    _, rev = routes_core._rev(detail["route_id"])
    if config.UAV_CREATE_REAL_TASK and rev and rev.get("platform_route_id") and d.get("device_sn"):
        try:
            ptid = uuid.uuid4().hex
            platform_task = get_client().create_flight_task(
                ptid, f"{config.ROUTE_NAME_PREFIX}核查-{task_id}", rev["platform_route_id"], d["device_sn"]
            )
            platform_task["platform_task_id"] = ptid
            if config.UAV_REAL_PUBLISH:
                pub = get_client().publish_flight_task(ptid)
                platform_task["published"] = pub
                platform_task["real_takeoff"] = True
            else:
                platform_task["published"] = False
        except Exception as exc:  # noqa: BLE001
            logger.warning("平台创建/下发任务失败（本地任务继续）：%s", exc)

    task = {
        "flight_task_id": task_id,
        "drone_id": d["drone_id"],
        "route_id": detail["route_id"],
        "route_version": detail["version"],
        "status": "flying",
        "started_at": time.time(),
        "duration_min": detail["duration_min"],
        "covered_plots": [c["plot_id"] for c in detail["covered_plots"]],
        "platform_task": platform_task,
    }
    STATE.flight_tasks[task_id] = task
    out = {
        "status": "airborne",
        "flight_task_id": task_id,
        "drone_id": d["drone_id"],
        "route_id": detail["route_id"],
        "duration_min": detail["duration_min"],
    }
    if platform_task:
        out["platform_task"] = platform_task
        out["note"] = (
            "已在管理平台创建任务并下发计划到机场执行（真实起飞）"
            if platform_task.get("real_takeoff")
            else "已在管理平台创建任务（待执行，未下发；开启 UAV_REAL_PUBLISH 才会真下发起飞）"
        )
    else:
        out["note"] = "未在平台创建任务（UAV_CREATE_REAL_TASK 未开启或航线无平台编号）"
    return out


def get_task_status(flight_task_id: str) -> dict[str, Any]:
    t = STATE.flight_tasks.get(flight_task_id.upper()) or STATE.flight_tasks.get(flight_task_id)
    if not t:
        return {"error": f"任务 {flight_task_id} 不存在"}
    # 平台任务：以平台状态为准
    ptask = t.get("platform_task") or {}
    if ptask.get("platform_task_id"):
        try:
            remote = get_client().get_flight_task(ptask["platform_task_id"])
            return {
                "flight_task_id": t["flight_task_id"],
                "platform_task_id": ptask["platform_task_id"],
                "status": remote.get("status"),
                "drone_id": t["drone_id"],
                "route_id": t["route_id"],
                "covered_plots": t["covered_plots"],
                "source": "平台任务状态",
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("平台任务状态查询失败，返回本地状态：%s", exc)
    # 本地任务：按时长推进（未接平台任务时的估算口径）
    elapsed_min = (time.time() - t["started_at"]) / 60
    progress = min(100, round(elapsed_min / t["duration_min"] * 100)) if t["duration_min"] else 100
    if progress >= 100 and t["status"] == "flying":
        t["status"] = "completed"
        d = STATE.drones.get(t["drone_id"])
        if d:
            d["status"] = "idle"
    return {
        "flight_task_id": t["flight_task_id"],
        "status": t["status"],
        "progress_pct": progress,
        "drone_id": t["drone_id"],
        "route_id": t["route_id"],
        "covered_plots": t["covered_plots"],
        "source": "本地估算（未创建平台任务）",
    }


def get_task_report(flight_task_id: str) -> dict[str, Any]:
    """任务成果报告（举证摘要）。任务完成后可用；进行中返回带进度的提示。"""
    t = STATE.flight_tasks.get(flight_task_id.upper()) or STATE.flight_tasks.get(flight_task_id)
    if not t:
        return {"error": f"任务 {flight_task_id} 不存在"}
    status = get_task_status(t["flight_task_id"])  # 顺带推进完成态
    if t["status"] != "completed":
        return {
            "error": f"任务 {t['flight_task_id']} 尚未完成（{status.get('status')}，进度 {status.get('progress_pct', '—')}%），完成后才能生成成果报告",
        }
    detail = routes_core.get_route_detail(t["route_id"], t.get("route_version"))
    photo_num = 4
    _, rev = routes_core._rev(t["route_id"], t.get("route_version"))
    if rev:
        photo_num = rev.get("photo_num", 4)
    plots = t.get("covered_plots", [])
    finished_at = t.get("started_at", 0) + t.get("duration_min", 0) * 60
    return {
        "flight_task_id": t["flight_task_id"],
        "drone_id": t["drone_id"],
        "route": {
            "route_id": t["route_id"],
            "version": t.get("route_version"),
            "length_km": detail.get("length_km"),
            "altitude_m": detail.get("altitude_m"),
        },
        "covered_plots": plots,
        "photos": {"per_plot": photo_num, "total": photo_num * len(plots)},
        "started_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(t.get("started_at", 0))),
        "finished_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(finished_at)),
        "duration_min": t.get("duration_min"),
        "evidence_note": "照片按图斑编号归档于管理平台媒体库，可作为图斑核查举证材料；"
        "AI 变化识别分析待智能识别能力接入（规划中）",
    }


def list_task_history(status: str | None = None, drone_id: str | None = None, limit: int = 10) -> dict[str, Any]:
    """历史任务列表（倒序）。可按状态（flying/completed）或无人机过滤。"""
    items = []
    for t in STATE.flight_tasks.values():
        get_task_status(t["flight_task_id"])  # 推进完成态
        if status and t["status"] != status:
            continue
        if drone_id and drone_id not in t["drone_id"]:
            continue
        items.append({
            "flight_task_id": t["flight_task_id"],
            "status": t["status"],
            "drone_id": t["drone_id"],
            "route_id": t["route_id"],
            "covered_plots_count": len(t.get("covered_plots", [])),
            "started_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(t.get("started_at", 0))),
            "duration_min": t.get("duration_min"),
        })
    items.sort(key=lambda i: i["started_at"], reverse=True)
    return {"total": len(items), "tasks": items[: max(1, min(limit, 50))]}
