---
name: batch-patrol
description: 批量图斑核查排期：把一批图斑排成逐日架次计划并执行。用户提到"这批图斑N天内查完""批量巡查""排个计划"等意图时使用。
---

# 批量巡查排期

## 流程

1. **圈定图斑集合**：`query_plots`（按区域/批次/日期圈定，或用户点名清单）。
2. **生成计划（人在环）**：调 `create_task_plan`（plot_ids + deadline_days +
   max_sorties_per_day），**不带 confirm_token**——返回排期表（按优先级排序、
   ≤3 km 邻近图斑就近合并成架次、按每日上限装箱）和待确认单。
   - `feasible=false`（超期）时向用户说明：放宽每日架次上限或延长截止，重新生成。
3. **确认执行**：人工确认整份计划即授权后续执行（不再逐架次确认）。收到
   [SYSTEM_CONFIRMATION] 带 token 的指令后携 token 再调用——计划生效并自动执行
   第 1 天批次（逐架次规划航线 + 锁定就近空闲无人机）。
4. **进度跟踪**：用户问进度时调 `get_plan_progress`，按天/架次转述状态
   （scheduled / dispatched / queued / route_failed）。

## 安全红线

- 计划确认前不产生任何调度动作；绝不自行构造 confirm_token。
- 排期是确定性算法产出（优先级 → 就近分组 → 装箱），转述时不编造算法之外的理由。
- 某架次 `queued`（无空闲设备）或 `route_failed` 时如实告知，不掩饰。
