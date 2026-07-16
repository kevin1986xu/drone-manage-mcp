# 部署与接入 DeerFlow

## 组件清单

| 组件 | 来源 | 端口 |
|---|---|---|
| DeerFlow 2.0（Gateway+Web） | 官方仓库 `make dev` 或其 docker | 8001 / 3000 |
| uav-mcp-services（四域） | ../mcp-services | 8201-8204 |
| 审批服务 | ../uav_extensions | 8205 |
| Nacos 同步桥（可选） | ../uav_extensions | — |
| Nacos 3.x | 现网 192.168.101.21:8998 | — |
| drone-manage（Java） | 现网 192.168.101.21:10009 | — |

我方服务：`docker compose -f deploy/docker-compose.yml up -d --build`
（同步桥按需：`--profile bridge`）。本地开发直接 `python -m ...` 起三个进程即可。

## DeerFlow 接入步骤（零 fork）

1. **装扩展包进 DeerFlow 的 Python 环境**：
   ```bash
   cd deer-flow/backend && uv pip install -e /path/to/正式开发/uav_extensions
   ```
2. **config.yaml**：以官方 example 为基底，合并本目录 `config.yaml.example` 的
   models（qwen3.7-max）与 subagents（flight-ops / recognition 受限工具集）段落。
3. **extensions_config.json**：用本目录版本（四个 uav server 直连 + 两个拦截器）。
   - `$UAV_MCP_API_KEY` 由宿主环境解析，与 mcp-services 侧同值；
   - 启用同步桥后，四个 uav-* 条目改由桥自动维护，人工不用写。
4. **skills**：把 `../skills/*` 拷贝或软链到 deer-flow 的 `skills/custom/` 下。
5. 起 DeerFlow，对话验证（详见 ../poc/runbook.md）。

## 网络注意事项

- **注册 IP**：mcp-services 自动探测的是到 Nacos 的出口网卡地址，VPN/Docker 环境
  可能探到 172.x（本机开发实测如此）。现场部署必须设 `MCP_SERVICE_IP=<对外可达 IP>`。
- **鉴权链**：DeerFlow →（X-API-Key）→ mcp-services →（HTTP）→ drone-manage；
  审批服务管理口用 `X-Admin-Key`。内网信任边界之外的防绕过（多消费方收口/限流）
  当前不设防，需要时在工具面插 Higress（见 docs/02 §2.4）。
- **真实起飞开关**：compose 中 `UAV_REAL_PUBLISH` 恒为 0；开启只能在现场人工改
  服务环境并经安全审批。
