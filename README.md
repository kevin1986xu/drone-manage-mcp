# 无人机智能体（低空智察 · 智能体控制台）

## 一、项目作用

面向**自然资源核查业务**的无人机飞行作业智能体。业务人员在一个分屏控制台里用自然语言完成过去要跨多个系统、多次点击的完整作业流：

> "帮我查一下光明区的图斑" → 图斑落图 → "调度周边无人机" → 机标落图 + 调度建议 → "规划航线" → 自动多图斑覆盖 + 主动解释规划逻辑 → "我手动调整一下" → 免登录编辑器拖航点、结果自动回传 → "我要起飞" → 五项飞前检查逐项展示 → **人工点击确认** → 起飞、地图实时飞行动画 → 任务完成主动播报

核心产品理念（来自 `docs/` 四份设计文档）：

1. **业务能力原子化**：每个业务动作封装为一个独立工具（MCP tool），Agent 自由组装，而非写死的线性工作流。
2. **人掌握最终决定权**：起飞、调度等高危写操作，Agent 只能"提案"，人工确认后才执行（人在环，安全红线）。
3. **全程一个界面、一条对话流**：MCP/CLI 全部藏在 Agent 背后，用户不切页面、不点菜单。

本仓库实现 **P0 四场景 / 15 个工具**（对应 7.22 演示），业务数据**双数据源**：默认内存 mock（脱网可演）；配置 `DRONE_API_BASE` 后接入真实的 drone-manage 无人机管理平台（若依模块）——图斑、机场设备、航线规划（平台图斑巡检算法）、飞行任务均走真实接口，失败自动逐工具回落 mock。llm（LangGraph+Qwen）与 scripted（关键词兜底）两种 Agent 模式均已在真实数据上端到端验证。

## 二、整体架构

