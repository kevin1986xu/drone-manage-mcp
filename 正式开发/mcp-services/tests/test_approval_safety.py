"""人在环安全红线（本地审批模式，不依赖平台与审批服务）。

红线清单（与演示版对齐）：
1. 无 token 只登记待确认单，绝不执行
2. 伪造 token 拒绝
3. token 一次性（重放拒绝）
4. token 动作绑定（A 动作的 token 不能用于 B 动作）
5. 执行以确认单锁定参数为准
6. 过期确认单不可批准
"""

import time

import pytest

from uav_mcp import approval
from uav_mcp.state import STATE


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    # 强制本地审批模式
    monkeypatch.setattr("uav_mcp.config.APPROVAL_BASE", "")
    STATE.reset()
    yield
    STATE.reset()


def _pending(action="take_off", params=None):
    return approval.create_pending_action(
        action, params or {"drone_id": "D1", "route_id": "R-001"}, {"title": "t", "rows": []}
    )


def test_no_token_returns_none():
    assert approval.validate_and_consume("take_off", None) is None


def test_forged_token_rejected():
    _pending()
    assert approval.validate_and_consume("take_off", "forged-token-abcdefghijklmn") is None


def test_approve_then_consume_once_only():
    item = _pending()
    granted = approval.approve_local(item["action_id"])
    token = granted["confirm_token"]
    first = approval.validate_and_consume("take_off", token)
    assert first is not None and first["params"]["drone_id"] == "D1"
    # 重放
    assert approval.validate_and_consume("take_off", token) is None


def test_token_bound_to_action():
    item = _pending(action="take_off")
    granted = approval.approve_local(item["action_id"])
    assert approval.validate_and_consume("dispatch_drone", granted["confirm_token"]) is None


def test_double_approve_rejected():
    item = _pending()
    assert "confirm_token" in approval.approve_local(item["action_id"])
    again = approval.approve_local(item["action_id"])
    assert "error" in again


def test_expired_pending_cannot_approve():
    item = _pending()
    STATE.pending_actions[item["action_id"]]["expires_at"] = time.time() - 1
    assert "error" in approval.approve_local(item["action_id"])


def test_consume_returns_locked_params():
    item = _pending(params={"drone_id": "D-locked", "route_id": "R-009"})
    granted = approval.approve_local(item["action_id"])
    consumed = approval.validate_and_consume("take_off", granted["confirm_token"])
    assert consumed["params"] == {"drone_id": "D-locked", "route_id": "R-009"}


def test_refusal_shape():
    r = approval.refusal("take_off")
    assert r["status"] == "rejected" and "take_off" in r["reason"]
