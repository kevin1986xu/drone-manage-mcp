"""高危写操作（人在环）与飞行任务域。

dispatch_drone / take_off：无有效 confirm_token 时**只生成待确认单**，
绝不执行（安全红线，见《开发计划》§六）。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app import config
from app.core import confirm, drones as drones_core, routes as routes_core
from app.core.store import STORE
from app.datasource import get_real

logger = logging.getLogger(__name__)


def dispatch_drone(drone_id: str, task_type: str, plot_ids: list[str], confirm_token: str | None = None) -> dict[str, Any]:
    d = drones_core._find(drone_id)
    if not d:
        return {"error": f"无人机 {drone_id} 不存在"}
    if confirm_token is None:
        summary = {
            "title": f"锁定无人机 {d['drone_id']}",
            "rows": [
                {"label": "执行无人机", "value": f"{d['drone_id']} · {d['model']}"},
                {"label": "任务类型", "value": task_type},
                {"label": "关联图斑", "value": " / ".join(i.upper() for i in plot_ids)},
                {"label": "当前电量", "value": f"{d['battery_pct']}%"},
            ],
        }
        item = confirm.create_pending_action(
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
    item = confirm.validate_and_consume("dispatch_drone", confirm_token)
    if not item:
        return confirm.refusal("dispatch_drone")
    return drones_core.lock_drone(**item["params"])


def take_off(drone_id: str, route_id: str, confirm_token: str | None = None) -> dict[str, Any]:
    d = drones_core._find(drone_id)
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
        item = confirm.create_pending_action(
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
    item = confirm.validate_and_consume("take_off", confirm_token)
    if not item:
        return confirm.refusal("take_off")

    # 以确认单中锁定的参数为准执行，防止确认内容与实际执行不一致
    d = STORE.drones[item["params"]["drone_id"]]
    detail = routes_core.get_route_detail(item["params"]["route_id"])
    task_id = STORE.next_id("T", 4)
    d["status"] = "flying"

    # 真实模式：在平台创建飞行任务（只建不下发——平台自动调度器可能执行
    # 待执行任务，故默认关闭，DRONE_CREATE_REAL_TASK=1 才开）
    platform_task = None
    real = get_real()
    _, rev = routes_core._rev(detail["route_id"])
    if real and config.DRONE_CREATE_REAL_TASK and rev and rev.get("platform_route_id") and d.get("device_sn"):
        try:
            import uuid

            platform_task = real.create_flight_task(
                uuid.uuid4().hex, f"低空智察Agent核查-{task_id}", rev["platform_route_id"], d["device_sn"]
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("平台创建任务失败（本地任务继续）：%s", exc)

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
    STORE.flight_tasks[task_id] = task
    out = {
        "status": "airborne",
        "flight_task_id": task_id,
        "drone_id": d["drone_id"],
        "route_id": detail["route_id"],
        "telemetry": "MQTT 遥测已订阅（1 Hz）",
        "duration_min": detail["duration_min"],
    }
    if platform_task:
        out["platform_task"] = platform_task
        out["note"] = "已在管理平台创建任务（待执行，未直接下发）"
    return out


def get_task_status(flight_task_id: str) -> dict[str, Any]:
    t = STORE.flight_tasks.get(flight_task_id.upper())
    if not t:
        return {"error": f"任务 {flight_task_id} 不存在"}
    # 演示：按真实时长的 1/60 加速推进（20min 任务 → 20s 演完）
    elapsed = time.time() - t["started_at"]
    progress = min(100, round(elapsed / (t["duration_min"] * 1.0) * 100)) if t["duration_min"] else 100
    if progress >= 100 and t["status"] == "flying":
        t["status"] = "completed"
        STORE.drones[t["drone_id"]]["status"] = "idle"
    return {
        "flight_task_id": t["flight_task_id"],
        "status": t["status"],
        "progress_pct": progress,
        "drone_id": t["drone_id"],
        "route_id": t["route_id"],
        "covered_plots": t["covered_plots"],
    }
