"""图斑域业务原子能力（真实平台直连，无 mock）。"""

from __future__ import annotations

import logging
import time
from typing import Any

from uav_mcp import geo
from uav_mcp.drone_manage import DroneManageError, get_client
from uav_mcp.state import STATE

logger = logging.getLogger(__name__)

HYDRATE_TTL_S = 15.0
_last_hydrate = 0.0


def hydrate() -> None:
    """真实图斑全量 → STATE.plots。失败抛 DroneManageError（工具层转错误返回）。

    带短 TTL 缓存：同一轮对话内多工具连续调用不重复拉平台
    （图斑分钟级变化，15s 足够新鲜；也吸收 VPN 慢链路的放大效应）。
    """
    global _last_hydrate
    if STATE.plots and time.time() - _last_hydrate < HYDRATE_TTL_S:
        return
    plots = get_client().list_plots()
    _last_hydrate = time.time()
    old = STATE.plots
    STATE.plots = {}
    for p in plots:
        existing = old.get(p["plot_id"])
        if existing:
            p["status"] = existing["status"]  # 保留会话内的状态流转
        STATE.plots[p["plot_id"]] = p


def _plot_view(p: dict[str, Any], include_geometry: bool) -> dict[str, Any]:
    v = {
        "plot_id": p["plot_id"],
        "plot_type": p["plot_type"],
        "priority": p["priority"],
        "batch_no": p["batch_no"],
        "region": p["region"],
        "issued_at": p["issued_at"],
        "status": p["status"],
        "area_mu": p["area_mu"],
        "centroid": p["centroid"],
    }
    if include_geometry:
        v["geometry"] = p["geometry"]
    return v


def query_plots(
    region: str | None = None,
    plot_ids: list[str] | None = None,
    plot_type: str | None = None,
    date_range: list[str] | None = None,
    batch_no: str | None = None,
    include_geometry: bool = False,
) -> dict[str, Any]:
    """查询图斑。默认瘦身返回（不含 GeoJSON 边界，省 LLM 上下文）；
    GIS 前端/BFF 需要边界时传 include_geometry=True。"""
    try:
        hydrate()
    except DroneManageError as exc:
        return {"error": f"无人机平台不可达：{exc}", "plots": [], "count": 0}
    items = list(STATE.plots.values())
    if plot_ids:
        wanted = {i.upper() for i in plot_ids}
        # 精确编号或名称包含均可命中（真实图斑编号较长，支持说尾号）
        items = [
            p for p in items
            if p["plot_id"].upper() in wanted or any(w in p["plot_id"].upper() for w in wanted)
        ]
    if region:
        items = [p for p in items if region.replace("区", "") in p["region"]]
    if plot_type:
        items = [p for p in items if plot_type in p["plot_type"]]
    if batch_no:
        items = [p for p in items if p["batch_no"] == batch_no]
    if date_range and len(date_range) == 2:
        lo, hi = date_range
        items = [p for p in items if lo <= p["issued_at"] <= hi]
    views = [_plot_view(p, include_geometry) for p in items]
    return {
        "count": len(views),
        "batch_no": views[0]["batch_no"] if views else None,
        "plots": views,
    }


def get_plot(plot_id: str, include_geometry: bool = False) -> dict[str, Any] | None:
    p = STATE.plots.get(plot_id) or STATE.plots.get(plot_id.upper())
    if not p:  # 真实图斑编号较长，支持部分匹配（如尾号）
        p = next((v for k, v in STATE.plots.items() if plot_id.upper() in k.upper()), None)
    return _plot_view(p, include_geometry) if p else None


def resolve_pid(plot_id: str) -> str | None:
    """图斑编号（可为尾号片段）→ STATE 键。"""
    if plot_id in STATE.plots:
        return plot_id
    up = plot_id.upper()
    if up in STATE.plots:
        return up
    return next((k for k in STATE.plots if up in k.upper()), None)
