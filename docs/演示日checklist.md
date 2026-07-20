# 演示日 Checklist（2026-07-22 深圳）

> 主秀 = 演示版（GIS 分屏，5173）。正式版（DeerFlow）作为"平台化演进"环节展示。
> 以下按时间顺序执行；每一步有验证命令,全绿再进下一步。

## 一、到场先做（网络必变,预留 30 分钟）

1. **确认本机 IP 与 VPN**（现场 Wi-Fi 下 en0 必是新 IP）：
   ```bash
   ipconfig getifaddr en0                       # 记下新 IP
   curl -m 5 http://192.168.101.21:8998/nacos/  # VPN 到 Nacos 通?
   curl -m 5 http://192.168.101.21:10009/       # 平台通?
   ```
   VPN 不通先修 VPN（历史上会整段抖断,重连即可）。
2. **改注册 IP 并重启 mcp-services**：
   - `正式开发/mcp-services/.env` → `MCP_SERVICE_IP=<新 IP>`
   - 重启 runner（kill 用 `lsof -ti TCP:8201 -sTCP:LISTEN`,别不带 -sTCP:LISTEN——会误杀桥/BFF）
   - 现为**八域**（8201-8204 + 8206-8209）；Nacos 并发注册在 VPN 上会集体超时,
     runner 已按端口错峰+退避重试,等日志里"已注册/已更新"两词合计 8 条再进下一步
3. **起 nacos_bridge**（它会自动把 DeerFlow 配置跟到新 IP,不用手改 extensions_config.json）。
4. **跑平台契约冒烟**（接口漂移早发现——平台侧仍在活跃迭代）：
   ```bash
   cd 正式开发/mcp-services && PYTHONPATH=src .venv/bin/python scripts/contract_smoke.py
   ```
   19 项断言全绿再继续；任一红项说明平台接口变了,当场排查别硬演。

## 二、起服务（顺序）与验证

| # | 服务 | 端口 | 验证 |
|---|---|---|---|
| 1 | 演示版后端 `cd backend && uv run uvicorn app.main:app --port 8000` | 8000 | `curl localhost:8000/api/config` → **必须 `"agent_mode":"llm"`**（scripted 说明 .env 的 LLM_API_KEY 没生效或带了 override） |
| 2 | 演示版前端 `cd frontend && npm run dev` | 5173 | 浏览器打开,地图右上角**不能**出现"Canvas 2D(降级)"——见"真机 WebGL"节 |
| 3 | mcp-services（见上一节,已起） | 8201-04 + 8206-09 | `for p in 8201 8202 8203 8204 8206 8207 8208 8209; do curl -m 3 localhost:$p/healthz; done` 八个全 ok |
| 4 | 审批服务 | 8205 | `curl localhost:8205/healthz` |
| 5 | nacos_bridge（已起） | — | 日志出现"已同步 8 个 server 到 DeerFlow" |
| 6 | DeerFlow Gateway | 8001 | `curl -X POST localhost:8001/api/threads -d '{}' -H 'Content-Type: application/json'` 返回 thread_id;日志 **`loaded 45 tool(s)`**（八域全发现）且**无 `Skipping MCP server`**（有=工具发现失败,重启 Gateway） |
| 7 | BFF | 8300 | `curl localhost:8300/api/config` → `"agent_mode":"deerflow"` |
| 8 | DeerFlow 前端（可选,平台化环节用） `pnpm exec next dev --webpack` | 3000 | 首次要 /setup 建管理员账号;**必须 --webpack**（Turbopack 中文路径 panic） |

启动命令细节见 `正式开发/README.md` 的"本地全链路启动"。

## 三、真机 WebGL 人工检查（无头测试永远盖不到的盲区）

演示机上人工过一遍（无头环境走 Canvas2D 降级,MapLibre 适配器只有真机能验）：

- [ ] 5173 地图正常渲染,右上角**没有**"Canvas 2D(降级) · WebGL 不可用"角标
- [ ] 发"查一下图斑" → 图斑多边形落图、点击有高亮
- [ ] 规划航线 → 航线线条 + 航点渲染、无人机 Marker 位置正确（历史 bug:Marker 必须先 setLngLat 再 addTo,反了崩 `reading 'lng'`）
- [ ] 打开航线编辑器 → iframe 加载、拖航点、保存回传成功
- [ ] 起飞后遥测动画推进(底栏进度/地图动点)

## 四、演示叙事红线（勿动）

- GM-04 规划 multi_cover 合并 GM-02/GM-03、6.3km/19min、节省约 25 分钟——依赖
  `backend/app/core/routes.py` 的 RESERVE_RATIO=0.15 与 `_survey_min`,**演示前不改这两处、不动 mock 坐标**。
- 高危红线话术:对话说"我确认起飞"必须被拒并引导点卡片——彩排时验一次。
- `UAV_CREATE_REAL_TASK=0`、`UAV_REAL_PUBLISH=0` 双开关确认关闭(除非现场安排真飞并已安全审批)。

## 五、彩排流程（演示前一天完整走一遍）

1. 演示版主线:查图斑 → 周边无人机 → GM-04 规划(合并叙事) → 飞前检查 → 确认卡片起飞 → 遥测 → 编辑器手动调整。
2. 批量排期:"把这些图斑按优先级排期,本周飞完,每天不超过3架" → 排期表 → 确认执行第 1 天。
3. 平台化环节(可选):3000 端口 DeerFlow 原生界面,一句话全流程 + 原生确认卡片。
4. **P0 四新域串场（正式版新增能力,一条自然对话线）**：
   "现在有什么告警" → "庙头镇那台机健康状况怎么样" → "这条航线会不会穿禁飞区" →
   "这批图斑下周飞完帮我排一下避开下雨天" → "这次任务拍了哪些照片"。
   注意:告警/排期/照片墙在 GIS 前端(8300)是**文本兜底**(无专用可视化指令),
   若要落图效果走 3000 或口头带过;`create_scheduled_task` 落库需 `UAV_CREATE_REAL_TASK=1`,
   演示排期建议(suggest_schedule 只算不写)不需要开,**真落定时任务才需要,谨慎**。
5. 兜底预案:**现场断网/LLM 挂** → 演示版后端以 `AGENT_MODE=scripted` 重启(秒级响应、可脱网,38 条话术全兜底);**演完必须不带 override 重启回 llm**。

## 六、撤场

- [ ] 平台测试航线清理:跑 `UAV_MCP_API_KEY=... mcp-services/.venv/bin/python eval/run_eval.py 26`(其清理步骤会扫掉本机状态里的平台航线),或到平台删"低空智察Agent-"前缀航线
- [ ] 若在现场建过 flighttask:平台确认无"待执行"任务残留（防自动调度器执行）
