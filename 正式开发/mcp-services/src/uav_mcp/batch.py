"""批量任务编排域（Plan-and-Execute）。

Plan：确定性调度算法把一批图斑排成逐日架次表——按优先级排序 → 就近贪心
分组成架次（同架次内相邻图斑靠 multi_cover 合并）→ 按每日架次上限装箱到
各天 → 截止天数校验。算法产出可解释的排期，LLM 只转述。

Execute：整份计划人在环确认一次即授权后续执行；确认后执行第 1 天批次
（generate_route + 锁定无人机），未来天保持 scheduled。
"""

from __future__ import annotations

import logging
from typing import Any

from uav_mcp import approval, geo
from uav_mcp import drones as drones_core
from uav_mcp import plots as plots_core
from uav_mcp import routes as routes_core
from uav_mcp.drone_manage import DroneManageError
from uav_mcp.state import STATE

logger = logging.getLogger(__name__)

PRIO_RANK = {"高": 0, "中": 1, "低": 2}
SORTIE_MAX_PLOTS = 3  # 单架次最多顺带覆盖的图斑数（续航/航程约束的经验上限）
NEARBY_KM = 3.0       # 架次内就近合并的距离阈值


def _cluster_sorties(plot_ids: list[str]) -> list[list[str]]:
    """把图斑就近贪心分组成架次：每组≤SORTIE_MAX_PLOTS 且组内两两≤NEARBY_KM。"""
    infos = []
    for pid in plot_ids:
        key = plots_core.resolve_pid(pid)
        if not key:
            continue
        p = STATE.plots[key]
        infos.append({"pid": key, "centroid": geo.centroid(p["geometry"]["coordinates"][0]),
                      "prio": PRIO_RANK.get(p["priority"], 3)})
    infos.sort(key=lambda x: x["prio"])  # 优先级高的先做种子
    remaining = infos[:]
    sorties: list[list[str]] = []
    while remaining:
        seed = remaining.pop(0)
        group = [seed]
        for cand in remaining[:]:
            if len(group) >= SORTIE_MAX_PLOTS:
                break
            if any(geo.dist_m(cand["centroid"], g["centroid"]) / 1000 <= NEARBY_KM for g in group):
                group.append(cand)
                remaining.remove(cand)
        sorties.append([g["pid"] for g in group])
    return sorties


