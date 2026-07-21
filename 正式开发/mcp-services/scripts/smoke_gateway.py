"""Higress 网关消费者鉴权 · 端到端冒烟（deploy/README-higress.md §4）。

对全部 8 个正式版 MCP 域：经网关完成完整 MCP 会话（initialize → tools/list）；
调度域再做真实工具调用（query_plots，穿透后端→平台回源）。
负面矩阵：无 key / 错 key / 后端统一 key 直打网关都必须 401。
兼容性：直连后端 820x，租户 key 与老单 key 都应可用（收口后此项应改为失败）。

前置：Higress 容器 + runner 已起（约 40 个请求，远低于网关 120/min 限流）。
运行：.venv/bin/python scripts/smoke_gateway.py
"""

from __future__ import annotations

import asyncio
import json

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

GW = "http://localhost:8080/mcp"
TENANT_KEY = "demo-key-2026-a1b2c3"
BACKEND_KEY = "uav-m1-test-key-2026"
SERVERS = [
    "uav-drone-dispatch-mcp",
    "uav-route-planning-mcp",
    "uav-preflight-mcp",
    "uav-flight-task-mcp",
    "uav-airspace-mcp",
    "uav-alert-mcp",
    "uav-media-mcp",
    "uav-task-schedule-mcp",
]

PASS, FAIL = "✅", "❌"
failures: list[str] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    print(f"{PASS if ok else FAIL} {name}" + (f" —— {extra}" if extra else ""))
    if not ok:
        failures.append(name)


async def full_session(url: str, key: str) -> list[str]:
    async with streamablehttp_client(url, headers={"X-API-Key": key}) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            return [t.name for t in tools.tools]


async def call_tool(url: str, key: str, tool: str, args: dict) -> dict:
    async with streamablehttp_client(url, headers={"X-API-Key": key}) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool(tool, args)
            return json.loads(res.content[0].text)


async def main() -> None:
    print("── 1. 经网关完整 MCP 会话（initialize → tools/list）× 8 域，租户 key ──")
    total = 0
    for srv in SERVERS:
        try:
            names = await full_session(f"{GW}/{srv}/mcp", TENANT_KEY)
            total += len(names)
            check(f"{srv} 完整会话", len(names) > 0, f"{len(names)} 个 tool")
        except Exception as e:  # noqa: BLE001
            check(f"{srv} 完整会话", False, repr(e)[:120])
    check("8 域工具总数", total >= 40, f"共 {total} 个 tool")

    print("── 2. 经网关真实工具调用（调度域 query_plots → 后端 → 平台回源）──")
    try:
        plots = await call_tool(f"{GW}/uav-drone-dispatch-mcp/mcp", TENANT_KEY, "query_plots", {})
        check("query_plots 真实数据", plots.get("count", 0) > 0, f"{plots.get('count')} 个图斑")
    except Exception as e:  # noqa: BLE001
        check("query_plots 真实数据", False, repr(e)[:160])

    print("── 3. 负面矩阵（直打网关，必须全 401）──")
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    hdrs = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    async with httpx.AsyncClient(timeout=10) as c:
        for label, extra in [("无 key", {}), ("错 key", {"X-API-Key": "bad-key-xyz"}),
                             ("后端统一 key", {"X-API-Key": BACKEND_KEY})]:
            for srv in SERVERS:
                r = await c.post(f"{GW}/{srv}/mcp", json=body, headers={**hdrs, **extra})
                if r.status_code != 401:
                    check(f"{label} → {srv}", False, f"竟然 {r.status_code}")
                    break
            else:
                check(f"{label} × 8 域全 401", True)

        print("── 4. 直连后端兼容性（绕过网关，收口前应两把 key 都可用）──")
        for label, key in [("租户 key", TENANT_KEY), ("老单 key", BACKEND_KEY)]:
            r = await c.post("http://127.0.0.1:8201/mcp", json=body, headers={**hdrs, "X-API-Key": key})
            check(f"直连 8201 · {label}", r.status_code == 200, f"{r.status_code}")

    print()
    if failures:
        print(f"{FAIL} {len(failures)} 项失败：{failures}")
        raise SystemExit(1)
    print(f"{PASS} 全部通过")


asyncio.run(main())
