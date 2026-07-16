"""服务端 API key 校验（无 Higress 架构下的工具面鉴权）。

消费方（DeerFlow extensions_config 的 headers、curl 调试等）须带
`X-API-Key: $UAV_MCP_API_KEY`。未配置 key 时放行并打告警（仅限本机开发）。
/healthz 免鉴权（存活探测）。
"""

from __future__ import annotations

import hmac
import json
import logging

logger = logging.getLogger(__name__)


class ApiKeyMiddleware:
    def __init__(self, app, api_key: str) -> None:
        self.app = app
        self.api_key = api_key
        if not api_key:
            logger.warning("UAV_MCP_API_KEY 未配置——工具面鉴权关闭，仅限本机开发！")

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope.get("path", "").rstrip("/") == "/healthz":
            await self._respond(send, 200, {"status": "ok"})
            return
        if self.api_key:
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
            provided = headers.get("x-api-key", "")
            if not hmac.compare_digest(provided, self.api_key):
                logger.warning("API key 校验失败：path=%s client=%s", scope.get("path"), scope.get("client"))
                await self._respond(send, 401, {"error": "invalid or missing X-API-Key"})
                return
        await self.app(scope, receive, send)

    @staticmethod
    async def _respond(send, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())],
        })
        await send({"type": "http.response.body", "body": payload})
