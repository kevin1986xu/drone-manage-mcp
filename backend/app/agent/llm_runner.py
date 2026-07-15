"""LLM 模式：LangGraph ReAct 执行轨迹 → AG-UI 事件流。"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from app.agui import events as ag


def _parse_tool_output(output: Any) -> Any:
    """on_tool_end 的 output 可能是 dict / ToolMessage / str，统一还原为结构化数据。"""
    if isinstance(output, (dict, list)):
        return output
    content = getattr(output, "content", output)
    if isinstance(content, list):
        content = "".join(c if isinstance(c, str) else c.get("text", "") for c in content)
    if isinstance(content, str):
        try:
            return json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return content
    return content


async def run_llm(thread_id: str, message: str) -> AsyncIterator[dict[str, Any]]:
    from app.agent.graph import get_agent  # 延迟导入：scripted 模式不加载 LLM 依赖

    agent = get_agent()
    run_id = uuid.uuid4().hex[:12]
    yield ag.run_started(run_id)

    msg_id: str | None = None
    cfg = {"configurable": {"thread_id": thread_id}}
    try:
        async for ev in agent.astream_events(
            {"messages": [{"role": "user", "content": message}]}, cfg, version="v2"
        ):
            kind = ev["event"]
            if kind == "on_chat_model_stream":
                chunk = ev["data"]["chunk"]
                text = chunk.content
                if isinstance(text, list):
                    text = "".join(c.get("text", "") for c in text if isinstance(c, dict))
                if text:
                    if msg_id is None:
                        msg_id = uuid.uuid4().hex[:12]
                        yield ag.text_start(msg_id)
                    yield ag.text_content(msg_id, text)
            elif kind == "on_chat_model_end":
                if msg_id is not None:
                    yield ag.text_end(msg_id)
                    msg_id = None
            elif kind == "on_tool_start":
                yield ag.tool_start(ev["run_id"], ev["name"], ev["data"].get("input") or {})
            elif kind == "on_tool_end":
                result = _parse_tool_output(ev["data"].get("output"))
                yield ag.tool_end(ev["run_id"], ev["name"], result)
                for d in ag.directives_for(ev["name"], result):
                    yield d
    except Exception as exc:  # noqa: BLE001 —— 演示现场：错误也要走事件流反馈到界面
        yield ag.run_error(f"Agent 执行异常：{exc}")
    finally:
        if msg_id is not None:
            yield ag.text_end(msg_id)
    yield ag.run_finished(run_id)
