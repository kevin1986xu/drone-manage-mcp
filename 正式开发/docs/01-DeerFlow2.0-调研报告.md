# DeerFlow 2.0 调研报告

**日期**：2026-07-16
**方法**：克隆 [bytedance/deer-flow](https://github.com/bytedance/deer-flow) 仓库通读一手代码（backend/packages/harness、app/gateway、app/channels、skills、config）+ 公开资料交叉验证。结论均标注证据来源。
**评估目标**：作为"无人机飞控 Agent 平台"正式版底座的适配性（对话式飞控、MCP + Skills 能力沉淀、智能识别等场景）。

---

## 一、项目基本面

| 项 | 实况 |
|---|---|
| 定位 | **通用 SuperAgent 底座（harness）**——2.0 是彻底重写，与 1.x（深度研究框架）无共享代码 |
| 底座 | LangGraph + LangChain（与我们演示版同源，迁移成本低的根本原因） |
| 许可证 | MIT（无绑定风险） |
| 社区 | 77.2k star / 10.5k fork；2.0.0 正式发布 2026-06-25；主干已在 2.1.0-dev；594 open issues（活跃但年轻） |
| 版本纪律 | 2.0→2.1 有 breaking change（memory 子系统重构），但 CHANGELOG 迁移说明与自动迁移代码写得规矩 |

## 二、架构（读 backend/AGENTS.md + 代码确认）

```
Nginx(2026 统一入口)
 ├─ Frontend：Next.js Web UI（3000）+ TUI
 └─ Gateway API(8001)：FastAPI + 内嵌 LangGraph 兼容运行时（SSE 流式）
      ├─ agents/lead_agent    主 Agent（中间件链）
      ├─ subagents/           子代理委派：内置 general-purpose/bash；
      │                       自定义子代理走 config.yaml 声明式注册（模型/超时/轮数各配）
      ├─ mcp/                 MCP 集成（详见 §四）
      ├─ skills/              Skills 发现/加载/解析（详见 §三）
      ├─ memory/              可插拔记忆后端（2.1 起 manager_class 可换）
      ├─ sandbox/             本地 / Docker AIO 沙箱
      ├─ models/              模型工厂（详见 §六）
      └─ app/channels/        IM 通道：微信/企微/钉钉/飞书/Slack/Telegram/Discord/GitHub
 另有：scheduler（定时任务，复用同一 run 生命周期）、多用户 AuthMiddleware
```

## 三、Skills 系统（与我们的诉求直接对口）

- Skill = **SKILL.md**（frontmatter `name/description` + markdown 正文），与 Claude Code 技能格式同构，甚至内置 `claude-to-deerflow` 迁移 skill
- 目录：`skills/public`（内置 20+：deep-research/ppt/图表/图像生成等）+ `skills/custom`（自有技能，gitignored）
- 安全：SkillScan 确定性扫描（阻断 CRITICAL）+ LLM 上下文复审
- **对我们的意义**：「图斑核查派飞流程」「批量巡查排期」「智能识别」等业务流程各写一个 SKILL.md 即成为对话可触发的能力——正是"能力沉淀为 Skills"的设想

## 四、MCP 集成（读 deerflow/mcp/* 确认）

- 配置：`extensions_config.json` 标准 `mcpServers`（stdio / HTTP+SSE / streamable），支持 OAuth（client_credentials/refresh_token）、每工具超时
- **运行时热更新**：`PUT /api/mcp/config`（带锁校验）+ 工具缓存/会话池重载端点——不改文件不重启即可增删 server（这是做 Nacos 同步桥的基础）
- `mcpInterceptors`：每次工具调用可拦截（注入鉴权头/短路返回，基于 langchain-mcp-adapters 拦截器协议）
- **没有注册中心发现**：grep 全仓库无 nacos/registry/discovery 集成——接 Nacos 需 nacos-mcp-router 或自建同步桥（见 02 文档）
- **对我们的意义**：演示版 4 个 FastMCP server（streamable-http）零改造可接

## 五、人在环（HITL）——飞控场景的关键缺口

**结论：没有"高危工具须人工确认"的工具级门禁。** 现有机制：

- LangGraph 节点级 `interrupt_before/after`（thread_runs API 暴露）——粒度是图节点，不是单个工具
- 内置 `ask_clarification` 工具（交互式运行时向用户提问；定时任务中不可用）
- 停止按钮 / SkillScan / 沙箱隔离——是"容器化防护"，不是"逐动作审批"

**对策（已验证可行）**：我们的 confirm_token 机制**长在 MCP 工具层内部**（take_off 无 token 自拒、token 一次性/绑定动作/防重放，且实测拦截过大模型伪造 token）——框架无关、原样带走。需补的是确认交互的呈现（用 ask_clarification 流承载，或改造其前端），POC 重点验证项。

## 六、模型与国产化（读 models/ 目录确认）

模型工厂内置 provider：**vLLM、MindIE（华为昇腾）**、DeepSeek/MiniMax/MiMo/StepFun 补丁、Claude、OpenAI-Codex 等。
私有化/信创路径比预期完整（昇腾推理直接有 provider），政企部署底子好。

## 七、其他与场景相关的能力

| 能力 | 状态 | 对飞控平台的意义 |
|---|---|---|
| IM 通道（企微/钉钉/飞书/微信…） | 内置（app/channels/，含 run_policy） | "对话入口"白捡——在企微里@机器人下达巡查指令 |
| 定时任务 scheduler | 内置，复用统一 run 生命周期 | 常态化巡查排班的载体 |
| 多用户鉴权 | AuthMiddleware，含 no-auth→auth 迁移 | 平台化多人使用 |
| 运行 journal | runtime/journal.py | 审计留痕素材 |
| 前端 | 通用聊天 + 文件形态（Next.js） | **没有地图/态势/航线编辑**——需保留我们的 AG-UI 分屏前端对接其 LangGraph 兼容 API，或深改其 UI |

## 八、风险与已知短板汇总

1. **无工具级审批门禁**（§五）——靠我们工具层 confirm_token + 交互适配补
2. **无注册中心发现**（§四）——靠 nacos-mcp-router / 同步桥补
3. **长任务取向**：为"分钟~小时级"任务设计，lead-agent+中间件链比单 ReAct 重，**飞控对话单轮延迟需 POC 实测**
4. **UI 无 GIS 能力**：地图/航线/态势必须自建或复用演示版前端
5. **实时遥测主动播报**（MQTT→对话推送）需自建，其 message bus 可作载体
6. 项目年轻、迭代快：2.0→2.1 已有 breaking change；跟随升级需要预算维护人力
7. 其 Gateway 是"用户→Agent"的应用网关，**不是工具面治理网关**（详见 02 文档网关澄清一节）
