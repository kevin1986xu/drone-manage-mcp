"""P3 单轮延迟对比：同一话术，演示版（LangGraph 直连）vs DeerFlow，各 N 次。

话术选"查询00005图斑"（单工具调用轮，度量框架开销为主）。
每次都用新 thread（两边都免会话状态干扰）。
"""

from __future__ import annotations

import json
import statistics
import time
import urllib.request

N = 5
UTTERANCE = "查询一下编号00005的图斑"


def demo_once() -> float:
    t0 = time.time()
    req = urllib.request.Request(
        "http://127.0.0.1:8000/api/agent/run",
        data=json.dumps({"thread_id": f"p3-{time.time()}", "message": UTTERANCE}).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        for _ in r:  # 排空 SSE 至结束
            pass
    return time.time() - t0


def deerflow_once() -> float:
    body = json.dumps({}).encode()
    req = urllib.request.Request("http://127.0.0.1:8001/api/threads", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        tid = json.loads(r.read())["thread_id"]
    t0 = time.time()
    req = urllib.request.Request(
        f"http://127.0.0.1:8001/api/threads/{tid}/runs/stream",
        data=json.dumps({
            "input": {"messages": [{"type": "human", "content": UTTERANCE}]},
            "context": {"model_name": "qwen3.7-plus"},
            "stream_mode": ["values"],
        }).encode(),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        for _ in r:
            pass
    return time.time() - t0


def bench(name: str, fn) -> list[float]:
    xs = []
    for i in range(N):
        s = fn()
        xs.append(s)
        print(f"  {name} #{i+1}: {s:.1f}s")
    return xs


if __name__ == "__main__":
    print(f"话术：{UTTERANCE} × {N}")
    demo = bench("演示版", demo_once)
    df = bench("DeerFlow", deerflow_once)
    p50d, p50f = statistics.median(demo), statistics.median(df)
    print(f"\n演示版   P50={p50d:.1f}s  min={min(demo):.1f}  max={max(demo):.1f}")
    print(f"DeerFlow P50={p50f:.1f}s  min={min(df):.1f}  max={max(df):.1f}")
    print(f"劣化倍数 P50: {p50f / p50d:.2f}x（标准 ≤2x）")
