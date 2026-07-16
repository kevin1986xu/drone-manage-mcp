# 正式开发工作区

本目录用于**无人机飞控 Agent 平台的正式版开发**（区别于仓库根目录的 P0/P1 演示版）。

## 目标形态（一句话）

以 **DeerFlow 2.0** 为 SuperAgent 底座，无人机业务能力沉淀为 **MCP 工具 + Skills**，
通过对话（Web / 企微 / 钉钉等）完成无人机飞控、批量巡查、智能识别等场景；
**Nacos 为注册与发现中枢，无人机平台（Java）零改动**。

## 定稿链路

```
DeerFlow 2.0 ──MCP──▶ [核心业务域：直连/同步桥]───▶ FastMCP 服务(Python) ──HTTP──▶ 无人机平台(Java，零改动)
     │                        ▲                            │
     └─MCP─▶ nacos-mcp-router ┴──发现──▶ Nacos 3.x ◀──注册──┘
             （长尾/新能力动态发现）
```

全程不引入 Higress；工具面治理 = DeerFlow 客户端 interceptors（注入 API key）
+ FastMCP 服务端校验 + 工具内 confirm_token 人在环（框架无关）。

## 文档索引

| 文档 | 内容 |
|---|---|
| [docs/01-DeerFlow2.0-调研报告.md](docs/01-DeerFlow2.0-调研报告.md) | 一手代码调研：架构、Skills、MCP、HITL 缺口、消息通道、国产化、社区状态 |
| [docs/02-架构定稿与建议.md](docs/02-架构定稿与建议.md) | 定稿链路、router 模式取舍、网关问题澄清、治理口径、现有资产迁移映射、风险清单 |
| [docs/03-POC计划.md](docs/03-POC计划.md) | 1~2 天 POC 的验证点、步骤与通过标准 |
| [docs/04-实现方案.md](docs/04-实现方案.md) | 零 fork 插件式集成、目录结构、审批设计、同步桥、BFF、子代理划分、里程碑 |

## 与演示版的关系

仓库根目录（backend/frontend/deploy/eval）是已交付的演示版，**继续维护、不动**。
正式版在本目录下推进；演示版的以下资产将平移复用（详见 02 文档 §迁移映射）：

- 4 个 FastMCP server（已注册 Nacos 3.2.1、已实测消费闭环）
- 工具内 confirm_token 人在环安全机制（含伪造 token 拦截实测）
- 38 条评测集与调优过的工具描述（llm 命中率 91%+）
- Nacos 注册模块、drone-manage API 客户端、Open-Meteo 气象自查
