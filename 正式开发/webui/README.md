# DeerFlow Web UI 原生 HITL 确认卡片

DeerFlow 自带 Next.js 前端（3000 端口）里渲染无人机高危操作确认卡片,
不再依赖 BFF + 演示版前端才能走人在环。BFF 链路（GIS 分屏）不受影响,两条前端并存。

## 组成（deerflow/ 是 gitignore 克隆,改动以本目录为真身）

| 文件 | 进 deerflow 的方式 | 作用 |
|---|---|---|
| `uav-confirm-card.tsx` | 软链 → `frontend/src/components/workspace/messages/` | 确认卡片组件 + `isUavConfirmation` 判别 |
| `approval-proxy-route.ts` | 软链 → `frontend/src/app/api/uav/approval/[actionId]/[verb]/route.ts` | 同源服务端代理,X-Admin-Key 不进浏览器 |
| `deerflow-webui-hitl.patch` | `git apply`（对既有文件的 30 行增量） | ① message-group.tsx 的 ToolCall 加 requires_confirmation 分支;② 聊天页加 `uav:system-confirmation` 事件监听,批准后以 `hide_from_ui` 隐藏消息回发 `[SYSTEM_CONFIRMATION]` |

## 全新克隆的安装

```bash
./install.sh          # 建软链 + git apply 补丁（幂等,已应用会跳过）
```

## 运行

```bash
cd deerflow/frontend && \
  UAV_APPROVAL_ADMIN_KEY=<与审批服务同值> \
  UAV_APPROVAL_BASE=http://127.0.0.1:8205 \
  pnpm dev            # http://localhost:3000
```

Gateway（8001）、mcp-services、审批服务照常起（见 ../README.md 启动手册）。

## 数据流

```
take_off(无token) → requires_confirmation JSON → ToolCall 分支渲染卡片
  → 点「确认执行」→ POST /api/uav/approval/{id}/approve（Next 服务端,带 X-Admin-Key）
  → 审批服务签发一次性 confirm_token → 卡片派发 window 事件
  → 聊天页监听器 sendMessage([SYSTEM_CONFIRMATION] ... , hide_from_ui)
  → 模型携真 token 重调 take_off → 执行
```

红线不变:token 唯一签发方是审批服务;对话文本"我确认"不构成授权;token 一次性、动作绑定、TTL 10 分钟。
