"""flight-task-mcp：飞行任务域标准 MCP server（stdio）。

运行：uv run python -m app.mcp_servers.flight_task
P0：take_off（高危·人在环）、get_task_status；
P1 在此扩展：subscribe_task_events / return_home / pause_task / resume_task / get_task_results。
"""

from typing import Any

from app.mcp_servers.base import create_mcp, run_mcp

from app.core import tasks as tasks_core

mcp = create_mcp("flight-task-mcp", default_port=8104)


@mcp.tool()
def take_off(drone_id: str, route_id: str, confirm_token: str | None = None) -> dict[str, Any]:
    """【高危·人在环】下发起飞指令。无 confirm_token 时仅生成待确认单，人工确认后携带 token 再调用才执行；无效 token 一律拒绝。"""
    return tasks_core.take_off(drone_id, route_id, confirm_token)


@mcp.tool()
def get_task_status(flight_task_id: str) -> dict[str, Any]:
    """查询飞行任务状态：进度百分比、当前状态（flying/completed）、执行无人机、覆盖图斑。"""
    return tasks_core.get_task_status(flight_task_id)


if __name__ == "__main__":
    run_mcp(mcp)
