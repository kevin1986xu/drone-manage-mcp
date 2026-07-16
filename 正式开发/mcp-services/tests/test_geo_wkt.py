"""几何与 WKT 解析（不依赖平台）。"""

from uav_mcp import geo
from uav_mcp.drone_manage import wkt_polygon_to_geojson

SQUARE = [[113.0, 30.0], [113.001, 30.0], [113.001, 30.001], [113.0, 30.001], [113.0, 30.0]]


def test_polygon_area_roughly_correct():
    # ~96m x ~111m ≈ 10700 m²
    area = geo.polygon_area_m2(SQUARE)
    assert 9000 < area < 12000


def test_dist_m_symmetry():
    a, b = [113.0, 30.0], [113.01, 30.01]
    assert abs(geo.dist_m(a, b) - geo.dist_m(b, a)) < 1e-6


def test_wkt_polygon_z():
    wkt = "POLYGON Z ((113.0 30.0 5, 113.001 30.0 5, 113.001 30.001 5, 113.0 30.0 5))"
    g = wkt_polygon_to_geojson(wkt)
    assert g and g["type"] == "Polygon"
    ring = g["coordinates"][0]
    assert ring[0] == ring[-1]  # 闭环
    assert len(ring) >= 4


def test_wkt_multipolygon_takes_largest_ring():
    big = "113.0 30.0, 113.01 30.0, 113.01 30.01, 113.0 30.01, 113.0 30.0"
    small = "114.0 31.0, 114.0001 31.0, 114.0001 31.0001, 114.0 31.0001, 114.0 31.0"
    wkt = f"MULTIPOLYGON ((({big})), (({small})))"
    g = wkt_polygon_to_geojson(wkt)
    assert g["coordinates"][0][0] == [113.0, 30.0]  # 主面 = 大环


def test_wkt_garbage_returns_none():
    assert wkt_polygon_to_geojson("") is None
    assert wkt_polygon_to_geojson("POINT (1 2)") is None
