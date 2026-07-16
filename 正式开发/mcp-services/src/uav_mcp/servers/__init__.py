"""四个业务域 MCP server 的工具声明。

工具描述沿用演示版三轮调优后的文案（llm 命中率 91%+），
列表型参数统一 `list[str] | str`（吸收模型把列表串成字符串的常见错误）。
"""

from __future__ import annotations

import json
from typing import Any


def as_list(v: Any) -> Any:
    """'["0005"]' → ["0005"]；'0005' → ["0005"]。吸收 LLM 的字符串化列表。"""
    if v is None or isinstance(v, list):
        return v
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("["):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return [str(x) for x in parsed]
            except (ValueError, TypeError):
                pass
        return [v]
    return v
