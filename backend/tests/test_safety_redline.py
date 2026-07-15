"""安全红线测试（《开发计划》§六，不可妥协项）：

无 confirm_token / 伪造 token / 重放 token 时，take_off 与 dispatch_drone
一律不得执行。
"""

import pytest

from app.agent.tools import ALL_TOOLS
from app.core import confirm
from app.core.store import STORE


@pytest.fixture(autouse=True)
def fresh_world():
    STORE.reset()
    yield


def _tool(name: str):
    return next(t for t in ALL_TOOLS if t.name == name)


def _route_id():
    return _tool("generate_route").func(drone_id="D-12", plot_ids=["GM-04"])["route_id"]


def test_take_off_without_token_never_flies():
    rid = _route_id()
    r = _tool("take_off").func(drone_id="D-12", route_id=rid)
    assert r["status"] == "requires_confirmation"
    assert STORE.drones["D-12"]["status"] == "idle", "无确认不得改变无人机状态"
    assert not STORE.flight_tasks, "无确认不得创建飞行任务"


def test_take_off_with_forged_token_rejected():
    rid = _route_id()
    r = _tool("take_off").func(drone_id="D-12", route_id=rid, confirm_token="forged-token-123")
    assert r["status"] == "rejected"
    assert not STORE.flight_tasks


def test_confirm_token_is_single_use():
    rid = _route_id()
    pending = _tool("take_off").func(drone_id="D-12", route_id=rid)
    token = confirm.approve(pending["action_id"])["confirm_token"]
    first = _tool("take_off").func(drone_id="D-12", route_id=rid, confirm_token=token)
    assert first["status"] == "airborne"
    replay = _tool("take_off").func(drone_id="D-12", route_id=rid, confirm_token=token)
    assert replay["status"] == "rejected", "token 重放必须被拒绝"
    assert len(STORE.flight_tasks) == 1


def test_token_bound_to_action_type():
    rid = _route_id()
    pending = _tool("dispatch_drone").func(drone_id="D-12", task_type="图斑核查", plot_ids=["GM-04"])
    token = confirm.approve(pending["action_id"])["confirm_token"]
    # dispatch 的 token 不能用于 take_off
    r = _tool("take_off").func(drone_id="D-12", route_id=rid, confirm_token=token)
    assert r["status"] == "rejected"


def test_executes_confirmed_params_not_request_params():
    """确认后执行的是确认单锁定的参数，不受二次调用传参影响。"""
    rid = _route_id()
    pending = _tool("take_off").func(drone_id="D-12", route_id=rid)
    token = confirm.approve(pending["action_id"])["confirm_token"]
    done = _tool("take_off").func(drone_id="D-07", route_id=rid, confirm_token=token)
    assert done["status"] == "airborne"
    assert done["drone_id"] == "D-12", "必须执行确认单中的无人机，而非二次传入的"


def test_cancelled_action_cannot_be_approved():
    rid = _route_id()
    pending = _tool("take_off").func(drone_id="D-12", route_id=rid)
    confirm.cancel(pending["action_id"])
    r = confirm.approve(pending["action_id"])
    assert "error" in r
