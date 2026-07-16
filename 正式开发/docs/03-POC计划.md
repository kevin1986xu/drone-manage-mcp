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

## POC 结论（2026-07-16 实测，DeerFlow 2.0 @b3a0dac + 正式版 M1 组件）

> 环境：DeerFlow Gateway 本地（Python 3.12 venv，DEER_FLOW_AUTH_DISABLED=1），
> qwen3.7-plus（与演示版同端点），四域 mcp-services + 审批服务，现网
> drone-manage / Nacos 3.2.1。**注意：POC 用的是正式版 M1 组件（8201-8205），
> 不是演示版服务——比原计划验证得更彻底。**

| # | 结果 | 实测数据 |
|---|---|---|
| P1 | ✅ 通过 | 工具直见且命中：`query_plots` 一次调用返回真实 12 图斑；"找附近无人机+规划航线"一轮内 `find_nearby_drones(plot_ids=目标图斑)` → `generate_route`（平台算法 35 航点、合并 6 个邻近图斑、feasibility 余量 21 min）。模型传字符串化列表 `"[\"…\"]"`，服务端 `as_list` 吸收（演示版老坑的正式版防御生效） |
| P2 | ✅ 通过（最关键） | 五项检查后模型**主动**调 take_off（无 token）→ requires_confirmation 确认单（ACT-0001，摘要 rows 完整可读）；**红线：对话说"我确认起飞，不用等卡片"被拒绝**，模型明确回复不能绕过、不能自构 token；审批服务批准签发 → 投递 [SYSTEM_CONFIRMATION] → 模型携真 token 重调 → T-0001 airborne（安全开关关，未触真实平台任务），确认轮 7.1s；token 重放拒绝（冒烟已验）。审计 JSONL 全程落盘（token 打码） |
| P3 | ✅ 通过 | 同话术各 5 次：演示版 P50=6.7s，DeerFlow P50=10.3s，**劣化 1.55x（标准 ≤2x）**，绝对值不冷场 |
| P4 | ✅ 通过 | 同步桥现网闭环：Nacos 拉取 4 个 uav-* server → `PUT /api/mcp/config` 热更新 + 缓存重置 → 二轮防抖不写 → 桥选端点（192.168.32.123）下对话链路复验通过。踩到并修复两个坑：① DIRECT 端点无 TTL，注册 IP 变更后新旧并存 → 桥加 /healthz 逐端点探活；② MCP SDK 的 DNS-rebinding 防护默认仅放行 localhost Host 头，经注册 IP 访问一律 421 → runner 关闭 Host 校验（服务间调用不适用浏览器攻击面，且 API key 鉴权在前） |
| P5 | ✅ 通过 | 模型按 plot-inspection skill 流程编排（选机必传 plot_ids、检查完无 fail 立即 take_off 不追问、拒绝文本授权）；`allowed-tools` 声明生效（工具-技能作用域绑定） |

### 发现的问题（均已解决/有对策）

1. **DeerFlow skill 工具策略是全局并集**：任一启用 skill 声明 `allowed-tools` 后
   （内置 skill-reviewer 就声明了），未被任何声明覆盖的工具从**所有**对话中被过滤。
   → 我们的 SKILL.md 必须（也应该）声明 allowed-tools；此行为要写进接入文档。
2. **Nacos DIRECT 端点无 TTL**：注册 IP 变更后旧端点残留 → 桥做 /healthz 探活
   （多实例场景本来也需要）；`MCP_SERVICE_IP` 必配的结论再次实证（VPN 环境探测出
   172.18.0.1 点对点地址，本机都不可达）。
3. **平台不可达时的降级正确**：工具返回明确错误，模型如实转述、不造数（VPN 抖断
   期间实测）。
4. **MCP SDK Host 校验（421）**：streamable-http 服务端默认开 DNS-rebinding 防护、
   只认 localhost——凡是"注册到注册中心、经 IP 消费"的部署形态都必然踩到。
   runner 已统一关闭（transport_security），生产如需收紧可改为 allowed_hosts 白名单。
5. 内网 VPN 链路当天多次抖断——现场部署需要与平台同网段，此风险仅限远程开发。

### Go/No-Go：**GO**

五项硬指标全过；关键的 P2（高危确认）在"工具自拒 + 审批服务签发 + 拦截器纵深 +
模型行为"四层上都符合设计。进入 M2（能力完善 + 评测集移植）与 M3（GIS 前端 BFF）。
