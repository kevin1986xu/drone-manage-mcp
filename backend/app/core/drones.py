"""无人机与调度域业务原子能力。

真实模式：drone-manage 设备注册表（domain=3 机场）灌入 STORE，
实时电量经 OSD 接口（Java 侧读 Redis）补充；失败回落 mock。
"""

from __future__ import annotations

import logging
from typing import Any

from app.core import geo, plots
from app.core.store import STORE
from app.datasource import get_real

logger = logging.getLogger(__name__)

STATUS_CN = {"idle": "空闲", "dispatched": "已锁定", "flying": "任务中", "maintenance": "维保", "offline": "离线"}


def _hydrate_from_real() -> bool:
    real = get_real()
    if not real:
        return False
    try:
        docks = real.list_docks()
    except Exception as exc:  # noqa: BLE001
        logger.warning("真实设备接口失败，回落 mock：%s", exc)
        return False
    old = STORE.drones
    STORE.drones = {}
    for d in docks:
        existing = old.get(d["drone_id"])
        if existing and existing.get("status") in ("dispatched", "flying"):
            d["status"] = existing["status"]  # 保留会话内锁定/任务状态
        d.setdefault("firmware", "-")
        d.setdefault("obstacle_avoidance", True)
        STORE.drones[d["drone_id"]] = d
    return True


def _enrich_battery(d: dict[str, Any]) -> None:
    """机场在线时经 OSD 接口补实时电量（一次失败不影响主链路）。"""
    real = get_real()
    if not real or d.get("battery_pct") is not None or not d.get("device_sn") or not d.get("online"):
        return
    try:
        osd = real.dock_osd(d["device_sn"])
        if osd and osd.get("batteryPercent") is not None:
            d["battery_pct"] = round(float(osd["batteryPercent"]))
    except Exception as exc:  # noqa: BLE001
        logger.debug("OSD 查询失败 %s：%s", d.get("device_sn"), exc)


def _drone_view(d: dict[str, Any], ref: list[float] | None = None) -> dict[str, Any]:
    v = {
        "drone_id": d["drone_id"],
        "device_sn": d.get("device_sn"),
        "model": d["model"],
        "battery_pct": d["battery_pct"],
        "payload": d["payload"],
        "status": d["status"],
        "status_cn": STATUS_CN.get(d["status"], d["status"]),
        "endurance_min": d["endurance_min"],
        "location": d["location"],
    }
    if ref is not None:
        v["distance_km"] = round(geo.dist_m(d["location"]["coordinates"], ref) / 1000, 2)
    return v


def _find(drone_id: str) -> dict[str, Any] | None:
    d = STORE.drones.get(drone_id) or STORE.drones.get(drone_id.upper())
    if not d:
        d = next(
            (v for k, v in STORE.drones.items()
             if k.upper() == drone_id.upper() or v.get("device_sn", "").upper() == drone_id.upper()),
            None,
        )
    return d


def find_nearby_drones(
    plot_id: str | None = None,
    location: dict[str, Any] | None = None,
    radius_km: float = 5.0,
) -> dict[str, Any]:
    """查询参照物周边的可用无人机。

    参照物三种情形：
      - plot_id：指定单个图斑；
      - location：指定坐标；
      - 都不给："这些图斑附近" → 以**当前查询到的全部待核查图斑**为参照集，
        无人机只要落在**任一图斑** radius 内即纳入，距离取到最近图斑的距离，
        并标注离哪个图斑最近（避免用平均中心把离某个图斑很近的机误排除）。
    """
    _hydrate_from_real()
    plots._hydrate_from_real()  # 确保图斑参照也是真实数据（可能未经 query_plots 直接进入）
    # refs: [(label, [lon, lat]), ...]
    if plot_id:
        p = plots.get_plot(plot_id)
        if not p:
            return {"error": f"图斑 {plot_id} 不存在", "drones": []}
        refs = [(p["plot_id"], p["centroid"])]
    elif location and location.get("coordinates"):
        refs = [("指定位置", location["coordinates"])]
    else:
        all_plots = plots.query_plots()["plots"]
        if not all_plots:
            return {"error": "无参照位置", "drones": []}
        refs = [(p["plot_id"], p["centroid"]) for p in all_plots]

    def _nearest(dcoord: list[float]) -> tuple[float, str]:
        """无人机到参照集中最近图斑的距离(km)及该图斑标签。"""
        label, ref = min(refs, key=lambda r: geo.dist_m(dcoord, r[1]))
        return geo.dist_m(dcoord, ref) / 1000, label

    def _within(r_km: float) -> list[tuple[dict[str, Any], float, str]]:
        hits = []
        for d in STORE.drones.values():
            dist, near = _nearest(d["location"]["coordinates"])
            if dist <= r_km:
                hits.append((d, dist, near))
        return hits

    hits = _within(radius_km)
    note = None
    if not hits and radius_km <= 10:
        # 机场部署稀疏时自动扩大搜索半径（真实场景"周边"通常是市域范围）
        for expanded in (20.0, 50.0):
            hits = _within(expanded)
            if hits:
                note = f"{radius_km:.0f} km 内无可用设备，已自动扩大搜索半径至 {expanded:.0f} km"
                radius_km = expanded
                break

    hits.sort(key=lambda t: t[1])
    for d, _, _ in hits[:8]:  # 限制 OSD 查询次数
        _enrich_battery(d)
    multi = len(refs) > 1
    drones_out = []
    for d, dist, near in hits:
        v = _drone_view(d)
        v["distance_km"] = round(dist, 2)
        if multi:
            v["nearest_plot"] = near  # 离哪个图斑最近
        drones_out.append(v)
    out = {
        "radius_km": radius_km,
        "reference": "查询到的全部图斑" if multi else refs[0][0],
        "reference_plot_count": len(refs) if multi else 1,
        "count": len(drones_out),
        "drones": drones_out,
    }
    if note:
        out["note"] = note
    return out


def get_drone_status(drone_id: str) -> dict[str, Any]:
    _hydrate_from_real()
    d = _find(drone_id)
    if not d:
        return {"error": f"无人机 {drone_id} 不存在"}
    _enrich_battery(d)
    v = _drone_view(d)
    v.update(
        firmware=d.get("firmware", "-"),
        obstacle_avoidance=d.get("obstacle_avoidance", True),
        health_check="正常" if d.get("status") != "offline" else "离线，无法自检",
    )
    return v


def lock_drone(drone_id: str, task_type: str, plot_ids: list[str]) -> dict[str, Any]:
    """确认后的实际锁定动作（由 confirm 流程调用，不直接暴露给 Agent）。"""
    d = _find(drone_id)
    if not d:
        return {"error": f"无人机 {drone_id} 不存在"}
    if d["status"] != "idle":
        return {"error": f"{drone_id} 当前状态为 {STATUS_CN.get(d['status'])}，不可调度"}
    order_id = STORE.next_id("DSP")
    d["status"] = "dispatched"
    order = {
        "order_id": order_id,
        "drone_id": d["drone_id"],
        "task_type": task_type,
        "plot_ids": [p.upper() for p in plot_ids],
        "status": "locked",
    }
    STORE.dispatch_orders[order_id] = order
    return order
