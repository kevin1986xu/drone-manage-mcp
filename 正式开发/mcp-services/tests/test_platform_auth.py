"""回源身份注入框架（关三，docs/07 §4.3）——纯本地，不连平台。

验证 DroneManageClient._auth_headers 的三态：
- 不配任何 → 空头（裸调，向后兼容当前不校验的平台）；
- 配服务账号 token → Authorization: Bearer；
- 配透传头名 + 上下文用户身份 → 带用户头（P1 dataScope 前提）。
"""

import pytest

from uav_mcp import config
from uav_mcp.drone_manage import (
    DroneManageClient,
    reset_platform_identity,
    set_platform_identity,
)


@pytest.fixture
def client():
    return DroneManageClient("http://platform.invalid")  # 不发请求，只测 _auth_headers


@pytest.fixture(autouse=True)
def _clean_config(monkeypatch):
    monkeypatch.setattr(config, "DRONE_PLATFORM_TOKEN", "")
    monkeypatch.setattr(config, "DRONE_USER_ID_HEADER", "")
    # .env 配了网关会走真实登录（2026-07-20 起），必须掐掉才是"纯本地"
    monkeypatch.setattr(config, "DRONE_GATEWAY_BASE", "")


def test_bare_when_unconfigured(client):
    assert client._auth_headers() == {}


def test_service_account_token(client, monkeypatch):
    monkeypatch.setattr(config, "DRONE_PLATFORM_TOKEN", "svc-token-123")
    assert client._auth_headers() == {"Authorization": "Bearer svc-token-123"}


def test_user_identity_passthrough(client, monkeypatch):
    monkeypatch.setattr(config, "DRONE_USER_ID_HEADER", "X-User-Id")
    tok = set_platform_identity("user-42")
    try:
        assert client._auth_headers() == {"X-User-Id": "user-42"}
    finally:
        reset_platform_identity(tok)


def test_no_user_header_without_config(client, monkeypatch):
    # 设了身份但没配头名 → 不透传（默认不启用）
    monkeypatch.setattr(config, "DRONE_USER_ID_HEADER", "")
    tok = set_platform_identity("user-42")
    try:
        assert client._auth_headers() == {}
    finally:
        reset_platform_identity(tok)


def test_token_and_user_combined(client, monkeypatch):
    monkeypatch.setattr(config, "DRONE_PLATFORM_TOKEN", "svc")
    monkeypatch.setattr(config, "DRONE_USER_ID_HEADER", "X-User-Id")
    tok = set_platform_identity("u1")
    try:
        h = client._auth_headers()
        assert h == {"Authorization": "Bearer svc", "X-User-Id": "u1"}
    finally:
        reset_platform_identity(tok)


def test_identity_isolated_after_reset(client, monkeypatch):
    monkeypatch.setattr(config, "DRONE_USER_ID_HEADER", "X-User-Id")
    tok = set_platform_identity("u1")
    reset_platform_identity(tok)
    assert client._auth_headers() == {}  # 复位后不残留
