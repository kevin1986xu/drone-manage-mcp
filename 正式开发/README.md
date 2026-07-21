# 正式开发工作区

本目录用于**无人机飞控 Agent 平台的正式版开发**（区别于仓库根目录的 P0/P1 演示版）。

## 目标形态（一句话）

以 **DeerFlow 2.0** 为 SuperAgent 底座，无人机业务能力沉淀为 **MCP 工具 + Skills**，
通过对话（Web / 企微 / 钉钉等）完成无人机飞控、批量巡查、智能识别等场景；
**Nacos 为注册与发现中枢，无人机平台（Java）零改动**。

## 定稿链路

```
DeerFlow 2.0 ──MCP──▶ [核心业务域：直连/同步桥]───▶ FastMCP 服务(Python) ──HTTP──▶ 无人机平台(Java，零改动)
     │                        ▲                            │
     └─MCP─▶ nacos-mcp-router ┴──发现──▶ Nacos 3.x ◀──注册──┘
             （长尾/新能力动态发现）
```

内部链路（DeerFlow→工具）不经网关：治理 = DeerFlow 客户端 interceptors（注入
API key）+ FastMCP 服务端校验 + 工具内 confirm_token 人在环（框架无关）。
**对外服务化**（外部消费方→工具）另走 Higress 网关：Nacos MCP Registry 服务发现 +
消费者鉴权（多租户 key）+ 按租户限流，已全链路落地（docs/07 + deploy/README-higress.md）。

## 目录结构与状态（M1+M2+M3 + P0 四新域 + 对外网关 + P1 三新域已落地，2026-07-21）

| 目录 | 内容 | 状态 |
|---|---|---|
| [mcp-services/](mcp-services/) | **十一域** FastMCP 业务服务（调度/航线/预检/飞行 + 空域/告警/媒体/排期 + **直播/飞控/机场调试**；真实平台直连、多租户 key 鉴权、紧急白名单⚡、设备级操作锁、Nacos 注册） | ✅ 单测 41 + 契约冒烟 + 网关冒烟（现网实测；飞控/调试写面为真机联调项） |
| [uav_extensions/](uav_extensions/) | 审批服务（token 签发在 Agent 之外）、DeerFlow 拦截器（硬白名单+审计）、Nacos 同步桥、**GIS 前端 BFF**（bff.py，8300） | ✅ 单测 12/12；桥/审批/BFF 现网实测 |
| [skills/](skills/) | 8 个：plot-inspection / batch-patrol / duty-watch / evidence-report / smart-scheduling / **emergency-response** / **dock-maintenance** / smart-recognition(占位) | ✅ 对话实测；可见性矩阵见 docs/05 §6.1 |
| [deploy/](deploy/) | config.yaml.example（子代理受限工具集）/ extensions_config.json / docker-compose / 接入指南 / **Higress 对外网关**（README-higress.md） | ✅ 网关鉴权+限流实测 |
| [poc/](poc/) | POC runbook（P1-P5 全过，GO） | ✅ |
| [webui/](webui/) | DeerFlow 原生 Web UI 确认卡片（组件软链+路由拷贝+30 行 patch，install.sh 幂等安装） | ✅ 3000 端口对话实测全流程 |
| [eval/](eval/) | **76 条**评测集（41-61 四新域；62-76 P1 三域+紧急白名单注入反向）+ Gateway 跑批 runner | 全量跑批结果见 eval/last_run.json |

## 本地全链路启动（M2/M3 实测口径）

```bash
# 1. 十一域 MCP（8201-8204、8206-8212；.env 里 MCP_SERVICE_IP 必须是当前 en0 IP，见排障①）
cd mcp-services && .venv/bin/python -m uav_mcp.runner

# 2. 审批服务（8205）
cd uav_extensions && APPROVAL_ADMIN_KEY=... \
  ../deerflow/backend/.venv/bin/python -m uav_extensions.approval_service

# 3. Nacos 同步桥（强烈建议常驻：IP 变更自愈，见排障①）
cd uav_extensions && NACOS_SERVER_ADDR=192.168.101.21:8998 NACOS_PASSWORD=... \
  UAV_MCP_API_KEY=... ../deerflow/backend/.venv/bin/python -m uav_extensions.nacos_bridge

# 4. DeerFlow Gateway（8001）
cd deerflow/backend && DEER_FLOW_AUTH_DISABLED=1 DASHSCOPE_API_KEY=<LLM key> \
  UAV_MCP_API_KEY=... PYTHONPATH=. .venv/bin/python -m uvicorn app.gateway.app:app --port 8001

# 5. GIS BFF（8300，演示版前端零改动接 DeerFlow）
cd uav_extensions && UAV_MCP_API_KEY=... APPROVAL_ADMIN_KEY=... \
  ../deerflow/backend/.venv/bin/python -m uav_extensions.bff

# 6. 前端（演示版，BACKEND_PORT 切到 BFF）
cd ../frontend && BACKEND_PORT=8300 npm run dev   # http://localhost:5173

# 评测（必须用 mcp-services 的 venv，清理步骤依赖 mcp+httpx）
UAV_MCP_API_KEY=... mcp-services/.venv/bin/python eval/run_eval.py        # 全量 76 条
UAV_MCP_API_KEY=... mcp-services/.venv/bin/python eval/run_eval.py 3 9   # 指定 id
```

