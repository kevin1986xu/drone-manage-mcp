"""P0 全部 15 个工具的契约测试：参数 schema 校验 + 返回结构断言。

对应《开发计划》§三：每个 MCP tool 做自动化契约测试。
"""

import pytest

from app.agent.tools import ALL_TOOLS
from app.core import confirm
from app.core import routes as routes_core
from app.core.store import STORE


@pytest.fixture(autouse=True)
def fresh_world():
    STORE.reset()
    yield


def _tool(name: str):
    return next(t for t in ALL_TOOLS if t.name == name)


def _route():
    """演示主线的标准航线：D-12 覆盖 GM-04（multi_cover 自动合并）。"""
    return _tool("generate_route").func(drone_id="D-12", plot_ids=["GM-04"])


# ── 工具集完整性 ─────────────────────────────────────────────


def test_p0_tool_inventory():
    names = {t.name for t in ALL_TOOLS}
    assert names == {
        "query_plots", "find_nearby_drones", "get_drone_status", "dispatch_drone",
        "generate_route", "get_route_detail", "explain_route", "open_route_editor",
        "check_weather", "check_battery", "check_route_obstacle", "check_drone_obstacle",
        "check_airspace", "preflight_check", "take_off",
    }
    assert len(ALL_TOOLS) == 15


def test_every_tool_has_schema_and_description():
    for t in ALL_TOOLS:
        assert t.description, f"{t.name} 缺少 description"
        schema = t.args_schema.model_json_schema() if hasattr(t.args_schema, "model_json_schema") else t.args_schema
        assert schema.get("properties") is not None, f"{t.name} 缺少参数 schema"


def test_tool_rejects_bad_args():
    with pytest.raises(Exception):
        _tool("get_drone_status").invoke({})  # 缺必填参数
    with pytest.raises(Exception):
        _tool("generate_route").invoke({"plot_ids": ["GM-04"]})  # 缺必填 drone_id


def test_tool_accepts_stringified_list():
    # 模型偶发把数组参数传成字符串（qwen 常见）；工具边界应归一化而非报错重试
    r = _tool("query_plots").invoke({"plot_ids": '["GM-03"]'})  # JSON 字符串形式
    assert r["count"] == 1
    r2 = _tool("query_plots").invoke({"plot_ids": "GM-03"})  # 裸串形式
    assert r2["count"] == 1


# ── drone-dispatch 域 ────────────────────────────────────────


def test_query_plots_contract():
    # LLM 工具层：瘦身返回（无 geometry，省 token），保留推理必需字段
    r = _tool("query_plots").func(region="光明区")
    assert r["count"] == 5 and r["batch_no"] == "SZ-2607"
    p = r["plots"][0]
    for key in ("plot_id", "plot_type", "priority", "area_mu", "issued_at", "centroid"):
        assert key in p
    assert "geometry" not in p, "LLM 层应剔除大体积 geometry"
    # 过滤参数
    assert _tool("query_plots").func(plot_ids=["GM-03"])["count"] == 1
    assert _tool("query_plots").func(region="南山区")["count"] == 0


