"""uav-airspace-mcp：空域与电子围栏域（禁飞区/限高区查询·航线合规·临时管制区）。"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from uav_mcp import zones as zones_core


def build() -> FastMCP:
    mcp = FastMCP(
        "uav-airspace-mcp",
        instructions="空域与电子围栏域：禁飞区/限飞区/限高区/警告区查询、航线围栏冲突检测、"
        "临时管制区创建与删除（人在环确认）。围栏与图斑同源于平台 flyWorkZone。",
    )

    @mcp.tool()
    def list_zones(zone_type: str | None = None, region: str | None = None,
                   include_geometry: bool = False) -> dict[str, Any]:
        """查询电子围栏/管控区（禁飞区、限飞区、限高区、限速区、警告区等）。用户问"有哪些禁飞区""这附近有没有管控"时调用；zone_type 按类型过滤，region 为行政区名或区划代码。GIS 展示需要边界时传 include_geometry=true。"""
        return zones_core.list_zones(zone_type, region, include_geometry)

    @mcp.tool()
    def check_route_conflict(route_id: str, altitude_m: float | None = None) -> dict[str, Any]:
        """航线与围栏的几何冲突分析：穿越哪些禁飞区/限飞区、是否超限高，返回冲突清单与冲突多边形（可落图）。fail=禁止起飞需重新规划，warn=可飞但需注意。用户问"空域许可/申请了吗"这类合规结论时用 preflight 域的 check_airspace，不用本工具。"""
        return zones_core.check_route_conflict(route_id, altitude_m)

    @mcp.tool()
    def create_zone(zone_type: str, zone_name: str, geometry: dict[str, Any],
                    limit_height_m: float | None = None, expire_at: str | None = None,
                    confirm_token: str | None = None) -> dict[str, Any]:
        """【高危·人在环】新建管控区（禁飞区/限高区/警告区等，如"明天上午这片临时管制"）。geometry 为 GeoJSON Polygon；expire_at 仅作登记（平台不自动失效，到期需人工删除）。无 confirm_token 时仅生成待确认单；严禁自行构造 token。"""
        return zones_core.create_zone(zone_type, zone_name, geometry, limit_height_m, expire_at, confirm_token)

    @mcp.tool()
    def delete_zone(zone_id: str, confirm_token: str | None = None) -> dict[str, Any]:
        """【高危·人在环】删除管控区（临时管制解除/围栏作废）。无 confirm_token 时仅生成待确认单；只能删管控围栏，图斑记录会被拒绝。"""
        return zones_core.delete_zone(zone_id, confirm_token)

    return mcp
