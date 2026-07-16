---
name: smart-recognition
description: 智能识别编排（M4 规划中）：起飞采集 → 提交识别任务 → 结果落图。用户提到"识别""变化检测""看看拍到了什么"等意图时使用。
---

# 智能识别编排（占位，M4 落地）

> recognition 域 MCP server 尚未上线。当前收到识别类请求时：
> 1. 如实告知识别能力在建；
> 2. 可先完成采集侧流程（按 plot-inspection skill 派飞拍摄）；
> 3. 不编造识别结果。

## 规划中的流程（M4）

1. 采集：按 plot-inspection 完成目标图斑拍摄（照片挂 platform_task）。
2. 提交识别：`submit_recognition_task`（任务类型：变化检测/违建识别/地物分类）。
3. 轮询结果：`get_recognition_result` → 疑似变化图斑 + 置信度。
4. 落图与闭环：结果推送 GIS 前端叠加展示；高置信度变化可引导用户
   发起复核派飞（回到 plot-inspection 流程）。

## 边界

- recognition 子代理只读 + 提交识别任务，**无任何飞控权限**（工具集配置隔离，
  调不到 take_off / dispatch_drone）。
