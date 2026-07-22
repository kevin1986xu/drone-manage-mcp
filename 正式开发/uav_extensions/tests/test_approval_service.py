"""审批服务全流程（登记→批准→消费→重放/伪造/动作绑定拒绝）。"""

import httpx
import pytest

from uav_extensions import approval_service


@pytest.fixture()
def client():
    approval_service._pending.clear()
    approval_service._seq["n"] = 0
    transport = httpx.ASGITransport(app=approval_service.app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _register(c) -> str:
    r = await c.post("/api/approval/pending", json={
        "action": "take_off",
        "params": {"drone_id": "D1", "route_id": "R-001"},
        "summary": {"title": "起飞确认", "rows": []},
    })
    assert r.status_code == 200
    return r.json()["action_id"]


@pytest.mark.asyncio
async def test_full_flow(client):
    async with client as c:
        aid = await _register(c)
        # 列表可见
        lst = (await c.get("/api/approval/pending", params={"status": "pending"})).json()
        assert [i["action_id"] for i in lst] == [aid]
        # 批准 → 签发 token
        ok = await c.post(f"/api/approval/{aid}/approve")
        token = ok.json()["confirm_token"]
        assert ok.status_code == 200 and len(token) >= 24
        # 消费 → 返回锁定参数
        used = await c.post("/api/approval/consume", json={"action": "take_off", "confirm_token": token})
        assert used.status_code == 200 and used.json()["params"]["drone_id"] == "D1"
        # 重放拒绝
        replay = await c.post("/api/approval/consume", json={"action": "take_off", "confirm_token": token})
        assert replay.status_code == 403


@pytest.mark.asyncio
async def test_forged_token_403(client):
    async with client as c:
        await _register(c)
        r = await c.post("/api/approval/consume",
                         json={"action": "take_off", "confirm_token": "forged-token-abcdefghij"})
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_token_action_bound(client):
    async with client as c:
        aid = await _register(c)
        token = (await c.post(f"/api/approval/{aid}/approve")).json()["confirm_token"]
        r = await c.post("/api/approval/consume",
                         json={"action": "dispatch_drone", "confirm_token": token})
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_double_approve_409(client):
    async with client as c:
        aid = await _register(c)
        assert (await c.post(f"/api/approval/{aid}/approve")).status_code == 200
        assert (await c.post(f"/api/approval/{aid}/approve")).status_code == 409


@pytest.mark.asyncio
async def test_cancel_blocks_approve(client):
    async with client as c:
        aid = await _register(c)
        assert (await c.post(f"/api/approval/{aid}/cancel")).status_code == 200
        assert (await c.post(f"/api/approval/{aid}/approve")).status_code == 409


@pytest.mark.asyncio
async def test_admin_key_guard(client, monkeypatch):
    monkeypatch.setattr(approval_service, "ADMIN_KEY", "admin-secret")
    async with client as c:
        aid = await _register(c)  # 登记不受 admin key 限制（服务间调用）
        assert (await c.post(f"/api/approval/{aid}/approve")).status_code == 401
        ok = await c.post(f"/api/approval/{aid}/approve", headers={"X-Admin-Key": "admin-secret"})
        assert ok.status_code == 200


@pytest.mark.asyncio
async def test_identity_recorded_and_page_detail(client):
    """docs/09：发起人透传、审批人记录、卡片页详情含身份。"""
    async with client as c:
        r = await c.post("/api/approval/pending", json={
            "action": "take_off", "params": {"drone_id": "D1"},
            "summary": {"rows": []}, "initiated_by": "zhangsan"})
        body = r.json()
        aid, pt = body["action_id"], body["page_token"]
        page = (await c.get(f"/api/approval/{aid}/page", params={"t": pt})).json()
        assert page["initiated_by"] == "zhangsan" and page["confirmed_by"] is None
        # 审批人经 X-User-Id 记录
        ok = (await c.post(f"/api/approval/{aid}/approve", headers={"X-User-Id": "zhangsan"})).json()
        assert ok["confirmed_by"] == "zhangsan"


@pytest.mark.asyncio
async def test_four_eyes_blocks_self_approval(client, monkeypatch):
    """docs/09 四眼：配置的动作，发起人不得自批；第二人放行。"""
    monkeypatch.setattr(approval_service, "FOUR_EYES_ACTIONS", {"delete_zone"})
    async with client as c:
        r = await c.post("/api/approval/pending", json={
            "action": "delete_zone", "params": {"zone_id": "Z1"},
            "summary": {"rows": []}, "initiated_by": "zhangsan"})
        aid = r.json()["action_id"]
        # 自批 → 403
        self_ap = await c.post(f"/api/approval/{aid}/approve", headers={"X-User-Id": "zhangsan"})
        assert self_ap.status_code == 403
        # 缺审批人身份 → 403
        anon = await c.post(f"/api/approval/{aid}/approve")
        assert anon.status_code == 403
        # 第二人 → 放行
        second = await c.post(f"/api/approval/{aid}/approve", headers={"X-User-Id": "lisi"})
        assert second.status_code == 200 and second.json()["confirmed_by"] == "lisi"


@pytest.mark.asyncio
async def test_four_eyes_off_by_default_allows_self(client):
    """未配置四眼的动作，发起=审批同人放行（单人运维不挡）。"""
    async with client as c:
        r = await c.post("/api/approval/pending", json={
            "action": "take_off", "params": {}, "summary": {"rows": []},
            "initiated_by": "zhangsan"})
        aid = r.json()["action_id"]
        ok = await c.post(f"/api/approval/{aid}/approve", headers={"X-User-Id": "zhangsan"})
        assert ok.status_code == 200
