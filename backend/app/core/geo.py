"""轻量地理计算（EPSG:4490 经纬度，米制近似），避免引入 GIS 重依赖。"""

from __future__ import annotations

import math

EARTH_M_PER_DEG_LAT = 111_320.0


def m_per_deg_lon(lat: float) -> float:
    return EARTH_M_PER_DEG_LAT * math.cos(math.radians(lat))


def dist_m(a: list[float], b: list[float]) -> float:
    """两点距离（米），equirectangular 近似，8km 尺度误差可忽略。"""
    lat0 = (a[1] + b[1]) / 2
    dx = (b[0] - a[0]) * m_per_deg_lon(lat0)
    dy = (b[1] - a[1]) * EARTH_M_PER_DEG_LAT
    return math.hypot(dx, dy)


def path_len_m(pts: list[list[float]]) -> float:
    return sum(dist_m(pts[i - 1], pts[i]) for i in range(1, len(pts)))


def centroid(ring: list[list[float]]) -> list[float]:
    """多边形外环质心（顶点均值即可，图斑近似凸四边形）。"""
    pts = ring[:-1] if ring[0] == ring[-1] else ring
    return [
        sum(p[0] for p in pts) / len(pts),
        sum(p[1] for p in pts) / len(pts),
    ]


def polygon_area_m2(ring: list[list[float]]) -> float:
    """鞋带公式面积（平方米）。"""
    pts = ring[:-1] if ring[0] == ring[-1] else ring
    lat0 = sum(p[1] for p in pts) / len(pts)
    kx, ky = m_per_deg_lon(lat0), EARTH_M_PER_DEG_LAT
    xy = [(p[0] * kx, p[1] * ky) for p in pts]
    s = 0.0
    for i in range(len(xy)):
        x1, y1 = xy[i]
        x2, y2 = xy[(i + 1) % len(xy)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2


def bearing_deg(a: list[float], b: list[float]) -> float:
    lat0 = (a[1] + b[1]) / 2
    dx = (b[0] - a[0]) * m_per_deg_lon(lat0)
    dy = (b[1] - a[1]) * EARTH_M_PER_DEG_LAT
    return (math.degrees(math.atan2(dx, dy)) + 360) % 360
