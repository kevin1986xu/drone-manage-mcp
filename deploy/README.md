# 部署：Nacos MCP Registry + Higress AI 网关

为"MCP 工具层的注册发现 + 网关统一治理"提供运行底座。数据流：

```
backend 4 个 MCP server ──注册──▶ Nacos MCP Registry ──发现──▶ Higress AI 网关 ──▶ 外部消费方
     (streamable-http)          (工具清单+端点)         (鉴权/审计/限流/白名单)   (Agent/业务系统)
```

- **Nacos**：服务注册中心 + MCP Registry（登记每个 MCP server 的端点与工具清单）。
- **Higress**：AI 网关，从 Nacos 发现 MCP server，对外暴露一个**统一、可鉴权、可审计**的 MCP 入口，是生产环境防提示注入越权、满足政企审计的收口点。

> 已有 Nacos 的情况：drone-manage 现网已有 Nacos（`192.168.101.21:8998`）。若复用它，注释掉 compose 里的 `nacos` 服务，把 `higress` 与 backend 的 `NACOS_SERVER_URL` / `NACOS_SERVER_ADDR` 指向现网即可，不必再起一个。

---

## 一、前置条件

- Docker ≥ 24、Docker Compose v2
- 单机建议 ≥ 4C8G（Higress all-in-one 含控制面，占用较高）
- 放行端口：Nacos `8848`（控制台+OpenAPI+MCP Registry）、`9848`（gRPC，ref 模式需要）；Higress `80/443/8001`

## 二、启动

```bash
cd deploy
cp .env.example .env          # 按需改 HOST_IP、鉴权 token、端口
docker compose --env-file .env up -d
docker compose ps             # 等 nacos 变 healthy、higress 起来（约 1~2 分钟）
```

**验证 Nacos**：浏览器开 `http://<HOST_IP>:8848/nacos`。Nacos 3.x 首次访问控制台会**要求设置 admin 密码**（账号固定 `nacos`）——设好后回填到 `.env` 的 `NACOS_ADMIN_PASSWORD`（Higress 连 Nacos 用），`docker compose up -d` 重载 higress。

**验证 Higress 控制台**：浏览器开 `http://<HOST_IP>:8001`，首次进入设置管理员账号。

> 关于鉴权 token：`NACOS_AUTH_TOKEN` 必须是 Base64 串且解码后 ≥32 字节，否则 Nacos 启动报错。生成自己的：`echo -n "你的≥32位随机串" | base64`。

## 三、把 MCP server 注册进 Nacos

基础设施起来后，让 backend 的 4 个 MCP server 注册进来（见根 `README.md` §五）：

```bash
cd ../backend
# .env 里指向本机 Nacos（注意鉴权账号密码与 deploy/.env 一致）
#   NACOS_SERVER_ADDR=<HOST_IP>:8848
#   NACOS_USERNAME=nacos
#   NACOS_PASSWORD=<NACOS_ADMIN_PASSWORD>
#   MCP_SERVICE_IP=<本机对外 IP，VPN/多网卡时必填>
for s in drone_dispatch route_planning preflight flight_task; do
  uv run python -m app.mcp_servers.$s &
done
```

在 Nacos 控制台 **AI / MCP 管理** 下应能看到 4 个 server（`drone-dispatch-mcp` 等）及各自的工具清单。命令行核对：

```bash
TOKEN=$(curl -s -X POST "http://<HOST_IP>:8848/nacos/v1/auth/users/login" \
  -d "username=nacos&password=<密码>" | python3 -c "import sys,json;print(json.load(sys.stdin)['accessToken'])")
curl -s "http://<HOST_IP>:8848/nacos/v3/admin/ai/mcp/list?pageNo=1&pageSize=20&namespaceId=public" \
  -H "accessToken: $TOKEN"
```

## 四、配置 Higress（对接 Nacos MCP Registry）

Higress 的 MCP 治理主要在**控制台**完成。以下为标准流程（不同 Higress 版本菜单文案略有差异，按语义对应即可）：

### 1. 添加 Nacos 为服务来源（Service Source）

控制台 → **服务来源 / 服务发现** → 新增，类型选 **Nacos**：

| 字段 | 值 |
|---|---|
| 服务地址 | `nacos:8848`（compose 同网络内用服务名；跨机填 `<HOST_IP>:8848`）|
| 命名空间 | `public` |
| 认证 | 用户名 `nacos` / 密码同 `NACOS_ADMIN_PASSWORD` |

保存后 Higress 即可从该 Nacos 拉取服务与 **MCP Registry** 中的 MCP server。

### 2. 发布 MCP Server 路由

控制台 → **MCP 管理 / AI 网关 → MCP Server** → 新增，把上一步发现的 MCP server 发布为网关路由：

