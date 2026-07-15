"""图斑域业务原子能力。

真实模式（配置 DRONE_API_BASE）：从 drone-manage 拉 zoneType=图斑 的
FlyWorkZone 灌入 STORE 后复用本地过滤逻辑；失败回落 mock（L1 降级）。
"""

from __future__ import annotations

import logging
from typing import Any

from app.core import geo
from app.core.store import STORE
from app.datasource import get_real

logger = logging.getLogger(__name__)


def _hydrate_from_real() -> bool:
    """真实图斑全量 → STORE.plots（替换 mock 种子）。成功返回 True。"""
    real = get_real()
    if not real:
        return False
    try:
        plots = real.list_plots()
    except Exception as exc:  # noqa: BLE001
        logger.warning("真实图斑接口失败，回落 mock：%s", exc)
        return False
    old = STORE.plots
    STORE.plots = {}
    for p in plots:
        existing = old.get(p["plot_id"])
        if existing:
            p["status"] = existing["status"]  # 保留本会话内的状态流转
        STORE.plots[p["plot_id"]] = p
    return True


def _plot_view(p: dict[str, Any]) -> dict[str, Any]:
    ring = p["geometry"]["coordinates"][0]
    return {
        "plot_id": p["plot_id"],
        "plot_type": p["plot_type"],
        "priority": p["priority"],
        "batch_no": p["batch_no"],
        "region": p["region"],
        "issued_at": p["issued_at"],
        "status": p["status"],
        "area_mu": round(geo.polygon_area_m2(ring) / 666.67, 1),  # 亩
        "centroid": [round(v, 6) for v in geo.centroid(ring)],
        "geometry": p["geometry"],
    }


def query_plots(
    region: str | None = None,
    plot_ids: list[str] | None = None,
    plot_type: str | None = None,
    date_range: list[str] | None = None,
    batch_no: str | None = None,
) -> dict[str, Any]:
    _hydrate_from_real()
    items = list(STORE.plots.values())
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
    views = [_plot_view(p) for p in items]
    return {
        "count": len(views),
        "batch_no": views[0]["batch_no"] if views else None,
        "plots": views,
    }


def get_plot(plot_id: str) -> dict[str, Any] | None:
    p = STORE.plots.get(plot_id) or STORE.plots.get(plot_id.upper())
    if not p:  # 真实图斑编号较长，支持部分匹配（如尾号）
        p = next((v for k, v in STORE.plots.items() if plot_id.upper() in k.upper()), None)
    return _plot_view(p) if p else None
