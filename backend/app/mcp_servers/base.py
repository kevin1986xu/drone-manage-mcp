"""MCP server 工厂：本地 stdio / Nacos 注册双模式。

- 未配置 NACOS_SERVER_ADDR：`run_mcp` 走 stdio（本地调试、MCP 客户端子进程拉起）。
- 配置了 NACOS_SERVER_ADDR：以 streamable-http 常驻监听，并把 server 元数据 +
  tool 清单注册到 Nacos 3.0.x MCP Registry（见 nacos_registry.py：
  HTTP v3 admin API + gRPC 临时实例 + REF 端点）。注册失败只告警，服务照常跑。

环境变量（backend/.env）：
  NACOS_SERVER_ADDR   如 192.168.101.21:8898（主服务端口；SDK 会用 +1000 的 gRPC 端口）
  NACOS_NAMESPACE     默认 public
  NACOS_USERNAME / NACOS_PASSWORD   Nacos 开启鉴权时必填
  MCP_TRANSPORT       stdio | sse | streamable-http（默认：配了 Nacos 为 streamable-http，否则 stdio）
  MCP_HOST            监听地址，默认 0.0.0.0
  MCP_PORT            覆盖该 server 的默认端口
  MCP_SERVICE_IP      注册到 Nacos 的对外 IP（默认自动探测）
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()
logger = logging.getLogger(__name__)

# 保持 naming client 引用（gRPC 长连接续活临时实例）
_keepalive: list[object] = []


def _nacos_enabled() -> bool:
    return bool(os.getenv("NACOS_SERVER_ADDR", "").strip())


def create_mcp(name: str, default_port: int) -> FastMCP:
    return FastMCP(
        name,
        host=os.getenv("MCP_HOST", "0.0.0.0"),
        port=int(os.getenv("MCP_PORT", default_port)),
    )


async def _serve_with_registry(mcp: FastMCP, transport: str) -> None:
    async def _register() -> None:
        await asyncio.sleep(1.5)  # 等 uvicorn 起监听
        try:
            from app.mcp_servers.nacos_registry import register_to_nacos

            naming = await register_to_nacos(mcp, mcp.name, mcp.settings.port)
            _keepalive.append(naming)
        except Exception as exc:  # noqa: BLE001 —— 注册失败不拖垮服务
            logger.error("Nacos 注册失败（MCP 服务继续运行）：%s", exc)

    reg = asyncio.create_task(_register())
    try:
        if transport == "sse":
            await mcp.run_sse_async()
        else:
            await mcp.run_streamable_http_async()
    finally:
        reg.cancel()


def run_mcp(mcp: FastMCP) -> None:
    default = "streamable-http" if _nacos_enabled() else "stdio"
    transport = os.getenv("MCP_TRANSPORT", default)
    if transport == "stdio":
        mcp.run(transport="stdio")
        return
    if _nacos_enabled():
        asyncio.run(_serve_with_registry(mcp, transport))
    else:
        mcp.run(transport=transport)  # type: ignore[arg-type]