- **来源**：选 Nacos 服务来源里的 `route-planning-mcp` / `drone-dispatch-mcp` 等
- **匹配路径**：如 `/mcp/route-planning`（网关对外的统一前缀，消费方连这里，而非直连 :8102）
- **协议**：streamable-http（与 backend 一致）

发布后，外部 MCP 客户端连 `http://<HOST_IP>/mcp/route-planning` 即可，经由网关转发到后端 server——后端地址、端口对消费方完全隐藏。

### 3. 开启鉴权（防裸奔）

对 MCP 路由启用 **key-auth**（API Key）或 **jwt-auth** 插件：

- 控制台 → 该路由 → **插件** → 启用 `key-auth`
- 配置消费者（Consumer）与其可访问的路由白名单
- 消费方请求头带 `Authorization` / `x-api-key`，网关校验后才放行

> 这一步解决 drone-manage `/out/*` 当前无鉴权裸奔的问题——所有工具调用统一从网关收口鉴权。

### 4. 开启访问日志（审计留痕）

Higress 基于 Envoy，默认输出访问日志。政企审计需要**工具级留痕**时，为 MCP 路由挂日志插件或在网关全局日志里保留 `path / consumer / 时间 / 状态`。配合后端的 Agent 决策留痕（生产期 LangGraph checkpoint 落库），形成"谁、何时、调了哪个工具、传了什么参"的完整链路。

### 5. 工具白名单（防提示注入越权）

安全红线：即使 Agent 被提示注入诱导，也只能调到白名单内的工具。两层收口：

- **网关层**：消费者只被授权访问特定 MCP 路由（第 3 步的 Consumer 白名单）。
- **后端层**：高危写工具（`dispatch_drone` / `take_off`）本身要求人在环 confirm_token，网关放行也无法绕过（见根 `README.md` 安全红线测试）。

## 五、端到端验证

```bash
# 经网关调用（而非直连后端），列出工具并调一个只读工具
# 用支持 streamable-http 的 MCP 客户端，或 curl 走 MCP 协议握手
curl -s http://<HOST_IP>/mcp/drone-dispatch \
  -H "Authorization: Bearer <你在 Higress 配的 key>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

预期返回 `query_plots / find_nearby_drones / get_drone_status / dispatch_drone` 四个工具。

### 6. （可选）启用 9080 独立 MCP Registry 端口

核心的 MCP 注册与发现走 Nacos 主端口 8848 的 v3 admin API（本项目已实测，Higress 也从这里发现）。若需要 Nacos 3.2 的**独立 MCP Registry 端点**（`nacos.ai.mcp.registry.port=9080`，drone-manage 现网即开了此项），该配置无对应环境变量，需挂自定义属性文件：

```bash
# deploy/nacos-custom.properties
nacos.ai.mcp.registry.enabled=true
nacos.ai.mcp.registry.port=9080
```

然后在 compose 的 nacos 服务里取消挂载注释、补 `9080:9080` 端口映射即可。

## 七、生产化建议

- **Nacos 外部数据库**：standalone Derby 仅适合演示/单机。生产改 MySQL 8（Nacos 官方支持）或集群模式，compose 增加 `mysql` 服务并配 `SPRING_DATASOURCE_PLATFORM=mysql` 等环境变量；数据卷做持久化备份。
- **鉴权强化**：`NACOS_AUTH_TOKEN` 换足够长的随机串；控制台密码改强密码；Higress key 定期轮换。
- **TLS**：Higress 网关入口配证书走 HTTPS（443），内网也建议开启。
- **高可用**：Nacos 3 节点集群 + Higress 多副本（生产用 K8s Helm 部署更合适，见 higress.cn 官方文档）。
- **与 drone-manage 共用 Nacos**：若两套系统共用一个 Nacos，用**命名空间隔离**（如 `drone-manage` / `uav-agent` 各自 namespace），避免服务名冲突。

## 八、常见问题

| 现象 | 原因与处置 |
|---|---|
| MCP server 注册报 `client not connected` | 客户端 gRPC 端口（主端口+1000，如 9848）未放行；或用 `NACOS_ENDPOINT_MODE=direct`（纯 HTTP，无 gRPC 依赖，本项目默认）|
| Nacos 里注册的端点是 `172.x` 内网地址、Higress 连不通 | 后端 `.env` 设 `MCP_SERVICE_IP=<对外 IP>` 显式指定（VPN/多网卡机器自动探测会拿到隧道地址）|
| Higress 控制台看不到 MCP server | 确认第四步服务来源已保存且命名空间为 `public`；确认 Nacos 版本 ≥ 3.2（MCP Registry 需要）|
| Higress all-in-one 启动慢/占用高 | 正常，含完整控制面；资源紧张时生产用 K8s 拆分部署 |