## 排障速查（都是踩过的坑）

1. **本机 en0 IP 是动态的（实测一天变两次）**。症状：评测/对话全部命中 0、
   Gateway 日志出现 `Skipping MCP server ... after tool discovery failed`、
   模型去摸 `read_file` 等内置工具。处理：改 `mcp-services/.env` 的
   `MCP_SERVICE_IP` → 重启 mcp-services（重注册 Nacos）→ **同步桥自动改写
   DeerFlow 配置并热重载**（没跑桥就手改 `deerflow/extensions_config.json`
   四个 url 并重启 Gateway）。Gateway 对"发现失败"会缓存跳过，修好后必须重启。
2. **改 SKILL.md / 工具描述后**：mcp-services 和 Gateway 都要重启（skill 与
   工具列表各有缓存）；同步桥的热重载只覆盖 MCP 配置，不覆盖 skills。
3. **重启 mcp-services 别用 `lsof -ti:8201`**——它会把连着这些端口的客户端
   （桥/BFF）一起列出来误杀，用 `lsof -ti TCP:8201 -sTCP:LISTEN`。
4. VPN 到 192.168.101.x 会整段抖断，排障先 `curl` Nacos；评测单条耗时
   数百秒基本是链路问题不是模型问题。
5. 编辑器 PUT 航点必须是 `{lon,lat}` 对象数组（服务端已加 422 校验）。

与演示版**代码完全独立**（不 import 根目录任何模块）；十一域共享单进程状态，
端口 8201-8204、8206-8212，审批 8205（与演示版 8101+ 错开）。

## 文档索引

| 文档 | 内容 |
|---|---|
| [docs/01-DeerFlow2.0-调研报告.md](docs/01-DeerFlow2.0-调研报告.md) | 一手代码调研：架构、Skills、MCP、HITL 缺口、消息通道、国产化、社区状态 |
| [docs/02-架构定稿与建议.md](docs/02-架构定稿与建议.md) | 定稿链路、router 模式取舍、网关问题澄清、治理口径、现有资产迁移映射、风险清单 |
| [docs/03-POC计划.md](docs/03-POC计划.md) | 1~2 天 POC 的验证点、步骤与通过标准 |
| [docs/04-实现方案.md](docs/04-实现方案.md) | 零 fork 插件式集成、目录结构、审批设计、同步桥、BFF、子代理划分、里程碑 |
| [docs/05-MCP工具与Skills扩展规划.md](docs/05-MCP工具与Skills扩展规划.md) | 平台 8 模块代码盘点 → 11 个新 MCP 域分组（含任务排期调度、机场调试）、8 个新 skill、紧急动作白名单、权限透传、P0-P2 路线 |
| [docs/06-MCP功能明细.md](docs/06-MCP功能明细.md) | 功能字典：主线八域（航线/任务/调度/飞行/直播/操控/debug/成果）+ 安全/智能/管理三支撑面,约 90 项细分功能逐项列参数/风险/状态 |
| [docs/07-对外服务化与鉴权架构.md](docs/07-对外服务化与鉴权架构.md) | 三关鉴权（接入/操作/回源）+ Higress 对外网关架构与落地状态 |

## 与演示版的关系

仓库根目录（backend/frontend/deploy/eval）是已交付的演示版，**继续维护、不动**。
正式版在本目录下推进；演示版的以下资产将平移复用（详见 02 文档 §迁移映射）：

- 4 个 FastMCP server（已注册 Nacos 3.2.1、已实测消费闭环）
- 工具内 confirm_token 人在环安全机制（含伪造 token 拦截实测）
- 38 条评测集与调优过的工具描述（llm 命中率 91%+）
- Nacos 注册模块、drone-manage API 客户端、Open-Meteo 气象自查
