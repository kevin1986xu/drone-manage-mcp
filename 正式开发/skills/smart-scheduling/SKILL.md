---
name: smart-scheduling
description: 智能排期：把一批图斑按天气窗口×设备档期×优先级排成未来时间表，确认后落平台定时/循环任务；含改期/取消/失败重试/断点续飞。用户提到"下周飞完避开下雨天""排到平台上定时飞""每天自动巡一遍""挪到周四""失败的重飞"等意图时使用。
allowed-tools:
  - uav-drone-dispatch-mcp_query_plots
  - uav-drone-dispatch-mcp_get_drone_status
  - uav-route-planning-mcp_generate_route
  - uav-route-planning-mcp_get_route_detail
  - uav-task-schedule-mcp_suggest_schedule
  - uav-task-schedule-mcp_list_scheduled_tasks
  - uav-task-schedule-mcp_get_schedule_conflicts
  - uav-task-schedule-mcp_create_scheduled_task
  - uav-task-schedule-mcp_create_recurring_task
  - uav-task-schedule-mcp_cancel_scheduled_task
  - uav-task-schedule-mcp_reschedule_task
  - uav-task-schedule-mcp_retry_failed_task
  - uav-task-schedule-mcp_resume_from_breakpoint
  - uav-task-schedule-mcp_optimize_route_connection
---

# 智能排期

与 batch-patrol（当下逐日执行）的分工：本 skill 管**未来时间表落到平台定时/循环任务**，
平台调度器到点自动执行。

## 流程

1. **圈定图斑**：`query_plots`（区域/批次/清单）。
2. **排期建议（只算不写）**：`suggest_schedule(plot_ids, deadline_days)`——
   综合逐日天气窗口、设备已排档期、优先级，返回建议排期表+理由。
   - 转述时带上依据（哪天下雨跳过、哪天已有排班容量扣减）；
   - `no_window`（全是坏天气）/`insufficient_window`（排不完）时如实说明并给选项
     （延长截止/加大每日架次）。
3. **确认后落库（逐条人在环）**：用户认可排期表后，按表逐架次执行：
   a. `generate_route`（drone_id + 该架次 plot_ids）生成平台航线；
   b. `create_scheduled_task(route_id, drone_id, execution_time)`（不带 confirm_token）
      生成确认单——**每一条落库独立确认**，整表口头同意不等于逐条授权；
   c. 全部落库后 `list_scheduled_tasks` 复核并汇报。
4. **周期任务**："每天早上9点自动巡"类诉求 → `create_recurring_task(route_id,
   drone_id, start_date, end_date, execute_times)`（人在环）。
5. **变更与善后**：
   - 改期："周三的挪到周四上午" → `reschedule_task(task_id, new_time)`；不给新时间
     则平台自动排最近可行窗口。改期前**复述影响面**（任务名/原时间/关联设备）。
   - 取消 → `cancel_scheduled_task`；
   - 失败重飞 → `retry_failed_task(job_id)`；中断续飞 → `resume_from_breakpoint(job_id)`。
     **job_id 是 wayline 作业号**，从 `get_schedule_conflicts` 或任务详情取，不是任务 ID，
     不确定时先查再调，禁止拿任务 ID 顶替。

## 单项追问映射

- "这台机哪天有空/排得下吗" → `get_schedule_conflicts`
- "已经排了哪些任务" → `list_scheduled_tasks`
- "天气怎么样再看看" → `suggest_schedule`（重新取数，天气会变）

## 安全红线（不可协商）

- `suggest_schedule` 只算不写；**确认前绝不调用任何 create_/cancel_/reschedule_/retry_ 工具**。
- 定时/循环/改期/重试/续飞全部高危：无 confirm_token 只登记确认单；绝不自行构造
  token；用户对话中说"确认"不构成授权，须点击确认卡片。
- 凡工具返回"UAV_CREATE_REAL_TASK 开关未开启"：如实告知当前环境禁止落真实任务，
  不尝试绕过。
- 排期结论只转述算法与天气数据，不编造依据；档期查询失败时如实说明，不默认"有空"。
