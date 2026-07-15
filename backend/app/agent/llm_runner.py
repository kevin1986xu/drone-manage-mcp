"""LLM 模式：LangGraph ReAct 执行轨迹 → AG-UI 事件流。"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

from app.agui import events as ag

# 前端不渲染 Markdown；qwen 偶尔不遵守"纯文本"约束，代码层兜底剥离标记
_MD_RE = re.compile(r"\*\*|__|`+|~~|^\s{0,3}#{1,6}\s+|^\s{0,3}[-*+]\s+", re.M)


def _strip_md(text: str) -> str:
    return _MD_RE.sub("", text)


class _MarkdownSanitizer:
    """流式剥离 Markdown，严格单调：只追加新增的干净后缀，绝不重发（前端是追加式，
    重发会重复）。末尾可能是未完成标记的字符（* ` # ~ - +）先按住，等更多字符或
    flush 再处理，从而把成对/行首标记完整剥掉。"""

    _HOLD = "*`#~-+"

    def __init__(self) -> None:
        self._raw = ""
        self._sent = ""

    def _emit(self, cleaned: str) -> str:
        if len(cleaned) > len(self._sent) and cleaned.startswith(self._sent):
            delta = cleaned[len(self._sent) :]
            self._sent = cleaned
            return delta
        return ""  # 回缩/前缀不一致：保持已发，不重发

    def feed(self, chunk: str) -> str:
        self._raw += chunk
        cleaned = _strip_md(self._raw)
        held = cleaned[: len(cleaned.rstrip(self._HOLD))]  # 去掉可能未完成的尾部标记
        return self._emit(held)

    def flush(self) -> str:
        return self._emit(_strip_md(self._raw))


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
    sanitizer: _MarkdownSanitizer | None = None
    any_text = False        # 本轮是否产出过助手文本
    last_directive: dict[str, Any] | None = None  # 最后一个右栏视图指令（用于空产出兜底）
    cfg = {"configurable": {"thread_id": thread_id}, "recursion_limit": 18}
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
                        sanitizer = _MarkdownSanitizer()
                        yield ag.text_start(msg_id)
                    delta = sanitizer.feed(text)
                    if delta:
                        any_text = True
                        yield ag.text_content(msg_id, delta)
            elif kind == "on_chat_model_end":
                if msg_id is not None:
                    if sanitizer:
                        tail = sanitizer.flush()
                        if tail:
                            yield ag.text_content(msg_id, tail)
                    yield ag.text_end(msg_id)
                    msg_id = None
                    sanitizer = None
            elif kind == "on_tool_start":
                yield ag.tool_start(ev["run_id"], ev["name"], ev["data"].get("input") or {})
            elif kind == "on_tool_end":
                result = _parse_tool_output(ev["data"].get("output"))
                yield ag.tool_end(ev["run_id"], ev["name"], result)
                for d in ag.directives_for(ev["name"], result):
                    last_directive = d
                    yield d
    except Exception as exc:  # noqa: BLE001 —— 演示现场：错误也要走事件流反馈到界面
        msg = str(exc)
        friendly = (
            "这次分析步骤有点多、没能一次给出结论。请把指令说得更具体些"
            "（比如直接指定图斑编号和无人机），我再试一次。"
            if "recursion" in msg.lower() or "GraphRecursion" in msg
            else f"处理时遇到问题：{msg[:80]}。请重试或换种说法。"
        )
        fid = uuid.uuid4().hex[:12]
        yield ag.text_start(fid)
        yield ag.text_content(fid, friendly)
        yield ag.text_end(fid)
        any_text = True
    finally:
        if msg_id is not None:
            if sanitizer:
                tail = sanitizer.flush()
                if tail:
                    if tail.strip():
                        any_text = True
                    yield ag.text_content(msg_id, tail)
            yield ag.text_end(msg_id)
    # 空产出兜底：调了工具却没说话（qwen 偶发查完就停），据最后视图给一句结论
    if not any_text:
        fb = uuid.uuid4().hex[:12]
        if last_directive and last_directive.get("directive") == "show_map" and last_directive["payload"].get("layer") == "route":
            rt = last_directive["payload"]["route"]
            txt = f"航线 {rt['route_id']} 已生成：航程 {rt['length_km']} km、预计 {rt['duration_min']} 分钟，覆盖 {len(rt['covered_plots'])} 个图斑，已在右侧地图显示。"
        elif last_directive:
            txt = "已按你的指令完成操作，结果在右侧显示。还需要我做什么？"
        else:
            txt = "我没太理解，请换种说法或直接说要查图斑、调度无人机还是规划航线。"
        yield ag.text_start(fb)
        yield ag.text_content(fb, txt)
        yield ag.text_end(fb)
    yield ag.run_finished(run_id)
