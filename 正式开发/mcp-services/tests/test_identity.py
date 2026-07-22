"""用户身份链单测（docs/09 阶段1）——contextvar 注入 / 确认单发起人 / 回源透传。"""

from __future__ import annotations

import pytest

from uav_mcp import approval, config, identity
from uav_mcp.drone_manage import (
    DroneManageClient,
    reset_platform_identity,
    set_platform_identity,
)
from uav_mcp.state import STATE


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(config, "APPROVAL_BASE", "")  # 本地审批模式
    monkeypatch.setattr(config, "DRONE_GATEWAY_BASE", "")
    STATE.reset()
    tok = identity.set_identity({"tenant": "t", "user": None, "scopes": ["*"]})
    yield
    identity.reset_identity(tok)
    STATE.reset()


def test_pending_records_initiator_from_identity():
    tok = identity.set_identity({"tenant": "gov", "user": "zhangsan", "scopes": ["*"]})
    try:
        item = approval.create_pending_action("take_off", {"drone_id": "D1"}, {"rows": []})
        assert item["initiated_by"] == "zhangsan"
        assert item["confirmed_by"] is None
    finally:
        identity.reset_identity(tok)


def test_pending_initiator_none_when_anonymous():
    item = approval.create_pending_action("take_off", {"drone_id": "D1"}, {"rows": []})
    assert item["initiated_by"] is None


def test_回源_user_header_from_identity(monkeypatch):
    monkeypatch.setattr(config, "DRONE_USER_ID_HEADER", "X-User-Id")
    c = DroneManageClient("http://platform.invalid")
    tok = identity.set_identity({"tenant": "gov", "user": "lisi", "scopes": ["*"]})
    try:
        assert c._auth_headers().get("X-User-Id") == "lisi"
    finally:
        identity.reset_identity(tok)


def test_回源_explicit_identity_wins_over_request(monkeypatch):
    monkeypatch.setattr(config, "DRONE_USER_ID_HEADER", "X-User-Id")
    c = DroneManageClient("http://platform.invalid")
    ident_tok = identity.set_identity({"tenant": "gov", "user": "req-user", "scopes": ["*"]})
    plat_tok = set_platform_identity("explicit-user")
    try:
        assert c._auth_headers().get("X-User-Id") == "explicit-user"
    finally:
        reset_platform_identity(plat_tok)
        identity.reset_identity(ident_tok)


def test_回源_no_header_when_unconfigured():
    c = DroneManageClient("http://platform.invalid")
    tok = identity.set_identity({"tenant": "gov", "user": "lisi", "scopes": ["*"]})
    try:
        assert "X-User-Id" not in c._auth_headers()  # DRONE_USER_ID_HEADER 未配
    finally:
        identity.reset_identity(tok)
