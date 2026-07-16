"""高危审批服务（独立进程）——confirm_token 的唯一签发方。

原则：**token 签发在 Agent 之外**。Agent（经 MCP 工具）只能登记待确认单；
批准动作来自人：GIS 前端确认卡片、企微/钉钉交互卡片按钮的回调，都打到
本服务的 /approve。工具执行前回到本服务 /consume 校验并消费（一次性、
动作绑定、TTL 10 分钟）。

对接方：
  mcp-services（uav_mcp.approval 客户端）：POST /pending、POST /consume
  GIS 前端 / IM 卡片回调：GET /pending 列表、POST /{id}/approve、/{id}/cancel
  审批后向 DeerFlow thread 投递 [SYSTEM_CONFIRMATION] 由 BFF/IM 桥完成（M3/M4）。

运行：python -m uav_extensions.approval_service   # 默认 0.0.0.0:8205
环境：APPROVAL_ADMIN_KEY —— 配置后 /approve、/cancel、GET /pending
      需带 X-Admin-Key（防止旁路直批；/pending 登记与 /consume 供服务间调用，
      走内网+服务 API key 边界）。
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from typing import Any

import uvicorn
from fastapi import Body, FastAPI, Header, HTTPException

logger = logging.getLogger(__name__)

TOKEN_TTL_S = 600
ADMIN_KEY = os.getenv("APPROVAL_ADMIN_KEY", "").strip()

app = FastAPI(title="UAV 高危审批服务", version="0.1.0")

# action_id -> item；单实例内存态（重启即失效——待确认单本就短时）
_pending: dict[str, dict[str, Any]] = {}
_seq = {"n": 0}


def _next_id() -> str:
    _seq["n"] += 1
    return f"ACT-{_seq['n']:04d}"


def _check_admin(x_admin_key: str | None) -> None:
    if ADMIN_KEY and (x_admin_key or "") != ADMIN_KEY:
        raise HTTPException(401, "invalid or missing X-Admin-Key")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/approval/pending")
def create_pending(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """登记待确认单（由 MCP 工具调用；不签发任何 token）。"""
    for field in ("action", "params", "summary"):
        if field not in body:
            raise HTTPException(422, f"缺少字段 {field}")
    item = {
        "action_id": _next_id(),
        "action": body["action"],
        "params": body["params"],
        "summary": body["summary"],
        "status": "pending",  # pending -> approved -> consumed / cancelled / expired
        "token": None,
        "expires_at": time.time() + TOKEN_TTL_S,
        "created_at": time.time(),
    }
    _pending[item["action_id"]] = item
    logger.info("待确认单登记：%s %s", item["action_id"], item["action"])
    return {k: item[k] for k in ("action_id", "action", "summary", "status", "expires_at")}


@app.get("/api/approval/pending")
def list_pending(status: str | None = None,
                 x_admin_key: str | None = Header(default=None)) -> list[dict[str, Any]]:
    """确认单列表（GIS 前端/IM 桥轮询用；不含 token）。"""
    _check_admin(x_admin_key)
    items = [
        {k: v[k] for k in ("action_id", "action", "summary", "status", "created_at", "expires_at")}
        for v in _pending.values()
    ]
    if status:
        items = [i for i in items if i["status"] == status]
    return sorted(items, key=lambda i: i["created_at"], reverse=True)


@app.post("/api/approval/{action_id}/approve")
def approve(action_id: str, x_admin_key: str | None = Header(default=None)) -> dict[str, Any]:
    """人工批准 → 签发一次性 confirm_token（唯一签发点）。"""
    _check_admin(x_admin_key)
    item = _pending.get(action_id)
    if not item:
        raise HTTPException(404, "确认单不存在")
    if item["status"] != "pending":
        raise HTTPException(409, f"确认单状态为 {item['status']}，不可确认")
    if time.time() > item["expires_at"]:
        item["status"] = "expired"
        raise HTTPException(410, "确认单已过期，请重新发起")
    item["status"] = "approved"
    item["token"] = secrets.token_urlsafe(24)
    item["expires_at"] = time.time() + TOKEN_TTL_S
    logger.info("确认单批准：%s %s", action_id, item["action"])
    return {"action_id": action_id, "action": item["action"],
            "confirm_token": item["token"], "params": item["params"]}


@app.post("/api/approval/{action_id}/cancel")
def cancel(action_id: str, x_admin_key: str | None = Header(default=None)) -> dict[str, Any]:
    _check_admin(x_admin_key)
    item = _pending.get(action_id)
    if not item:
        raise HTTPException(404, "确认单不存在")
    item["status"] = "cancelled"
    item["token"] = None
    return {"action_id": action_id, "status": "cancelled"}


@app.post("/api/approval/consume")
def consume(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """校验并消费一次性 token（由 MCP 工具执行前调用）。

    成功返回确认单（含锁定参数）；无效一律 403（不区分原因，防探测）。
    """
    action = body.get("action")
    token = body.get("confirm_token")
    if not action or not token:
        raise HTTPException(403, "confirm_token 无效")
    for item in _pending.values():
        if (
            item["token"] == token
            and item["action"] == action
            and item["status"] == "approved"
            and time.time() <= item["expires_at"]
        ):
            item["status"] = "consumed"
            logger.info("确认单消费：%s %s", item["action_id"], action)
            return {k: item[k] for k in ("action_id", "action", "params", "summary", "status")}
    logger.warning("token 校验失败：action=%s", action)
    raise HTTPException(403, "confirm_token 无效")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    uvicorn.run(app, host=os.getenv("APPROVAL_HOST", "0.0.0.0"),
                port=int(os.getenv("APPROVAL_PORT", "8205")), log_level="info")
