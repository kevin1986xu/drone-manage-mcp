"""给现网 Nacos 注册 8 个 MCP 域的**普通服务实例**（供 Higress 服务来源发现）。

背景：mcp-services 用 nacos_registry 走 ai/mcp Registry 注册（MCP 规格），
Higress v2.2.3 的「创建 MCP 服务 → 后端服务」下拉读的是**普通服务列表**（ns/instance），
两套注册面不通，所以 Higress 发现不到。本脚本补注册普通实例（persistent，免心跳），
IP 用 MCP_SERVICE_IP（Higress 容器可达的宿主 en0），端口为各域端口。

用法：PYTHONPATH=src .venv/bin/python scripts/register_gateway_instances.py
IP 变更后重跑即可（会覆盖同名实例）。
"""

from __future__ import annotations

import sys

sys.path.insert(0, "src")

import httpx  # noqa: E402

from uav_mcp import config  # noqa: E402

DOMAINS = {
    "uav-drone-dispatch-mcp": 8201, "uav-route-planning-mcp": 8202,
    "uav-preflight-mcp": 8203, "uav-flight-task-mcp": 8204,
    "uav-airspace-mcp": 8206, "uav-alert-mcp": 8207,
    "uav-media-mcp": 8208, "uav-task-schedule-mcp": 8209,
}
GROUP = "DEFAULT_GROUP"


def main() -> int:
    addr = config.NACOS_SERVER_ADDR
    ip = config.MCP_SERVICE_IP
    if not addr or not ip:
        print("需配置 NACOS_SERVER_ADDR 与 MCP_SERVICE_IP")
        return 1
    base = f"http://{addr}/nacos"
    tok = httpx.post(f"{base}/v1/auth/login",
                     data={"username": config.NACOS_USERNAME, "password": config.NACOS_PASSWORD},
                     timeout=10).json()["accessToken"]
    ns = "" if config.NACOS_NAMESPACE == "public" else config.NACOS_NAMESPACE
    ok = 0
    for name, port in DOMAINS.items():
        params = {
            "accessToken": tok, "serviceName": name, "groupName": GROUP,
            "ip": ip, "port": port, "namespaceId": ns,
            "ephemeral": "false",  # persistent：免心跳，Nacos 持久保留
            "metadata": '{"scheme":"http","transport":"streamable-http","path":"/mcp"}',
        }
        # Nacos 3.2.1：v1 被禁(501)、v2 无此端点(404)，用 v3 admin API
        r = httpx.post(f"{base}/v3/admin/ns/instance", params=params, timeout=10)
        ok_resp = r.status_code == 200 and (r.json().get("code") == 0 if r.headers.get("content-type","").startswith("application/json") else "ok" in r.text.lower())
        status = "✓" if ok_resp else f"✗ {r.status_code} {r.text[:50]}"
        print(f"  {name} @ {ip}:{port}  {status}")
        if "✓" in status:
            ok += 1
    print(f"注册 {ok}/{len(DOMAINS)} 个普通实例（Higress 服务来源可发现）")
    return 0 if ok == len(DOMAINS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
