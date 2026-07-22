# Docker 部署——核心三件套（生产 Linux）

对外交付的核心能力层容器化：**mcp-services（十一域工具）+ 审批服务 + UI 服务**。
Gateway/BFF/前端是验证脚手架，不在本编排（按需本机跑或后续单独镜像）。
Higress 对外网关用独立 compose（`../higress-standalone.docker-compose.yml`）。

## 镜像与容器

| 容器 | 镜像 | 端口 | 对宿主暴露 | 说明 |
|---|---|---|---|---|
| uav-mcp-services | uav-mcp-services | 8201-8204、8206-8212 | ✅ | 供 Higress 经 Nacos 发现、消费方经网关到达 |
| uav-ui | uav-approval（同镜像） | 8213 | ✅ | 确认卡片/视图页，浏览器打开 view_url |
| uav-approval | uav-approval | 8205 | ❌ 仅内网 | confirm_token 签发，服务间调用 |

## 部署步骤

```bash
cd 正式开发/deploy/docker
cp .env.docker.example .env.docker
vim .env.docker                       # 填 §下方「必改项」
docker compose --env-file .env.docker up -d --build
docker compose --env-file .env.docker ps
```

### .env.docker 必改项（生产 Linux）

| 变量 | 填什么 | 为什么 |
|---|---|---|
| `MCP_SERVICE_IP` | **宿主机内网 IP**（`ip addr` 查，非容器 IP、非 127.0.0.1） | mcp 注册进现网 Nacos 的地址；Higress/消费方经此 IP+端口到达。容器端口已映射到宿主，故填宿主 IP 即可达 |
| `UI_PUBLIC_BASE` | **对外可达地址**（`http://<宿主IP>:8213` 或反代域名） | view_url 由用户浏览器打开，必须外部可达——不能是容器名/内网名 |
| `NACOS_PASSWORD` / `DRONE_LOGIN_PASSWORD` | 现网凭据 | — |
| `UAV_MCP_API_KEY` | 服务间 key | 三容器一致 |
| `APPROVAL_ADMIN_KEY` | 强随机值 | 生产必配 |

其余（Nacos/平台地址、租户 key、写开关）沿用示例默认或按现网调整。

## 网络模型（关键，别踩坑）

```
                  现网 Nacos(192.168.101.21:8998) ◀─注册─┐
                  现网平台(demo-lt:11412) ◀─回源────────┤ 容器直连外部（生产 Linux 默认可出网）
                                                          │
  ┌───────────── uav-net (bridge) ──────────────┐        │
  │  mcp-services ──http://approval:8205──▶ approval      │  服务间：容器名互连
  │  ui ──────────http://approval:8205──▶ approval        │
  └──────────────────────────────────────────────┘
        │ ports 映射到宿主                    │ ports 8213
        ▼                                     ▼
   宿主 MCP_SERVICE_IP:820x                宿主 :8213
   （Higress 经 Nacos 发现后到达）         （浏览器开 view_url）
```

三条地址规则记牢：
1. **服务间**（mcp/ui → 审批）用**容器名** `http://approval:8205`——compose 已配死，不用改。
2. **`MCP_SERVICE_IP`** 注册给外部发现，用**宿主内网 IP**（配合端口映射）。
3. **`UI_PUBLIC_BASE`** 进 view_url 给浏览器，用**外部可达地址**。
   ——2、3 若填成容器内地址，外部一律打不开，这是最常见错误。

## 验证

```bash
# 容器起来
docker compose --env-file .env.docker ps          # 三个 Up

# mcp 注册满 11 条
docker compose --env-file .env.docker logs mcp-services | grep -cE "Registry 已注册|Registry 已更新"

# 工具面（宿主上直接打，需带 key）
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://<宿主IP>:8201/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H 'X-API-Key: <UAV_MCP_API_KEY>' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'      # 200

# 审批（仅内网，从容器里验）
docker compose --env-file .env.docker exec ui \
  python -c "import httpx;print(httpx.get('http://approval:8205/healthz').json())"

# UI 页（浏览器）
curl -s http://<宿主IP>:8213/healthz                        # {"status":"ok"}
```

