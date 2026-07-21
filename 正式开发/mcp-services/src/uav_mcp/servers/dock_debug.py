"""uav-dock-debug-mcp：机场调试与远程运维域（不飞的时候照顾好机器）。"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from uav_mcp import dock_debug as dd_core


def build() -> FastMCP:
    mcp = FastMCP(
        "uav-dock-debug-mcp",
        instructions="机场调试与远程运维域。硬顺序：debug_mode open → 操作 → 复位 → "
        "debug_mode close，禁止跳步（工具会拒绝乱序调用）。未来 2 小时有排期任务的机场"
        "拒绝进调试。舱盖/推杆/电源/充电/重启/电池保养为高危🔒（confirm_token 两阶段）；"
        "空调/补光灯中危免 token；环境读数纯读。",
    )

    @mcp.tool()
    def debug_mode(dock_id: str, op: str, confirm_token: str | None = None) -> dict[str, Any]:
        """【高危·人在环】机场调试模式开关（op=open/close）。所有调试动作的前置；操作完必须 close 复位。未来 2 小时有排期的机场会被拒绝。无 confirm_token 时仅生成待确认单。"""
        if op not in ("open", "close"):
            return {"error": "op 须为 open / close"}
        return dd_core.debug_mode(dock_id, op == "open", confirm_token)

    @mcp.tool()
    def dock_cover(dock_id: str, op: str, confirm_token: str | None = None) -> dict[str, Any]:
        """【高危·人在环】舱盖控制（op=open/close/force_close）。"开一下舱盖看看"场景；需先进调试模式。force_close 仅异物卡滞时用。"""
        return dd_core.dock_cover(dock_id, op, confirm_token)

    @mcp.tool()
    def dock_putter(dock_id: str, op: str, confirm_token: str | None = None) -> dict[str, Any]:
        """【高危·人在环】推杆（归中机构）控制（op=open/close）。需先进调试模式。"""
        return dd_core.dock_putter(dock_id, op, confirm_token)

    @mcp.tool()
    def drone_power(dock_id: str, on: bool, confirm_token: str | None = None) -> dict[str, Any]:
        """【高危·人在环】舱内无人机远程开关机。需先进调试模式。"""
        return dd_core.drone_power(dock_id, on, confirm_token)

    @mcp.tool()
    def charge_control(dock_id: str, on: bool, confirm_token: str | None = None) -> dict[str, Any]:
        """【高危·人在环】充电控制。"给 XX 机场的无人机充上电"场景；需先进调试模式。"""
        return dd_core.charge_control(dock_id, on, confirm_token)

    @mcp.tool()
    def device_reboot(dock_id: str, confirm_token: str | None = None) -> dict[str, Any]:
        """【高危·人在环】重启机场（故障恢复终极手段，重启期间机场完全不可用）。需先进调试模式。"""
        return dd_core.device_reboot(dock_id, confirm_token)

    @mcp.tool()
    def battery_maintenance(dock_id: str, on: bool, confirm_token: str | None = None) -> dict[str, Any]:
        """【高危·人在环】电池保养模式开关（长期驻场电池健康）。需先进调试模式。"""
        return dd_core.battery_maintenance(dock_id, on, confirm_token)

    @mcp.tool()
    def air_conditioner(dock_id: str, mode: str) -> dict[str, Any]:
        """机场空调模式：关闭/制冷/制热/除湿（高低温天气预处理，中危免确认入审计）。"""
        return dd_core.air_conditioner(dock_id, mode)

    @mcp.tool()
    def supplement_light(dock_id: str, on: bool) -> dict[str, Any]:
        """舱内补光灯开关（配合舱盖打开查看舱内，中危免确认入审计）。"""
        return dd_core.supplement_light(dock_id, on)

    @mcp.tool()
    def get_dock_environment(dock_id: str) -> dict[str, Any]:
        """机场环境读数：温湿度/风速/雨量/舱盖状态/无人机在舱/电量（纯读）。"检查一下 XX 机场"的体检第一步。"""
        return dd_core.get_dock_environment(dock_id)

    return mcp
