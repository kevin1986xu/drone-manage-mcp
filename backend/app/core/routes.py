"""航线域业务原子能力：生成（多图斑覆盖合并决策）、解释、编辑。

explain_route 返回的是**算法真实决策过程**的结构化数据（覆盖图斑、
合并原因、放弃原因、避让要素、与逐个单飞的对比），LLM 只做转述，
不允许编造理由 —— 对应《建议场景》场景 3 的验收标准。
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import Any

from app.core import geo
from app.core.store import STORE
from app.data import mock_data
from app.datasource import get_real

logger = logging.getLogger(__name__)

CRUISE_MS = 8.0           # 巡航速度 m/s
SORTIE_OVERHEAD_MIN = 8.0  # 单独起降一次的固定开销（转场/起降/换电）
RESERVE_RATIO = 0.15       # 续航预留
EDITOR_TOKEN_TTL_S = 600


def _survey_min(area_mu: float) -> float:
    """图斑核查环绕拍摄耗时（分钟），面积驱动、区间钳制。"""
    return round(min(2.5, max(1.0, area_mu / 150)), 1)


def _resolve_pid(plot_id: str) -> str | None:
    """图斑编号 → STORE 键（mock 的 GM-xx 大写；真实 zoneName 支持尾号部分匹配）。"""
    if plot_id in STORE.plots:
        return plot_id
    up = plot_id.upper()
    if up in STORE.plots:
        return up
    return next((k for k in STORE.plots if up in k.upper()), None)


def _plot_info(plot_id: str) -> dict[str, Any]:
    p = STORE.plots[plot_id]
    ring = p["geometry"]["coordinates"][0]
    return {
        "plot_id": p["plot_id"],
        "centroid": geo.centroid(ring),
        "ring": ring,
        "area_mu": geo.polygon_area_m2(ring) / 666.67,
        "plot_type": p["plot_type"],
        "priority": p["priority"],
    }


def _tour_len_km(start: list[float], order: list[dict[str, Any]]) -> float:
    pts = [start] + [p["centroid"] for p in order] + [start]
    return geo.path_len_m(pts) / 1000


def _nn_order(start: list[float], infos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """最近邻排序构造访问顺序。"""
    remain, order, cur = infos[:], [], start
    while remain:
        remain.sort(key=lambda p: geo.dist_m(cur, p["centroid"]))
        nxt = remain.pop(0)
        order.append(nxt)
        cur = nxt["centroid"]
    return order


def _duration_min(tour_km: float, infos: list[dict[str, Any]]) -> float:
    return tour_km * 1000 / CRUISE_MS / 60 + sum(_survey_min(p["area_mu"]) for p in infos)


def _insertion_cost_km(start: list[float], order: list[dict[str, Any]], cand: dict[str, Any]) -> tuple[float, int]:
    """将候选图斑插入现有巡回的最小增量航程（km）及插入位置。"""
    base = _tour_len_km(start, order)
    best, best_i = float("inf"), 0
    for i in range(len(order) + 1):
        trial = order[:i] + [cand] + order[i:]
        cost = _tour_len_km(start, trial) - base
        if cost < best:
            best, best_i = cost, i
    return best, best_i


def _seg_cross(a: list[float], b: list[float], c: list[float], d: list[float]) -> bool:
    def ccw(p, q, r):
        return (r[1] - p[1]) * (q[0] - p[0]) > (q[1] - p[1]) * (r[0] - p[0])
    return ccw(a, c, d) != ccw(b, c, d) and ccw(a, b, c) != ccw(a, b, d)


def _crossed_features(waypoints: list[list[float]]) -> list[dict[str, Any]]:
    hits = []
    for f in mock_data.AVOID_FEATURES:
        line = f["geometry"]["coordinates"]
        crossed = any(
            _seg_cross(waypoints[i - 1], waypoints[i], line[j - 1], line[j])
            for i in range(1, len(waypoints))
            for j in range(1, len(line))
        )
        if crossed:
            hits.append(
                {
                    "feature_id": f["feature_id"],
                    "kind": f["kind"],
                    "action": f"交叉段仿地抬升至净空 ≥{f['clearance_m']} m，可在编辑器中进一步横向避让",
                }
            )
    return hits


def _build_waypoints(start: list[float], order: list[dict[str, Any]], altitude_m: float) -> list[dict[str, Any]]:
    """航点序列：起降点 → 每个图斑（进入点/穿越点）→ 返回起降点。"""
    pts: list[list[float]] = [start]
    cur = start
    for p in order:
        c = p["centroid"]
        verts = p["ring"][:-1]
        near = min(verts, key=lambda v: geo.dist_m(cur, v))
        far = max(verts, key=lambda v: geo.dist_m(near, v))
        entry = [(near[0] + c[0]) / 2, (near[1] + c[1]) / 2]
        leave = [(far[0] + c[0]) / 2, (far[1] + c[1]) / 2]
        pts += [entry, leave]
        cur = leave
    pts.append(start)
    return [
        {"seq": i + 1, "lon": round(p[0], 6), "lat": round(p[1], 6), "alt_m": altitude_m, "speed_ms": CRUISE_MS}
        for i, p in enumerate(pts)
    ]


def _coverage_rate(plot_id: str, requested: bool) -> float:
    if requested:
        return 100.0
    return 95.0 + (sum(ord(c) for c in plot_id) % 5)


def generate_route(
    drone_id: str,
    plot_ids: list[str],
    strategy: str = "multi_cover",
    altitude_m: float = 120.0,
    overlap_rate: float = 0.7,
    photo_num: int = 4,
    replace_route_id: str | None = None,
) -> dict[str, Any]:
    from app.core import drones as drones_core

    drone = drones_core._find(drone_id)
    if not drone:
        return {"error": f"无人机 {drone_id} 不存在"}
    # 重规划：记录被取代航线的关键指标用于前后对比，并清理其平台孤儿航线
    prev_stats: dict[str, Any] | None = None
    if replace_route_id:
        old_r, old_rev = _rev(replace_route_id)
        if old_rev:
            prev_stats = {
                "route_id": old_r["route_id"],
                "length_km": old_rev["length_km"],
                "duration_min": old_rev["duration_min"],
                "altitude_m": old_rev["altitude_m"],
                "photo_num": old_rev.get("photo_num", 4),
                "covered": len(old_rev["covered_plots"]),
            }
    resolved = [(i, _resolve_pid(i)) for i in plot_ids]
    missing = [raw for raw, key in resolved if key is None]
    if missing:
        return {"error": f"图斑不存在：{', '.join(missing)}"}
    requested_ids = [key for _, key in resolved]
    if not requested_ids:
        return {"error": "plot_ids 不能为空"}

    start = drone["location"]["coordinates"]
    requested = [_plot_info(i) for i in requested_ids]
    order = _nn_order(start, requested)
    battery_pct = drone.get("battery_pct")
    if battery_pct is None:
        battery_pct = 100  # 无实时遥测按满电估算（换电机场典型情况），预留比例照扣
    budget_min = drone["endurance_min"] * battery_pct / 100 * (1 - RESERVE_RATIO)

    merged: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    if strategy == "multi_cover":
        candidates = [
            _plot_info(pid)
            for pid, p in STORE.plots.items()
            if pid not in requested_ids and p["status"] == "待核查"
        ]
        # 高优先级图斑优先占用续航预算，同级按合并增量成本从低到高评估
        prio_rank = {"高": 0, "中": 1, "低": 2}
        candidates = [(c, *_insertion_cost_km(start, order, c)) for c in candidates]
        candidates.sort(key=lambda t: (prio_rank.get(t[0]["priority"], 3), t[1]))
        for cand, _, _ in candidates:
            marginal_km, pos = _insertion_cost_km(start, order, cand)
            separate_km = 2 * geo.dist_m(start, cand["centroid"]) / 1000
            trial = order[:pos] + [cand] + order[pos:]
            trial_min = _duration_min(_tour_len_km(start, trial), trial)
            if marginal_km >= separate_km:
                rejected.append(
                    {"plot_id": cand["plot_id"], "reason": "偏离本次航向带，顺带覆盖不如单独起飞经济"}
                )
            elif trial_min > budget_min:
                rejected.append(
                    {
                        "plot_id": cand["plot_id"],
                        "reason": f"合并后预计 {trial_min:.0f} min，超出续航预算 {budget_min:.0f} min（含 20% 预留）",
                    }
                )
            else:
                order = trial
                merged.append(
                    {
                        "plot_id": cand["plot_id"],
                        "marginal_km": round(marginal_km, 2),
                        "separate_sortie_km": round(separate_km, 2),
                    }
                )

    # 航线航点：真实模式走平台图斑巡检算法（PLOT_INSPECTION，多图斑一条航线），
    # 失败或未配置时用本地几何构造（L1 降级）
    source, platform_route_id = "算法生成", None
    waypoints = platform_duration = None
    real = get_real()
    if real:
        try:
            polys = [STORE.plots[p["plot_id"]]["geometry"]["coordinates"] for p in order]
            planned = real.plan_plot_inspection_route(
                f"低空智察Agent-{time.strftime('%m%d%H%M%S')}", polys,
                photo_num=photo_num, altitude_m=altitude_m, overlap_rate=overlap_rate,
            )
            waypoints = planned["waypoints"]
            platform_route_id = planned["platform_route_id"]
            altitude_m = planned["altitude_m"]
            platform_duration = planned["duration_min"]
            source = "平台图斑巡检算法"
        except Exception as exc:  # noqa: BLE001
            logger.warning("平台航线规划失败，回落本地算法：%s", exc)
            waypoints = None
    if waypoints is None:
        waypoints = _build_waypoints(start, order, altitude_m)
    coords = [[w["lon"], w["lat"]] for w in waypoints]
    length_km = round(geo.path_len_m(coords) / 1000, 1)
    # 时长：平台规划时用平台口径（距离/速度 + 每航点 3s），本地口径含环拍时间
    duration_min = platform_duration or round(_duration_min(length_km, order))

    covered = [
        {
            "plot_id": p["plot_id"],
            "plot_type": p["plot_type"],
            "requested": p["plot_id"] in requested_ids,
            "coverage_rate": _coverage_rate(p["plot_id"], p["plot_id"] in requested_ids),
            "survey_min": _survey_min(p["area_mu"]),
        }
        for p in order
    ]

    # 对比基线：每个覆盖图斑单独起飞一个架次
    separate_total_min = sum(
        2 * geo.dist_m(start, p["centroid"]) / 1000 * 1000 / CRUISE_MS / 60
        + _survey_min(p["area_mu"])
        + SORTIE_OVERHEAD_MIN
        for p in order
    )
    merged_total_min = duration_min + SORTIE_OVERHEAD_MIN
    decision = {
        "strategy": strategy,
        "covered_plots": covered,
        "merge_reason": (
            f"以 {'、'.join(requested_ids)} 为核查目标构造巡回后，"
            f"{'、'.join(m['plot_id'] for m in merged)} 位于同一航向带，"
            "顺带覆盖的增量航程小于单独起飞的往返航程"
            if merged
            else "无可经济合并的相邻图斑"
        ),
        "merged_candidates": merged,
        "rejected_candidates": rejected,
        "avoided_features": _crossed_features(coords),
        "baseline_comparison": {
            "separate_sorties": len(order),
            "separate_total_min": round(separate_total_min),
            "merged_total_min": round(merged_total_min),
            "saved_min": round(separate_total_min - merged_total_min),
        },
        "endurance_budget_min": round(budget_min),
    }

    route_id = STORE.next_id("R", 3)
    rev = {
        "version": 1,
        "waypoints": waypoints,
        "length_km": length_km,
        "duration_min": duration_min,
        "altitude_m": altitude_m,
        "overlap_rate": overlap_rate,
        "photo_num": photo_num,
        "covered_plots": covered,
        "decision": decision,
        "created_at": time.time(),
        "source": source,
        "platform_route_id": platform_route_id,
    }
    STORE.routes[route_id] = {"route_id": route_id, "drone_id": drone["drone_id"], "versions": [rev]}

    # 重规划收尾：删掉被取代航线的平台孤儿 + 从内存移除，构造前后对比
    change_vs_previous = None
    if prev_stats and prev_stats["route_id"] != route_id:
        old_r, old_rev = _rev(prev_stats["route_id"])
        if old_rev and old_rev.get("platform_route_id") and real:
            try:
                real.delete_route(old_rev["platform_route_id"])
            except Exception as exc:  # noqa: BLE001
                logger.warning("清理被取代平台航线失败：%s", exc)
        STORE.routes.pop(prev_stats["route_id"].upper(), None)
        change_vs_previous = {
            "replaced_route_id": prev_stats["route_id"],
            "length_km": f"{prev_stats['length_km']} → {length_km}",
            "duration_min": f"{prev_stats['duration_min']} → {duration_min}",
            "altitude_m": f"{prev_stats['altitude_m']} → {altitude_m}",
            "photo_num": f"{prev_stats['photo_num']} → {photo_num}",
            "covered_plots": f"{prev_stats['covered']} → {len(covered)}",
        }

    within_budget = duration_min <= budget_min
    return {
        "route_id": route_id,
        "version": 1,
        "drone_id": drone["drone_id"],
        "length_km": length_km,
        "duration_min": duration_min,
        "altitude_m": altitude_m,
        "photo_num": photo_num,
        "strategy": strategy,
        "covered_plots": covered,
        "geometry": {"type": "LineString", "coordinates": coords},
        "waypoints": waypoints,
        # 硬约束校验（供 LLM 软约束优化时判断可行性/是否需放宽参数）
        "feasibility": {
            "battery_pct": battery_pct,
            "endurance_budget_min": round(budget_min),
            "duration_min": duration_min,
            "within_budget": within_budget,
            "margin_min": round(budget_min - duration_min),
            "hint": None if within_budget
            else "预计时长超出续航预算，请放宽参数（降低 photo_num、减少覆盖图斑、或改用电量更高的设备）后重规划",
        },
        "change_vs_previous": change_vs_previous,
    }


def _rev(route_id: str, version: int | None = None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    r = STORE.routes.get(route_id.upper())
    if not r:
        return None, None
    revs = r["versions"]
    if version is None:
        return r, revs[-1]
    for rev in revs:
        if rev["version"] == version:
            return r, rev
    return r, None


def get_route_detail(route_id: str, version: int | None = None) -> dict[str, Any]:
    r, rev = _rev(route_id, version)
    if not r:
        return {"error": f"航线 {route_id} 不存在"}
    if not rev:
        return {"error": f"航线 {route_id} 无版本 {version}"}
    out = {
        "route_id": r["route_id"],
        "drone_id": r["drone_id"],
        "version": rev["version"],
        "source": rev["source"],
        "length_km": rev["length_km"],
        "duration_min": rev["duration_min"],
        "altitude_m": rev["altitude_m"],
        "covered_plots": rev["covered_plots"],
        "waypoints": rev["waypoints"],
        "geometry": {"type": "LineString", "coordinates": [[w["lon"], w["lat"]] for w in rev["waypoints"]]},
    }
    # 与上一版本 diff
    prev = next((v for v in r["versions"] if v["version"] == rev["version"] - 1), None)
    if prev:
        moved = []
        for a, b in zip(prev["waypoints"], rev["waypoints"]):
            d = geo.dist_m([a["lon"], a["lat"]], [b["lon"], b["lat"]])
            if d > 1:
                moved.append({"seq": a["seq"], "moved_m": round(d), "bearing_deg": round(geo.bearing_deg([a["lon"], a["lat"]], [b["lon"], b["lat"]]))})
        out["diff_vs_prev"] = {
            "prev_version": prev["version"],
            "moved_waypoints": moved,
            "waypoint_count_delta": len(rev["waypoints"]) - len(prev["waypoints"]),
            "length_km_delta": round(rev["length_km"] - prev["length_km"], 1),
            "duration_min_delta": round(rev["duration_min"] - prev["duration_min"]),
        }
    return out


def explain_route(route_id: str) -> dict[str, Any]:
    r, rev = _rev(route_id)
    if not r or not rev:
        return {"error": f"航线 {route_id} 不存在"}
    return {
        "route_id": r["route_id"],
        "version": rev["version"],
        "length_km": rev["length_km"],
        "duration_min": rev["duration_min"],
        "decision": rev["decision"],
        "note": "以上为航线规划算法的真实决策数据，转述时不得增加数据之外的理由",
    }


def open_route_editor(route_id: str) -> dict[str, Any]:
    r, rev = _rev(route_id)
    if not r or not rev:
        return {"error": f"航线 {route_id} 不存在"}
    token = secrets.token_urlsafe(16)
    STORE.editor_tokens[token] = {"route_id": r["route_id"], "expires_at": time.time() + EDITOR_TOKEN_TTL_S}
    return {
        "route_id": r["route_id"],
        "url": f"/route-editor.html?route_id={r['route_id']}&token={token}",
        "token_ttl_min": EDITOR_TOKEN_TTL_S // 60,
        "channel": "编辑完成后编辑器通过 postMessage 回传，前端转 AG-UI 事件通知 Agent",
    }


def validate_editor_token(route_id: str, token: str) -> bool:
    item = STORE.editor_tokens.get(token)
    return bool(item and item["route_id"] == route_id.upper() and time.time() <= item["expires_at"])


def update_waypoints(route_id: str, waypoints: list[dict[str, Any]], source: str = "人工编辑") -> dict[str, Any]:
    """编辑器保存回调（REST，不是 Agent 工具）：生成新版本，并回写平台航线。"""
    r, rev = _rev(route_id)
    if not r or not rev:
        return {"error": f"航线 {route_id} 不存在"}
    coords = [[w["lon"], w["lat"]] for w in waypoints]
    length_km = round(geo.path_len_m(coords) / 1000, 1)
    extra_min = (length_km - rev["length_km"]) * 1000 / CRUISE_MS / 60
    new_rev = {
        **rev,
        "version": rev["version"] + 1,
        "waypoints": [
            {"seq": i + 1, "alt_m": rev["altitude_m"], "speed_ms": CRUISE_MS, **w} for i, w in enumerate(waypoints)
        ],
        "length_km": length_km,
        "duration_min": round(rev["duration_min"] + extra_min),
        "created_at": time.time(),
        "source": source,
    }
    # 平台航线回写（真实模式）：坐标按索引对位更新，保留平台侧拍照/云台动作
    real = get_real()
    if real and rev.get("platform_route_id"):
        try:
            synced = real.update_route_waypoints(rev["platform_route_id"], new_rev["waypoints"])
            new_rev["platform_synced"] = synced
            if not synced:
                logger.warning("航点数量变化，平台航线未回写（仅本地版本更新）：%s", route_id)
        except Exception as exc:  # noqa: BLE001
            new_rev["platform_synced"] = False
            logger.warning("平台航线回写失败（仅本地版本更新）：%s", exc)
    r["versions"].append(new_rev)
    return get_route_detail(route_id)
