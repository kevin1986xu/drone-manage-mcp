"""服务入口：一个进程内起四个业务域的 streamable-http 端点（共享世界状态）。

为什么单进程：航线/确认单/任务是跨域状态（route-planning 生成的航线，
preflight 和 flight-task 都要读）。四个域各占一个端口、各自注册 Nacos、
在 DeerFlow 里是四个独立 server（子代理工具集按 server 划分），但状态同源。

用法：
  python -m uav_mcp.runner                 # 起全部四个域
  python -m uav_mcp.runner drone-dispatch  # 只起指定域（调试）
"""

from __future__ import annotations

import asyncio
import logging
import sys

import uvicorn

from uav_mcp import config
from uav_mcp.auth import ApiKeyMiddleware
from uav_mcp.servers import (
    airspace,
    alert,
    drone_dispatch,
    flight_task,
    media,
    preflight,
    route_planning,
    task_schedule,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

BUILDERS = {
    "drone-dispatch": drone_dispatch.build,
    "route-planning": route_planning.build,
    "preflight": preflight.build,
    "flight-task": flight_task.build,
    "airspace": airspace.build,
    "alert": alert.build,
    "media": media.build,
    "task-schedule": task_schedule.build,
}

# 保持 naming client 引用（ref 模式 gRPC 长连接续活临时实例）
_keepalive: list[object] = []


async def _serve_one(domain: str) -> None:
    mcp = BUILDERS[domain]()
    port = config.PORTS[domain]
    # stateless：每次 tool 调用独立会话，DeerFlow 侧会话池/多客户端均可直连
    mcp.settings.stateless_http = True
    # MCP SDK 的 DNS-rebinding 防护默认只放行 localhost Host 头，经注册 IP
    # 访问会 421。内网服务 + API key 鉴权在前，这里关闭 Host 校验
    # （浏览器 DNS-rebinding 攻击面不适用于服务间调用）。
    from mcp.server.transport_security import TransportSecuritySettings

    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    )
    app = ApiKeyMiddleware(mcp.streamable_http_app(), config.UAV_MCP_API_KEY, config.UAV_TENANT_KEYS)
    server = uvicorn.Server(uvicorn.Config(app, host=config.MCP_HOST, port=port, log_level="warning"))

    async def _register() -> None:
        # 错峰：八域并发注册会在 VPN 慢链路上集体超时（2026-07-20 实测 7/8 失败），
        # 按端口序号错开 + 失败退避重试
        await asyncio.sleep(1.5 + (port - 8201) * 0.8)
        from uav_mcp.nacos_registry import register_to_nacos

        for attempt in range(3):
            try:
                naming = await register_to_nacos(mcp, mcp.name, port)
                if naming:
                    _keepalive.append(naming)
                return
            except Exception as exc:  # noqa: BLE001 —— 注册失败不拖垮服务
                logger.error("Nacos 注册失败（第 %s/3 次，%s）：%r", attempt + 1, mcp.name, exc)
                await asyncio.sleep(5 * (attempt + 1))

    reg = None
    if config.NACOS_SERVER_ADDR:
        reg = asyncio.create_task(_register())
    logger.info("%s 监听 %s:%s（API key 校验：%s）", mcp.name, config.MCP_HOST, port,
                "开" if config.UAV_MCP_API_KEY else "关")
    try:
        await server.serve()
    finally:
        if reg:
            reg.cancel()


async def main(domains: list[str]) -> None:
    await asyncio.gather(*(_serve_one(d) for d in domains))


if __name__ == "__main__":
    picked = sys.argv[1:] or list(BUILDERS)
    unknown = [d for d in picked if d not in BUILDERS]
    if unknown:
        raise SystemExit(f"未知业务域：{unknown}；可选：{list(BUILDERS)}")
    asyncio.run(main(picked))