def create_task_plan(
    plot_ids: list[str],
    deadline_days: int = 5,
    max_sorties_per_day: int = 3,
    priority_first: bool = True,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """生成/确认批量核查排期计划（🔒 人在环）。"""
    try:
        plots_core.hydrate()
    except DroneManageError as exc:
        return {"error": f"无人机平台不可达：{exc}"}
    resolved = [plots_core.resolve_pid(p) for p in plot_ids]
    missing = [raw for raw, key in zip(plot_ids, resolved) if key is None]
    if missing:
        return {"error": f"图斑不存在：{', '.join(missing)}"}
    ids = [k for k in resolved if k]
    if not ids:
        return {"error": "plot_ids 不能为空"}

    sorties = _cluster_sorties(ids)
    days: list[dict[str, Any]] = []
    for i in range(0, len(sorties), max_sorties_per_day):
        day_sorties = sorties[i : i + max_sorties_per_day]
        days.append(
            {
                "day": len(days) + 1,
                "sorties": [
                    {"plot_ids": s, "status": "scheduled", "route_id": None, "drone_id": None}
                    for s in day_sorties
                ],
            }
        )
    need_days = len(days)
    feasible = need_days <= deadline_days

    summary = {
        "title": f"批量核查排期计划（{len(ids)} 图斑 / {len(sorties)} 架次 / {need_days} 天）",
        "rows": [
            {"label": "图斑总数", "value": f"{len(ids)} 个"},
            {"label": "架次总数", "value": f"{len(sorties)} 架次（每架次≤{SORTIE_MAX_PLOTS}图斑就近合并）"},
            {"label": "排期天数", "value": f"{need_days} 天（每天≤{max_sorties_per_day}架次）"},
            {"label": "截止约束", "value": f"{deadline_days} 天内 · {'可满足' if feasible else '⚠ 超期，需放宽每日架次或延长截止'}"},
        ],
    }

    if confirm_token is None:
        item = approval.create_pending_action(
            "create_task_plan",
            {"plot_ids": ids, "deadline_days": deadline_days, "max_sorties_per_day": max_sorties_per_day,
             "days": days, "priority_first": priority_first},
            summary,
        )
        return {
            "status": "requires_confirmation",
            "action_id": item["action_id"],
            "action": "create_task_plan",
            "summary": summary,
            "schedule": _schedule_view(days),
            "feasible": feasible,
            "message": "批量计划需人工确认后生效并开始执行第 1 天批次",
        }

    citem = approval.validate_and_consume("create_task_plan", confirm_token)
    if not citem:
        return approval.refusal("create_task_plan")
    return _activate_and_run_day1(citem["params"])


def _activate_and_run_day1(params: dict[str, Any]) -> dict[str, Any]:
    plan_id = STATE.next_id("PLAN", 3)
    days = params["days"]
    plan = {
        "plan_id": plan_id,
        "constraints": {k: params[k] for k in ("deadline_days", "max_sorties_per_day", "priority_first")},
        "days": days,
        "status": "executing",
    }
    STATE.task_plans[plan_id] = plan

    executed = []
    if days:
        for sortie in days[0]["sorties"]:
            drone = _pick_drone(sortie["plot_ids"])
            if not drone:
                sortie["status"] = "queued"  # 无空闲设备，排队待机
                continue
            route = routes_core.generate_route(drone["drone_id"], sortie["plot_ids"])
            if route.get("error"):
                sortie["status"] = "route_failed"
                continue
            order = drones_core.lock_drone(drone["drone_id"], "批量图斑核查", sortie["plot_ids"])
            sortie.update(
                status="dispatched" if order.get("order_id") else "route_ready",
                route_id=route["route_id"],
                drone_id=drone["drone_id"],
                length_km=route["length_km"],
                duration_min=route["duration_min"],
            )
            executed.append(sortie)
        days[0]["status"] = "executing"
    return {
        "status": "plan_activated",
        "plan_id": plan_id,
        "schedule": _schedule_view(days),
        "day1_executed": len(executed),
        "message": f"计划 {plan_id} 已生效，第 1 天 {len(executed)} 个架次已规划航线并锁定无人机；后续天次待执行",
    }


def _pick_drone(plot_ids: list[str]) -> dict[str, Any] | None:
    """为一个架次选最近的**空闲**无人机（距离按该架次全部目标图斑算）。"""
    r = drones_core.find_nearby_drones(plot_ids=plot_ids, radius_km=1000)
    idle = [d for d in r.get("drones", []) if d["status"] == "idle"]
    if not idle:
        return None
    best = max(idle, key=lambda d: (d["battery_pct"] or 50) - d["distance_km"] * 5)
    return drones_core.find(best["drone_id"])


def _schedule_view(days: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "day": d["day"],
            "sorties": [
                {
                    "plot_ids": s["plot_ids"],
                    "status": s["status"],
                    "route_id": s.get("route_id"),
                    "drone_id": s.get("drone_id"),
                }
                for s in d["sorties"]
            ],
        }
        for d in days
    ]


def get_plan_progress(plan_id: str) -> dict[str, Any]:
    plan = STATE.task_plans.get(plan_id.upper()) or STATE.task_plans.get(plan_id)
    if not plan:
        return {"error": f"计划 {plan_id} 不存在"}
    all_sorties = [s for d in plan["days"] for s in d["sorties"]]
    done = sum(1 for s in all_sorties if s["status"] in ("dispatched", "route_ready", "completed"))
    return {
        "plan_id": plan["plan_id"],
        "status": plan["status"],
        "total_sorties": len(all_sorties),
        "executed_sorties": done,
        "schedule": _schedule_view(plan["days"]),
    }
