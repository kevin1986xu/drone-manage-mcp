# 通用前端与 MCP-UI 方案（docs/08）

> 2026-07-22 起草并落地第一层原型。问题：GIS 前端、DeerFlow webui、route-editor
> 三个可视化出口都是**逐宿主定制**的（BFF 回灌 / webui patch / iframe postMessage），
> 每接一个新宿主（如 Claude 桌面端这类自带沙盒浏览器的通用 Agent 客户端）都要再定制。

## 1. 目标形态

**工具返回 = 结构化数据 + `view_url`（自包含业务视图页）**。任何能打开网页的宿主
零适配可用；支持 MCP Apps 的宿主升级为对话内嵌渲染。业务/设计改版只改一处（UI 服务）。

```
                       ┌── 定制宿主（现状保留）────────────┐
  MCP 工具返回          │  GIS 前端（BFF 8300 view_directive）│
  {data..., view_url} ──┤  DeerFlow webui（确认卡片 patch）    │
                       ├── 通用宿主（本方案新增）──────────┤
                       │  Claude 桌面端等：沙盒打开 view_url  │
                       │  MCP Apps 宿主：ui:// 资源内嵌渲染   │
                       └──────────────────────────────────┘
                                    ▼
                    UI 服务（uav_extensions.ui_service，:8213）
                    /ui/approval/{id}?t=…   确认卡片页（人在环通用化）
                    /ui/view/{vtoken}       通用视图页（map/trajectory/…）
```

## 2. 两层投放

### 第一层：URL 兜底（✅ 2026-07-22 原型落地）

| 页面 | 用途 | 数据来源 |
|---|---|---|
| `/ui/approval/{action_id}?t=<page_token>` | 确认卡片：渲染确认单 rows → 人点确认 → 显示 `[SYSTEM_CONFIRMATION]` 一行让用户带回对话 | UI 服务后端代理审批服务（admin key 不出服务端） |
| `/ui/view/{vtoken}` | 通用视图：按 type 渲染（trajectory 轨迹折线 / map 图斑围栏，纯 Canvas 自包含、无 CDN） | 工具执行时把几何快照 POST 注册到 UI 服务（X-API-Key），拿回 view_url |

设计要点：
- **页面 token 门禁**：确认单创建时审批服务顺带发 `page_token`（随机、绑定单据）；
  视图快照注册时发 `vtoken`（随机、TTL 30 分钟）。URL 即能力凭证，泄露只在窗口期有效。
- **X-API-Key / admin key 永不进浏览器**：页面只调 UI 服务自己的 `/ui/api/*`，
  敏感凭证都在服务端代理层。
- **人在环通用化（本方案的核心价值）**：高危工具在任何宿主里返回
  `requires_confirmation + view_url` → 用户沙盒里打开页面看确认单原文 → 点确认 →
  页面显示 `[SYSTEM_CONFIRMATION] action=… confirm_token=…` 一行 → **用户手工带回
  对话** → Agent 携 token 重调工具。token 仍然只在人点击后存在、一次性、动作绑定、
  参数锁定——安全语义与 GIS 卡片完全一致，且无需宿主任何集成。
- 降级：UI 服务未部署（`UAV_UI_BASE` 未配）时一切如旧，view_url 缺省不出现。

### 第二层：MCP Apps（规划，等目标客户宿主明确）

MCP 生态的 UI 扩展（mcp-ui / MCP Apps 规范，Claude 桌面端已支持）：工具声明
`ui://` HTML 资源，宿主在对话内沙盒 iframe 渲染，postMessage 双向通信。
对我们=同一套 `/ui/*` 页面再注册一份 `ui://` 资源声明：
- 支持的宿主：确认卡片在对话里直接弹出，点确认经 postMessage 回注 token（免手工复制）；
- 不支持的宿主：自动落回第一层 view_url；
- 经 Higress 对外交付时 UI 资源随 MCP server 声明走同一网关（消费者鉴权/限流复用）。

## 3. 安全边界（不可退让）

1. confirm_token 签发仍在 Agent 之外（审批服务），页面只是"确认按钮"的新皮肤；
2. page_token/vtoken 是**展示与确认能力**，不是执行能力——执行永远要 token 回到工具调用；
3. 页面全部只读渲染 + 单一确认动作，无任何直接写平台的路径；
4. 对外暴露 8213 时必须过 Higress（消费者鉴权 + 限流 + 审计），与 820x 同收口。

## 4. 落地状态

- ✅ UI 服务原型（approval 页 + view 页，8213）、审批服务 page_token、
  live 域轨迹 view_url、P1 两域确认单 view_url 透传
- ⬜ 老域（take_off/dispatch/排期等）确认单 view_url 透传（机械改造，随下批）
- ⬜ map 页接图斑/围栏快照（zones/plots 工具侧注册）
- ⬜ MCP Apps `ui://` 声明（等宿主明确）
- ⬜ 8213 经 Higress 对外 + 收口
