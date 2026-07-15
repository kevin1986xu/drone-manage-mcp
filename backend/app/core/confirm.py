"""人在环确认机制（安全红线）。

高危写操作（dispatch_drone / take_off / return_home …）Agent 只能生成
待确认动作；人工点击确认后签发一次性 confirm_token，工具携带有效
token 再次调用才真正执行。token 一次性、10 分钟有效。
"""

from __future__ import annotations

import secrets
import time
from typing import Any

from app.core.store import STORE

TOKEN_TTL_S = 600


def create_pending_action(action: str, params: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    action_id = STORE.next_id("ACT")
    item = {
        "action_id": action_id,
        "action": action,
        "params": params,
        "summary": summary,
        "status": "pending",  # pending -> approved -> consumed / cancelled / expired
        "token": None,
        "expires_at": time.time() + TOKEN_TTL_S,
        "created_at": time.time(),
    }
    STORE.pending_actions[action_id] = item
    return item


def approve(action_id: str) -> dict[str, Any]:
    item = STORE.pending_actions.get(action_id)
    if not item:
        return {"error": "确认单不存在"}
    if item["status"] != "pending":
        return {"error": f"确认单状态为 {item['status']}，不可确认"}
    if time.time() > item["expires_at"]:
        item["status"] = "expired"
        return {"error": "确认单已过期，请重新发起"}
    item["status"] = "approved"
    item["token"] = secrets.token_urlsafe(24)
    item["expires_at"] = time.time() + TOKEN_TTL_S
    return {"action_id": action_id, "action": item["action"], "confirm_token": item["token"], "params": item["params"]}


def cancel(action_id: str) -> dict[str, Any]:
    item = STORE.pending_actions.get(action_id)
    if not item:
        return {"error": "确认单不存在"}
    item["status"] = "cancelled"
    return {"action_id": action_id, "status": "cancelled"}


def validate_and_consume(action: str, confirm_token: str | None) -> dict[str, Any] | None:
    """校验并消费一次性 token。返回确认单；无效返回 None。"""
    if not confirm_token:
        return None
    for item in STORE.pending_actions.values():
        if (
            item["token"] == confirm_token
            and item["action"] == action
            and item["status"] == "approved"
            and time.time() <= item["expires_at"]
        ):
            item["status"] = "consumed"
            return item
    return None


def refusal(action: str) -> dict[str, Any]:
    return {
        "status": "rejected",
        "reason": f"{action} 为高危操作，confirm_token 缺失或无效。"
        "请先不带 token 调用以生成待确认单，由人工在界面上确认。",
    }
