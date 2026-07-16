"""航线域业务原子能力：生成（多图斑覆盖合并决策）、解释、编辑。

规划主链路 = 平台图斑巡检算法（planDynamicRoute · PLOT_INSPECTION，
与平台批量调度器同款）；合并决策（哪些邻近图斑顺带覆盖）在本层完成。
explain_route 返回**算法真实决策过程**的结构化数据，LLM 只做转述，
不允许编造理由。
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import Any

from uav_mcp import config, geo, plots as plots_core
from uav_mcp.drone_manage import DroneManageError, get_client
from uav_mcp.state import STATE

logger = logging.getLogger(__name__)

CRUISE_MS = 8.0            # 巡航/拍摄速度 m/s（图斑内环绕拍摄）
FERRY_MS = 15.0            # 转场速度 m/s（机场↔作业区往返，明显快于拍摄速度）
SORTIE_OVERHEAD_MIN = 8.0  # 单独起降一次的固定开销（转场/起降/换电）
RESERVE_RATIO = 0.15       # 续航预留
EDITOR_TOKEN_TTL_S = 600


def _survey_min(area_mu: float) -> float:
    """图斑核查环绕拍摄耗时（分钟），面积驱动、区间钳制。"""
    return round(min(2.5, max(1.0, area_mu / 150)), 1)


def _plot_info(plot_id: str) -> dict[str, Any]:
    p = STATE.plots[plot_id]
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
    # 转场航程用转场速度估时（往返机场是主要里程），拍摄时长按图斑面积单列。
    return tour_km * 1000 / FERRY_MS / 60 + sum(_survey_min(p["area_mu"]) for p in infos)


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


def _build_waypoints(start: list[float], order: list[dict[str, Any]], altitude_m: float) -> list[dict[str, Any]]:
    """本地几何构造（平台规划失败时的降级）：起降点 → 每图斑进出点 → 返回。"""
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


def generate_route(
    drone_id: str,
    plot_ids: list[str],
    strategy: str = "multi_cover",
    altitude_m: float = 120.0,
    overlap_rate: float = 0.7,
    photo_num: int = 4,
    replace_route_id: str | None = None,
) -> dict[str, Any]:
    from uav_mcp import drones as drones_core

    try:
        plots_core.hydrate()
        drones_core.hydrate()
    except DroneManageError as exc:
        return {"error": f"无人机平台不可达：{exc}"}
    drone = drones_core.find(drone_id)
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
    resolved = [(i, plots_core.resolve_pid(i)) for i in plot_ids]
    missing = [raw for raw, key in resolved if key is None]
    if missing:
        return {"error": f"图斑不存在：{', '.join(missing)}"}
    requested_ids = [key for _, key in resolved]
    if not requested_ids:
        return {"error": "plot_ids 不能为空"}

    start = drone["location"]["coordinates"]
    requested = [_plot_info(i) for i in requested_ids]
    order = _nn_order(start, requested)
    drones_core.enrich_battery(drone)
    battery_pct = drone.get("battery_pct")
    if battery_pct is None:
        battery_pct = 100  # 无实时遥测按满电估算（换电机场典型情况），预留比例照扣
    budget_min = drone["endurance_min"] * battery_pct / 100 * (1 - RESERVE_RATIO)

    merged: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    if strategy == "multi_cover":
        candidates = [
            _plot_info(pid)
            for pid, p in STATE.plots.items()
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
                        "reason": f"合并后预计 {trial_min:.0f} min，超出续航预算 {budget_min:.0f} min（含预留）",
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

    # 航点：平台图斑巡检算法为主（PLOT_INSPECTION，多图斑一条航线），
    # 平台规划单点失败时降级本地几何构造
    source, platform_route_id = "本地几何构造（平台规划失败降级）", None
    waypoints = platform_duration = None
    try:
        polys = [STATE.plots[p["plot_id"]]["geometry"]["coordinates"] for p in order]
        planned = get_client().plan_plot_inspection_route(
            f"{config.ROUTE_NAME_PREFIX}-{time.strftime('%m%d%H%M%S')}", polys,
            photo_num=photo_num, altitude_m=altitude_m, overlap_rate=overlap_rate,
        )
        waypoints = planned["waypoints"]
        platform_route_id = planned["platform_route_id"]
        altitude_m = planned["altitude_m"]
        platform_duration = planned["duration_min"]
        source = "平台图斑巡检算法"
    except Exception as exc:  # noqa: BLE001
        logger.warning("平台航线规划失败，降级本地几何构造：%s", exc)
    if waypoints is None:
        waypoints = _build_waypoints(start, order, altitude_m)
    coords = [[w["lon"], w["lat"]] for w in waypoints]
    length_km = round(geo.path_len_m(coords) / 1000, 1)
    duration_min = platform_duration or round(_duration_min(length_km, order))

    covered = [
        {
            "plot_id": p["plot_id"],
            "plot_type": p["plot_type"],
            "requested": p["plot_id"] in requested_ids,
            "survey_min": _survey_min(p["area_mu"]),
        }
        for p in order
    ]

    # 对比基线：每个覆盖图斑单独起飞一个架次（往返转场用转场速度）
    separate_total_min = sum(
        2 * geo.dist_m(start, p["centroid"]) / 1000 * 1000 / FERRY_MS / 60
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
        # 避让要素库正式版 M4 接入；平台规划已含地形抬升与安全高度校验
        "avoided_features": [],
        "baseline_comparison": {
            "separate_sorties": len(order),
            "separate_total_min": round(separate_total_min),
            "merged_total_min": round(merged_total_min),
            "saved_min": round(separate_total_min - merged_total_min),
        },
        "endurance_budget_min": round(budget_min),
    }

    route_id = STATE.next_id("R", 3)
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
    STATE.routes[route_id] = {"route_id": route_id, "drone_id": drone["drone_id"], "versions": [rev]}

    # 重规划收尾：删掉被取代航线的平台孤儿 + 从内存移除，构造前后对比
    change_vs_previous = None
    if prev_stats and prev_stats["route_id"] != route_id:
        old_r, old_rev = _rev(prev_stats["route_id"])
        if old_rev and old_rev.get("platform_route_id"):
            try:
                get_client().delete_route(old_rev["platform_route_id"])
            except Exception as exc:  # noqa: BLE001
                logger.warning("清理被取代平台航线失败：%s", exc)
        STATE.routes.pop(prev_stats["route_id"].upper(), None)
        change_vs_previous = {
            "replaced_route_id": prev_stats["route_id"],
            "length_km": f"{prev_stats['length_km']} → {length_km}",
            "duration_min": f"{prev_stats['duration_min']} → {duration_min}",
            "altitude_m": f"{prev_stats['altitude_m']} → {altitude_m}",
            "photo_num": f"{prev_stats['photo_num']} → {photo_num}",
            "covered_plots": f"{prev_stats['covered']} → {len(covered)}",
        }

    within_budget = duration_min <= budget_min
    # 瘦身返回：航点/几何不进 LLM 上下文（waypoint_count 代替），
    # 全量数据经 get_route_detail(include_waypoints=True) 供 BFF/前端取用
    return {
        "route_id": route_id,
        "version": 1,
        "drone_id": drone["drone_id"],
        "source": source,
        "length_km": length_km,
        "duration_min": duration_min,
        "altitude_m": altitude_m,
        "photo_num": photo_num,
        "strategy": strategy,
        "waypoint_count": len(waypoints),
        "covered_plots": covered,
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
    r = STATE.routes.get(route_id.upper()) or STATE.routes.get(route_id)
    if not r:
        return None, None
    revs = r["versions"]
    if version is None:
        return r, revs[-1]
    for rev in revs:
        if rev["version"] == version:
            return r, rev
    return r, None


def get_route_detail(route_id: str, version: int | None = None, include_waypoints: bool = False) -> dict[str, Any]:
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
        "waypoint_count": len(rev["waypoints"]),
        "platform_route_id": rev.get("platform_route_id"),
    }
    if include_waypoints:
        out["waypoints"] = rev["waypoints"]
        out["geometry"] = {"type": "LineString", "coordinates": [[w["lon"], w["lat"]] for w in rev["waypoints"]]}
    prev = next((v for v in r["versions"] if v["version"] == rev["version"] - 1), None)
    if prev:
        moved = []
        for a, b in zip(prev["waypoints"], rev["waypoints"]):
            d = geo.dist_m([a["lon"], a["lat"]], [b["lon"], b["lat"]])
            if d > 1:
                moved.append({"seq": a["seq"], "moved_m": round(d),
                              "bearing_deg": round(geo.bearing_deg([a["lon"], a["lat"]], [b["lon"], b["lat"]]))})
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
    STATE.editor_tokens[token] = {"route_id": r["route_id"], "expires_at": time.time() + EDITOR_TOKEN_TTL_S}
    return {
        "route_id": r["route_id"],
        "url": f"/route-editor.html?route_id={r['route_id']}&token={token}",
        "token_ttl_min": EDITOR_TOKEN_TTL_S // 60,
        "channel": "编辑完成后编辑器经 BFF 回传保存（update_waypoints），并回写平台航线",
    }


def validate_editor_token(route_id: str, token: str) -> bool:
    item = STATE.editor_tokens.get(token)
    return bool(item and item["route_id"] == route_id.upper() and time.time() <= item["expires_at"])


def update_waypoints(route_id: str, waypoints: list[dict[str, Any]], source: str = "人工编辑") -> dict[str, Any]:
    """编辑器保存回调（供 BFF REST 调用，不是 Agent 工具）：生成新版本并回写平台。"""
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
    # 平台航线回写：坐标按索引对位更新，保留平台侧拍照/云台动作
    if rev.get("platform_route_id"):
        try:
            synced = get_client().update_route_waypoints(rev["platform_route_id"], new_rev["waypoints"])
            new_rev["platform_synced"] = synced
            if not synced:
                logger.warning("航点数量变化，平台航线未回写（仅本地版本更新）：%s", route_id)
        except Exception as exc:  # noqa: BLE001
            new_rev["platform_synced"] = False
            logger.warning("平台航线回写失败（仅本地版本更新）：%s", exc)
    r["versions"].append(new_rev)
    return get_route_detail(route_id, include_waypoints=True)
