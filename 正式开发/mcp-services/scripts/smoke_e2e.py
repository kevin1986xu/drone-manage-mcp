"""端到端冒烟：MCP 客户端 →（X-API-Key）四域服务 → 真实平台 + 审批服务。

前置：runner 与 approval_service 已起，.env 指向现网。
验证：真实数据链路 / 跨域共享状态 / 人在环全流程（无token自拒→伪造拒绝→
人工批准→携token执行→重放拒绝）/ 平台孤儿航线清理。

运行：.venv/bin/python scripts/smoke_e2e.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

API_KEY = os.getenv("UAV_MCP_API_KEY", "uav-m1-test-key-2026")
ADMIN_KEY = os.getenv("APPROVAL_ADMIN_KEY", "adm-m1-test")
HEADERS = {"X-API-Key": API_KEY}
APPROVAL = "http://127.0.0.1:8205"

PASS, FAIL = "✅", "❌"
failures: list[str] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    print(f"{PASS if ok else FAIL} {name}" + (f" —— {extra}" if extra else ""))
    if not ok:
        failures.append(name)


async def call(port: int, tool: str, args: dict) -> dict:
    async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp", headers=HEADERS) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool(tool, args)
            return json.loads(res.content[0].text)


async def main() -> None:
    # 1. 调度域：真实图斑
    plots = await call(8201, "query_plots", {})
    check("query_plots 真实数据", plots.get("count", 0) > 0, f"{plots.get('count')} 个图斑")
    pid = plots["plots"][0]["plot_id"]
    check("瘦身返回（无 geometry）", "geometry" not in plots["plots"][0])

    # 2. 目标图斑选机
    drones = await call(8201, "find_nearby_drones", {"plot_ids": [pid], "radius_km": 5})
    check("find_nearby_drones（目标图斑基准）", drones.get("count", 0) > 0,
          f"{drones.get('count')} 台 · 参照 {drones.get('reference')}")
    drone_id = drones["drones"][0]["drone_id"]

    # 3. 航线域：平台图斑巡检算法规划
    route = await call(8202, "generate_route", {"drone_id": drone_id, "plot_ids": [pid]})
    check("generate_route", "route_id" in route,
          f"{route.get('route_id')} · {route.get('source')} · {route.get('length_km')} km · {route.get('waypoint_count')} 航点")
    route_id = route["route_id"]
    check("平台算法规划成功", route.get("source") == "平台图斑巡检算法")

    # 字符串化列表参数吸收（LLM 常见错误形态）
    route2 = await call(8202, "get_route_detail", {"route_id": route_id})
    check("跨域前置：航线详情可查", route2.get("route_id") == route_id)

    # 4. 飞前域（另一端口，同进程共享状态）
    pf = await call(8203, "preflight_check", {"drone_id": drone_id, "route_id": route_id})
    check("preflight_check 跨域读到航线", pf.get("overall") in ("pass", "warn"),
          f"overall={pf.get('overall')}")

    # 5. 人在环：无 token 自拒 → 待确认单
    t1 = await call(8204, "take_off", {"drone_id": drone_id, "route_id": route_id})
    check("take_off 无token → requires_confirmation", t1.get("status") == "requires_confirmation",
          t1.get("action_id", ""))
    action_id = t1["action_id"]

    # 6. 伪造 token 拒绝
    t2 = await call(8204, "take_off", {"drone_id": drone_id, "route_id": route_id,
                                       "confirm_token": "x" * 32})
    check("伪造 token 拒绝", t2.get("status") == "rejected")

    # 7. 审批服务：列表可见 → 人工批准 → 签发 token
    async with httpx.AsyncClient() as c:
        lst = (await c.get(f"{APPROVAL}/api/approval/pending",
                           params={"status": "pending"}, headers={"X-Admin-Key": ADMIN_KEY})).json()
        check("审批服务列表可见待确认单", any(i["action_id"] == action_id for i in lst))
        ok = await c.post(f"{APPROVAL}/api/approval/{action_id}/approve",
                          headers={"X-Admin-Key": ADMIN_KEY})
        token = ok.json()["confirm_token"]
        check("人工批准签发 token", ok.status_code == 200 and len(token) >= 24)

    # 8. 携 token 执行（UAV_CREATE_REAL_TASK=0 → 不创建平台任务、不会飞）
    t3 = await call(8204, "take_off", {"drone_id": drone_id, "route_id": route_id,
                                       "confirm_token": token})
    check("携 token 执行", t3.get("status") == "airborne", t3.get("note", ""))
    check("未创建平台任务（安全开关关）", not t3.get("platform_task"))

    # 9. 重放拒绝
    t4 = await call(8204, "take_off", {"drone_id": drone_id, "route_id": route_id,
                                       "confirm_token": token})
    check("token 重放拒绝", t4.get("status") == "rejected")

    # 10. 清理平台测试航线（孤儿治理纪律）
    detail = await call(8202, "get_route_detail", {"route_id": route_id})
    prid = detail.get("platform_route_id")
    if prid:
        from uav_mcp.drone_manage import get_client

        get_client().delete_route(prid)
        print(f"🧹 已清理平台测试航线 {prid}")

    print()
    if failures:
        print(f"{FAIL} {len(failures)} 项失败：{failures}")
        sys.exit(1)
    print(f"{PASS} 端到端冒烟全部通过")


if __name__ == "__main__":
    asyncio.run(main())
