"""运行配置（全部环境变量，.env 支持）。"""

from __future__ import annotations

import json
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

# ── 工具面鉴权（关一·接入鉴权，见 docs/07 §4.1）──────────────
# 消费方（DeerFlow extensions_config 的 headers）须带 X-API-Key: <该值>；
# 未配置则不校验并打告警（仅限本机开发）。
UAV_MCP_API_KEY = os.getenv("UAV_MCP_API_KEY", "").strip()
# 多租户 key 表（JSON）：key → {tenant, scopes}，区分调用方并注入租户身份。
# 与单 key 并存（单 key 作 default 租户兜底）。例：
#   {"key-abc":{"tenant":"partner-a","scopes":["read"]},"key-xyz":{"tenant":"gov","scopes":["*"]}}
def _load_tenant_keys() -> dict:
    raw = os.getenv("UAV_TENANT_KEYS", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}

UAV_TENANT_KEYS = _load_tenant_keys()

# ── 平台回源鉴权（关三，见 docs/07 §4.3）─────────────────────
# 【网关认证模式】配置 DRONE_GATEWAY_BASE 后，回源走平台网关（若依 Sa-Token）：
#   base=网关地址、path 加 DRONE_GATEWAY_PREFIX 前缀、账号密码登录拿 JWT 并带
#   Authorization: Bearer；token 缓存 + 401 自动重登。这是正规回源（平台认身份、
#   可做 dataScope），取代直连内部端口 10009（绕过认证）。
# 未配置 DRONE_GATEWAY_BASE 则保持直连 DRONE_API_BASE 裸调（向后兼容，演示可用）。
DRONE_GATEWAY_BASE = os.getenv("DRONE_GATEWAY_BASE", "").strip().rstrip("/")
DRONE_GATEWAY_PREFIX = os.getenv("DRONE_GATEWAY_PREFIX", "/drone").strip()
DRONE_LOGIN_PATH = os.getenv("DRONE_LOGIN_PATH", "/auth/dronelogin").strip()
DRONE_LOGIN_USERNAME = os.getenv("DRONE_LOGIN_USERNAME", "").strip()
DRONE_LOGIN_PASSWORD = os.getenv("DRONE_LOGIN_PASSWORD", "").strip()

# 服务账号静态 token（备选，不走登录时直接带；网关模式优先用登录 token）。
DRONE_PLATFORM_TOKEN = os.getenv("DRONE_PLATFORM_TOKEN", "").strip()
# 透传用户身份的请求头名（P1 用户级授权：拦截器把发起用户身份注入此头，
# 平台据此做 dataScope 过滤）。默认空=不启用透传。
DRONE_USER_ID_HEADER = os.getenv("DRONE_USER_ID_HEADER", "").strip()

# ── 审批服务（uav_extensions.approval_service）───────────────
# 配置后高危确认单/一次性 token 全部由独立审批服务签发与消费；
# 未配置则进程内本地模式（语义相同，仅限开发）。
APPROVAL_BASE = os.getenv("APPROVAL_BASE", "").strip().rstrip("/")

# ── 通用前端 UI 服务（docs/08；不配则工具不返回 view_url，全部照旧）──
UAV_UI_BASE = os.getenv("UAV_UI_BASE", "").strip().rstrip("/")

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
    # P1 三域（2026-07-21 起）
    "live": int(os.getenv("PORT_LIVE", "8210")),
    "flight-control": int(os.getenv("PORT_FLIGHT_CONTROL", "8211")),
    "dock-debug": int(os.getenv("PORT_DOCK_DEBUG", "8212")),
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