```
┌────────────────────────── frontend (React 18 + TS + Vite, :5173) ──────────────────────────┐
│                                                                                             │
│  左栏 Chat                                右栏 Stage（视图指令驱动）                            │
│  · 用户输入 / Agent 流式回复                · show_map    地图（图斑/机标/航线/飞行动画）          │
│  · CoT 工具调用步骤（spinner → ✓ → 返回摘要） · show_iframe 免登录航线编辑器（postMessage 回传）    │
│  · AG-UI 事件分隔线                        · show_report 飞前检查报告卡片                       │
│  · 人在环确认卡片（确认/取消）               · show_confirm 确认卡片（渲染在左栏）                 │
│                                                                                             │
│  地图封装层 IMapAdapter（addPlots/drawRoute/flyTo…）                                          │
│    ├─ MapLibre GL（默认，WebGL）                                                             │
│    └─ Canvas 2D（WebGL 不可用自动降级；生产期可换 Cesium 3D / SuperMap iClient3D，上层不动）      │
└──────────────────────────────┬──────────────────────────────────────────────────────────────┘
                               │  AG-UI 风格事件流（SSE，POST /api/agent/run）
                               │  + 确认/编辑器/任务 REST
┌──────────────────────────────▼───────────────── backend (FastAPI, :8000) ───────────────────┐
│                                                                                             │
│  Agent 运行层（双模式，事件流完全一致，前端无感知）                                                │
│    ├─ llm      LangGraph 最小图（单节点 ReAct）+ Qwen（OpenAI 兼容接口，可指内网 vLLM）           │
│    └─ scripted 关键词兜底路由（无 API Key 默认；即《开发计划》L2 降级，可脱网演示）                  │
│                                                                                             │
│  AG-UI 事件层（app/agui）：执行轨迹 → RUN/TEXT/TOOL 事件 + 工具结果 → 右栏视图指令路由             │
│                                                                                             │
│  工具层（15 个 P0 工具 + 2 个 P1 批量编排工具，双形态共用同一业务原子代码）                        │
│    ├─ LangChain tools（app/agent/tools.py，产品链路进程内直连，减少演示故障点）                   │
│    └─ FastMCP servers（app/mcp_servers，标准 MCP stdio，供外部 Agent/客户端接入）               │
│         drone-dispatch-mcp │ route-planning-mcp │ preflight-mcp │ flight-task-mcp           │
│                                                                                             │
│  业务原子层（app/core）：图斑 / 无人机 / 航线（多图斑覆盖合并算法 + 结构化决策解释）/               │
│                        飞前检查 / 人在环确认（一次性 confirm_token）/ 飞行任务 /                 │
│                        批量编排（Plan-and-Execute：优先级排期 + 逐日装箱，场景8）                 │
│                                                                                             │
│  数据源层（app/datasource）：真实优先、失败逐工具回落 mock（L1 降级）                              │
│    ├─ real.py     drone-manage 平台客户端（图斑/设备/OSD/航线规划/任务，WKT→GeoJSON）             │
│    ├─ weather.py  Open-Meteo 实时气象自查（免 key，适飞判定）                                    │
│    └─ app/data    mock 种子数据（光明区，CGCS2000/EPSG:4490，脱网演示保底）                       │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

**三协议分工**：MCP = Agent↔工具；AG-UI = Agent↔用户界面；A2A = Agent↔Agent（三期预留）。

### 关键设计决策

| 决策 | 说明 |
|---|---|
| 演示期单 Agent，不做多智能体 | 四场景是一条串行工具链，一个 ReAct 循环走通；生产期同源升级 LangGraph Supervisor |
| 工具层 MCP 化、业务原子化 | tool 粒度标准：一句话说得清、参数可由上下文推断；换模型/换编排框架工具层不动 |
| explain_route 返回结构化决策数据 | 覆盖率/合并原因/放弃原因/避让要素/架次对比均来自真实算法决策，LLM 只转述、不允许编造 |
| 航线的大模型软约束优化 | 用户用自然语言调航线（"飞低点""每块多拍几张""只飞这块"）→ LLM 翻译成 generate_route 参数（altitude/photo_num/strategy/plot_ids）+ `replace_route_id` 重规划 → 算法校验硬约束（续航 feasibility）+ 返回前后对比 → LLM 复述变化。**LLM 定策略、算法定几何与硬约束、平台做不到的约束如实引导用编辑器手动改**——不确定性关在可校验的边界内 |
| 批量编排（Plan-and-Execute，场景8） | "把这些图斑排期本周飞完、每天≤N架次" → `create_task_plan`🔒 确定性调度（优先级排序 + 就近合并成架次 + 逐日装箱 + 截止校验）生成逐日排期表 → 人工确认整份计划（即授权后续执行，不再逐架次确认）→ 自动执行第 1 天批次（逐架次 generate_route + 锁定无人机），后续天次排期待执行 → `get_plan_progress` 查进度。**单 Agent 长成工作图（循环 + 分支 + 人在环）的首个真实验证**，LLM 编排、算法调度、人把关 |
| 高危操作人在环 | `dispatch_drone`/`take_off` 无 token 只生成待确认单；人工确认签发一次性 confirm_token（10min、绑定动作、防重放、按确认单锁定参数执行） |
| 三级降级 | 工具级（mock 数据兜底）→ 意图级（scripted 关键词路由）→ 渲染级（WebGL→Canvas 2D） |

### AG-UI 事件协议（前后端契约）

SSE 每行 `data: {json}`，类型：`RUN_STARTED/FINISHED/ERROR`、`TEXT_MESSAGE_START/CONTENT/END`（流式回复）、`TOOL_CALL_START/END`（左栏 CoT）、`VIEW_DIRECTIVE`（右栏视图切换，directive ∈ show_map/show_iframe/show_report/show_confirm/show_plan）。定义见 `backend/app/agui/events.py` 与 `frontend/src/types.ts`。

### 后端 REST 接口

| 接口 | 作用 |
|---|---|
| `POST /api/agent/run` | Agent 运行（SSE 事件流），入参 `{thread_id, message}` |
| `POST /api/confirmations/{id}/approve` | 人工确认 → 签发一次性 confirm_token |
| `POST /api/confirmations/{id}/cancel` | 取消待确认动作 |
| `GET /api/routes/{id}?token=` | 编辑器读取航线（免登录 token 鉴权） |
| `PUT /api/routes/{id}/waypoints` | 编辑器保存航点（生成新版本 + diff） |
| `GET /api/tasks/{id}` | 飞行任务进度（前端 1s 轮询驱动飞行动画） |
| `GET /api/config` / `POST /api/reset` | 运行配置 / 重置演示 |

### 真实业务数据源（drone-manage 若依模块）

`.env` 配置 `DRONE_API_BASE`（如 `http://192.168.101.21:10009`）后，工具优先走真实平台接口，**单次调用失败自动回落 mock（L1 降级），Agent 链路与前端不受影响**：

