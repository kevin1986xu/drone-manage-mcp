"""drone-dispatch-mcp：调度域标准 MCP server（stdio）。

运行：uv run python -m app.mcp_servers.drone_dispatch
与产品链路（app/agent/tools.py）共用 app/core 业务原子层。
"""

from typing import Any

from app.mcp_servers.base import create_mcp, run_mcp

from app.core import batch as batch_core
from app.core import drones as drones_core
from app.core import plots as plots_core
from app.core import tasks as tasks_core

mcp = create_mcp("drone-dispatch-mcp", default_port=8101)


@mcp.tool()
def query_plots(
    region: str | None = None,
    plot_ids: list[str] | None = None,
    plot_type: str | None = None,
    date_range: list[str] | None = None,
    batch_no: str | None = None,
) -> dict[str, Any]:
    """查询自然资源核查图斑（下发的疑似变化地块）。region 为行政区名；返回图斑编号、类型、面积、优先级、GeoJSON 边界。"""
    return plots_core.query_plots(region, plot_ids, plot_type, date_range, batch_no)


@mcp.tool()
def find_nearby_drones(
    plot_id: str | None = None,
    location: dict[str, Any] | None = None,
    radius_km: float = 5.0,
    plot_ids: list[str] | None = None,
) -> dict[str, Any]:
    """查询周边可用无人机；为某批图斑选机时传 plot_ids（距离按目标图斑算），泛盘点则不传参照。radius_km 默认 5。"""
    return drones_core.find_nearby_drones(plot_id, location, radius_km, plot_ids)


@mcp.tool()
def get_drone_status(drone_id: str) -> dict[str, Any]:
    """查询单架无人机详情：电量、位置、固件、避障开关、健康自检。"""
    return drones_core.get_drone_status(drone_id)


@mcp.tool()
def dispatch_drone(
    drone_id: str, task_type: str, plot_ids: list[str], confirm_token: str | None = None
) -> dict[str, Any]:
    """【高危·人在环】锁定无人机执行任务。无 confirm_token 时仅生成待确认单，人工确认后携带 token 再调用才执行。"""
    return tasks_core.dispatch_drone(drone_id, task_type, plot_ids, confirm_token)


@mcp.tool()
def create_task_plan(
    plot_ids: list[str],
    deadline_days: int = 5,
    max_sorties_per_day: int = 3,
    priority_first: bool = True,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """【高危·人在环】批量排期：按优先级+就近合并成架次、按每日上限装箱到各天。无 confirm_token 仅生成待确认计划，确认后生效并执行第1天。"""
    return batch_core.create_task_plan(plot_ids, deadline_days, max_sorties_per_day, priority_first, confirm_token)


@mcp.tool()
def get_plan_progress(plan_id: str) -> dict[str, Any]:
    """查询批量核查计划各天各架次的执行进度。"""
    return batch_core.get_plan_progress(plan_id)


if __name__ == "__main__":
    run_mcp(mcp)
