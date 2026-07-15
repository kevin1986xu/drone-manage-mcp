"""Nacos MCP Registry 注册（HTTP v3 admin API）。

为什么不用 nacos-mcp-wrapper-python：其注册走 gRPC 的 AI 接口，且部分
部署环境客户端 gRPC 端口（主端口 +1000）不对外放行。这里全部走 HTTP
v3 admin API（Nacos ≥3.0 均有），发布 MCP server 规格 + 工具清单。

端点两种模式（NACOS_ENDPOINT_MODE）：
  direct（默认）：端点地址直接写入规格，纯 HTTP、零 gRPC 依赖。
  ref：先经 SDK gRPC 注册临时实例（长连接自动续活、进程退出自动摘除），
       规格用 REF 引用该服务——需要 gRPC 端口可达；适合多实例动态上下线。
"""

from __future__ import annotations

import json
import logging
import os
import socket
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GROUP = "DEFAULT_GROUP"


def _local_ip(nacos_addr: str) -> str:
    """探测本机对外 IP：向 Nacos 建立真实 TCP 连接后取本端地址。

    （UDP connect 只查路由表，在有 Docker 网桥/VPN 虚拟网卡时可能返回
    172.x 等外部不可达地址，故用真实连接。）
    """
    host, _, port = nacos_addr.partition(":")
    s = socket.create_connection((host, int(port or 8848)), timeout=5)
    try:
        return s.getsockname()[0]
    finally:
        s.close()


async def _register_instance(addr: str, namespace: str, username: str, password: str,
                             service: str, ip: str, port: int):
    """注册 gRPC 临时实例，返回 naming client（须保持引用以维持心跳）。"""
    from v2.nacos import ClientConfigBuilder, NacosNamingService, RegisterInstanceParam

    cfg = (
        ClientConfigBuilder()
        .server_address(addr)
        .namespace_id("" if namespace == "public" else namespace)
        .username(username)
        .password(password)
        .build()
    )
    naming = await NacosNamingService.create_naming_service(cfg)
    await naming.register_instance(
        RegisterInstanceParam(
            service_name=service,
            group_name=GROUP,
            ip=ip,
            port=port,
            weight=1.0,
            enabled=True,
            healthy=True,
            ephemeral=True,
            metadata={"scheme": "http", "transport": "streamable-http", "path": "/mcp"},
        )
    )
    logger.info("Nacos 临时实例已注册：%s @ %s:%s", service, ip, port)
    return naming


async def _publish_mcp_spec(addr: str, namespace: str, username: str, password: str,
                            name: str, version: str, description: str,
                            tools: list[dict[str, Any]],
                            endpoint_spec: dict[str, Any]) -> None:
    base = f"http://{addr}/nacos"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(f"{base}/v1/auth/users/login",
                              data={"username": username, "password": password})
        r.raise_for_status()
        token = r.json()["accessToken"]
        headers = {"accessToken": token}

        server_spec = {
            "protocol": "mcp-streamable",
            "frontProtocol": "mcp-streamable",
            "name": name,
            "description": description,
            "versionDetail": {"version": version},
            "enabled": True,
            "remoteServerConfig": {"exportPath": "/mcp"},
        }
        tool_spec = {"tools": tools, "toolsMeta": {}}
        params = {
            "namespaceId": namespace,
            "serverSpecification": json.dumps(server_spec, ensure_ascii=False),
            "toolSpecification": json.dumps(tool_spec, ensure_ascii=False),
            "endpointSpecification": json.dumps(endpoint_spec, ensure_ascii=False),
        }

        exists = await client.get(f"{base}/v3/admin/ai/mcp", headers=headers,
                                  params={"namespaceId": namespace, "mcpName": name})
        already = exists.status_code == 200 and exists.json().get("code") == 0
        if already:
            resp = await client.put(f"{base}/v3/admin/ai/mcp", headers=headers,
                                    params={"mcpName": name}, data=params)
        else:
            resp = await client.post(f"{base}/v3/admin/ai/mcp", headers=headers, data=params)
        body = resp.json()
        if body.get("code") != 0:
            raise RuntimeError(f"发布 MCP 规格失败：{body}")
        logger.info("Nacos MCP Registry 已%s：%s（%d 个 tool）",
                    "更新" if already else "注册", name, len(tools))


async def register_to_nacos(mcp, name: str, port: int, version: str = "0.1.0"):
    """完整注册流程。ref 模式返回 naming client（调用方保持引用）；失败抛异常。"""
    addr = os.environ["NACOS_SERVER_ADDR"].strip()
    namespace = os.getenv("NACOS_NAMESPACE", "public")
    username = os.getenv("NACOS_USERNAME", "nacos")
    password = os.getenv("NACOS_PASSWORD", "")
    mode = os.getenv("NACOS_ENDPOINT_MODE", "direct").lower()
    ip = os.getenv("MCP_SERVICE_IP") or _local_ip(addr)

    mcp_tools = await mcp.list_tools()
    tools = [
        {"name": t.name, "description": t.description or "", "inputSchema": t.inputSchema}
        for t in mcp_tools
    ]

    naming = None
    if mode == "ref":
        endpoint_spec: dict[str, Any] = {
            "type": "REF",
            "data": {"namespaceId": namespace, "groupName": GROUP, "serviceName": name},
        }
        naming = await _register_instance(addr, namespace, username, password, name, ip, port)
    else:
        endpoint_spec = {"type": "DIRECT", "data": {"address": ip, "port": str(port)}}

    await _publish_mcp_spec(addr, namespace, username, password,
                            name, version, mcp.instructions or name, tools, endpoint_spec)
    return naming
