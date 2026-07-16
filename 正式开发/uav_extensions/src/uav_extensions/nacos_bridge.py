"""Nacos MCP Registry → DeerFlow 同步桥（独立进程，几十行核心逻辑）。

作用：核心业务域在 DeerFlow 里保持"直见工具"（不走 router 的搜→转调），
但 server 端点不写死——本桥轮询 Nacos v3 MCP Registry，把命中前缀的
server 同步进 DeerFlow 的 MCP 配置（`PUT /api/mcp/config`，官方热更新
API，写盘+重载+工具缓存重置一步完成；接口已对其源码核实）。

Nacos 里注册/下线/换地址，DeerFlow 自动跟随，零配置变更。
稳定后此桥可作为 PR 贡献回 DeerFlow 上游（其当前缺注册中心发现）。

行为约束：
- 只管理名字命中 UAV_BRIDGE_PREFIX（默认 "uav-"）的 server——桥永远
  不碰人工配置的其他 server；
- diff 只比 url/enabled/描述（GET 返回的 headers 会被网关脱敏为 ***，
  不能参与比较；PUT 时按需重新下发完整 headers）；
- 无变化不写（防抖）；Nacos 拉取失败保持现状，不误删。

运行：python -m uav_extensions.nacos_bridge
环境：NACOS_SERVER_ADDR / NACOS_USERNAME / NACOS_PASSWORD / NACOS_NAMESPACE
      DEERFLOW_BASE（默认 http://127.0.0.1:8001）
      DEERFLOW_ADMIN_TOKEN（网关开鉴权时的 Bearer token）
      UAV_MCP_API_KEY（同步进各 server 连接 headers 的 X-API-Key）
      UAV_BRIDGE_PREFIX（默认 uav-）  BRIDGE_INTERVAL_S（默认 30）
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

NACOS_ADDR = os.getenv("NACOS_SERVER_ADDR", "").strip()
NACOS_NS = os.getenv("NACOS_NAMESPACE", "public")
NACOS_USER = os.getenv("NACOS_USERNAME", "nacos")
NACOS_PASS = os.getenv("NACOS_PASSWORD", "")
DEERFLOW_BASE = os.getenv("DEERFLOW_BASE", "http://127.0.0.1:8001").rstrip("/")
DEERFLOW_TOKEN = os.getenv("DEERFLOW_ADMIN_TOKEN", "").strip()
API_KEY = os.getenv("UAV_MCP_API_KEY", "").strip()
PREFIX = os.getenv("UAV_BRIDGE_PREFIX", "uav-")
INTERVAL_S = int(os.getenv("BRIDGE_INTERVAL_S", "30"))


async def _nacos_token(client: httpx.AsyncClient) -> str:
    r = await client.post(f"http://{NACOS_ADDR}/nacos/v1/auth/users/login",
                          data={"username": NACOS_USER, "password": NACOS_PASS})
    r.raise_for_status()
    return r.json()["accessToken"]


async def fetch_nacos_servers(client: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
    """Nacos Registry 中命中前缀的 MCP server → {name: {url, description}}。"""
    token = await _nacos_token(client)
    headers = {"accessToken": token}
    base = f"http://{NACOS_ADDR}/nacos/v3/admin/ai/mcp"
    r = await client.get(f"{base}/list", headers=headers,
                         params={"namespaceId": NACOS_NS, "pageNo": 1, "pageSize": 100})
    r.raise_for_status()
    page = r.json().get("data") or {}
    items = page.get("pageItems") or page.get("list") or []
    out: dict[str, dict[str, Any]] = {}
    for it in items:
        name = it.get("name") or ""
        if not name.startswith(PREFIX):
            continue
        d = await client.get(base, headers=headers,
                             params={"namespaceId": NACOS_NS, "mcpName": name})
        detail = (d.json() or {}).get("data") or {}
        eps = detail.get("backendEndpoints") or []
        if not eps:
            logger.warning("忽略无端点的 server：%s", name)
            continue
        ep = eps[0]
        path = ((detail.get("remoteServerConfig") or {}).get("exportPath")) or "/mcp"
        out[name] = {
            "url": f"http://{ep.get('address')}:{ep.get('port')}{path}",
            "description": detail.get("description") or name,
        }
    return out


def _df_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {DEERFLOW_TOKEN}"} if DEERFLOW_TOKEN else {}


async def sync_once(client: httpx.AsyncClient) -> bool:
    """一轮对账。返回是否发生了配置写入。"""
    desired = await fetch_nacos_servers(client)

    r = await client.get(f"{DEERFLOW_BASE}/api/mcp/config", headers=_df_headers())
    r.raise_for_status()
    current: dict[str, Any] = r.json().get("mcp_servers") or {}

    managed_now = {k: v for k, v in current.items() if k.startswith(PREFIX)}
    changed = False
    merged = dict(current)

    for name, info in desired.items():
        cur = managed_now.get(name)
        if not cur or cur.get("url") != info["url"] or not cur.get("enabled", True):
            changed = True
        entry: dict[str, Any] = {
            "enabled": True,
            "type": "http",
            "url": info["url"],
            "description": f"[nacos-bridge] {info['description']}",
        }
        if API_KEY:
            entry["headers"] = {"X-API-Key": API_KEY}
        merged[name] = entry
    for name in managed_now:
        if name not in desired:  # Nacos 已下线 → 摘除（仅限桥管理的前缀）
            merged.pop(name, None)
            changed = True

    if not changed:
        return False
    w = await client.put(f"{DEERFLOW_BASE}/api/mcp/config", headers=_df_headers(),
                         json={"mcp_servers": merged})
    w.raise_for_status()
    # PUT 已含写盘+重载+缓存重置；这里再显式 reset 一次作为兜底（幂等）
    try:
        await client.post(f"{DEERFLOW_BASE}/api/mcp/cache/reset", headers=_df_headers())
    except Exception:  # noqa: BLE001
        pass
    logger.info("已同步 %d 个 server 到 DeerFlow：%s", len(desired), ", ".join(desired) or "—")
    return True


async def main() -> None:
    if not NACOS_ADDR:
        raise SystemExit("需要 NACOS_SERVER_ADDR")
    async with httpx.AsyncClient(timeout=15) as client:
        while True:
            try:
                await sync_once(client)
            except Exception as exc:  # noqa: BLE001 —— 单轮失败保持现状，下轮重试
                logger.warning("同步失败（保持现状）：%s", exc)
            await asyncio.sleep(INTERVAL_S)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())
