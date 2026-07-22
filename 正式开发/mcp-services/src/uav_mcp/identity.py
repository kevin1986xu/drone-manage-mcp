"""调用方身份（docs/09 阶段1 声明式）——请求级 contextvar。

ApiKeyMiddleware 从 `X-User-Id` 头取用户、连同租户写入本 contextvar；
下游工具、审批客户端（initiated_by）、回源客户端（X-User-Id 透传）读取。

阶段1 身份是"声明"的（网关/BFF 说你是谁就是谁），安全性依赖 820x 收口 +
内网信任边界；阶段2 换 OAuth JWT 后身份可验签。接口不变，换来源即可。
"""

from __future__ import annotations

import contextvars
from typing import Any

# {"tenant": str, "user": str | None, "scopes": list[str]}
_current: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "uav_identity", default={"tenant": "anonymous", "user": None, "scopes": ["*"]}
)


def set_identity(meta: dict[str, Any]) -> contextvars.Token:
    return _current.set(meta)


def reset_identity(token: contextvars.Token) -> None:
    _current.reset(token)


def current() -> dict[str, Any]:
    return _current.get()


def current_user() -> str | None:
    return _current.get().get("user")