| 能力 | 真实接口 | 说明 |
|---|---|---|
| 图斑查询 | `POST /flyWorkZone/page`（zoneType=图斑） | WKT (MULTI)POLYGON Z → GeoJSON；plot_id 用业务编号（zoneName），支持尾号部分匹配 |
| 周边设备 | `POST /device/statistics/devices`（domain=3 机场） | 距离本地计算；默认半径无结果自动扩到 20/50 km（机场稀疏场景） |
| 实时电量 | `GET /drone/dock/osd/latest/{sn}`（Java 侧读 Redis） | 机场离线/未上报时如实提示"需人工核实"（不编数据） |
| 航线规划 | `POST /drone/route/planDynamicRoute`（PLOT_INSPECTION） | **平台图斑巡检算法**（每图斑边界 photo_num 点对中拍照 + 中心高空），支持 MultiPolygon 多图斑一条航线；合并决策（哪些图斑值得合并）仍由 Python 层承担并生成 explain 数据。可调参数：photo_num（拍照点数，最有效的密度杠杆）、altitude（平台强制 ≤120m 法定上限、安全带约 100~120m）、strategy（single/multi_cover）；overlap 对 PLOT_INSPECTION 无效不采用 |
| 航线详情 | `GET /drone/route/detail|points/{routeId}` | 版本/diff 本地管理 |
| 航线编辑回写 | `PUT /drone/route`（更新 routePointList） | 编辑器保存后自动回写平台：按索引对位更新经纬度/高度，**保留平台侧拍照/云台动作**；航点数量变化时不回写（动作无法推断），仅本地版本并标记 `platform_synced=false` |
| 气象 | **后端自查 Open-Meteo**（免费无 key）→ 平台气象接口 → 本地 mock 三级回落 | 以航线/图斑位置实时取风速/阵风/降水/温度并给适飞判定（风限 12 m/s）；`WEATHER_PROVIDER=mock` 可禁用出网（测试默认） |
| 飞行任务 | `POST /api/tasks`（创建）· `PUT /api/tasks`（更新，按数字主键）· `PUT /api/tasks/cancel/{taskId}` | **只建不下发**：创建的任务是"待执行、未派发"状态，自动调度器只处理已派发任务，不会触发起飞（`start`/`sync` 才下发，本系统不调用）；创建默认关闭，`DRONE_CREATE_REAL_TASK=1` 开启；服务端创建耗时可达分钟级（同步做禁飞区/KMZ），客户端已放宽至 120s 超时 |

无对应接口保留 mock：空域许可、航线净空分析；`dispatch_drone` 的"锁定"是 Agent 侧人在环概念，无需平台支持。测试/评测强制 mock（`tests/conftest.py`、`eval/run_eval.py`），不会打真实系统。

## 三、目录结构

```
无人机智能体/
├── docs/                      设计文档（技术路线/场景/界面/开发计划，实现依据）
├── backend/
│   ├── app/
│   │   ├── core/              业务原子层：plots/drones/routes/preflight/confirm/tasks/store/geo
│   │   ├── agent/             tools.py（15 个工具）· graph.py（LangGraph ReAct）· scripted.py（兜底路由）
│   │   ├── agui/              events.py（AG-UI 事件 + 视图指令路由）
│   │   ├── datasource/        real.py（drone-manage 客户端）· weather.py（Open-Meteo 气象自查）
│   │   ├── mcp_servers/       4 个 FastMCP server（stdio / Nacos streamable-http 双模式）+ nacos_registry.py
│   │   ├── data/              mock_data.py（光明区图斑/无人机/环境要素，mock 种子）
│   │   ├── config.py          环境变量配置
│   │   └── main.py            FastAPI 入口
│   ├── tests/                 契约测试 + 安全红线测试（pytest，21 项）
│   ├── pyproject.toml         uv 工程
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── components/        Chat / Stage / MapView / ReportView / EditorView
│   │   ├── map/               IMapAdapter（adapter.ts=MapLibre，canvas2d.ts=降级）
│   │   ├── store.ts           Zustand：AG-UI 事件中央处理器
│   │   ├── agent.ts / api.ts  SSE 客户端与 REST 封装
│   │   └── types.ts           事件与业务类型
│   └── public/route-editor.html   免登录航线编辑器（独立页，iframe 嵌入）
├── eval/                      评测集（32 条话术）+ run_eval.py 跑批脚本
└── deploy/                    基础设施部署：Nacos + Higress docker-compose + 配置指南
```

