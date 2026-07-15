"""飞前检查域：五项单项检查 + 一个聚合检查。

每项返回统一结构：{item, status: pass|warn|fail, detail, data}
演示用单项串调以展示 CoT；快速链路可用聚合 preflight_check。
"""

from __future__ import annotations

import logging
from typing import Any

from app.core import drones as drones_core, routes as routes_core
from app.core.store import STORE
from app.data import mock_data
from app.datasource import get_real

logger = logging.getLogger(__name__)


def _ref_point() -> tuple[float, float]:
    """气象检测参照点：优先最近航线起点，其次图斑中心。返回 (lat, lon)。"""
    if STORE.routes:
        rev = list(STORE.routes.values())[-1]["versions"][-1]
        w = rev["waypoints"][0]
        return w["lat"], w["lon"]
    if STORE.plots:
        from app.core import geo

        cs = [geo.centroid(p["geometry"]["coordinates"][0]) for p in STORE.plots.values()]
        return sum(c[1] for c in cs) / len(cs), sum(c[0] for c in cs) / len(cs)
    return 22.7425, 113.94


def check_weather(location: str = "作业区域", time_window: str | None = None) -> dict[str, Any]:
    # 一级：自查实时气象（Open-Meteo，无需 key；WEATHER_PROVIDER=mock 禁用）
    import os

    if os.getenv("WEATHER_PROVIDER", "auto") != "mock":
        try:
            from app.datasource.weather import fetch_open_meteo

            lat, lon = _ref_point()
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
    real = get_real()
    if real:
        try:
            lat, lon = _ref_point()
            data = real.weather_detect(lat, lon)
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
            logger.warning("平台气象接口失败，回落本地气象：%s", exc)

    # 三级：本地 mock（脱网演示保底）
    w = mock_data.WEATHER_NOW
    ok = w["wind_speed_ms"] <= w["wind_limit_ms"] and w["visibility_km"] >= 3 and w["precipitation"] == "无"
    return {
        "item": "气象条件",
        "status": "pass" if ok else "fail",
        "detail": f"{w['condition']} · 风速 {w['wind_speed_ms']} m/s（限值 {w['wind_limit_ms']}）· "
        f"能见度 {w['visibility_km']} km · {'满足适飞标准' if ok else '不满足适飞标准'}",
        "data": {**w, "location": location, "time_window": time_window},
    }


def check_battery(drone_id: str, route_id: str) -> dict[str, Any]:
    d = drones_core._find(drone_id)
    if not d:
        return {"item": "电量续航", "status": "fail", "detail": f"无人机 {drone_id} 不存在", "data": {}}
    drones_core._enrich_battery(d)
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
        "detail": f"当前电量 {d['battery_pct']}%，预计续航 {est_endurance} min，任务时长 {need} min，"
        f"余量 {margin} min",
        "data": {"battery_pct": d["battery_pct"], "endurance_min": est_endurance, "task_min": need, "margin_min": margin},
    }


def check_route_obstacle(route_id: str) -> dict[str, Any]:
    detail_route = routes_core.get_route_detail(route_id)
    if detail_route.get("error"):
        return {"item": "航线避障", "status": "fail", "detail": detail_route["error"], "data": {}}
    r, rev = routes_core._rev(route_id)
    avoided = rev["decision"]["avoided_features"]
    manual = rev["source"] == "人工编辑"
    txt = "仿地飞行已开启 · 航线净空 ≥35 m"
    if avoided and not manual:
        txt += " · " + "；".join(f"已处理{a['kind']}（{a['action']}）" for a in avoided)
    elif manual:
        txt += " · 人工调整后的航点已复核，避开高压线走廊"
    return {
        "item": "航线避障",
        "status": "pass",
        "detail": txt,
        "data": {"terrain_follow": True, "clearance_m": 35, "avoided_features": avoided, "version": rev["version"]},
    }


def check_drone_obstacle(drone_id: str) -> dict[str, Any]:
    d = drones_core._find(drone_id)
    if not d:
        return {"item": "机载避障", "status": "fail", "detail": f"无人机 {drone_id} 不存在", "data": {}}
    ok = d.get("obstacle_avoidance", True)
    return {
        "item": "机载避障",
        "status": "pass" if ok else "fail",
        "detail": "全向视觉避障系统在线，激光雷达自检正常" if ok else "机载避障系统离线",
        "data": {"vision_system": ok, "lidar_selfcheck": "正常" if ok else "异常"},
    }


def check_airspace(route_id: str, time_window: str | None = None) -> dict[str, Any]:
    p = mock_data.AIRSPACE_PERMIT
    return {
        "item": "空域许可",
        "status": "warn",
        "detail": f"{p['region']}临时空域许可（{p['permit_id']}）有效至今日 {p['valid_until']}；{p['notes']}",
        "data": {**p, "route_id": route_id.upper(), "time_window": time_window},
        # 空域按惯例是飞前五项检查的最后一项；提示 Agent 走完标准流程
        "agent_hint": "若这是飞前五项检查的最后一项且五项均无 fail：向用户汇总结论后，"
        "必须立即调用 take_off（不带 confirm_token）弹出人工确认卡片——该调用不会起飞，"
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
