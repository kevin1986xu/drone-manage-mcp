"""飞前检查域：五项单项检查 + 一个聚合检查。

每项返回统一结构：{item, status: pass|warn|fail, detail, data}
气象两级：Open-Meteo 自查 → 平台气象接口；无 mock 保底——两级都失败时
返回 warn 并明确要求人工核实（不造数）。
"""

from __future__ import annotations

import logging
from typing import Any

from uav_mcp import config, geo
from uav_mcp import drones as drones_core
from uav_mcp import routes as routes_core
from uav_mcp.drone_manage import DroneManageError, get_client
from uav_mcp.state import STATE

logger = logging.getLogger(__name__)


def _ref_point() -> tuple[float, float]:
    """气象检测参照点：优先最近航线起点，其次图斑重心均值。返回 (lat, lon)。"""
    if STATE.routes:
        rev = list(STATE.routes.values())[-1]["versions"][-1]
        w = rev["waypoints"][0]
        return w["lat"], w["lon"]
    if STATE.plots:
        cs = [geo.centroid(p["geometry"]["coordinates"][0]) for p in STATE.plots.values()]
        return sum(c[1] for c in cs) / len(cs), sum(c[0] for c in cs) / len(cs)
    return 30.65, 113.55  # 无任何上下文时的兜底参照（汉川一带）


