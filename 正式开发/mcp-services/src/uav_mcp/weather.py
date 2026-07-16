"""自查实时气象：Open-Meteo（免费、无需 API Key、支持国内坐标）。

check_weather 两级链路的第一级；超时/失败由调用方回落平台气象接口。
"""

from __future__ import annotations

from typing import Any

import httpx

# WMO weather code → 中文天况
WMO = {
    0: "晴", 1: "基本晴", 2: "多云", 3: "阴",
    45: "雾", 48: "冻雾",
    51: "毛毛雨", 53: "小雨", 55: "中雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "米雪",
    80: "阵雨", 81: "强阵雨", 82: "暴雨",
    85: "阵雪", 86: "强阵雪",
    95: "雷暴", 96: "雷暴伴冰雹", 99: "强雷暴伴冰雹",
}

WIND_LIMIT_MS = 12.0   # M350 RTK 抗风限值
FAIL_CODES = {65, 66, 67, 75, 82, 86, 95, 96, 99}   # 大雨/冻雨/暴雨/雷暴等
WARN_CODES = {45, 48, 55, 61, 63, 71, 73, 80, 81, 85}  # 雾/降水/降雪


def fetch_open_meteo(lat: float, lon: float, timeout: float = 6.0) -> dict[str, Any]:
    """返回统一气象结构 + 适飞判定。失败抛异常（调用方回落）。"""
    resp = httpx.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": round(lat, 4),
            "longitude": round(lon, 4),
            "current": "temperature_2m,relative_humidity_2m,precipitation,weather_code,"
                       "wind_speed_10m,wind_gusts_10m",
            "wind_speed_unit": "ms",
            "timezone": "Asia/Shanghai",
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    cur = resp.json()["current"]
    code = int(cur.get("weather_code", 0))
    wind = float(cur.get("wind_speed_10m") or 0)
    gust = float(cur.get("wind_gusts_10m") or 0)
    precip = float(cur.get("precipitation") or 0)

    if code in FAIL_CODES or wind >= WIND_LIMIT_MS or gust >= WIND_LIMIT_MS * 1.5:
        status = "fail"
    elif code in WARN_CODES or wind >= WIND_LIMIT_MS * 0.7 or precip > 0:
        status = "warn"
    else:
        status = "pass"

    return {
        "status": status,
        "condition": WMO.get(code, f"天况代码 {code}"),
        "wind_speed_ms": wind,
        "wind_gust_ms": gust,
        "wind_limit_ms": WIND_LIMIT_MS,
        "precipitation_mm": precip,
        "temperature_c": cur.get("temperature_2m"),
        "humidity_pct": cur.get("relative_humidity_2m"),
        "observed_at": cur.get("time"),
        "source": "Open-Meteo 实时气象",
    }
