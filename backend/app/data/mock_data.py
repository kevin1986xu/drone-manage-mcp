"""演示用 Mock 数据：深圳光明区图斑与无人机。

坐标系 CGCS2000 / EPSG:4490（经纬度）。布局与 docs/界面原型.html 的归一化
画布（1000×640）保持一致，通过线性映射换算到光明区实际经纬度范围，
方便前端地图与原型视觉对齐。
"""

from __future__ import annotations

# 映射范围：光明区中心附近约 4km × 3.9km
LON_MIN, LON_SPAN = 113.920, 0.040   # x: 0..1000
LAT_MAX, LAT_SPAN = 22.760, 0.035    # y: 0..640（y 向下）


def norm_to_lonlat(x: float, y: float) -> list[float]:
    """原型画布归一化坐标 → [lon, lat]"""
    return [
        round(LON_MIN + x / 1000 * LON_SPAN, 6),
        round(LAT_MAX - y / 640 * LAT_SPAN, 6),
    ]


def _ring(pts: list[list[float]]) -> list[list[float]]:
    ring = [norm_to_lonlat(x, y) for x, y in pts]
    ring.append(ring[0])  # GeoJSON 闭合环
    return ring


# ── 图斑（本周下发批次 SZ-2607）───────────────────────────────
RAW_PLOTS = [
    {
        "plot_id": "GM-01",
        "plot_type": "疑似新增推填土",
        "priority": "中",
        "pts": [[150, 120], [235, 108], [262, 178], [182, 200]],
    },
    {
        "plot_id": "GM-02",
        "plot_type": "疑似新增建设用地",
        "priority": "高",
        "pts": [[420, 96], [508, 112], [492, 186], [404, 172]],
    },
    {
        "plot_id": "GM-03",
        "plot_type": "疑似新增建设用地",
        "priority": "高",
        "pts": [[610, 238], [700, 226], [722, 308], [624, 322]],
    },
    {
        "plot_id": "GM-04",
        "plot_type": "疑似违建（重点核查）",
        "priority": "高",
        "pts": [[336, 398], [424, 382], [450, 460], [352, 480]],
    },
    {
        "plot_id": "GM-05",
        "plot_type": "疑似耕地流出",
        "priority": "低",
        "pts": [[770, 452], [852, 438], [874, 514], [782, 528]],
    },
]

PLOTS_SEED = [
    {
        "plot_id": p["plot_id"],
        "plot_type": p["plot_type"],
        "priority": p["priority"],
        "batch_no": "SZ-2607",
        "region": "光明区",
        "issued_at": "2026-07-10",
        "status": "待核查",
        "geometry": {"type": "Polygon", "coordinates": [_ring(p["pts"])]},
    }
    for p in RAW_PLOTS
]

# ── 无人机 ───────────────────────────────────────────────────
DRONES_SEED = [
    {
        "drone_id": "D-07",
        "model": "Matrice 350 RTK",
        "battery_pct": 92,
        "payload": "禅思 P1 全画幅航测相机",
        "status": "idle",
        "endurance_min": 30,
        "firmware": "v07.01.0022",
        "obstacle_avoidance": True,
        "location": {"type": "Point", "coordinates": norm_to_lonlat(118, 420)},
    },
    {
        "drone_id": "D-12",
        "model": "Matrice 350 RTK",
        "battery_pct": 87,
        "payload": "禅思 L2 激光雷达",
        "status": "idle",
        "endurance_min": 28,
        "firmware": "v07.01.0022",
        "obstacle_avoidance": True,
        "location": {"type": "Point", "coordinates": norm_to_lonlat(552, 552)},
    },
    {
        "drone_id": "D-21",
        "model": "Mavic 3 行业版",
        "battery_pct": 64,
        "payload": "广角 + 长焦 + 热成像",
        "status": "idle",
        "endurance_min": 22,
        "firmware": "v09.00.0801",
        "obstacle_avoidance": True,
        "location": {"type": "Point", "coordinates": norm_to_lonlat(872, 150)},
    },
]

# ── 环境要素（航线规划需避开）────────────────────────────────
AVOID_FEATURES = [
    {
        "feature_id": "HV-01",
        "kind": "高压线走廊",
        "clearance_m": 35,
        "geometry": {
            "type": "LineString",
            "coordinates": [norm_to_lonlat(500, 0), norm_to_lonlat(520, 280), norm_to_lonlat(480, 640)],
        },
    },
]

# ── 空域许可（演示当日；空域管理平台未接入，文案区域中性）─────
AIRSPACE_PERMIT = {
    "permit_id": "KY-20260722-018",
    "region": "作业空域",
    "valid_until": "18:00",
    "notes": "16:30 后有轻型航空器活动，注意避让",
}

# ── 天气（演示当日，适飞）────────────────────────────────────
WEATHER_NOW = {
    "condition": "晴",
    "wind_speed_ms": 3.2,
    "wind_limit_ms": 12.0,
    "visibility_km": 10,
    "precipitation": "无",
    "temperature_c": 31,
}
