"""route-planning-mcp：航线域标准 MCP server（stdio）。

运行：uv run python -m app.mcp_servers.route_planning
"""

from typing import Any

from app.mcp_servers.base import create_mcp, run_mcp

from app.core import routes as routes_core

mcp = create_mcp("route-planning-mcp", default_port=8102)


@mcp.tool()
def generate_route(
    drone_id: str,
    plot_ids: list[str],
    strategy: str = "multi_cover",
    altitude_m: float = 120.0,
    overlap_rate: float = 0.7,
    photo_num: int = 4,
    replace_route_id: str | None = None,
) -> dict[str, Any]:
    """生成/重规划核查航线；multi_cover 自动合并同航向带图斑；photo_num 每图斑拍照点数（整条统一）；重规划传 replace_route_id 替换旧航线并返回前后对比与 feasibility。"""
    return routes_core.generate_route(
        drone_id, plot_ids, strategy, altitude_m, overlap_rate, photo_num, replace_route_id
    )


@mcp.tool()
def get_route_detail(route_id: str, version: int | None = None) -> dict[str, Any]:
    """查询航线全量详情：航点、航程、时长、覆盖图斑、与上一版本 diff。"""
    return routes_core.get_route_detail(route_id, version)


@mcp.tool()
def explain_route(route_id: str) -> dict[str, Any]:
    """返回航线规划算法的结构化决策依据（覆盖率/合并原因/放弃原因/避让要素/架次对比）。"""
    return routes_core.explain_route(route_id)


@mcp.tool()
def open_route_editor(route_id: str) -> dict[str, Any]:
    """生成航线编辑界面免登录链接（临时 token，10 分钟有效）。"""
    return routes_core.open_route_editor(route_id)


if __name__ == "__main__":
    run_mcp(mcp)
