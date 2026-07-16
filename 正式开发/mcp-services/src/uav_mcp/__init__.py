"""uav_mcp：无人机飞控 Agent 平台的 MCP 工具层（正式版）。

与演示版（仓库根目录 backend/）完全独立：
- 真实平台优先：数据一律来自 drone-manage（Java），无 mock 种子；
  平台不可达时工具返回明确错误，不静默造数。
- 四个业务域（调度/航线/飞前/飞行任务）在**同一进程**内各起一个
  streamable-http 端点（8201-8204），共享世界状态（航线/确认单/任务
  跨域可见），并各自注册到 Nacos MCP Registry。
- 服务端 API key 校验（X-API-Key）——无 Higress 架构下的工具面鉴权。
- 高危操作人在环：confirm_token 签发在 Agent 之外（独立审批服务，
  见 uav_extensions.approval_service；未配置时进程内本地模式兜底）。
"""

__version__ = "0.1.0"
