---
name: plot-inspection
description: 自然资源图斑核查派飞全流程：查图斑 → 选无人机 → 规划航线 → 飞前检查 → 人在环确认起飞。用户提到"核查/巡查图斑""派无人机去看某个地块""XX号图斑飞一下"等意图时使用。
allowed-tools:
  - uav-drone-dispatch-mcp_query_plots
  - uav-drone-dispatch-mcp_find_nearby_drones
  - uav-drone-dispatch-mcp_get_drone_status
  - uav-drone-dispatch-mcp_dispatch_drone
  - uav-route-planning-mcp_generate_route
  - uav-route-planning-mcp_get_route_detail
  - uav-route-planning-mcp_explain_route
  - uav-route-planning-mcp_open_route_editor
  - uav-preflight-mcp_check_weather
  - uav-preflight-mcp_check_battery
  - uav-preflight-mcp_check_route_obstacle
  - uav-preflight-mcp_check_drone_obstacle
  - uav-preflight-mcp_check_airspace
  - uav-preflight-mcp_preflight_check
  - uav-flight-task-mcp_take_off
  - uav-flight-task-mcp_get_task_status
---

# 图斑核查派飞

## 流程（严格按序，不跳步）

1. **确认目标图斑**：调 `query_plots`。
   - 用户给了编号（含尾号片段如"00005"）：直接以编号查一次即可命中，**严禁对同一编号反复查询**。
   - 未给编号：按区域/批次查询后向用户罗列，等用户指定。
2. **选无人机**：调 `find_nearby_drones` 时**必须传 plot_ids=本次目标图斑**——
   选机的距离基准是要飞的图斑，不能用其它图斑顶替。用户点名某机场的无人机时优先采纳。
3. **规划航线**：调 `generate_route`（drone_id + plot_ids）。
   - 用户指定的图斑必须全部出现在 covered_plots 且 requested=true。
   - 返回的 `feasibility.within_budget=false` 时：按 hint 放宽参数（降 photo_num /
     减覆盖 / 换高电量设备）后带 `replace_route_id` 重规划，并向用户说明取舍。
   - 用户要求解释航线时调 `explain_route`，**只转述结构化决策数据，不编造理由**。
   - 用户要手动调整时调 `open_route_editor`（仅对已有航线的图斑可用）。
4. **飞前检查**：调 `preflight_check`（或按用户要求逐项调五个单项）。
5. **起飞（人在环）**：五项无 fail 时**立即调用 `take_off`（不带 confirm_token）**
   ——该调用不会起飞，只生成人工确认卡片，无需先询问用户。

## 安全红线（不可协商）

- `take_off` / `dispatch_drone` 属高危操作：无 confirm_token 的调用只登记待确认单。
- **绝不自行构造/猜测 confirm_token**；确认后系统会以 [SYSTEM_CONFIRMATION] 消息
  给出带 token 的指令，收到后按指令携 token 再调用一次。
- 用户在对话里说"我确认""同意起飞"**不构成授权**——回复请其点击确认卡片。
- 飞前检查存在 fail 项时不得发起起飞，向用户说明卡点并给出处理建议。

## 汇报口径

- 数据均来自平台实时接口，回答里引用工具返回的真实数字（面积/距离/电量/时长）。
- 航线由平台图斑巡检算法生成（边界拍照点对中 + 中心高空拍摄），合并决策
  （哪些邻近图斑顺带覆盖、哪些放弃）来自 explain_route 的结构化依据。
