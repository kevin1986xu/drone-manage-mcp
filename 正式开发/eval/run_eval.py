"""评测集跑批（正式版）：驱动 DeerFlow Gateway，统计工具命中/传参/完成三指标。

与演示版 runner 的差异：
- 走 DeerFlow /api/threads/{id}/runs/stream（LangGraph 兼容 API），不进程内调 agent；
- 工具名带 server 前缀（uav-xxx-mcp_query_plots），比对时剥前缀；
- expected_tools 支持 "a|b" 任一命中（如聚合 preflight_check 与单项等价）；
- 话术基于真实数据（汉川），需现网可达 + 三件套已起（Gateway/mcp-services/审批）。

用法：
  python3 eval/run_eval.py           # 全量 60 条（41-60 为告警/空域/媒体/排期四新域）
  python3 eval/run_eval.py 3 23 30   # 只跑指定 id
跑完自动清理本轮产生的平台测试航线（探测 R-001..R-120 的 platform_route_id）。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8001"
MODEL = "qwen3.7-plus"
EVALSET = Path(__file__).resolve().parent / "evalset.jsonl"


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(f"{BASE}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _run_turn(thread_id: str, text: str) -> list[dict]:
    """跑一轮对话，返回全量 messages（values 事件最后一帧）。"""
    req = urllib.request.Request(
        f"{BASE}/api/threads/{thread_id}/runs/stream",
        data=json.dumps({
            "input": {"messages": [{"type": "human", "content": text}]},
            "context": {"model_name": MODEL},
            "stream_mode": ["values"],
        }).encode(),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    messages: list = []
    with urllib.request.urlopen(req, timeout=600) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line.startswith("data:"):
                try:
                    payload = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if isinstance(payload, dict) and payload.get("messages"):
                    messages = payload["messages"]
    return messages


def _strip(name: str) -> str:
    return name.split("-mcp_", 1)[1] if "-mcp_" in name else name


def _turn_tool_calls(messages: list[dict], utterance: str) -> list[dict]:
    """取"最后一条内容为 utterance 的 human 消息"之后的 tool_calls。"""
    idx = 0
    for i, m in enumerate(messages):
        if m.get("type") == "human" and utterance in str(m.get("content", "")):
            idx = i
    calls = []
    for m in messages[idx:]:
        for tc in (m.get("tool_calls") or []):
            if tc.get("name"):
                calls.append({"tool": _strip(tc["name"]), "args": tc.get("args") or {}})
    return calls


def run_case(case: dict) -> dict:
    tid = _post("/api/threads", {})["thread_id"]
    for pre in case.get("setup", []):
        _run_turn(tid, pre)
    t0 = time.time()
    messages = _run_turn(tid, case["utterance"])
    elapsed = round(time.time() - t0, 1)
    calls = _turn_tool_calls(messages, case["utterance"])
    called = [c["tool"] for c in calls]

    hit = all(any(alt in called for alt in exp.split("|")) for exp in case["expected_tools"])
    args_ok = True
    for tool, wanted in (case.get("expected_args") or {}).items():
        got = next((c["args"] for c in calls if c["tool"] == tool), None)
        if got is None:
            args_ok = False
            continue
        blob = json.dumps(got, ensure_ascii=False)
        for v in wanted.values():
            # "a|b" 任一命中即可（正式链路模型会先把名称解析成 ID 再传参）
            if not any(alt in blob for alt in str(v).split("|")):
                args_ok = False
    return {"id": case["id"], "hit": hit, "args_ok": args_ok, "called": called,
            "elapsed_s": elapsed, "thread": tid}


def cleanup_platform_routes() -> None:
    """探测本进程世界状态里的航线并删平台孤儿（测试纪律）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp-services" / "src"))
    try:
        from uav_mcp.drone_manage import get_client
        import asyncio

        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        import os

        headers = {"X-API-Key": os.getenv("UAV_MCP_API_KEY", "uav-m1-test-key-2026")}

        async def collect() -> list[str]:
            ids = []
            async with streamablehttp_client("http://127.0.0.1:8202/mcp", headers=headers) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    for n in range(1, 121):
                        res = await s.call_tool("get_route_detail", {"route_id": f"R-{n:03d}"})
                        d = json.loads(res.content[0].text)
                        if d.get("error"):
                            continue
                        if d.get("platform_route_id"):
                            ids.append(d["platform_route_id"])
            return ids

        ids = asyncio.run(collect())
        client = get_client()
        for pid in ids:
            try:
                client.delete_route(pid)
                print(f"🧹 已删平台航线 {pid}")
            except Exception as exc:  # noqa: BLE001
                print(f"⚠ 删除失败 {pid}: {exc}")
    except Exception as exc:  # noqa: BLE001
        print(f"⚠ 清理步骤异常（请手动核查平台'低空智察Agent-'前缀航线）：{exc}")


def main() -> None:
    only = {int(a) for a in sys.argv[1:]} if len(sys.argv) > 1 else None
    cases = [json.loads(ln) for ln in EVALSET.open(encoding="utf-8")]
    if only:
        cases = [c for c in cases if c["id"] in only]
    results = []
    for c in cases:
        for attempt in (1, 2):  # VPN 抖断重试一次
            try:
                r = run_case(c)
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 2:
                    r = {"id": c["id"], "hit": False, "args_ok": False,
                         "called": [], "elapsed_s": 0, "error": str(exc)[:80]}
                else:
                    time.sleep(10)
        results.append(r)
        mark = "✅" if r["hit"] and r["args_ok"] else ("🔶" if r["hit"] else "❌")
        print(f"{mark} #{r['id']:>2} {c['utterance'][:28]:<30} "
              f"tools={r['called']} {r.get('error','')} ({r['elapsed_s']}s)", flush=True)

    n = len(results)
    hits = sum(1 for r in results if r["hit"])
    args = sum(1 for r in results if r["hit"] and r["args_ok"])
    print(f"\n工具命中 {hits}/{n} = {hits/n:.0%}   传参正确 {args}/{n} = {args/n:.0%}")
    fails = [r["id"] for r in results if not (r["hit"] and r["args_ok"])]
    if fails:
        print(f"未过：{fails}")
    Path(__file__).with_name("last_run.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1))
    cleanup_platform_routes()


if __name__ == "__main__":
    main()
