"""uav-route-planning-mcp：航线域（规划/详情/决策解释/编辑器）。"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from uav_mcp import routes as routes_core
from uav_mcp.servers import as_list


def build() -> FastMCP:
    mcp = FastMCP(
        "uav-route-planning-mcp",
        instructions="无人机航线域：图斑核查航线生成（平台图斑巡检算法+多图斑合并决策）、决策解释、人工编辑",
    )

    @mcp.tool()
    def generate_route(
        drone_id: str,
        plot_ids: list[str] | str,
        strategy: str = "multi_cover",
        altitude_m: float = 120.0,
        overlap_rate: float = 0.7,
        photo_num: int = 4,
        replace_route_id: str | None = None,
    ) -> dict[str, Any]:
        """生成/重规划核查航线（平台图斑巡检算法）。plot_ids 为用户指定的目标图斑（必须生效）；multi_cover 自动合并同航向带邻近图斑；photo_num 每图斑拍照点数（整条统一）。重规划/调参数传 replace_route_id 替换旧航线并返回前后对比。返回 feasibility（续航预算校验），超预算时按 hint 放宽参数重规划。"""
        return routes_core.generate_route(
            drone_id, as_list(plot_ids), strategy, altitude_m, overlap_rate, photo_num, replace_route_id
        )

    @mcp.tool()
    def get_route_detail(route_id: str, version: int | None = None, include_waypoints: bool = False) -> dict[str, Any]:
        """查询航线详情：航程、时长、覆盖图斑、与上一版本 diff。默认不含航点坐标（省上下文）；GIS 展示需要时传 include_waypoints=true。"""
        return routes_core.get_route_detail(route_id, version, include_waypoints)

    @mcp.tool()
    def explain_route(route_id: str) -> dict[str, Any]:
        """返回航线规划算法的结构化决策依据（合并原因/放弃原因/架次对比/续航预算）。转述时不得编造数据之外的理由。"""
        return routes_core.explain_route(route_id)

    @mcp.tool()
    def open_route_editor(route_id: str) -> dict[str, Any]:
        """生成航线编辑界面免登录链接（临时 token，10 分钟有效）。仅对已生成航线的图斑可用。"""
        return routes_core.open_route_editor(route_id)

    return mcp