## 运维

```bash
docker compose --env-file .env.docker logs -f mcp-services   # 跟日志
docker compose --env-file .env.docker restart mcp-services   # 重启单服务
docker compose --env-file .env.docker down                   # 停
docker compose --env-file .env.docker up -d --build          # 改代码后重建
```

## 与本机进程版的差异（迁移注意）

- 服务间不再用 `127.0.0.1:820x`，改容器名；`.env` 里的 `APPROVAL_BASE`/`UAV_UI_BASE`
  由 compose 覆盖，别再手填 127。
- 本机版 mcp 端口本地监听即可；容器版必须 `ports` 映射到宿主，否则 Higress 到不了。
- **别把宿主的 `mcp-services/.env` 挂进容器**——里面是本机 en0 IP + 127 地址，会覆盖
  compose 的正确值。环境变量统一走 `.env.docker`。
- 收口（docs/07 信任边界②）：生产用防火墙只放行 Higress 到 mcp 端口；审批 8205
  本编排已不对宿主暴露（仅 uav-net 内）。

## 完整自包含 stack（Nacos + Higress + 三件套）

`docker-compose.full.yml` 把 **Nacos 与 Higress 也纳入**，全栈一个网络自包含。
与上面「核心三件套 + 外部现网 Nacos」互为两种部署形态，按需选一：

| | docker-compose.yml（核心三件套） | docker-compose.full.yml（完整） |
|---|---|---|
| Nacos | 用现网（客户已有） | **自带**（干净 Nacos 3.x，规避现网 gRPC 9998 坑） |
| Higress | 独立部署/复用现网 | **自带** |
| mcp 注册地址 | 宿主内网 IP（MCP_SERVICE_IP） | **容器名**（同网 Higress 直接发现，无需宿主 IP） |
| 对外暴露 | mcp 端口 + ui | 仅 Higress 8080/8888 + ui 8213 |
| 适用 | 客户 Nacos gRPC 健康、想复用 | 想要开箱即用、隔离于客户 Nacos |

平台 drone-manage 两种都是**外部现网**（客户已有，不部署）。

### 完整 stack 部署

```bash
cd 正式开发/deploy/docker
cp .env.docker.example .env.docker
# 生成 Nacos token 填进 NACOS_AUTH_TOKEN：
python3 -c "import secrets,base64;print(base64.b64encode(secrets.token_bytes(32)).decode())"
vim .env.docker        # 填 NACOS_AUTH_TOKEN / HIGRESS_ADMIN_PASS / 平台凭据 / key

docker compose -f docker-compose.full.yml --env-file .env.docker up -d --build

# 首次：浏览器开 http://<宿主>:8888 初始化 Higress 管理员（密码=HIGRESS_ADMIN_PASS）
./init-higress.sh      # 自动配服务来源 + 消费者 + 全局认证 + 限流（幂等）
```

`init-higress.sh` 固化了 docs/07 + README-higress 的全部网关配置动作
（登录→服务来源 nacos3→消费者 key-auth→global_auth→key-rate-limit），
读 `.env.docker` 取凭据，可重复跑。

### 完整 stack 验证

```bash
# 经网关带租户 key 调工具（对外唯一入口）
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://<宿主>:8080/mcp/uav-alert-mcp/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H 'X-API-Key: <HIGRESS_CONSUMER_KEY>' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'   # 200
```

> ⚠ Higress↔Nacos 的 MCP 服务发现（自带 Nacos 的 gRPC/MCP Registry）建议在
> 目标机首次部署时按上面验证确认一遍——它依赖 Nacos 3.x 的 MCP Registry
> 特性与 Higress 服务来源配置正确对接（本仓库已固化配置，但跨环境镜像版本
> 差异需实测）。mcp 镜像构建与容器内十一域启动已本机验证通过。

## 下一步（不在本批）

- Gateway + 前端镜像（DeerFlow 上游克隆构建较重）；
- 收口细化：mcp/审批完全不映射宿主（完整 stack 已仅 Higress 对外）；
- 镜像推私有 registry + CI 构建。
