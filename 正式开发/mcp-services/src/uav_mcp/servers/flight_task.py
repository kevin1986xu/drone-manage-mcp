"""uav-flight-task-mcp：飞行任务域（起飞·人在环 / 任务状态）。"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from uav_mcp import tasks as tasks_core


def build() -> FastMCP:
    mcp = FastMCP(
        "uav-flight-task-mcp",
        instructions="无人机飞行任务域：起飞指令（人在环确认）、任务状态查询。"
        "真实起飞 = 平台创建 flighttask + 下发计划，受服务端两级开关控制",
    )

    @mcp.tool()
    def take_off(drone_id: str, route_id: str, confirm_token: str | None = None) -> dict[str, Any]:
        """【高危·人在环】下发起飞指令。无 confirm_token 时仅生成待确认单（不会起飞），人工确认后系统给出带 token 的指令再调用才执行；无效/伪造 token 一律拒绝，严禁自行构造。"""
        return tasks_core.take_off(drone_id, route_id, confirm_token)

    @mcp.tool()
    def get_task_status(flight_task_id: str) -> dict[str, Any]:
        """查询飞行任务状态：进度、当前状态、执行无人机、覆盖图斑（有平台任务时以平台状态为准）。"""
        return tasks_core.get_task_status(flight_task_id)

    @mcp.tool()
    def get_task_report(flight_task_id: str) -> dict[str, Any]:
        """任务成果报告（举证摘要）：覆盖图斑、拍照数、起止时间、照片归档说明。

        用户问"成果报告""举证材料""这次任务拍了多少照片""任务成果"时调用。
        仅任务完成后可用；进行中会返回当前进度提示，如实转告即可。
        """
        return tasks_core.get_task_report(flight_task_id)

    @mcp.tool()
    def list_task_history(status: str | None = None, drone_id: str | None = None, limit: int = 10) -> dict[str, Any]:
        """历史飞行任务查询（倒序）。用户问"历史任务""之前飞过哪些""今天飞了几次"时调用；可按状态（flying/completed）或无人机名过滤。"""
        return tasks_core.list_task_history(status, drone_id, limit)

    return mcp
