"""拦截器（guard/audit）——按 DeerFlow 调用形态：await interceptor(request, handler)。"""

import json
from types import SimpleNamespace

import pytest

from uav_extensions import interceptors


def _req(name: str, args: dict, server: str = "uav-flight-task-mcp"):
    return SimpleNamespace(name=name, args=args, server_name=server, runtime=None, headers=None)


async def _ok_handler(request):
    return {"ok": True, "tool": request.name}


@pytest.mark.asyncio
async def test_guard_passes_normal_tool():
    guard = interceptors.build_uav_guard()
    assert (await guard(_req("query_plots", {}), _ok_handler))["ok"]


@pytest.mark.asyncio
async def test_guard_passes_dangerous_without_token():
    """无 token 是登记待确认单的合法第一阶段，必须放行（由工具自拒）。"""
    guard = interceptors.build_uav_guard()
    r = await guard(_req("take_off", {"drone_id": "D1", "route_id": "R-1"}), _ok_handler)
    assert r["ok"]


@pytest.mark.asyncio
async def test_guard_blocks_forged_token_shape():
    guard = interceptors.build_uav_guard()
    r = await guard(_req("take_off", {"confirm_token": "假token!"}), _ok_handler)
    assert r.isError
    payload = json.loads(r.content[0].text)
    assert payload["status"] == "rejected"


@pytest.mark.asyncio
async def test_guard_passes_wellformed_token():
    """形态合法的 token 放行——真伪由审批服务/工具校验（纵深防御分层）。"""
    guard = interceptors.build_uav_guard()
    r = await guard(_req("take_off", {"confirm_token": "A" * 32}), _ok_handler)
    assert r["ok"]


@pytest.mark.asyncio
async def test_audit_writes_and_masks_token(tmp_path, monkeypatch):
    monkeypatch.setattr(interceptors, "_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    audit = interceptors.build_uav_audit()
    await audit(_req("take_off", {"confirm_token": "secret-token-value-123456"}), _ok_handler)
    lines = (tmp_path / "audit.jsonl").read_text().strip().splitlines()
    rec = json.loads(lines[-1])
    assert rec["tool"] == "take_off" and rec["dangerous"]
    assert rec["args"]["confirm_token"] == "***"


@pytest.mark.asyncio
async def test_audit_skips_non_uav_servers(tmp_path, monkeypatch):
    monkeypatch.setattr(interceptors, "_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    audit = interceptors.build_uav_audit()
    await audit(_req("some_tool", {}, server="github-mcp"), _ok_handler)
    assert not (tmp_path / "audit.jsonl").exists()
