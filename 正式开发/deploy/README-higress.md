# Higress 对外网关部署（关一 · docs/07）

对外服务化的唯一入口：消费方 → Higress → 从现网 Nacos MCP Registry 发现的 8 个 MCP 域。
本机 docker standalone 部署，**2026-07-21 全链路验证通过**（经网关 tools/list 返回 200）。

## 0. 最终可用状态（TL;DR）

- 对外访问：`POST http://<HOST>:8080/mcp/<server名>/mcp` + header `X-API-Key: <租户key>`
  （消费者鉴权已启用：网关只认消费者 key，如 tenant-demo 的 `demo-key-2026-a1b2c3`，见 §4）
  - 例：`http://localhost:8080/mcp/uav-alert-mcp/mcp`
  - SSE 协议在末尾加 `/sse`
- Higress 从 Nacos **MCP Registry** 自动发现（服务来源开「MCP Server 功能」），
  后端地址/端口对消费方隐藏；**不需要**手动「创建 MCP 服务」选后端。
- 路由不显示在「路由配置」列表里（Higress 提示如此），MCP server 在「MCP 管理」页。

## 1. 起容器

```bash
cd 正式开发/deploy
docker compose -f higress-standalone.docker-compose.yml up -d
```

| 宿主端口 | 用途 | 容器内端口 |
|---|---|---|
| 8080 | 网关 HTTP 入口（对外 MCP 端点） | 8080（envoy） |
| 8443 | 网关 HTTPS 入口 | 8443 |
| **8888** | **Higress 控制台** → http://localhost:8888 | **8001** |

> ⚠ all-in-one 容器内：网关 8080/8443、控制台 **8001**（别按标准版的 80/8080 记，
> 映射错会打到网关数据面，浏览器只见 "Welcome to Higress"）。内置配置存储，不污染现网 Nacos。

## 2. 前置条件（血泪，缺一不可）

1. **Nacos gRPC（主端口+1000）必须是有效 gRPC**：Higress 走 Nacos SDK gRPC=`8998+1000=9998`
   订阅服务/读 MCP 配置。验证：`httpx.get("http://<nacos>:9998/")` 应抛 **RemoteProtocolError**
   （=HTTP2/gRPC 活）；若返回 HTTP/1.1 则该端口不是有效 gRPC（曾因此 controller 卡
   `client not connected, STARTING`、MCP 管理空、网关 404）。9080 是 MCP Registry 端口
   （`nacos.ai.mcp.registry.port`），同为 gRPC。
2. **容器到 Nacos 9998/9080 + 到后端 mcp-services 都要通**：Mac docker 容器实测可达宿主
   en0（`.116:820x`）与现网 Nacos。（`/dev/tcp` 在容器 busybox 不可靠，用 wget/httpx 验。）
3. **endpoint 实例存在**：MCP server 规格（postgres 持久）引用 `mcp-endpoints` 组的
   `<name>::<ver>` 服务实例（ephemeral）。**Nacos 或 mcp-services 重启后 endpoint 实例会丢
   → 网关 503**，需重启 mcp-services（runner）重新注册找回：
   ```bash
   cd mcp-services && pkill -f uav_mcp.runner; nohup .venv/bin/python -m uav_mcp.runner &
   # 等日志 "Registry 已注册/更新" 满 8 条
   ```

## 3. 控制台配置

浏览器 `http://localhost:8888`：

1. **初始化管理员账号**（首次；本环境 admin/Geostar2025!）。
2. **服务来源**（服务来源 → 创建）：
   - 类型 `Nacos 3.x`、注册中心地址 `192.168.101.21`、端口 `8998`
   - **是否开启认证：是** → 用户名 `nacos`、密码 `Geostar2025!`（**现网 Nacos 开了认证，
     不开则 403、拉不到任何东西**——曾卡在此）
   - Nacos 命名空间ID `public`、**Nacos 服务分组列表 `DEFAULT_GROUP`**
   - **是否启用 MCP Server 功能：是**、MCP Server 路由路径前缀 `/mcp`
   - 关联域名留空（=全部域名）
   保存后，Higress 从 MCP Registry 拉到 8 个 server，出现在「AI网关管理 → MCP 管理」。
3. **验证**：`curl` / httpx `POST http://localhost:8080/mcp/uav-alert-mcp/mcp`
   带 `X-API-Key` + body `{"jsonrpc":"2.0","id":1,"method":"tools/list"}` → 200 返回工具。

## 4. 消费者鉴权（关一多租户，**2026-07-21 已落地**）

架构（docs/07 §4.1 阶段1）：**租户 key 网关校验后原样透传**，后端 `UAV_TENANT_KEYS`
查表识别租户（注入 `scope.uav_tenant` 供审计）——不做 header 改写，两层都认识租户 key。

