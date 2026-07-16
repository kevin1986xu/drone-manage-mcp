# uav-extensions

DeerFlow 的无人机业务扩展包（**零 fork**：pip 装进 DeerFlow 运行环境 + 配置注入，不改其代码）。

## 模块

### approval_service —— 高危审批服务（独立进程，:8205）

confirm_token 的**唯一签发方**，签发在 Agent 之外：

```
MCP 工具（无token自拒）→ POST /api/approval/pending 登记确认单
人（GIS卡片/企微钉钉卡片按钮）→ POST /api/approval/{id}/approve → 签发一次性 token
MCP 工具（携token）→ POST /api/approval/consume → 校验+消费（一次性/动作绑定/TTL 10min）
```

`APPROVAL_ADMIN_KEY` 配置后 approve/cancel/列表需 `X-Admin-Key`（防旁路直批）。

```bash
python -m uav_extensions.approval_service
```

### interceptors —— DeerFlow mcpInterceptors 注入点

```json
"mcpInterceptors": [
  "uav_extensions.interceptors:build_uav_guard",
  "uav_extensions.interceptors:build_uav_audit"
]
```

- `build_uav_guard`：高危工具硬白名单——confirm_token 形态非法（模型伪造）时
  客户端侧直接短路拒绝（纵深防御；真正校验仍在审批服务/工具内，不可绕）。
  无 token 调用放行（那是登记确认单的合法第一阶段）。
- `build_uav_audit`：uav-* 服务的工具调用审计 JSONL 落盘（token 打码），
  默认 `~/.uav-agent/tool-audit.jsonl`，`UAV_AUDIT_LOG` 可改。

接口形态已对 DeerFlow 2.0 源码核实：builder 无参调用，返回 `async (request, next_handler)`。

### nacos_bridge —— Nacos → DeerFlow 同步桥（独立进程）

轮询 Nacos v3 MCP Registry（前缀 `uav-`）→ diff → `PUT /api/mcp/config` 热更新
（DeerFlow 官方 API：写盘+重载+工具缓存重置）。Nacos 注册/下线/换地址，DeerFlow
自动跟随。只管理命中前缀的 server，人工配置不碰；无变化不写。

```bash
NACOS_SERVER_ADDR=... DEERFLOW_BASE=http://127.0.0.1:8001 python -m uav_extensions.nacos_bridge
```

稳定后计划 PR 回 DeerFlow 上游（其当前缺注册中心发现）。

## 测试

```bash
uv venv && uv pip install -e ".[dev]" && .venv/bin/python -m pytest
```
