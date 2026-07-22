"""服务端 API key 校验（无 Higress 架构下的工具面鉴权）。

两种模式（关一·接入鉴权，见 docs/07 §4.1）：
- 单密钥（向后兼容）：`UAV_MCP_API_KEY`，服务间信任，所有消费方同一把 key；
- 多租户：`UAV_TENANT_KEYS`（JSON），key → 租户元数据，可区分调用方并注入
  租户身份供审计/拦截器使用。两者可并存（单 key 作为兜底）。

消费方须带 `X-API-Key: <key>`。命中后把租户身份注入 ASGI scope（`uav_tenant`），
下游拦截器/审计据此标记。**工具级白名单不在此执行**——MCP streamable-http 的
tool 名在 JSON-RPC body 里，中间件看不到；工具白名单是网关（Higress）职责，
这里只做认证 + 租户注入（docs/07 §4.1）。

未配置任何 key 时放行并告警（仅限本机开发）。/healthz 免鉴权。
"""

from __future__ import annotations

import hmac
import json
import logging
from typing import Any

from uav_mcp import identity

logger = logging.getLogger(__name__)

_ANON = {"tenant": "anonymous", "scopes": ["*"]}


class ApiKeyMiddleware:
    def __init__(self, app, api_key: str = "", tenant_keys: dict[str, dict[str, Any]] | None = None) -> None:
        self.app = app
        self.api_key = api_key or ""
        # key → {tenant, scopes, ...}；单 key 也并入表，统一走多租户查表逻辑
        self.tenants: dict[str, dict[str, Any]] = dict(tenant_keys or {})
        if self.api_key and self.api_key not in self.tenants:
            self.tenants[self.api_key] = {"tenant": "default", "scopes": ["*"]}
        if not self.tenants:
            logger.warning("未配置 UAV_MCP_API_KEY / UAV_TENANT_KEYS——工具面鉴权关闭，仅限本机开发！")
        else:
            logger.info("工具面鉴权已启用：%d 个租户 key", len(self.tenants))

    def _match(self, provided: str) -> dict[str, Any] | None:
        """常数时间比对所有已知 key，命中返回租户元数据。

        遍历全部 key 而非命中即返回，避免因比较次数泄露 key 存在性（时序侧信道）。
        """
        matched: dict[str, Any] | None = None
        for key, meta in self.tenants.items():
            if hmac.compare_digest(provided, key):
                matched = meta
        return matched

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope.get("path", "").rstrip("/") == "/healthz":
            await self._respond(send, 200, {"status": "ok"})
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        if self.tenants:
            provided = headers.get("x-api-key", "")
            meta = self._match(provided) if provided else None
            if meta is None:
                logger.warning("API key 校验失败：path=%s client=%s", scope.get("path"), scope.get("client"))
                await self._respond(send, 401, {"error": "invalid or missing X-API-Key"})
                return
            scope["uav_tenant"] = meta  # 下游拦截器/审计取用
        else:
            scope["uav_tenant"] = _ANON
        # 用户身份（docs/09 阶段1）：X-User-Id 声明的用户注入请求级 contextvar，
        # 供确认单 initiated_by / 回源 dataScope 透传 / 审计追责到人。
        user = (headers.get("x-user-id") or "").strip() or None
        base = scope["uav_tenant"]
        ident = {"tenant": base.get("tenant"), "user": user,
                 "scopes": base.get("scopes", ["*"])}
        scope["uav_identity"] = ident
        token = identity.set_identity(ident)
        try:
            await self.app(scope, receive, send)
        finally:
            identity.reset_identity(token)

    @staticmethod
    async def _respond(send, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())],
        })
        await send({"type": "http.response.body", "body": payload})
