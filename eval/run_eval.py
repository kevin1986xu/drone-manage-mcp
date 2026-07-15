"""评测集跑批（《开发计划》§三：智能体测试反向驱动）。

对每条话术统计三个指标：
  - 工具命中率：expected_tools ⊆ 实际调用
  - 传参正确率：expected_args 的关键参数值匹配（子串匹配）
  - 任务完成率：run 无 RUN_ERROR 且命中

用法（在 backend 目录）：
  SCRIPTED_FAST=1 uv run python ../eval/run_eval.py            # scripted 模式
  LLM_API_KEY=... AGENT_MODE=llm uv run python ../eval/run_eval.py   # LLM 模式跑分

调优顺序提醒：命中率低的 tool → 先改 name/description/参数说明，仍不行才改代码。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("SCRIPTED_FAST", "1")
# 默认跑 scripted 回归（shell 显式传 AGENT_MODE=llm 才用大模型跑分，.env 的 auto 不生效）
os.environ.setdefault("AGENT_MODE", "scripted")
# 评测集话术基于 mock 数据（GM-xx 编号）；EVAL_REAL=1 时才允许打真实数据源
if os.getenv("EVAL_REAL") != "1":
    os.environ["DRONE_API_BASE"] = ""
    os.environ["WEATHER_PROVIDER"] = "mock"  # 跑批不出网

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from app import config  # noqa: E402
from app.core.store import STORE  # noqa: E402

EVALSET = Path(__file__).resolve().parent / "evalset.jsonl"


def get_runner():
    if config.AGENT_MODE == "llm":
        from app.agent.llm_runner import run_llm

        return run_llm
    from app.agent.scripted import run_scripted

    return run_scripted


async def run_case(runner, case: dict, idx: int) -> dict:
    STORE.reset()
    if config.AGENT_MODE == "scripted":
        from app.agent.scripted import reset_threads

        reset_threads()
    thread = f"eval-{idx}"
    # 前置话术（构造上下文，不计入统计）
    for pre in case["setup"]:
        async for _ in runner(thread, pre):
            pass
    calls: list[tuple[str, dict]] = []
    errors: list[str] = []
    async for ev in runner(thread, case["utterance"]):
        if ev["type"] == "TOOL_CALL_START":
            calls.append((ev["tool_name"], ev.get("args") or {}))
        if ev["type"] == "RUN_ERROR":
            errors.append(ev["message"])

    called_names = [c[0] for c in calls]
    tool_hit = all(t in called_names for t in case["expected_tools"])

    args_ok = True
    for tool, expects in (case.get("expected_args") or {}).items():
        actual = next((a for n, a in calls if n == tool), None)
        if actual is None:
            args_ok = False
            continue
        for key, want in expects.items():
            got = str(actual.get(key, ""))
            if str(want).upper() not in got.upper():
                args_ok = False

    return {
        "id": case["id"],
        "scene": case["scene"],
        "utterance": case["utterance"],
        "expected": case["expected_tools"],
        "called": called_names,
        "tool_hit": tool_hit,
        "args_ok": tool_hit and args_ok,
        "completed": tool_hit and not errors,
    }


async def main() -> None:
    cases = [json.loads(line) for line in EVALSET.read_text().splitlines() if line.strip()]
    runner = get_runner()
    print(f"模式：{config.AGENT_MODE}（{config.LLM_MODEL if config.AGENT_MODE == 'llm' else '关键词兜底路由'}）"
          f" · {len(cases)} 条话术\n")
    results = []
    for i, case in enumerate(cases):
        r = await run_case(runner, case, i)
        results.append(r)
        mark = "✓" if r["tool_hit"] else "✗"
        argm = "" if r["args_ok"] else "  [传参✗]"
        print(f"{mark} #{r['id']:>2} 场景{r['scene']} {r['utterance']}"
              f"{argm}{'' if r['tool_hit'] else '  期望 ' + str(r['expected']) + ' 实际 ' + str(r['called'])}")

    n = len(results)
    hit = sum(r["tool_hit"] for r in results)
    args = sum(r["args_ok"] for r in results)
    done = sum(r["completed"] for r in results)
    print("\n──────── 汇总 ────────")
    print(f"工具命中率   {hit}/{n} = {hit / n:.0%}")
    print(f"传参正确率   {args}/{n} = {args / n:.0%}")
    print(f"任务完成率   {done}/{n} = {done / n:.0%}")
    by_scene: dict[int, list] = {}
    for r in results:
        by_scene.setdefault(r["scene"], []).append(r["tool_hit"])
    for sc in sorted(by_scene):
        v = by_scene[sc]
        print(f"  场景{sc} 命中 {sum(v)}/{len(v)}")
    misses = [r for r in results if not r["tool_hit"]]
    if misses:
        print("\n未命中清单（先改 tool 描述，再考虑改代码）：")
        for r in misses:
            print(f"  #{r['id']} {r['utterance']} → 期望 {r['expected']}，实际 {r['called']}")


if __name__ == "__main__":
    asyncio.run(main())
