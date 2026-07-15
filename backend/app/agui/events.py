"""AG-UI 风格事件定义与右栏视图指令路由。

事件经 SSE 下发，三协议分工：MCP=Agent↔工具，AG-UI=Agent↔界面。
事件类型（对齐 AG-UI 协议语义）：
  RUN_STARTED / RUN_FINISHED / RUN_ERROR
  TEXT_MESSAGE_START / TEXT_MESSAGE_CONTENT / TEXT_MESSAGE_END
  TOOL_CALL_START / TOOL_CALL_END
  VIEW_DIRECTIVE（CUSTOM 语义：驱动右栏 show_map/show_iframe/show_report/show_confirm）
"""

from __future__ import annotations

import json
from typing import Any


def sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def run_started(run_id: str) -> dict[str, Any]:
    return {"type": "RUN_STARTED", "run_id": run_id}


def run_finished(run_id: str) -> dict[str, Any]:
    return {"type": "RUN_FINISHED", "run_id": run_id}


def run_error(message: str) -> dict[str, Any]:
    return {"type": "RUN_ERROR", "message": message}


def text_start(message_id: str) -> dict[str, Any]:
    return {"type": "TEXT_MESSAGE_START", "message_id": message_id}


def text_content(message_id: str, delta: str) -> dict[str, Any]:
    return {"type": "TEXT_MESSAGE_CONTENT", "message_id": message_id, "delta": delta}


def text_end(message_id: str) -> dict[str, Any]:
    return {"type": "TEXT_MESSAGE_END", "message_id": message_id}


def tool_start(tool_call_id: str, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"type": "TOOL_CALL_START", "tool_call_id": tool_call_id, "tool_name": tool_name, "args": args}


def tool_end(tool_call_id: str, tool_name: str, result: Any) -> dict[str, Any]:
    return {"type": "TOOL_CALL_END", "tool_call_id": tool_call_id, "tool_name": tool_name, "result": result}


def view_directive(directive: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"type": "VIEW_DIRECTIVE", "directive": directive, "payload": payload}


# ── 工具结果 → 右栏视图指令 ─────────────────────────────────

CHECK_TOOLS = {"check_weather", "check_battery", "check_route_obstacle", "check_drone_obstacle", "check_airspace"}


def directives_for(tool_name: str, result: Any) -> list[dict[str, Any]]:
    if not isinstance(result, dict) or result.get("error"):
        return []

    if result.get("status") == "requires_confirmation":
        directives = []
        # 批量计划：先在右栏铺开逐日排期表，再弹计划确认卡片
        if result.get("action") == "create_task_plan" and result.get("schedule"):
            directives.append(view_directive("show_plan", {"schedule": result["schedule"], "feasible": result.get("feasible", True)}))
        directives.append(
            view_directive(
                "show_confirm",
                {"action_id": result["action_id"], "action": result["action"], "summary": result["summary"]},
            )
        )
        return directives

    # 批量计划生效：更新排期表（第 1 天已执行）
    if result.get("status") == "plan_activated":
        return [view_directive("show_plan", {"schedule": result.get("schedule", []), "plan_id": result.get("plan_id"), "active": True})]

    if tool_name == "get_plan_progress":
        return [view_directive("show_plan", {"schedule": result.get("schedule", []), "plan_id": result.get("plan_id"), "active": True})]

    if tool_name == "query_plots":
        # 给 LLM 的 result 已瘦身（无 geometry）；前端画图所需几何从 STORE 补齐
        from app.core.store import STORE

        plots = []
        for p in result.get("plots", []):
            full = STORE.plots.get(p.get("plot_id"))
            plots.append({**p, "geometry": full["geometry"]} if full and full.get("geometry") else p)
        return [view_directive("show_map", {"layer": "plots", "plots": plots})]

    if tool_name == "find_nearby_drones":
        return [view_directive("show_map", {"layer": "drones", "drones": result.get("drones", [])})]

    if tool_name in {"generate_route", "get_route_detail"}:
        from app.core import routes as routes_core

        _, rev = routes_core._rev(result["route_id"])
        geometry = result.get("geometry")
        if not geometry and rev:  # LLM 瘦身返回无几何，从 STORE 航点重建
            geometry = {"type": "LineString", "coordinates": [[w["lon"], w["lat"]] for w in rev["waypoints"]]}
        return [
            view_directive(
                "show_map",
                {
                    "layer": "route",
                    "route": {
                        "route_id": result["route_id"],
                        "version": result.get("version", rev["version"] if rev else 1),
                        "length_km": result["length_km"],
                        "duration_min": result["duration_min"],
                        "geometry": geometry,
                        "covered_plots": result["covered_plots"],
                    },
                },
            )
        ]

    if tool_name == "explain_route":
        covered = [c["plot_id"] for c in result.get("decision", {}).get("covered_plots", [])]
        return [view_directive("show_map", {"layer": "highlight", "plot_ids": covered})]

    if tool_name == "open_route_editor":
        return [view_directive("show_iframe", {"url": result["url"], "route_id": result["route_id"]})]

    if tool_name in CHECK_TOOLS:
        return [view_directive("show_report", {"mode": "append", "check": result})]

    if tool_name == "preflight_check":
        return [
            view_directive(
                "show_report",
                {"mode": "full", "checks": result.get("checks", []), "overall": result.get("overall")},
            )
        ]

    if tool_name == "take_off" and result.get("status") == "airborne":
        return [
            view_directive(
                "show_map",
                {
                    "layer": "flight",
                    "task": {
                        "flight_task_id": result["flight_task_id"],
                        "drone_id": result["drone_id"],
                        "route_id": result["route_id"],
                        "duration_min": result["duration_min"],
                    },
                },
            )
        ]

    return []
