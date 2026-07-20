"""uav-media-mcp：媒体与成果域（照片墙 / 取链 / 覆盖计算 / 三维重建 / 飞行录像）。"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from uav_mcp import media as media_core
from uav_mcp.servers import as_list


def build() -> FastMCP:
    mcp = FastMCP(
        "uav-media-mcp",
        instructions="媒体与成果域：任务照片/视频清单与取链（不搬文件）、相机地面覆盖计算、"
        "WebODM 三维重建（人在环）。举证成果的真实文件出口。",
    )

    @mcp.tool()
    def list_media(task_id: str | None = None, file_type: str | None = None,
                   date_range: list[str] | str | None = None, keyword: str | None = None,
                   limit: int = 20) -> dict[str, Any]:
        """查询媒体成果（照片/视频清单，按拍摄时间倒序）。用户问"这次任务拍了哪些照片""调出 XX 的成果"时调用。task_id 为飞行任务/mission ID；file_type：照片/视频；date_range 为 [起, 止] 日期。"""
        return media_core.list_media(task_id, file_type, as_list(date_range), keyword, limit)

    @mcp.tool()
    def get_media_link(file_id: str) -> dict[str, Any]:
        """取单个媒体文件的下载/预览链接（不搬运文件本体）。"""
        return media_core.get_media_link(file_id)

    @mcp.tool()
    def list_flight_videos(task_id: str) -> dict[str, Any]:
        """查询任务的飞行录像归档（按 mission 维度）。用户问"有没有录像""飞行视频"时调用。"""
        return media_core.list_flight_videos(task_id)

    @mcp.tool()
    def get_camera_coverage(task_id: str, max_photos: int = 50) -> dict[str, Any]:
        """计算任务照片的地面覆盖范围（GeoJSON，可直接落图）。用户问"这一趟拍全了没""覆盖范围"时调用；基于照片拍摄位姿元数据 + 平台覆盖算法。"""
        return media_core.get_camera_coverage(task_id, max_photos)

    @mcp.tool()
    def start_3d_modeling(flight_task_id: str, process_type: str | None = None,
                          confirm_token: str | None = None) -> dict[str, Any]:
        """【高危·人在环】对任务照片发起三维重建/正射影像（WebODM，重资源、耗时数十分钟）。无 confirm_token 时仅生成待确认单；严禁自行构造 token。"""
        return media_core.start_3d_modeling(flight_task_id, process_type, confirm_token)

    return mcp