1. **建消费者**（控制台 → 消费者管理）：`tenant-demo`，Key Auth，
   令牌来源=自定义 HTTP Header `X-API-Key`，key `demo-key-2026-a1b2c3`。
2. **开全局认证**：⚠ 这版控制台**没有**全局认证开关，且注册中心自动发现的 MCP server
   **不能**用「MCP 管理」的消费者授权 API（它要求 `mcp-server-<name>.internal` 路由，
   仅控制台手建 server 有）。唯一路径是改 key-auth 插件资源（容器内嵌 apiserver，
   匿名可写；不支持 merge-patch，须 GET→改→PUT 整个对象）：
   ```bash
   # GET 资源 → 把 spec.defaultConfig.global_auth 改为 true → PUT 回去
   docker exec uav-higress curl -sk https://localhost:18443/apis/extensions.higress.io/v1alpha1/namespaces/higress-system/wasmplugins/key-auth.internal
   ```
   watch 热更新，秒级生效，无需重启。console 自身的 default 路由已有豁免
   matchRule（`configDisable: true`），不受影响。
3. **后端识别租户**：`mcp-services/.env` 加
   `UAV_TENANT_KEYS={"demo-key-2026-a1b2c3": {"tenant": "tenant-demo", "scopes": ["*"]}}`
   后重启 runner（命令见 §2 前置条件 3）。
4. **验证结果**：8 个域带租户 key 全 200；无 key/错 key/后端 key
   `uav-m1-test-key-2026` 直打网关全 401（后端统一 key 不再对外可用，符合预期）。
   完整冒烟：`mcp-services/scripts/smoke_gateway.py`（8 域完整 MCP 会话 +
   真实工具调用穿透平台 + 负面矩阵 + 直连兼容，16 项）。

## 4.5 按租户限流（2026-07-21 已落地）

`key-rate-limit` 插件（单机令牌桶；容器无 Redis，cluster 版用不了），按
`X-API-Key` 的值限流，走控制台标准插件 API（这个 API 是正路，不用动 apiserver）：

```bash
curl -b <cookie> -X PUT http://localhost:8888/v1/global/plugin-instances/key-rate-limit \
  -H 'Content-Type: application/json' -d '{
  "pluginName": "key-rate-limit", "pluginVersion": "1.0.0",
  "scope": "GLOBAL", "enabled": true,
  "rawConfigurations": "limit_by_header: X-API-Key\nlimit_keys:\n- key: demo-key-2026-a1b2c3\n  query_per_minute: 120\n"}'
```

实测：130 连发 → 精确 120×200 + 10×429，窗口 1 分钟重置。新租户在
`limit_keys` 加条目即可；不在表内的 key 不限流（但早被 key-auth 401 挡了）。

## 5. 网络收口 + 两网关区别

- **收口（信任边界②，待做）**：mcp-services 限制 820x 仅网关可达。本机 demo 环境
  是 macOS 宿主跑服务 + docker 跑网关，动 pf 防火墙风险大不值当；生产（Linux 部署）
  用 iptables 只放行网关 IP，或 mcp-services 绑定内网网卡 + 安全组。做完后
  `smoke_gateway.py` §4 直连兼容项应改为「预期失败」。
- **别混淆两个网关**：Higress（本机 8080，关一，消费方→工具）vs 平台网关
  （demo-lt:11412，关三，工具→drone-manage，admin 认证）。

## 6. 排障速查（本次踩坑顺序）

| 症状 | 根因 | 解法 |
|---|---|---|
| MCP 管理空、网关 404 | 服务来源认证=否（Nacos 要认证） | 开认证 nacos/Geostar2025! |
| controller 日志 `STARTING` | 9998 非有效 gRPC（返 HTTP1） | 修 Nacos gRPC（重新部署/端口转发） |
| 网关 503 | endpoint 实例丢（Nacos/mcp-services 重启） | 重启 mcp-services 重新注册 |
| 网关 404（重注册后） | Higress 缓存旧路由 | 重启 Higress 强制重新同步 |
| 网关 401（空 body） | 没带/带错租户 key（global_auth 开启后后端统一 key 也不行） | 带消费者 key（如 tenant-demo 的） |
| 后端 401（`{"error": ...}` body） | 网关放行但后端 UAV_TENANT_KEYS 没这个 key | .env 加 key→租户映射，重启 runner |
| runner 起不来 `[Errno 48] 8201` | pkill 后老进程未退干净就起新的 | 等 1-2 秒确认 `lsof -iTCP:8201` 空了再起 |
| 网关 429 | 该租户 key 触发限流（120/min，压测后常见） | 等 1 分钟窗口重置；调 §4.5 limit_keys |

## 停/日志

```bash
docker exec uav-higress tail -f /var/log/higress/controller.log   # controller（发现/同步）
docker compose -f higress-standalone.docker-compose.yml down       # 加 -v 删数据卷
```
