"""uav_extensions：DeerFlow 的无人机业务扩展（零 fork、插件式）。

三个模块，全部经 DeerFlow 官方扩展点或独立进程接入，不改其核心代码：
- approval_service：高危审批服务（confirm_token 签发在 Agent 之外）——独立进程
- interceptors：mcpInterceptors 注入点（高危工具硬白名单短路 + 调用审计）
- nacos_bridge：Nacos MCP Registry → DeerFlow `PUT /api/mcp/config` 同步桥——独立进程
"""

__version__ = "0.1.0"
