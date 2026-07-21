# Higress 对外网关部署（关一 · docs/07）

对外服务化的唯一入口：消费方 → Higress → 从现网 Nacos 发现的 8 个 MCP 域。
承担多租户鉴权、限流、审计。本机 docker standalone 部署。

## 1. 起容器（已完成）

```bash
cd 正式开发/deploy
docker compose -f higress-standalone.docker-compose.yml up -d
```

| 端口 | 用途 | 备注 |
| 宿主端口 | 用途 | 容器内端口 |
|---|---|---|
| 8080 | 网关 HTTP 入口（对外聚合的 MCP 端点） | 8080（envoy） |
| 8443 | 网关 HTTPS 入口 | 8443 |
| **8888** | **Higress 控制台** → http://localhost:8888 | **8001**（all-in-one 控制台端口，非标准版的 8080） |

> ⚠ all-in-one 容器内：网关是 8080/8443、控制台是 **8001**（别按标准 Higress 的
> 网关 80、控制台 8080 记——映射错会打到网关数据面，浏览器只见 "Welcome to Higress"）。

- 用**内置**配置存储（不接外部 Nacos 存 higress 自身配置，避免污染现网生产 Nacos）；
- 现网 Nacos 仅作「服务来源」，控制台配（下一步）；
- 已验证：容器可访问现网 Nacos（192.168.101.21:8998）并用 `nacos/Geostar2025!` 登录成功。

## 2. 控制台配置（首次需浏览器初始化，五步）

浏览器打开 `http://localhost:8888`：

0. **⚠ 先注册普通服务实例（关键前置，否则「创建 MCP 服务」的后端服务下拉是空的）**：
   mcp-services 只注册在 Nacos **MCP Registry（ai/mcp）**，而 Higress v2.2.3「创建
   MCP 服务 → 后端服务」读的是**普通服务列表（ns/instance）**，两套注册面不通。
   跑一次补注册（Nacos 3.2.1 只认 v3/admin/ns/instance，v1 报 501、v2 报 404）：
   ```bash
   cd mcp-services && PYTHONPATH=src .venv/bin/python scripts/register_gateway_instances.py
   ```
   注册 8 个 persistent 普通实例（IP=MCP_SERVICE_IP，Higress 容器可达的宿主 en0；
   IP 变更后重跑）。Nacos 普通服务列表随即出现 8 个纯名 `uav-*-mcp`。
1. **初始化管理员账号**（首次访问，Higress 要求设一个控制台账号；本环境 admin/Geostar2025!）。
2. **服务来源**：新增 Nacos3 类型来源 → 地址 `192.168.101.21:8998`、用户名 `nacos`、
   密码 `Geostar2025!`、命名空间 `public`。保存后「服务列表」出现 8 个 `uav-*-mcp`。
3. **创建 MCP 服务**（AI 网关管理 → MCP 管理 → 创建 MCP 服务）：服务类型
   `streamable-http`、路径 `/mcp`、**后端服务**下拉选纯名 `uav-*-mcp`（非 ::0.1.0 后缀的）；
   对外访问 `http://<HOST>:8080/mcp-servers/<服务名>`，后端地址端口对消费方隐藏。
4. **消费者鉴权**（关一多租户）：建消费者 → 分配 key（对应 mcp-services 的
   `UAV_TENANT_KEYS` 租户），在路由上启用 key-auth / JWT 插件。
5. **限流 + 审计**：按消费者配速率；开访问日志。

> 完整的对外治理（多租户/限流/审计）落在这一层；mcp-services 代码零改。
> 工具级白名单也在此做——MCP 的 tool 名在 JSON-RPC body 里，服务端中间件看不到。

## 3. 网络收口（信任边界②）

Higress 就位后，mcp-services 应只收网关流量，堵直连 820x 端口绕过：
生产用防火墙/网络策略限制 820x 仅网关可达；更严用 mTLS。

## 4. 与平台回源网关（关三）区别

**别混淆两个网关**：
- **Higress（本机 8080）**：关一，对外入口，消费方 → mcp-services；
- **平台网关（demo-lt:11412）**：关三，回源出口，mcp-services → drone-manage（admin 认证）。

## 停/日志

```bash
docker compose -f higress-standalone.docker-compose.yml logs -f higress
docker compose -f higress-standalone.docker-compose.yml down      # 加 -v 删数据卷
```
