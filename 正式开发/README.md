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

全程不引入 Higress；工具面治理 = DeerFlow 客户端 interceptors（注入 API key）
+ FastMCP 服务端校验 + 工具内 confirm_token 人在环（框架无关）。

## 目录结构与状态（M1+M2+M3 已落地，2026-07-17）

| 目录 | 内容 | 状态 |
|---|---|---|
| [mcp-services/](mcp-services/) | 四域 FastMCP 业务服务（真实平台直连、API key 鉴权、Nacos 注册、瘦身返回、hydrate 15s TTL 缓存、编辑器 REST） | ✅ 单测 18/18 + 端到端冒烟 15/15（现网实测） |
| [uav_extensions/](uav_extensions/) | 审批服务（token 签发在 Agent 之外）、DeerFlow 拦截器（硬白名单+审计）、Nacos 同步桥、**GIS 前端 BFF**（bff.py，8300） | ✅ 单测 12/12；桥/审批/BFF 现网实测 |
| [skills/](skills/) | plot-inspection / batch-patrol / smart-recognition(占位) SKILL.md | ✅ 对话实测（含单项检查映射与行动纪律调优） |
| [deploy/](deploy/) | config.yaml.example（子代理受限工具集）/ extensions_config.json / docker-compose / 接入指南 | ✅ |
| [poc/](poc/) | POC runbook（P1-P5 全过，GO） | ✅ |
| [webui/](webui/) | DeerFlow 原生 Web UI 确认卡片（组件软链+路由拷贝+30 行 patch，install.sh 幂等安装） | ✅ 3000 端口对话实测全流程 |
| [eval/](eval/) | 40 条评测集 + Gateway 跑批 runner（自动清理平台测试航线） | ✅ 工具命中/传参双百（38 条 2026-07-16；+成果报告/历史 2 条 2026-07-17） |

## 本地全链路启动（M2/M3 实测口径）

```bash
# 1. 四域 MCP（8201-8204；.env 里 MCP_SERVICE_IP 必须是当前 en0 IP，见排障①）
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
UAV_MCP_API_KEY=... mcp-services/.venv/bin/python eval/run_eval.py        # 全量 38 条
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

与演示版**代码完全独立**（不 import 根目录任何模块）；四域共享单进程状态，
端口 8201-8205（与演示版 8101+ 错开）。

## 文档索引

| 文档 | 内容 |
|---|---|
| [docs/01-DeerFlow2.0-调研报告.md](docs/01-DeerFlow2.0-调研报告.md) | 一手代码调研：架构、Skills、MCP、HITL 缺口、消息通道、国产化、社区状态 |
| [docs/02-架构定稿与建议.md](docs/02-架构定稿与建议.md) | 定稿链路、router 模式取舍、网关问题澄清、治理口径、现有资产迁移映射、风险清单 |
| [docs/03-POC计划.md](docs/03-POC计划.md) | 1~2 天 POC 的验证点、步骤与通过标准 |
| [docs/04-实现方案.md](docs/04-实现方案.md) | 零 fork 插件式集成、目录结构、审批设计、同步桥、BFF、子代理划分、里程碑 |

## 与演示版的关系

仓库根目录（backend/frontend/deploy/eval）是已交付的演示版，**继续维护、不动**。
正式版在本目录下推进；演示版的以下资产将平移复用（详见 02 文档 §迁移映射）：

- 4 个 FastMCP server（已注册 Nacos 3.2.1、已实测消费闭环）
- 工具内 confirm_token 人在环安全机制（含伪造 token 拦截实测）
- 38 条评测集与调优过的工具描述（llm 命中率 91%+）
- Nacos 注册模块、drone-manage API 客户端、Open-Meteo 气象自查
