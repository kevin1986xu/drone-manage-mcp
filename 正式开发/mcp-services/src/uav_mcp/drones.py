"""无人机与调度域业务原子能力（drone-manage 设备注册表 + OSD 实时电量）。"""

from __future__ import annotations

import logging
import time
from typing import Any

from uav_mcp import geo, plots
from uav_mcp.drone_manage import DroneManageError, get_client
from uav_mcp.state import STATE

logger = logging.getLogger(__name__)

STATUS_CN = {"idle": "空闲", "dispatched": "已锁定", "flying": "任务中", "maintenance": "维保", "offline": "离线"}

HYDRATE_TTL_S = 15.0
_last_hydrate = 0.0


def hydrate() -> None:
    """设备注册表 → STATE.drones，带短 TTL 缓存（见 plots.hydrate 说明）。"""
    global _last_hydrate
    if STATE.drones and time.time() - _last_hydrate < HYDRATE_TTL_S:
        return
    docks = get_client().list_docks()
    _last_hydrate = time.time()
    old = STATE.drones
    STATE.drones = {}
    for d in docks:
        existing = old.get(d["drone_id"])
        if existing and existing.get("status") in ("dispatched", "flying"):
            d["status"] = existing["status"]  # 保留会话内锁定/任务状态
        d.setdefault("firmware", "-")
        d.setdefault("obstacle_avoidance", True)
        STATE.drones[d["drone_id"]] = d


def enrich_battery(d: dict[str, Any]) -> None:
    """机场在线时经 OSD 接口补实时电量（一次失败不影响主链路）。"""
    if d.get("battery_pct") is not None or not d.get("device_sn") or not d.get("online"):
        return
    try:
        osd = get_client().dock_osd(d["device_sn"])
        if osd and osd.get("batteryPercent") is not None:
            d["battery_pct"] = round(float(osd["batteryPercent"]))
    except Exception as exc:  # noqa: BLE001
        logger.debug("OSD 查询失败 %s：%s", d.get("device_sn"), exc)


def _drone_view(d: dict[str, Any]) -> dict[str, Any]:
    return {
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


def find(drone_id: str) -> dict[str, Any] | None:
    d = STATE.drones.get(drone_id) or STATE.drones.get(drone_id.upper())
    if not d:
        d = next(
            (v for k, v in STATE.drones.items()
             if k.upper() == drone_id.upper() or (v.get("device_sn") or "").upper() == drone_id.upper()
             or drone_id in k),  # 支持"庙头镇"等机场名片段
            None,
        )
    return d


def find_nearby_drones(
    plot_id: str | None = None,
    location: dict[str, Any] | None = None,
    radius_km: float = 5.0,
    plot_ids: list[str] | None = None,
) -> dict[str, Any]:
    """查询参照物周边的可用无人机。

    参照物四种情形（按优先级）：
      - plot_ids：**本次要执行任务的目标图斑集合**——为某批图斑选机必须用它，
        距离按这些目标图斑计算（选机的距离基准必须是要飞的图斑）；
      - plot_id：指定单个图斑；
      - location：指定坐标；
      - 都不给：泛盘点 → 以全部待核查图斑为参照集，无人机落在任一图斑
        radius 内即纳入，距离取到最近图斑并标注。
    """
    try:
        hydrate()
        plots.hydrate()  # 图斑参照也要真实数据（可能未经 query_plots 直接进入）
    except DroneManageError as exc:
        return {"error": f"无人机平台不可达：{exc}", "drones": [], "count": 0}
    if plot_ids:
        refs = []
        for pid in plot_ids:
            p = plots.get_plot(pid)
            if p:
                refs.append((p["plot_id"], p["centroid"]))
        if not refs:
            return {"error": f"目标图斑不存在：{', '.join(plot_ids)}", "drones": []}
    elif plot_id:
        p = plots.get_plot(plot_id)
        if not p:
            return {"error": f"图斑 {plot_id} 不存在", "drones": []}
        refs = [(p["plot_id"], p["centroid"])]
    elif location and location.get("coordinates"):
        refs = [("指定位置", location["coordinates"])]
    else:
        all_plots = [p for p in STATE.plots.values()]
        if not all_plots:
            return {"error": "无参照位置（平台无图斑数据）", "drones": []}
        refs = [(p["plot_id"], p["centroid"]) for p in all_plots]

    def _nearest(dcoord: list[float]) -> tuple[float, str]:
        label, ref = min(refs, key=lambda r: geo.dist_m(dcoord, r[1]))
        return geo.dist_m(dcoord, ref) / 1000, label

    def _within(r_km: float) -> list[tuple[dict[str, Any], float, str]]:
        hits = []
        for d in STATE.drones.values():
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
        enrich_battery(d)
    multi = len(refs) > 1
    drones_out = []
    for d, dist, near in hits:
        v = _drone_view(d)
        v["distance_km"] = round(dist, 2)
        if multi:
            v["nearest_plot"] = near
        drones_out.append(v)
    out = {
        "radius_km": radius_km,
        "reference": (
            f"本次任务目标图斑（{len(refs)} 个）" if plot_ids
            else ("查询到的全部图斑" if multi else refs[0][0])
        ),
        "reference_plot_count": len(refs) if multi else 1,
        "count": len(drones_out),
        "drones": drones_out,
    }
    if note:
        out["note"] = note
    return out


def get_drone_status(drone_id: str) -> dict[str, Any]:
    try:
        hydrate()
    except DroneManageError as exc:
        return {"error": f"无人机平台不可达：{exc}"}
    d = find(drone_id)
    if not d:
        return {"error": f"无人机 {drone_id} 不存在"}
    enrich_battery(d)
    v = _drone_view(d)
    v.update(
        firmware=d.get("firmware", "-"),
        obstacle_avoidance=d.get("obstacle_avoidance", True),
        health_check="正常" if d.get("status") != "offline" else "离线，无法自检",
    )
    return v


def lock_drone(drone_id: str, task_type: str, plot_ids: list[str]) -> dict[str, Any]:
    """确认后的实际锁定动作（由 confirm 流程调用，不直接暴露给 Agent）。"""
    d = find(drone_id)
    if not d:
        return {"error": f"无人机 {drone_id} 不存在"}
    if d["status"] != "idle":
        return {"error": f"{drone_id} 当前状态为 {STATUS_CN.get(d['status'])}，不可调度"}
    order_id = STATE.next_id("DSP")
    d["status"] = "dispatched"
    order = {
        "order_id": order_id,
        "drone_id": d["drone_id"],
        "task_type": task_type,
        "plot_ids": [p.upper() for p in plot_ids],
        "status": "locked",
    }
    STATE.dispatch_orders[order_id] = order
    return order
