"""preflight-mcp：飞前检查域标准 MCP server（stdio）。

运行：uv run python -m app.mcp_servers.preflight
"""

from typing import Any

from app.mcp_servers.base import create_mcp, run_mcp

from app.core import preflight as preflight_core

mcp = create_mcp("preflight-mcp", default_port=8103)


@mcp.tool()
def check_weather(location: str = "光明区", time_window: str | None = None) -> dict[str, Any]:
    """飞前检查：作业区域气象与适飞结论。"""
    return preflight_core.check_weather(location, time_window)


@mcp.tool()
def check_battery(drone_id: str, route_id: str) -> dict[str, Any]:
    """飞前检查：电量、预计续航 vs 任务时长、余量结论。"""
    return preflight_core.check_battery(drone_id, route_id)


@mcp.tool()
def check_route_obstacle(route_id: str) -> dict[str, Any]:
    """飞前检查：航线净空分析与避让要素。"""
    return preflight_core.check_route_obstacle(route_id)


@mcp.tool()
def check_drone_obstacle(drone_id: str) -> dict[str, Any]:
    """飞前检查：机载避障系统自检。"""
    return preflight_core.check_drone_obstacle(drone_id)


@mcp.tool()
def check_airspace(route_id: str, time_window: str | None = None) -> dict[str, Any]:
    """飞前检查：空域许可有效期与冲突提醒。"""
    return preflight_core.check_airspace(route_id, time_window)


@mcp.tool()
def preflight_check(drone_id: str, route_id: str) -> dict[str, Any]:
    """飞前检查聚合：五项一次返回。"""
    return preflight_core.preflight_check(drone_id, route_id)


if __name__ == "__main__":
    run_mcp(mcp)
