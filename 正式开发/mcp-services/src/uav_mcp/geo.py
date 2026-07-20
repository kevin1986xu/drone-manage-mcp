"""轻量地理计算（EPSG:4490 经纬度，米制近似），避免引入 GIS 重依赖。"""

from __future__ import annotations

import math

EARTH_M_PER_DEG_LAT = 111_320.0


def m_per_deg_lon(lat: float) -> float:
    return EARTH_M_PER_DEG_LAT * math.cos(math.radians(lat))


def dist_m(a: list[float], b: list[float]) -> float:
    """两点距离（米），equirectangular 近似，市域尺度误差可忽略。"""
    lat0 = (a[1] + b[1]) / 2
    dx = (b[0] - a[0]) * m_per_deg_lon(lat0)
    dy = (b[1] - a[1]) * EARTH_M_PER_DEG_LAT
    return math.hypot(dx, dy)


def path_len_m(pts: list[list[float]]) -> float:
    return sum(dist_m(pts[i - 1], pts[i]) for i in range(1, len(pts)))


def centroid(ring: list[list[float]]) -> list[float]:
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


def point_in_ring(pt: list[float], ring: list[list[float]]) -> bool:
    """射线法点在多边形内判断（含边界近似）。ring 首尾可闭合可不闭合。"""
    pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    x, y = pt[0], pt[1]
    inside = False
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i][0], pts[i][1]
        x2, y2 = pts[(i + 1) % n][0], pts[(i + 1) % n][1]
        if (y1 > y) != (y2 > y):
            xin = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < xin:
                inside = not inside
    return inside


def _ccw(a: list[float], b: list[float], c: list[float]) -> bool:
    return (c[1] - a[1]) * (b[0] - a[0]) > (b[1] - a[1]) * (c[0] - a[0])


def segments_intersect(p1: list[float], p2: list[float], p3: list[float], p4: list[float]) -> bool:
    """两线段是否相交（不含共线重叠的退化情形，围栏检测精度足够）。"""
    return _ccw(p1, p3, p4) != _ccw(p2, p3, p4) and _ccw(p1, p2, p3) != _ccw(p1, p2, p4)


def polyline_crosses_ring(line: list[list[float]], ring: list[list[float]]) -> bool:
    """折线是否穿越多边形：任一顶点在内，或任一段与多边形边相交。"""
    if any(point_in_ring(p, ring) for p in line):
        return True
    pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    n = len(pts)
    for i in range(1, len(line)):
        for j in range(n):
            if segments_intersect(line[i - 1], line[i], pts[j], pts[(j + 1) % n]):
                return True
    return False
