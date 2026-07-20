"""uav-drone-dispatch-mcp：调度域（图斑/设备/锁定/批量排期）。"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from uav_mcp import batch as batch_core
from uav_mcp import drones as drones_core
from uav_mcp import plots as plots_core
from uav_mcp import tasks as tasks_core
from uav_mcp.servers import as_list


def build() -> FastMCP:
    mcp = FastMCP(
        "uav-drone-dispatch-mcp",
        instructions="无人机调度域：自然资源核查图斑查询、机场/无人机盘点与选机、无人机锁定、批量核查排期",
    )

    @mcp.tool()
    def query_plots(
        region: str | None = None,
        plot_ids: list[str] | str | None = None,
        plot_type: str | None = None,
        date_range: list[str] | str | None = None,
        batch_no: str | None = None,
        include_geometry: bool = False,
    ) -> dict[str, Any]:
        """查询自然资源核查图斑（下发的疑似变化地块）。region 为行政区名（如"汉川"）或行政区代码（如"420984"）；plot_ids 支持完整编号或尾号片段（如 00005），一次查询即可命中，严禁对同一编号反复查询。默认不返回边界几何（省上下文）；GIS 展示需要时传 include_geometry=true。"""
        return plots_core.query_plots(region, as_list(plot_ids), plot_type, as_list(date_range), batch_no, include_geometry)

    @mcp.tool()
    def find_nearby_drones(
        plot_id: str | None = None,
        location: dict[str, Any] | None = None,
        radius_km: float = 5.0,
        plot_ids: list[str] | str | None = None,
    ) -> dict[str, Any]:
        """查询周边可用无人机（机场）。为某批图斑选机时必须传 plot_ids（距离按本次任务目标图斑计算，不能用其它图斑顶替）；泛盘点（"这些图斑附近有哪些设备"）则不传参照，按查询到的全部图斑展示。radius_km 默认 5，无结果自动扩大。"""
        return drones_core.find_nearby_drones(plot_id, location, radius_km, as_list(plot_ids))

    @mcp.tool()
    def get_drone_status(drone_id: str) -> dict[str, Any]:
        """查询单架无人机详情：实时电量（OSD）、位置、健康自检。drone_id 支持机场名称片段。"""
        return drones_core.get_drone_status(drone_id)

    @mcp.tool()
    def dispatch_drone(
        drone_id: str, task_type: str, plot_ids: list[str] | str, confirm_token: str | None = None
    ) -> dict[str, Any]:
        """【高危·人在环】锁定无人机执行任务。无 confirm_token 时仅生成待确认单（不会执行），人工确认后系统给出带 token 的指令再调用才执行；严禁自行构造 token。"""
        return tasks_core.dispatch_drone(drone_id, task_type, as_list(plot_ids), confirm_token)

    @mcp.tool()
    def create_task_plan(
        plot_ids: list[str] | str,
        deadline_days: int = 5,
        max_sorties_per_day: int = 3,
        priority_first: bool = True,
        confirm_token: str | None = None,
    ) -> dict[str, Any]:
        """【高危·人在环】批量核查排期：按优先级+就近合并成架次、按每日上限装箱到各天。无 confirm_token 仅生成待确认计划，人工确认整份计划后生效并执行第 1 天批次。"""
        return batch_core.create_task_plan(
            as_list(plot_ids), deadline_days, max_sorties_per_day, priority_first, confirm_token
        )

    @mcp.tool()
    def get_plan_progress(plan_id: str) -> dict[str, Any]:
        """查询批量核查计划各天各架次的执行进度。"""
        return batch_core.get_plan_progress(plan_id)

    return mcp
