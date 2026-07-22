"""通用前端 UI 服务客户端（docs/08）——视图快照注册，best-effort。

工具产出几何（轨迹/图斑/围栏）时注册一份快照到 UI 服务（8213），拿回
`view_url` 附在返回里；任何能开网页的宿主都能直接看图。UI 服务未部署
（UAV_UI_BASE 未配）或注册失败时返回 None——不影响工具主链路。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from uav_mcp import config

logger = logging.getLogger(__name__)


def register_view(vtype: str, title: str, payload: dict[str, Any]) -> str | None:
    """注册视图快照，返回 view_url；失败静默返回 None。"""
    if not config.UAV_UI_BASE:
        return None
    try:
        resp = httpx.post(
            f"{config.UAV_UI_BASE}/ui/api/view",
            json={"type": vtype, "title": title, "payload": payload},
            headers={"X-API-Key": config.UAV_MCP_API_KEY} if config.UAV_MCP_API_KEY else {},
            timeout=5,
        )
        if resp.status_code == 200:
            return resp.json().get("view_url")
        logger.warning("视图注册失败 %s：%s", vtype, resp.status_code)
    except Exception as exc:  # noqa: BLE001 —— 视图是增强，不阻塞主链路
        logger.debug("视图注册异常：%s", exc)
    return None
