# uav-mcp-services

无人机飞控 Agent 平台的 **MCP 工具层**（正式版，与仓库根目录演示版完全独立）。

## 结构

- 四个业务域，**同一进程**共享世界状态（航线/确认单/任务跨域可见），各占一个端口、各自注册 Nacos：

| 域 | server 名 | 端口 | 工具 |
|---|---|---|---|
| 调度 | uav-drone-dispatch-mcp | 8201 | query_plots / find_nearby_drones / get_drone_status / dispatch_drone🔒 / create_task_plan🔒 / get_plan_progress |
| 航线 | uav-route-planning-mcp | 8202 | generate_route / get_route_detail / explain_route / open_route_editor |
| 飞前 | uav-preflight-mcp | 8203 | 五项单项 + preflight_check |
| 飞行任务 | uav-flight-task-mcp | 8204 | take_off🔒 / get_task_status |

🔒 = 高危·人在环：无 confirm_token 只登记待确认单；token 由独立审批服务签发（`APPROVAL_BASE`），
一次性、动作绑定、10 分钟 TTL。

- **真实平台优先**：数据一律来自 drone-manage（`DRONE_API_BASE`），无 mock 种子；平台不可达返回明确错误。
- **服务端鉴权**：`UAV_MCP_API_KEY` 配置后所有请求须带 `X-API-Key`（/healthz 免）。
- **瘦身返回**：query_plots / generate_route 默认不含几何/航点（省 LLM 上下文）；
  BFF/GIS 用 `include_geometry` / `include_waypoints` 取全量。

## 运行

```bash
uv venv && uv pip install -e ".[dev]"
cp .env.example .env   # 填 DRONE_API_BASE / UAV_MCP_API_KEY / APPROVAL_BASE / NACOS_*
.venv/bin/python -m uav_mcp.runner                 # 四域全起
.venv/bin/python -m uav_mcp.runner drone-dispatch  # 单域调试
```

## 测试

```bash
.venv/bin/python -m pytest             # 单元（几何/WKT、审批红线、鉴权中间件），无需平台
.venv/bin/python scripts/smoke_e2e.py  # 端到端冒烟（需服务已起 + 现网可达 + 审批服务）
```

冒烟覆盖：真实图斑→选机→平台算法规划→跨域飞前检查→人在环全流程
（无token自拒/伪造拒/批准执行/重放拒）→ 平台测试航线清理。

## 安全开关（默认全关）

- `UAV_CREATE_REAL_TASK=1`：take_off 确认后在平台创建 flighttask（只建不下发，不会飞）
- `UAV_REAL_PUBLISH=1`：下发计划到机场执行（**真实起飞**）——需前一开关也开 + 现场安全审批
