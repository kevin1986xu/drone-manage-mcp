"""uav-task-schedule-mcp：任务排期与调度域（何时飞：建议/定时/循环/重排/重试/续飞）。"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from uav_mcp import schedule as schedule_core
from uav_mcp.servers import as_list


def build() -> FastMCP:
    mcp = FastMCP(
        "uav-task-schedule-mcp",
        instructions="任务排期与调度域（时间维度）。suggest_schedule 只算不写；"
        "所有产生平台'待执行'任务的动作（定时/循环/重排/取消/重试/续飞）均为高危人在环——"
        "平台自动调度器对待执行任务是真实执行。",
    )

    @mcp.tool()
    def suggest_schedule(plot_ids: list[str] | str, deadline_days: int = 7,
                         max_sorties_per_day: int = 3) -> dict[str, Any]:
        """排期建议（只算不写库）：综合逐日天气窗口、设备已排档期、图斑优先级，给出建议排期表与理由。用户说"这些图斑下周飞完，避开下雨天""帮我排一下"时调用。确认后用 create_scheduled_task 逐条落库。"""
        return schedule_core.suggest_schedule(as_list(plot_ids), deadline_days, max_sorties_per_day)

    @mcp.tool()
    def list_scheduled_tasks(status: str | None = None, date_range: list[str] | str | None = None,
                             limit: int = 20) -> dict[str, Any]:
        """查询平台任务清单（含定时/循环任务）。status：待执行/执行中/已完成/已取消/执行失败；date_range 为 [起, 止] 日期。"""
        return schedule_core.list_scheduled_tasks(status, as_list(date_range), limit)

    @mcp.tool()
    def get_schedule_conflicts(drone_id: str, date_range: list[str] | str | None = None) -> dict[str, Any]:
        """查设备档期：时间窗内已排/已飞作业清单（"D-07 这周还排得下吗"）。返回的 job_id 供失败重试/断点续飞使用。"""
        return schedule_core.get_schedule_conflicts(drone_id, as_list(date_range))

    @mcp.tool()
    def create_scheduled_task(route_id: str, drone_id: str, execution_time: str,
                              task_name: str | None = None,
                              confirm_token: str | None = None) -> dict[str, Any]:
        """【高危·人在环】创建平台定时任务（到点自动真实起飞）。execution_time 格式 yyyy-MM-dd HH:mm:ss。无 confirm_token 时仅生成待确认单；严禁自行构造 token。"""
        return schedule_core.create_scheduled_task(route_id, drone_id, execution_time, task_name, confirm_token)

    @mcp.tool()
    def create_recurring_task(route_id: str, drone_id: str, start_date: str, end_date: str,
                              execute_times: list[str] | str | None = None,
                              task_name: str | None = None,
                              confirm_token: str | None = None) -> dict[str, Any]:
        """【高危·人在环】创建平台循环任务（日期区间内每日按时真实起飞，"每天早上九点巡一遍"）。日期格式 yyyy-MM-dd；execute_times 如 ["09:00"]。无 confirm_token 时仅生成待确认单。"""
        return schedule_core.create_recurring_task(
            route_id, drone_id, start_date, end_date, as_list(execute_times), task_name, confirm_token)

    @mcp.tool()
    def cancel_scheduled_task(task_id: str, confirm_token: str | None = None) -> dict[str, Any]:
        """【高危·人在环】取消平台任务（含定时/循环）。无 confirm_token 时仅生成待确认单。"""
        return schedule_core.cancel_scheduled_task(task_id, confirm_token)

    @mcp.tool()
    def reschedule_task(task_id: str, new_time: str | None = None,
                        confirm_token: str | None = None) -> dict[str, Any]:
        """【高危·人在环】任务重排期（"周三的挪到周四上午"）。给 new_time（yyyy-MM-dd HH:mm:ss）改到指定时间；不给则平台自动排最近可行窗口。无 confirm_token 时仅生成待确认单。"""
        return schedule_core.reschedule_task(task_id, new_time, confirm_token)

    @mcp.tool()
    def retry_failed_task(job_id: str, confirm_token: str | None = None) -> dict[str, Any]:
        """【高危·人在环】失败架次重试（平台自动重排期并下发）。job_id 为 wayline 作业号（从 get_schedule_conflicts / 任务详情获取，不是任务 ID）。"""
        return schedule_core.retry_failed_task(job_id, confirm_token)

    @mcp.tool()
    def resume_from_breakpoint(job_id: str, confirm_token: str | None = None) -> dict[str, Any]:
        """【高危·人在环】断点续飞：中断的架次从断点航点继续执行（真实起飞）。job_id 为 wayline 作业号。"""
        return schedule_core.resume_from_breakpoint(job_id, confirm_token)

    @mcp.tool()
    def optimize_route_connection(task_id: str, min_height_above_terrain: float = 120.0) -> dict[str, Any]:
        """航线连接优化：机场到航线起点的安全连接段优化（不触发执行，低危）。"""
        return schedule_core.optimize_route_connection(task_id, min_height_above_terrain)

    return mcp
