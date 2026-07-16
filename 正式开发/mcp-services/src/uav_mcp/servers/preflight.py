"""uav-preflight-mcp：飞前检查域（五项单项 + 聚合）。"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from uav_mcp import preflight as preflight_core


def build() -> FastMCP:
    mcp = FastMCP(
        "uav-preflight-mcp",
        instructions="无人机飞前检查域：气象/电量/航线避障/机载避障/空域五项检查与聚合结论",
    )

    @mcp.tool()
    def check_weather(location: str = "作业区域", time_window: str | None = None) -> dict[str, Any]:
        """飞前检查：作业区域实时气象与适飞结论（Open-Meteo 自查，平台气象兜底）。"""
        return preflight_core.check_weather(location, time_window)

    @mcp.tool()
    def check_battery(drone_id: str, route_id: str) -> dict[str, Any]:
        """飞前检查：实时电量（OSD）、预计续航 vs 任务时长、余量结论。"""
        return preflight_core.check_battery(drone_id, route_id)

    @mcp.tool()
    def check_route_obstacle(route_id: str) -> dict[str, Any]:
        """飞前检查：航线净空/仿地情况（平台规划航线已含地形抬升与安全高度校验）。"""
        return preflight_core.check_route_obstacle(route_id)

    @mcp.tool()
    def check_drone_obstacle(drone_id: str) -> dict[str, Any]:
        """飞前检查：机载避障系统自检（依赖机场在线）。"""
        return preflight_core.check_drone_obstacle(drone_id)

    @mcp.tool()
    def check_airspace(route_id: str, time_window: str | None = None) -> dict[str, Any]:
        """飞前检查：空域许可核实提示（数据源接入前需人工核实）。"""
        return preflight_core.check_airspace(route_id, time_window)

    @mcp.tool()
    def preflight_check(drone_id: str, route_id: str) -> dict[str, Any]:
        """飞前检查聚合：五项一次返回 + 整体结论。检查完无 fail 应立即调用 take_off（不带 token）生成人工确认单，无需先询问用户。"""
        return preflight_core.preflight_check(drone_id, route_id)

    return mcp