def test_query_plots_core_has_geometry():
    # 业务原子层/前端数据源：保留完整 GeoJSON 边界
    from app.core import plots as plots_core

    p = plots_core.query_plots(region="光明区")["plots"][0]
    assert p["geometry"]["type"] == "Polygon"
    ring = p["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1], "GeoJSON 外环必须闭合"


def test_find_nearby_drones_contract():
    r = _tool("find_nearby_drones").func(plot_id="GM-04", radius_km=5)
    assert r["count"] >= 1
    d = r["drones"][0]
    for key in ("drone_id", "model", "battery_pct", "payload", "status", "distance_km", "location"):
        assert key in d
    dists = [x["distance_km"] for x in r["drones"]]
    assert dists == sorted(dists), "必须按距离升序"


def test_get_drone_status_contract():
    r = _tool("get_drone_status").func(drone_id="d-12")  # 大小写不敏感
    assert r["drone_id"] == "D-12" and "firmware" in r and "obstacle_avoidance" in r
    assert "error" in _tool("get_drone_status").func(drone_id="D-99")


# ── route-planning 域 ────────────────────────────────────────


def test_generate_route_multi_cover_merges_same_heading_band():
    r = _route()
    assert r["route_id"].startswith("R-") and r["version"] == 1
    covered = {c["plot_id"] for c in r["covered_plots"]}
    assert "GM-04" in covered
    assert len(covered) >= 2, "multi_cover 应合并同航向带图斑"
    assert "geometry" not in r and "waypoints" not in r, "LLM 层应剔除航点/几何"
    assert r["length_km"] > 0 and r["duration_min"] > 0
    # 时长必须在续航预算内（含预留）
    assert r["duration_min"] <= 28 * 0.87
    # 完整几何在业务原子层可取
    detail = routes_core.get_route_detail(r["route_id"])
    assert detail["geometry"]["type"] == "LineString" and len(detail["waypoints"]) > 0


def test_generate_route_single_strategy():
    r = _tool("generate_route").func(drone_id="D-12", plot_ids=["GM-04"], strategy="single")
    assert [c["plot_id"] for c in r["covered_plots"]] == ["GM-04"]


def test_explain_route_contract():
    route = _route()
    r = _tool("explain_route").func(route_id=route["route_id"])
    d = r["decision"]
    for key in ("covered_plots", "merge_reason", "merged_candidates", "rejected_candidates",
                "avoided_features", "baseline_comparison"):
        assert key in d
    bc = d["baseline_comparison"]
    assert bc["separate_total_min"] > bc["merged_total_min"], "合并方案必须优于逐个单飞"
    assert bc["saved_min"] == bc["separate_total_min"] - bc["merged_total_min"]
    for c in d["covered_plots"]:
        assert 0 < c["coverage_rate"] <= 100
    # 每个合并决策都有量化依据（不允许 LLM 编造的空口理由）
    for m in d["merged_candidates"]:
        assert m["marginal_km"] < m["separate_sortie_km"]


def test_get_route_detail_diff_after_edit():
    route = _route()
    rid = route["route_id"]
    ed = _tool("open_route_editor").func(route_id=rid)
    token = ed["url"].split("token=")[1]
    # 航点从业务原子层取（LLM 工具层已瘦身、无 waypoints）
    wps = [{"lon": w["lon"], "lat": w["lat"]} for w in routes_core.get_route_detail(rid)["waypoints"]]
    wps[2] = {"lon": wps[2]["lon"] - 0.0012, "lat": wps[2]["lat"]}  # 3 号航点西移约 120m
    assert routes_core.validate_editor_token(rid, token)
    updated = routes_core.update_waypoints(rid, wps)
    assert updated["version"] == 2
    diff = updated["diff_vs_prev"]
    assert diff["prev_version"] == 1
    assert any(m["seq"] == 3 and 100 <= m["moved_m"] <= 140 for m in diff["moved_waypoints"])


def test_open_route_editor_token():
    route = _route()
    r = _tool("open_route_editor").func(route_id=route["route_id"])
    assert "token=" in r["url"] and r["token_ttl_min"] == 10
    from app.core import routes as routes_core

    assert not routes_core.validate_editor_token(route["route_id"], "forged-token")


# ── preflight 域 ─────────────────────────────────────────────


def test_five_checks_and_aggregate():
    route = _route()
    rid = route["route_id"]
    singles = [
        _tool("check_weather").func(),
        _tool("check_battery").func(drone_id="D-12", route_id=rid),
        _tool("check_route_obstacle").func(route_id=rid),
        _tool("check_drone_obstacle").func(drone_id="D-12"),
        _tool("check_airspace").func(route_id=rid),
    ]
    for c in singles:
        assert c["status"] in {"pass", "warn", "fail"} and c["item"] and c["detail"]
    agg = _tool("preflight_check").func(drone_id="D-12", route_id=rid)
    assert len(agg["checks"]) == 5
    assert agg["overall"] == "warn"  # 空域许可为"注意"项
    assert agg["conclusion"]


def test_check_battery_margin_math():
    route = _route()
    c = _tool("check_battery").func(drone_id="D-12", route_id=route["route_id"])
    d = c["data"]
    assert d["margin_min"] == d["endurance_min"] - d["task_min"]
    assert c["status"] in {"pass", "warn"}


# ── 人在环链路 ───────────────────────────────────────────────


def test_dispatch_confirm_loop():
    r = _tool("dispatch_drone").func(drone_id="D-12", task_type="图斑核查", plot_ids=["GM-04"])
    assert r["status"] == "requires_confirmation" and r["action_id"]
    approved = confirm.approve(r["action_id"])
    done = _tool("dispatch_drone").func(
        drone_id="D-12", task_type="图斑核查", plot_ids=["GM-04"], confirm_token=approved["confirm_token"]
    )
    assert done["status"] == "locked" and done["order_id"].startswith("DSP-")
    assert STORE.drones["D-12"]["status"] == "dispatched"


def test_take_off_confirm_loop_and_task():
    route = _route()
    r = _tool("take_off").func(drone_id="D-12", route_id=route["route_id"])
    assert r["status"] == "requires_confirmation"
    approved = confirm.approve(r["action_id"])
    done = _tool("take_off").func(
        drone_id="D-12", route_id=route["route_id"], confirm_token=approved["confirm_token"]
    )
    assert done["status"] == "airborne" and done["flight_task_id"].startswith("T-")
    from app.core import tasks as tasks_core

    st = tasks_core.get_task_status(done["flight_task_id"])
    assert st["status"] in {"flying", "completed"} and 0 <= st["progress_pct"] <= 100
