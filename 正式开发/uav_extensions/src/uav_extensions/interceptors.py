"""DeerFlow mcpInterceptors 注入点：高危工具硬白名单 + 调用审计。

接入方式（extensions_config.json，接口已对 DeerFlow 2.0 源码核实——
builder 无参调用、返回 async (request, next_handler)，request 为
langchain_mcp_adapters.interceptors.MCPToolCallRequest）：

    "mcpInterceptors": [
        "uav_extensions.interceptors:build_uav_guard",
        "uav_extensions.interceptors:build_uav_audit"
    ]

纵深防御定位：token 的真正校验在工具内/审批服务（框架无关，不可绕）；
拦截器是**客户端侧提前拦截**——伪造 token 不出 DeerFlow 就被打回，
省一次网络往返并留下审计痕迹，同时防提示注入诱导的异常调用形态。

注意：服务鉴权（X-API-Key）不在拦截器做——streamable-http 的连接头
由 extensions_config 各 server 的 `headers` 字段静态下发。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 高危工具（与 mcp-services 的人在环工具一一对应；2026-07-20 随四新域扩充）
DANGEROUS_TOOLS = {
    "take_off", "dispatch_drone", "create_task_plan",
    # airspace
    "create_zone", "delete_zone",
    # media
    "start_3d_modeling",
    # task-schedule
    "create_scheduled_task", "create_recurring_task", "cancel_scheduled_task",
    "reschedule_task", "retry_failed_task", "resume_from_breakpoint",
    # flight-control（2026-07-21 P1；return_home/emergency_stop 是紧急白名单⚡故不在此）
    "pause_task", "resume_task", "fly_to_point", "takeoff_to_point",
    "speaker_tts", "set_height_limit",
    # dock-debug（P1）
    "debug_mode", "dock_cover", "dock_putter", "drone_power",
    "charge_control", "device_reboot", "battery_maintenance",
}
# 审批服务签发的 token 形态：secrets.token_urlsafe(24) → 32 位 url-safe
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{24,64}$")

_AUDIT_LOG = os.getenv("UAV_AUDIT_LOG", str(Path.home() / ".uav-agent" / "tool-audit.jsonl"))


def _deny(reason: str) -> Any:
    """短路拒绝：返回 isError 的 CallToolResult（无需触达 MCP 服务）。"""
    from mcp.types import CallToolResult, TextContent

    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(
            {"status": "rejected", "reason": reason}, ensure_ascii=False))],
        isError=True,
    )


def build_uav_guard():
    """高危工具硬白名单：
    - confirm_token 缺省 → 放行（这是登记待确认单的合法第一阶段，工具自拒）
    - confirm_token 存在但形态非法（模型伪造）→ 客户端侧直接短路拒绝
    """

    async def guard(request, handler):
        if request.name in DANGEROUS_TOOLS:
            token = (request.args or {}).get("confirm_token")
            if token is not None and not _TOKEN_RE.match(str(token)):
                logger.warning("拦截伪造 confirm_token：tool=%s server=%s", request.name, request.server_name)
                _audit_write({"event": "forged_token_blocked", "tool": request.name,
                              "server": request.server_name, "ts": time.time()})
                return _deny(
                    f"{request.name} 的 confirm_token 形态非法（疑似模型自行构造）。"
                    "请不带 token 调用以生成待确认单，由人工在界面上确认。"
                )
        return await handler(request)

    return guard


def build_uav_audit():
    """无人机域工具调用审计：JSONL 落盘（工具名/服务/参数摘要/耗时/成败）。"""

    async def audit(request, handler):
        if not str(request.server_name or "").startswith("uav-"):
            return await handler(request)
        t0 = time.time()
        error = None
        try:
            result = await handler(request)
            return result
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            raise
        finally:
            args = dict(request.args or {})
            if "confirm_token" in args and args["confirm_token"]:
                args["confirm_token"] = "***"  # token 不落日志
            _audit_write({
                "event": "tool_call",
                "tool": request.name,
                "server": request.server_name,
                "dangerous": request.name in DANGEROUS_TOOLS,
                "args": args,
                "elapsed_ms": round((time.time() - t0) * 1000),
                "error": error,
                "ts": time.time(),
            })

    return audit


def _audit_write(record: dict[str, Any]) -> None:
    try:
        path = Path(_AUDIT_LOG)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 —— 审计失败不阻断业务
        logger.warning("审计日志写入失败：%s", exc)
