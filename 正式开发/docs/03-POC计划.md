# POC 计划（1~2 天）

**目的**：在启动正式开发前，用最小代价验证定稿链路的三个硬指标。不达标则回到 02 文档重议方案。

---

## 验证点与通过标准

| # | 验证点 | 做法 | 通过标准 |
|---|---|---|---|
| P1 | **对话→MCP 飞控链路** | DeerFlow 本地起栈（`make dev`），extensions_config.json 直配演示版 4 个 FastMCP 端点（8101-8104），对话走"查图斑→选机→规划航线" | 工具直见且命中正确；qwen/私有模型下链路完整跑通 |
| P2 | **高危确认体验（最关键）** | 对话触发 take_off（工具无 token 自拒并返回待确认单），观察 DeerFlow 如何呈现；再试 ask_clarification 承载确认对话，人工答复后携 token 二次调用 | 确认语义清晰可用、误触发风险可接受、拒绝路径（伪造/缺失 token）正确；若体验不达标，评估改其前端的工作量 |
| P3 | **单轮延迟** | 同一话术（"查00005并规划航线"）在演示版与 DeerFlow 各跑 5 次对比 | 单轮 P50 延迟劣化 ≤ 2 倍且绝对值可接受（现场对话不冷场） |
| P4 | Nacos 动态发现（次要） | 起 nacos-mcp-router（router 模式，指向 192.168.101.21:8998），对话让模型 search→use_tool 调到注册的 server | 能发现并成功转调即可（仅长尾场景用） |
| P5 | Skill 化验证（次要） | 把"图斑核查派飞"流程写成 `skills/custom/plot-inspection/SKILL.md` | 对话能按 skill 定义的流程编排工具 |

## 环境准备

- DeerFlow：`git clone bytedance/deer-flow`（已在 scratchpad 有副本）、`config.yaml` 配模型（先用现有 token-plan qwen3.7-max，OpenAI 兼容）、`extensions_config.json` 配 4 个 FastMCP
- 演示版 backend 起 4 个 FastMCP server（`uv run python -m app.mcp_servers.*`，Nacos 注册开着）
- drone-manage（192.168.101.21:10009）与 Nacos（:8998）沿用现网

## 产出

- `poc/` 目录：配置文件、SKILL.md 样例、延迟对比数据
- 03 文档末尾补《POC 结论》一节：三个硬指标的实测数据 + Go/No-Go 决定

## POC 结论（待填）

> 完成后填写：P1~P5 实测结果、发现的问题、Go/No-Go。
