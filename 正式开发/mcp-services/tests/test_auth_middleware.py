"""API key 中间件（纯 ASGI 层，不依赖平台）。"""

import httpx
import pytest

from uav_mcp.auth import ApiKeyMiddleware


async def _ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"tool-response"})


def _client(api_key: str) -> httpx.AsyncClient:
    app = ApiKeyMiddleware(_ok_app, api_key)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_missing_key_401():
    async with _client("secret-key") as c:
        assert (await c.post("/mcp")).status_code == 401


@pytest.mark.asyncio
async def test_wrong_key_401():
    async with _client("secret-key") as c:
        assert (await c.post("/mcp", headers={"X-API-Key": "wrong"})).status_code == 401


@pytest.mark.asyncio
async def test_correct_key_passes():
    async with _client("secret-key") as c:
        r = await c.post("/mcp", headers={"X-API-Key": "secret-key"})
        assert r.status_code == 200 and r.text == "tool-response"


@pytest.mark.asyncio
async def test_healthz_exempt():
    async with _client("secret-key") as c:
        assert (await c.get("/healthz")).status_code == 200


@pytest.mark.asyncio
async def test_no_key_configured_passes_with_warning():
    async with _client("") as c:
        assert (await c.post("/mcp")).status_code == 200