## 四、运行环境与中间件

### 演示版（本仓库开箱即用）

**零外部中间件依赖**：业务状态全内存（`app/core/store.py`）、会话在进程内、无数据库/消息队列/缓存。只需要：

| 依赖 | 版本 | 用途 |
|---|---|---|
| Python | ≥ 3.12 | 后端运行时 |
| [uv](https://docs.astral.sh/uv/) | 最新 | Python 依赖与虚拟环境管理 |
| Node.js | ≥ 18 | 前端构建与开发服务器 |
| （可选）LLM API | OpenAI 兼容 | `llm` 模式：**默认阿里云百炼（DashScope），只需填 API Key**；不配则走 scripted 模式 |
| （可选）天地图 Key | — | 真实底图瓦片；不配默认 OSM，离线自动回落深色底 |

主要框架依赖（自动安装）：后端 FastAPI / LangGraph / langchain-openai / mcp（FastMCP）/ uvicorn；前端 React 18 / Zustand / maplibre-gl / @microsoft/fetch-event-source。

### 生产化所需中间件（规划，见 docs/开发计划.md §四）

| 中间件 | 用途 |
|---|---|
| vLLM / SGLang（后续阶段，当前不需要） | 客户要求纯内网时的 Qwen/GLM 私有化推理；OpenAI 兼容，届时只改 `LLM_BASE_URL` 即可，当前阶段用阿里云百炼 |
| **Nacos（MCP Registry）** — **已接入，3.2.1 实测** | `.env` 配置 `NACOS_SERVER_ADDR`（+ 鉴权账号）后，4 个 MCP server 自动以 streamable-http 常驻（:8101-8104）并注册到 Nacos MCP Registry；不配置则保持 stdio 本地模式。注册实现见 `app/mcp_servers/nacos_registry.py`：HTTP v3 admin API 发布规格，端点默认 DIRECT（纯 HTTP、零 gRPC 依赖），可选 `NACOS_ENDPOINT_MODE=ref`（gRPC 临时实例 + REF 引用，动态上下线）。VPN/多网卡环境用 `MCP_SERVICE_IP` 显式指定对外 IP |
| Higress（AI 网关） | 从 Nacos Registry 发现 MCP 工具，统一鉴权、审计日志、工具白名单，防提示注入越权调用。**Nacos + Higress 的 docker-compose 部署与 Higress 配置步骤见 [`deploy/`](./deploy/README.md)** |
| **PostgreSQL** | LangGraph checkpoint 持久化（人在环中断恢复、决策留痕审计）；业务库对接 |
| **MQTT Broker（如 EMQX）** | 大疆上云 API 物模型接入：遥测订阅、任务事件回调唤醒 Agent（P1 监控播报场景） |
| **Nginx / 瓦片缓存** | 天地图 WMTS 本地化缓存（演示现场断网保险）、前端静态托管 |

## 五、启动

### 1. 后端

```bash
cd backend
cp .env.example .env      # 可选：填 LLM_API_KEY 走真模型；不填默认 scripted 兜底模式
uv sync                   # 安装依赖（含 dev 测试依赖）
uv run uvicorn app.main:app --port 8000
# 验证：curl localhost:8000/api/config
```

**接入阿里云百炼（推荐）**：在[百炼控制台](https://bailian.console.aliyun.com/)创建 API-KEY，填入 `.env` 的 `LLM_API_KEY`（兼容 `DASHSCOPE_API_KEY` 变量名）即可，`LLM_BASE_URL` 默认已指向百炼 OpenAI 兼容端点，无需改动；`LLM_MODEL` 默认 `qwen-plus`，可按需换 `qwen-max` 等。重启后端后 `GET /api/config` 返回 `"agent_mode": "llm"` 即生效；不填 Key 则自动回落 scripted 兜底模式。

**接入真实无人机管理平台**：`.env` 配置 `DRONE_API_BASE=http://<drone-manage 地址>` 即启用真实数据源（图斑/设备/航线/任务），未配置或调用失败自动回落 mock。相关开关：`DRONE_CREATE_REAL_TASK`（确认后是否在平台创建任务，默认关）、`DRONE_WORKSPACE_ID`、`MCP_SERVICE_IP`（VPN/多网卡时注册用对外 IP）。

`.env` 关键项：`AGENT_MODE`（auto/llm/scripted）、`LLM_API_KEY/LLM_BASE_URL/LLM_MODEL`、`DRONE_API_BASE`、`NACOS_SERVER_ADDR`、`WEATHER_PROVIDER`。完整说明见 `.env.example`。

### 2. 前端

```bash
cd frontend
npm install
npm run dev               # http://localhost:5173（/api 已代理到 :8000）
```

打开浏览器按左下角提示话术走完整演示动线，顶栏「重新演示」一键重置。

- **mock 模式**话术（默认）："帮我查一下光明区的图斑" →（图斑编号 GM-01~05，叙事数字见 §七）
- **真实数据模式**话术（配 `DRONE_API_BASE` 后）："查一下汉川市的图斑" → "调度周边的无人机" → "用最近的机场给尾号 00001 的图斑规划航线"（支持业务编号尾号模糊匹配；机场稀疏时搜索半径自动扩大到 20/50km）

### 3. MCP server（可选，供外部 MCP 客户端接入）

```bash
cd backend
uv run python -m app.mcp_servers.drone_dispatch   # 调度域：query_plots / find_nearby_drones / get_drone_status / dispatch_drone🔒
uv run python -m app.mcp_servers.route_planning   # 航线域：generate_route / get_route_detail / explain_route / open_route_editor
uv run python -m app.mcp_servers.preflight        # 飞前检查域：五项单项检查 + preflight_check 聚合
uv run python -m app.mcp_servers.flight_task      # 飞行任务域：take_off🔒 / get_task_status（P1 的监控/返航工具在此扩展）
```

默认 stdio 传输，可直接配入 Claude Desktop / Qwen-Agent / MCP Inspector 等客户端调试。

**Nacos 注册模式**：在 `backend/.env` 配置 `NACOS_SERVER_ADDR`（以及鉴权账号 `NACOS_USERNAME/NACOS_PASSWORD`）后，同样的启动命令会自动切换为 streamable-http 常驻（默认端口 8101-8104，`/mcp` 路径），并把 server + tool 清单注册进 Nacos MCP Registry；MCP 客户端/Higress 网关即可从 Registry 按名称查到端点直连调用。注意：MCP server 与 FastAPI 后端是独立进程、独立内存世界（演示版状态不共享），生产化状态落库后消除。

已在 Nacos 3.2.1（192.168.101.21:8998）实测通过的完整链路：4 个 server 注册（DIRECT 端点，纯 HTTP）→ 列表/详情接口正常 → 消费者从 Registry 查询 `backendEndpoints` → streamable-http 连接 → `tools/list` + `tools/call`（含 `generate_route`）成功。为什么不用官方 `nacos-mcp-wrapper-python`：其注册走 gRPC AI 接口（主端口 +1000），部分部署环境该端口不对外放行；本实现全程走 HTTP v3 admin API，无 gRPC 依赖。备注：该部署另配有 `nacos.ai.mcp.registry.port=9080`（Nacos 3.2 的独立 MCP Registry 端口，非 HTTP/1.1 协议），Higress/网关对接时再启用，不影响当前注册与发现。

**为什么要注册到 Nacos（作用与边界）**

| 作用 | 说明 |
|---|---|
| 服务发现 | 消费方（其他 Agent / 业务系统 / MCP 客户端）向 Registry 按名称查端点，不再写死 IP:端口；部署位置变化对消费方透明 |
| 工具元数据登记 | 注册的不只是"服务在哪"，还有**每个 server 的工具清单与参数 schema**（15 个 tool 全量发布）——组织级"内网 MCP 市场"：新建 Agent 时到 Registry 浏览全部可用业务能力，即《技术实现路线》"业务原子化、Agent 自由组装"的基础设施形态 |
| Higress 网关前置 | 生产期 Higress 从 Registry 自动发现 MCP 工具，在网关层统一收口：**鉴权**（drone-manage 的 /out/* 目前无鉴权，网关是收口点）、**审计留痕**（政企合规）、**工具白名单**（防提示注入越权调用）；新增业务域 server 注册即接入治理 |
| 多实例弹性（ref 模式） | `NACOS_ENDPOINT_MODE=ref` 时用 gRPC 临时实例：进程退出自动摘除、扩容注册即生效，适合生产期多实例部署 |

边界（诚实说明）：对**当前演示链路没有直接作用**——演示中 Agent 进程内直连工具（刻意减少现场故障点），不经过 Registry。其价值在演示之后：向客户讲架构时是"纯内网 MCP 市场"的实证，生产化时是 Higress 治理的前置条件。因此做成可选配置，不配 `NACOS_SERVER_ADDR` 一切照常。

## 六、测试

### 1. 单元/契约测试（21 项）

```bash
cd backend
uv run pytest -v
```

- `tests/test_tools_contract.py`：15 个工具的参数 schema 校验、返回结构断言、合并算法正确性（合并方案必须优于逐个单飞、每个合并决策有量化依据）、编辑 diff、编辑器 token。
- `tests/test_safety_redline.py`：**安全红线**——无 token 不得起飞/不得改变任何状态、伪造 token 拒绝、token 一次性（重放拒绝）、token 绑定动作类型、按确认单参数执行、已取消不可再确认。

### 2. 评测集跑批（Agent 层三指标）

```bash
cd backend
SCRIPTED_FAST=1 uv run python ../eval/run_eval.py                  # scripted 模式（当前 32/32=100%）
AGENT_MODE=llm LLM_API_KEY=xxx uv run python ../eval/run_eval.py   # LLM 模式跑分
```

输出**工具命中率 / 传参正确率 / 任务完成率**及按场景分解与未命中清单。调优纪律（《开发计划》§三）：命中率低的 tool → **先改 name/description（命中率第一杠杆）→ 仍不行才改代码**；新增话术直接追加 `eval/evalset.jsonl`。

实际调优记录（qwen3.7-plus，2026-07-14）：首跑 72% → 工具描述强化（"每次追问必须重新调用、禁止凭对话记忆回答"；take_off 不带 token ≠ 起飞）84% → 末项检查返回注入 `agent_hint` 引导发起确认 **91%**。剩余 3 条为模型保守行为（检查完先口头询问再弹卡片），业务可接受；scripted 模式保持 32/32=100%。llm 模式已在浏览器走通真实数据全流程（含虚构 confirm_token 被安全红线拦截的实测）。

### 3. 端到端手动/自动验证

- 手动：按界面提示话术走六步主线；重点验收——航线解释数字与 explain_route 数据一致、编辑回传后 Agent 能复述"3 号航点移动约 xx m"、**不点确认卡片绝不起飞**。
- 自动（浏览器控制台钩子）：`window.__demo('我要起飞')` 直接发消息；编辑器 iframe 内 `window.__editor.getWps()/setWps()` 可编程改航点，用于自动化回归。
- 前端构建检查：`cd frontend && npm run build`（含 tsc 类型检查）。

## 七、已知边界

- 会话/航线版本/确认单等运行状态在内存，重启即重置（生产化落 PostgreSQL）；MCP server 与主后端是独立进程、状态不共享。
- 飞行动画为本地模拟（1 分钟 = 1 秒加速），即使开了 `DRONE_CREATE_REAL_TASK` 也**只在平台创建任务、绝不下发**（`start`/`sync` 不调用）。
- 空域许可、航线净空分析平台无接口，为 mock 数据（报告中如实标注）。
- llm 模式（qwen3.7-plus）已知保守行为：偶尔检查完先口头询问一句再弹确认卡片，用户答"确认起飞"即可继续；曾实测模型虚构 confirm_token 被安全红线拦截。
- mock 演示叙事关键参数已调优固化（`app/core/routes.py`：`RESERVE_RATIO=0.15`、`_survey_min` 系数）——改动 mock 坐标或这两处会改变"GM-04 顺带覆盖 GM-02/GM-03、节省约 25 分钟"的叙事数字。
- P1 已实现：批量编排（场景8，Plan-and-Execute）；待做：任务监控播报/成果报告/历史查询。生产化路线见 `docs/开发计划.md`。
