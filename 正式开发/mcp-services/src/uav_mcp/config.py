"""运行配置（全部环境变量，.env 支持）。"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

# ── 无人机平台（drone-manage，必配）─────────────────────────
DRONE_API_BASE = os.getenv("DRONE_API_BASE", "").strip().rstrip("/")
DRONE_WORKSPACE_ID = os.getenv("DRONE_WORKSPACE_ID", "drone")

# take_off 确认后是否在平台创建飞行任务（只建不下发，不会飞），默认关
UAV_CREATE_REAL_TASK = os.getenv("UAV_CREATE_REAL_TASK", "0") == "1"
# 是否真的下发计划到机场执行（= 真实起飞！），默认关；开启需现场安全审批
UAV_REAL_PUBLISH = os.getenv("UAV_REAL_PUBLISH", "0") == "1"

# ── 工具面鉴权（无 Higress 架构的治理口径）───────────────────
# 消费方（DeerFlow extensions_config 的 headers）须带 X-API-Key: <该值>；
# 未配置则不校验并打告警（仅限本机开发）。
UAV_MCP_API_KEY = os.getenv("UAV_MCP_API_KEY", "").strip()

# ── 审批服务（uav_extensions.approval_service）───────────────
# 配置后高危确认单/一次性 token 全部由独立审批服务签发与消费；
# 未配置则进程内本地模式（语义相同，仅限开发）。
APPROVAL_BASE = os.getenv("APPROVAL_BASE", "").strip().rstrip("/")

# ── 服务监听 ─────────────────────────────────────────────────
MCP_HOST = os.getenv("MCP_HOST", "0.0.0.0")
# 各域端口（与演示版 8101-8104 错开）
PORTS = {
    "drone-dispatch": int(os.getenv("PORT_DRONE_DISPATCH", "8201")),
    "route-planning": int(os.getenv("PORT_ROUTE_PLANNING", "8202")),
    "preflight": int(os.getenv("PORT_PREFLIGHT", "8203")),
    "flight-task": int(os.getenv("PORT_FLIGHT_TASK", "8204")),
    # 8205 为审批服务（uav_extensions.approval_service），新域从 8206 起
    "airspace": int(os.getenv("PORT_AIRSPACE", "8206")),
    "alert": int(os.getenv("PORT_ALERT", "8207")),
    "media": int(os.getenv("PORT_MEDIA", "8208")),
    "task-schedule": int(os.getenv("PORT_TASK_SCHEDULE", "8209")),
}

# ── Nacos 注册（可选；不配则只起服务不注册）──────────────────
NACOS_SERVER_ADDR = os.getenv("NACOS_SERVER_ADDR", "").strip()
NACOS_NAMESPACE = os.getenv("NACOS_NAMESPACE", "public")
NACOS_USERNAME = os.getenv("NACOS_USERNAME", "nacos")
NACOS_PASSWORD = os.getenv("NACOS_PASSWORD", "")
NACOS_ENDPOINT_MODE = os.getenv("NACOS_ENDPOINT_MODE", "direct").lower()
MCP_SERVICE_IP = os.getenv("MCP_SERVICE_IP", "").strip()

# ── 气象 ─────────────────────────────────────────────────────
# auto：Open-Meteo 自查 → 平台气象兜底；off：只走平台气象
WEATHER_PROVIDER = os.getenv("WEATHER_PROVIDER", "auto")

# 航线命名前缀（平台侧可识别、测试后可批量清理）
ROUTE_NAME_PREFIX = os.getenv("ROUTE_NAME_PREFIX", "低空智察Agent")
