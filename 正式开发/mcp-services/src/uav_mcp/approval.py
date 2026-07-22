"""人在环审批客户端（安全红线）。

原则：**confirm_token 的签发在 Agent 之外**。高危写操作（dispatch_drone /
take_off / create_task_plan）无有效 token 时只登记待确认单并自拒；人工在
GIS 卡片 / 企微钉钉交互卡片上点确认 → 审批服务签发一次性 token → 工具携
token 再调用才真正执行。token 一次性、动作绑定、10 分钟有效。

两种模式：
- 远程（配置 APPROVAL_BASE，生产必用）：确认单登记/消费全部走独立审批
  服务（uav_extensions.approval_service），签发彻底不在本进程；
- 本地（未配置，仅开发）：进程内内存实现，语义与远程一致。
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import Any

import httpx

from uav_mcp import config
from uav_mcp.state import STATE

logger = logging.getLogger(__name__)

TOKEN_TTL_S = 600


def create_pending_action(action: str, params: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    """登记待确认单，返回 {action_id, ...}。远程失败时**拒绝降级**——
    审批链路不可用即高危操作不可用（安全红线优先于可用性）。"""
    if config.APPROVAL_BASE:
        resp = httpx.post(
            f"{config.APPROVAL_BASE}/api/approval/pending",
            json={"action": action, "params": params, "summary": summary},
            timeout=8,
        )
        resp.raise_for_status()
        item = resp.json()
        # docs/08 通用前端：确认卡片页地址（page_token 即查看/确认能力）。
        # UI 服务未部署则无此字段，各宿主照旧走卡片组件。
        if config.UAV_UI_BASE and item.get("page_token"):
            item["view_url"] = (f"{config.UAV_UI_BASE}/ui/approval/"
                                f"{item['action_id']}?t={item.pop('page_token')}")
        else:
            item.pop("page_token", None)
        return item
    item = {
        "action_id": STATE.next_id("ACT"),
        "action": action,
        "params": params,
        "summary": summary,
        "status": "pending",  # pending -> approved -> consumed / cancelled / expired
        "token": None,
        "expires_at": time.time() + TOKEN_TTL_S,
        "created_at": time.time(),
    }
    STATE.pending_actions[item["action_id"]] = item
    return item


def validate_and_consume(action: str, confirm_token: str | None) -> dict[str, Any] | None:
    """校验并消费一次性 token。返回确认单（含锁定参数）；无效返回 None。"""
    if not confirm_token:
        return None
    if config.APPROVAL_BASE:
        try:
            resp = httpx.post(
                f"{config.APPROVAL_BASE}/api/approval/consume",
                json={"action": action, "confirm_token": confirm_token},
                timeout=8,
            )
            if resp.status_code == 200:
                return resp.json()
            return None
        except Exception as exc:  # noqa: BLE001 —— 审批服务不可达 = 拒绝执行
            logger.error("审批服务不可达，高危操作拒绝执行：%s", exc)
            return None
    for item in STATE.pending_actions.values():
        if (
            item["token"] == confirm_token
            and item["action"] == action
            and item["status"] == "approved"
            and time.time() <= item["expires_at"]
        ):
            item["status"] = "consumed"
            return item
    return None


def approve_local(action_id: str) -> dict[str, Any]:
    """本地模式的人工确认入口（开发/测试用；生产走审批服务 REST）。"""
    item = STATE.pending_actions.get(action_id)
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


def refusal(action: str) -> dict[str, Any]:
    return {
        "status": "rejected",
        "reason": f"{action} 为高危操作，confirm_token 缺失或无效。"
        "请先不带 token 调用以生成待确认单，由人工在界面上确认。",
    }
