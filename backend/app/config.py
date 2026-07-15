"""运行配置。全部走环境变量，便于内网/外网环境切换。

- LLM 走 OpenAI 兼容接口：默认指向阿里云百炼（Qwen），也可指向
  私有化 vLLM/SGLang 或任何兼容端点。
- AGENT_MODE=auto：有 LLM_API_KEY 用 LangGraph ReAct（llm），
  否则用 scripted 关键词兜底路由（即《开发计划》L2 降级，可脱网演示）。
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

LLM_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or ""
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen-plus")

_mode = os.getenv("AGENT_MODE", "auto").lower()
AGENT_MODE = _mode if _mode in {"llm", "scripted"} else ("llm" if LLM_API_KEY else "scripted")

CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")

# ── 真实业务数据源（drone-manage，若依模块）─────────────────
# 配置后业务工具优先走真实接口，未配置或调用失败回落 mock（L1 降级）
DRONE_API_BASE = os.getenv("DRONE_API_BASE", "").strip().rstrip("/")
# take_off 确认后是否在平台创建飞行任务（只建不下发，不会飞），默认关闭
DRONE_CREATE_REAL_TASK = os.getenv("DRONE_CREATE_REAL_TASK", "0") == "1"
# 是否真的下发计划到机场执行（= 真实起飞！immediate 模式立即飞），默认关闭；
# 开启需 DRONE_CREATE_REAL_TASK 也开，并确认现场安全与审批
DRONE_REAL_PUBLISH = os.getenv("DRONE_REAL_PUBLISH", "0") == "1"
DRONE_WORKSPACE_ID = os.getenv("DRONE_WORKSPACE_ID", "drone")
