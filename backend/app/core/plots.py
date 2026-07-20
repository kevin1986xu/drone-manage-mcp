"""图斑域业务原子能力。

真实模式（配置 DRONE_API_BASE）：从 drone-manage 拉 zoneType=图斑 的
FlyWorkZone 灌入 STORE 后复用本地过滤逻辑；失败回落 mock（L1 降级）。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app import datasource
from app.core import geo
from app.core.store import STORE
from app.datasource import get_real

logger = logging.getLogger(__name__)

# 图斑已到数千级、hydrate 要分页拉全：短 TTL 内直接复用快照，
# 避免同一轮对话多工具连续调用反复全量拉取（也吸收 VPN 慢链路放大）
HYDRATE_TTL_S = 30.0
_last_real_ts = 0.0

# 单次查询最多返回的图斑数（防止大区域查询撑爆 LLM 上下文）
MAX_RETURN = 50


def _hydrate_from_real() -> str:
    """真实图斑全量 → STORE.plots（替换 mock 种子）。

    返回数据源状态：real（本次拉取成功/TTL 内快照）/ cached（平台瞬时不可达，
    STORE 沿用最近一次真实快照）/ mock（从未连通平台，STORE 仍为演示种子）。
    """
    global _last_real_ts
    real = get_real()
    if not real:
        return "mock"
    if _last_real_ts and time.time() - _last_real_ts < HYDRATE_TTL_S:
        return "real"
    try:
        plots = real.list_plots()
    except Exception as exc:  # noqa: BLE001
        if datasource.real_succeeded_before():
            logger.warning("真实图斑接口失败，沿用上次真实快照：%s", exc)
            return "cached"
        logger.warning("真实图斑接口失败且本进程从未连通平台，回落 mock：%s", exc)
        return "mock"
    datasource.note_real_success()
    _last_real_ts = time.time()
    old = STORE.plots
    STORE.plots = {}
    for p in plots:
        existing = old.get(p["plot_id"])
        if existing:
            p["status"] = existing["status"]  # 保留本会话内的状态流转
        STORE.plots[p["plot_id"]] = p
    return "real"


def _plot_view(p: dict[str, Any]) -> dict[str, Any]:
    ring = p["geometry"]["coordinates"][0]
    return {
        "plot_id": p["plot_id"],
        "plot_type": p["plot_type"],
        "priority": p["priority"],
        "batch_no": p["batch_no"],
        "region": p["region"],
        "area_code": p.get("area_code", "-"),
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
    source = _hydrate_from_real()
    items = list(STORE.plots.values())
    if plot_ids:
        wanted = {i.upper() for i in plot_ids}
        # 精确编号或名称包含均可命中（真实图斑编号较长，支持说尾号）
        items = [
            p for p in items
            if p["plot_id"].upper() in wanted or any(w in p["plot_id"].upper() for w in wanted)
        ]
    if region:
        # 行政区名模糊匹配（"汉川"命中"汉川市"）；纯数字视为行政区代码前缀匹配
        # （平台区划码有 6 位与 12 位两种口径，"440311"命中"440311000000"）
        if region.isdigit():
            items = [p for p in items if str(p.get("area_code", "")).startswith(region)]
        else:
            items = [p for p in items if region.replace("区", "") in p["region"]]
    if plot_type:
        items = [p for p in items if plot_type in p["plot_type"]]
    if batch_no:
        items = [p for p in items if p["batch_no"] == batch_no]
    if date_range and len(date_range) == 2:
        lo, hi = date_range
        items = [p for p in items if lo <= p["issued_at"] <= hi]
    items.sort(key=lambda p: p["issued_at"], reverse=True)  # 最新下发在前
    matched = len(items)
    out = {
        "count": matched,
        "batch_no": items[0]["batch_no"] if items else None,
        "plots": [_plot_view(p) for p in items[:MAX_RETURN]],
        **datasource.source_meta(source),
    }
    if matched > MAX_RETURN:
        out["returned"] = MAX_RETURN
        out["note"] = (
            f"共命中 {matched} 个图斑，已按下发时间只返回最新 {MAX_RETURN} 个；"
            "如需其余图斑请用 plot_type/date_range/batch_no 进一步缩小范围"
        )
    return out


def get_plot(plot_id: str) -> dict[str, Any] | None:
    p = STORE.plots.get(plot_id) or STORE.plots.get(plot_id.upper())
    if not p:  # 真实图斑编号较长，支持部分匹配（如尾号）
        p = next((v for k, v in STORE.plots.items() if plot_id.upper() in k.upper()), None)
    return _plot_view(p) if p else None
