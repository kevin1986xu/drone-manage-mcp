"""POC 对话驱动脚本：向 DeerFlow Gateway 发一轮消息，解析 SSE，输出
工具调用序列 + 最终回复 + 单轮耗时。

用法：python3 poc/run_chat.py <thread_id|new> "话术" [--model qwen3.7-plus]
输出（JSON）：{thread_id, elapsed_s, tool_calls: [...], reply: "..."}
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8001"


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def stream_run(thread_id: str, text: str, model: str) -> dict:
    body = {
        "input": {"messages": [{"type": "human", "content": text}]},
        "context": {"model_name": model},
        "stream_mode": ["messages", "values"],
    }
    req = urllib.request.Request(
        f"{BASE}/api/threads/{thread_id}/runs/stream",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    tool_calls: list[dict] = []
    final_messages: list = []
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as resp:
        event = None
        for raw in resp:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
                if not data:
                    continue
                try:
                    payload = json.loads(data)
                except ValueError:
                    continue
                if event == "messages" and isinstance(payload, list) and payload:
                    msg = payload[0]
                    for tc in (msg.get("tool_calls") or []):
                        if tc.get("name"):
                            tool_calls.append({"tool": tc["name"], "args": tc.get("args")})
                elif event == "values" and isinstance(payload, dict) and payload.get("messages"):
                    final_messages = payload["messages"]
    elapsed = round(time.time() - t0, 1)

    reply = ""
    for m in reversed(final_messages):
        if m.get("type") == "ai" and not m.get("tool_calls"):
            c = m.get("content")
            reply = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
            break
    # 汇总 assistant 消息里的完整 tool_calls（values 事件为准，messages 流式片段可能重复/缺失）
    seen = []
    for m in final_messages:
        for tc in (m.get("tool_calls") or []):
            if tc.get("name"):
                seen.append({"tool": tc["name"], "args": tc.get("args")})
    return {"elapsed_s": elapsed, "tool_calls": seen or tool_calls, "reply": reply}


if __name__ == "__main__":
    tid = sys.argv[1]
    text = sys.argv[2]
    model = sys.argv[4] if len(sys.argv) > 4 and sys.argv[3] == "--model" else "qwen3.7-plus"
    if tid == "new":
        tid = post("/api/threads", {})["thread_id"]
    out = stream_run(tid, text, model)
    out["thread_id"] = tid
    print(json.dumps(out, ensure_ascii=False, indent=1))
