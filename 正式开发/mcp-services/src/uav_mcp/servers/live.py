"""uav-live-mcp：直播与遥测回放域（看得见的飞行）。"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from uav_mcp import live as live_core


def build() -> FastMCP:
    mcp = FastMCP(
        "uav-live-mcp",
        instructions="直播与遥测回放域：设备直播开流/停流/切镜头/画质（视频流操作，"
        "不动飞行器，免确认但入审计）、遥测历史与轨迹回放（纯读）。"
        "开流返回拉流地址，前端 show_live 内嵌播放。",
    )

    @mcp.tool()
    def get_live_capacity(drone_id: str) -> dict[str, Any]:
        """查询设备直播能力（可推流的镜头/清晰度）。用户问"XX 能不能看直播""有哪些镜头"时调用；开流失败时也先调它排查。"""
        return live_core.get_live_capacity(drone_id)

    @mcp.tool()
    def start_live(drone_id: str, source: str = "drone") -> dict[str, Any]:
        """开启设备直播并返回拉流地址。用户说"让我看看 XX 的画面""打开直播"时调用。source：drone=无人机镜头（默认）/ airport=机场镜头 / assist=辅助摄像。只动视频流不动飞行器；不用时应停流。"""
        return live_core.start_live(drone_id, source)

    @mcp.tool()
    def stop_live(drone_id: str) -> dict[str, Any]:
        """停止设备直播（释放通道）。用户说"不看了""关掉直播"时调用。"""
        return live_core.stop_live(drone_id)

    @mcp.tool()
    def switch_camera(drone_id: str, camera: str) -> dict[str, Any]:
        """切换直播镜头。camera：wide 广角 / zoom 变焦 / ir 红外（无人机），或机场镜头位数字。需先开流。"""
        return live_core.switch_camera(drone_id, camera)

    @mcp.tool()
    def set_live_quality(drone_id: str, quality: str) -> dict[str, Any]:
        """设置直播画质：高清/标清/流畅。画面卡顿时降画质。需先开流。"""
        return live_core.set_live_quality(drone_id, quality)

    @mcp.tool()
    def get_telemetry_history(drone_id: str, start_time: str, end_time: str,
                              limit: int = 100) -> dict[str, Any]:
        """查询历史遥测（位置/高度/速度/电量时序）。用户问"昨天下午那架机飞行数据""当时电量变化"时调用。时间格式 yyyy-MM-dd HH:mm:ss；返回抽样点。"""
        return live_core.get_telemetry_history(drone_id, start_time, end_time, limit)

    @mcp.tool()
    def get_flight_trajectory(task_id: str | None = None, drone_id: str | None = None,
                              start_time: str | None = None, end_time: str | None = None) -> dict[str, Any]:
        """取飞行轨迹用于落图回放。用户说"回放一下那次任务的轨迹""它飞过哪里"时调用。优先传 task_id；没有任务号则 drone_id+起止时间。"""
        return live_core.get_flight_trajectory(task_id, drone_id, start_time, end_time)

    return mcp