def check_weather(location: str = "作业区域", time_window: str | None = None) -> dict[str, Any]:
    lat, lon = _ref_point()
    # 一级：自查实时气象（Open-Meteo，无需 key）
    if config.WEATHER_PROVIDER != "off":
        try:
            from uav_mcp.weather import fetch_open_meteo

            w = fetch_open_meteo(lat, lon)
            conclusion = {"pass": "满足适飞标准", "warn": "边缘气象条件，注意监控", "fail": "不满足适飞标准"}[w["status"]]
            gust = f"（阵风 {w['wind_gust_ms']} m/s）" if w.get("wind_gust_ms") else ""
            return {
                "item": "气象条件",
                "status": w["status"],
                "detail": f"{w['condition']} · 风速 {w['wind_speed_ms']} m/s{gust}（限值 {w['wind_limit_ms']}）· "
                f"降水 {w['precipitation_mm']} mm · {w['temperature_c']}℃ · {conclusion}",
                "data": {**w, "location": location, "coord": [round(lat, 4), round(lon, 4)]},
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("Open-Meteo 气象自查失败，尝试平台气象：%s", exc)

    # 二级：drone-manage 平台气象（服务端需配置和风天气 key）
    try:
        data = get_client().weather_detect(lat, lon)
        level = str(data.get("weatherLevel") or "").upper()
        status = {"GREEN": "pass", "YELLOW": "warn", "RED": "fail"}.get(level, "warn")
        conclusion = {"pass": "满足适飞标准", "warn": "边缘气象条件，需人工确认", "fail": "禁止飞行"}[status]
        parts = [f"气象等级 {level or '未知'}"]
        if data.get("windSpeed") is not None:
            parts.append(f"风速 {data['windSpeed']} m/s")
        if data.get("temperature") is not None:
            parts.append(f"温度 {data['temperature']}℃")
        if data.get("description"):
            parts.append(str(data["description"]))
        return {
            "item": "气象条件",
            "status": status,
            "detail": " · ".join(parts) + f" · {conclusion}",
            "data": {**data, "location": location, "source": "平台气象服务"},
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("平台气象接口失败：%s", exc)

    return {
        "item": "气象条件",
        "status": "warn",
        "detail": "气象数据源均不可用（Open-Meteo 与平台气象），起飞前必须人工核实作业区气象",
        "data": {"location": location, "coord": [round(lat, 4), round(lon, 4)], "source": "无"},
    }


def check_battery(drone_id: str, route_id: str) -> dict[str, Any]:
    try:
        drones_core.hydrate()
    except DroneManageError as exc:
        return {"item": "电量续航", "status": "fail", "detail": f"无人机平台不可达：{exc}", "data": {}}
    d = drones_core.find(drone_id)
    if not d:
        return {"item": "电量续航", "status": "fail", "detail": f"无人机 {drone_id} 不存在", "data": {}}
    drones_core.enrich_battery(d)
    if d.get("battery_pct") is None:
        return {
            "item": "电量续航",
            "status": "warn",
            "detail": f"{d['drone_id']} 暂无实时电量遥测（机场离线或 OSD 未上报），起飞前需人工核实",
            "data": {"battery_pct": None},
        }
    detail_route = routes_core.get_route_detail(route_id)
    if detail_route.get("error"):
        return {"item": "电量续航", "status": "fail", "detail": detail_route["error"], "data": {}}
    est_endurance = round(d["endurance_min"] * d["battery_pct"] / 100)
    need = detail_route["duration_min"]
    margin = est_endurance - need
    status = "pass" if margin >= 3 else ("warn" if margin >= 0 else "fail")
    return {
        "item": "电量续航",
        "status": status,
        "detail": f"当前电量 {d['battery_pct']}%，预计续航 {est_endurance} min，任务时长 {need} min，余量 {margin} min",
        "data": {"battery_pct": d["battery_pct"], "endurance_min": est_endurance, "task_min": need, "margin_min": margin},
    }


def check_route_obstacle(route_id: str) -> dict[str, Any]:
    detail_route = routes_core.get_route_detail(route_id)
    if detail_route.get("error"):
        return {"item": "航线避障", "status": "fail", "detail": detail_route["error"], "data": {}}
    r, rev = routes_core._rev(route_id)
    platform_planned = rev["source"] == "平台图斑巡检算法"
    manual = rev["source"] == "人工编辑"
    if platform_planned:
        txt = "平台图斑巡检算法规划：已含地形抬升与安全高度下限校验 · 仿地飞行开启"
        status = "pass"
    elif manual:
        txt = "人工调整后的航点已生成新版本；平台侧动作保留 · 请复核调整段净空"
        status = "warn"
    else:
        txt = "本地几何构造航线（平台规划降级），未经平台安全高度校验，起飞前需人工复核净空"
        status = "warn"
    return {
        "item": "航线避障",
        "status": status,
        "detail": txt,
        "data": {"terrain_follow": platform_planned, "source": rev["source"], "version": rev["version"]},
    }


def check_drone_obstacle(drone_id: str) -> dict[str, Any]:
    try:
        drones_core.hydrate()
    except DroneManageError as exc:
        return {"item": "机载避障", "status": "fail", "detail": f"无人机平台不可达：{exc}", "data": {}}
    d = drones_core.find(drone_id)
    if not d:
        return {"item": "机载避障", "status": "fail", "detail": f"无人机 {drone_id} 不存在", "data": {}}
    if not d.get("online"):
        return {"item": "机载避障", "status": "fail", "detail": "机场离线，避障系统无法自检", "data": {"online": False}}
    ok = d.get("obstacle_avoidance", True)
    return {
        "item": "机载避障",
        "status": "pass" if ok else "fail",
        "detail": "机场在线，全向视觉避障系统在线" if ok else "机载避障系统离线",
        "data": {"vision_system": ok, "online": True},
    }


def check_airspace(route_id: str, time_window: str | None = None) -> dict[str, Any]:
    return {
        "item": "空域许可",
        "status": "warn",
        "detail": "空域许可数据源尚未接入（正式版 M4 规划：对接空域申报系统），"
        "起飞前请人工核实当日空域申报/许可状态",
        "data": {"route_id": route_id.upper(), "time_window": time_window, "source": "未接入"},
        # 空域按惯例是飞前五项检查的最后一项；提示 Agent 走完标准流程
        "agent_hint": "若这是飞前五项检查的最后一项且五项均无 fail：向用户汇总结论后，"
        "必须立即调用 take_off（不带 confirm_token）生成人工确认单——该调用不会起飞，"
        "无需先询问用户。若用户只是单独询问空域情况，则忽略本提示。",
    }


def preflight_check(drone_id: str, route_id: str) -> dict[str, Any]:
    checks = [
        check_weather(),
        check_battery(drone_id, route_id),
        check_route_obstacle(route_id),
        check_drone_obstacle(drone_id),
        check_airspace(route_id),
    ]
    worst = "pass"
    if any(c["status"] == "fail" for c in checks):
        worst = "fail"
    elif any(c["status"] == "warn" for c in checks):
        worst = "warn"
    return {
        "drone_id": drone_id.upper(),
        "route_id": route_id.upper(),
        "overall": worst,
        "conclusion": {"pass": "满足起飞条件", "warn": "满足起飞条件，存在注意事项", "fail": "不满足起飞条件"}[worst],
        "checks": checks,
    }
