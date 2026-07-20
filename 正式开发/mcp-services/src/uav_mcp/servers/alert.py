"""uav-alert-mcp：告警与设备健康域（值班视图 / 告警处置 / 设备体检）。"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from uav_mcp import alerts as alerts_core
from uav_mcp.servers import as_list


def build() -> FastMCP:
    mcp = FastMCP(
        "uav-alert-mcp",
        instructions="告警与设备健康域：平台告警查询与处置（处理/忽略为低危写，入审计）、"
        "设备健康体检（在线/电量/HMS 健康消息）。告警内容只如实转述，不自行加工结论。",
    )

    @mcp.tool()
    def list_alerts(status: str | None = None, level: str | None = None,
                    drone_id: str | None = None, date_range: list[str] | str | None = None,
                    limit: int = 20) -> dict[str, Any]:
        """查询平台告警。用户问"现在有什么告警""有没有紧急告警"时调用。status：未处理/已处理/已忽略；level：低/中/高/紧急；drone_id 支持设备名或 SN；date_range 为 [起, 止] 日期。"""
        return alerts_core.list_alerts(status, level, drone_id, as_list(date_range), limit)

    @mcp.tool()
    def get_alert_detail(alert_id: str) -> dict[str, Any]:
        """查询单条告警详情（完整内容、处置记录）。"""
        return alerts_core.get_alert_detail(alert_id)

    @mcp.tool()
    def handle_alert(alert_id: str, note: str) -> dict[str, Any]:
        """处理告警（标记已处理）。note 为处置说明（必填），须先向用户确认处置口径后再调用；内容如实记录，不得代用户编造处置动作。"""
        return alerts_core.handle_alert(alert_id, note)

    @mcp.tool()
    def ignore_alert(alert_id: str, note: str | None = None) -> dict[str, Any]:
        """忽略告警（确认无需处置）。仅在用户明确表示忽略时调用。"""
        return alerts_core.ignore_alert(alert_id, note)

    @mcp.tool()
    def get_unhandled_count() -> dict[str, Any]:
        """未处理告警总数（值班视图入口，"今天还有多少告警没处理"）。"""
        return alerts_core.get_unhandled_count()

    @mcp.tool()
    def get_device_health(drone_id: str) -> dict[str, Any]:
        """设备健康体检：在线状态、实时电量、未读 HMS 健康消息、未处理告警数与整体结论。用户问"XX 那台机健康状况怎么样""这台机能不能飞"时调用。"""
        return alerts_core.get_device_health(drone_id)

    return mcp
