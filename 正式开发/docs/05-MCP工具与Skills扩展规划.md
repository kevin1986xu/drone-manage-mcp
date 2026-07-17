# 05 - MCP 工具与 Skills 扩展规划

> 依据：通读 `drone-mange-gitee/ruoyi-modules` 全部 8 个模块（约 1900 个 Java 文件）的
> controller/service/domain 与 MQTT/Redis/InfluxDB 链路后，对照现有四域 MCP 的能力缺口
> 得出。所有能力均有平台代码证据（文中标注类名），不是想象出来的接口。
> 日期：2026-07-17。

## 0. 现状基线

已上线四域（单进程 8201-8204，Nacos 注册，X-API-Key 鉴权）：

| server | 工具 | 覆盖的平台面 |
|---|---|---|
| uav-drone-dispatch-mcp | query_plots / find_nearby_drones / get_drone_status / dispatch_drone🔒 / create_task_plan🔒 / get_plan_progress | flyWorkZone(zoneType=图斑)、device_registration、OSD 最新值 |
| uav-route-planning-mcp | generate_route / get_route_detail / explain_route / open_route_editor（+编辑器 REST） | planDynamicRoute 图斑巡检算法 |
| uav-preflight-mcp | check_weather / check_battery / check_route_obstacle / check_drone_obstacle / check_airspace / preflight_check | Open-Meteo 自查 + 平台气象；空域为占位 |
| uav-flight-task-mcp | take_off🔒 / get_task_status / get_task_report / list_task_history | flighttask 创建（默认只建不发）、本地估算遥测 |

🔒 = confirm_token 人在环。**平台还有约 70% 的业务面没有暴露给 Agent**，下文按域展开。

## 1. 平台能力全景（盘点结论速览）

| 模块 | 关键能力（证据） |
|---|---|
| drone-manage（核心） | 任务全生命周期含**断点续飞/失败重试/航线优化/重排期**（FlightTaskController）；设备/机场管理；**告警+HMS 健康**（DroneAlertController/DeviceHmsQueryController）；**媒体+相机覆盖计算+WebODM 三维重建/正射**（MediaFileController/CameraCoverageController/WebOdmModelingController）；**直播**（LiveStreamController：开/停/切镜头/画质）；**DRC 实时控制+机场控制**（DrcController/DockController：返航/急停/舱盖/指点飞行/起飞至点位/限高设置）；**电子围栏**（FlyWorkZone 的 zoneType 除图斑外还有禁飞区/限高区/限速区/警告区）；**平台自带飞前天气红黄绿+人工确认后下发**（FlightWeatherController——与我们的人在环同构！）；固件升级；OSD/轨迹时序（InfluxDB）；KMZ 航线导入导出 |
| cloud-sdk（大疆 Cloud API） | 相机/云台细粒度控制（拍照/录像/变焦/对焦/热成像测光）、**喊话器 TTS/探照灯**（DRC 下行）、POI 环绕、机场调试域（舱盖/推杆/充电/空调/重启）、媒体 STS 直传与指纹去重、OTA、飞行权限抢占 |
| other-drone-manage | **吉威(Geoway)多厂商接入**（云梦/应城端点）：设备拓扑同步、直播开流+ZLM 代理、遥测 InfluxDB 查询——证明多厂商抽象已存在 |
| task-dispatch | **派单协作**：主任务按 MultiPolygon 自动拆分子任务、指派/群发抢单/接拒单、成果上传-审核-评价、与 drone-manage 飞行任务联动同步（TdTask/TdAssignment/DroneTaskResultSyncScheduler） |
| ai-identification-consumer | **AI 识别闭环**：视频/图片批量/流式推理提交，RabbitMQ 消费识别结果，**像素框→地理坐标反投影**（ProjectionCalculator：OSD+相机内参），**告警规则引擎**（5 种算子：置信度/多类别/同图相交/IoU/数量统计，coord_hash 去重聚合），大屏 10+ 统计接口 |
| ruoyi-workflow | **自研审批流引擎**：DRONE_TASK_AUDIT_V1 已初始化（发起→审核→飞行执行→终态），支持转办/加签/撤回/退回，节点绑定 sys_role |
| ruoyi-system | 用户/角色/5 级数据权限（dataScope）、菜单权限树——"Agent 权限≤用户权限"的落点 |
| ruoyi-file | MinIO 主存储、飞行视频按 missionId/deviceSn 归档、指纹去重 |

## 2. 新增 MCP Server 分组建议

**分组原则**（延续现有架构决策）：
1. **按业务域切,而不是按平台模块切**——Agent 视角的"一件事"聚在一个 server;
2. **风险分层**——高危写操作集中到少数 server,便于拦截器硬白名单与审计收口;
   读操作域可以大胆开放给 router 模式做长尾发现;
3. 单进程多 server 继续（共享状态与平台客户端）,每个 server 独立注册 Nacos;
4. 所有高危写沿用 confirm_token（审批服务唯一签发）,新增"**紧急动作白名单**"例外（见 §4）。

### 2.1 uav-alert-mcp —— 告警与健康域（P0,纯读+低危写,演示价值高）

| 工具 | 平台依据 | 风险 |
|---|---|---|
| list_alerts(status/level/device/time) | /api/alerts/list | 读 |
| get_alert_detail(alert_id) | /api/alerts/{id} | 读 |
| handle_alert / ignore_alert(alert_id, note) | /api/alerts/{id}/handle·ignore | 低危写 |
| get_device_health(drone_id) | HMS /devices/hms + DroneOnlineStatus | 读 |
| get_unhandled_count() | /api/alerts/unhandled/count | 读 |

场景话术："现在有什么告警？""庙头镇那台机健康状况怎么样？""这条告警处理掉"。

### 2.2 uav-media-mcp —— 媒体与成果域（P0,读为主,补齐举证闭环）

| 工具 | 平台依据 | 风险 |
|---|---|---|
| list_media(task_id/plot_id/type/time) | /media/page | 读 |
| get_media_link(file_id) | /media/download/{fileId}（返回链接不搬文件） | 读 |
| get_camera_coverage(task_id) | /media/coverage/calculate（GeoJSON,可直接落图） | 读 |
| start_3d_modeling(flight_task_id)🔒 | /media/webodm/modeling/{id}/start（重资源,要确认） | 高危写 |
| get_modeling_status / get_modeling_result_link | 同上 status/download | 读 |
| list_flight_videos(task_id) | FlightHistoryVideo by missionId | 读 |

与现有 get_task_report 打通：报告里的"照片归档"从口径描述升级为真实文件清单+缩略链接;
GIS 前端可加 show_media 指令展示成果照片墙。

### 2.3 uav-live-mcp —— 直播与遥测回放域（P1,演示效果炸裂）

| 工具 | 平台依据 | 风险 |
|---|---|---|
| get_live_capacity(drone_id) | /live/capacity | 读 |
| start_live(drone_id, camera?)  | /live/start（吉威机型走 other-drone-manage /device/live-stream/start） | 中危写* |
| stop_live / switch_camera / set_live_quality | /live/stop·switch-camera·quality | 中危写 |
| get_telemetry_history(drone_id, range) | InfluxDB OSD_DRONE/OSD_DOCK | 读 |
| get_flight_trajectory(task_id) | FLIGHT_TRAJECTORY（轨迹回放落图） | 读 |

*直播只开视频流不动飞行器,建议免 token 但入审计;GIS 前端加 show_live 指令内嵌播放器
（平台已有 ZLMediaKit/Agora,拉流地址现成）。

### 2.4 uav-flight-control-mcp —— 实时飞行控制域（P1,全域高危,单独收口）

| 工具 | 平台依据 | 风险 |
|---|---|---|
| return_home(drone_id) | DockController service=return_home | 紧急白名单** |
| emergency_stop(drone_id) | service=emergency_stop | 紧急白名单** |
| pause_task / resume_task(task_id) | wayline pause/recovery（cloud-sdk flighttaskPause/Recovery） | 高危🔒 |
| fly_to_point(drone_id, lon, lat, alt)🔒 | /devices/{sn}/jobs/fly-to-point | 高危🔒 |
| takeoff_to_point(drone_id, …)🔒 | jobs/takeoff-to-point | 高危🔒 |
| speaker_tts(drone_id, text)🔒 | cloud-sdk droneSpeakerTTSSet/PlayStart（喊话驱离） | 高危🔒 |
| light_control(drone_id, on/off) | droneLight*（探照灯） | 中危 |
| set_height_limit(drone_id, m)🔒 | /api/tasks/setDroneHeightLimit | 高危🔒 |
| dock_cover(drone_id, open/close)🔒 | cloud-sdk coverOpen/Close | 高危🔒 |

**§4 详述:返航/急停是"止损动作",走确认流程反而增加风险——建议免 token + 强审计 + 事后通知。
DRC 摇杆级控制（stick_control）**不建议**做成 MCP 工具:LLM 不适合闭环操纵,保留给人。

### 2.5 uav-airspace-mcp —— 空域与电子围栏域（P0,把 preflight 空域占位变真）

| 工具 | 平台依据 | 风险 |
|---|---|---|
| list_zones(type=禁飞区/限高区/限速区/警告区) | flyWorkZone（zoneType 复用,我们只用过"图斑"！） | 读 |
| check_route_conflict(route_id) | 航线 WKT 与围栏求交（几何计算在 MCP 侧,geo.py 已有基础） | 读 |
| create_zone(type, geometry, expire?)🔒 | POST /flyWorkZone（临时管制区） | 高危🔒 |
| delete_zone(zone_id)🔒 | DELETE /flyWorkZone | 高危🔒 |

**preflight 的 check_airspace 立即受益**：从"数据源未接入请人工核实"升级为真实围栏冲突检测
（航线穿越禁飞区/超限高区直接 fail + 给出冲突多边形落图）。这是现有 40 条评测里
两条"注意"项的根治方案。

### 2.6 uav-recognition-mcp —— 智能识别域（P1,依赖 AI 平台部署,smart-recognition skill 的地基）

| 工具 | 平台依据 | 风险 |
|---|---|---|
| submit_inference(task_id, type=video/image_batch/stream, scene) | /ai/tasks/submit·batch-submit·stream-submit | 中危写 |
| get_inference_status / list_inference_results(task_id) | /ai/tasks、/ai/results/list（含地理坐标!） | 读 |
| list_ai_alerts(scene/level/time) | /ai/alerts/list（coord_hash 聚合后的规则告警） | 读 |
| mark_ai_alert_read / rebuild_alerts(task_id) | /ai/alerts/read·rebuild | 低危写 |
| get_alert_rules / update_alert_rule🔒 | AiAlertRuleProperties（Nacos 配置,改规则要确认） | 高危🔒 |
| get_situation_stats(dimension) | /ai/dashboard/*（趋势/分布/热点/地图点位,10+ 接口按需聚合） | 读 |

识别结果自带 target_longitude/latitude（像素框反投影,ProjectionCalculator）——
**可直接落图**,GIS 前端 show_map 加 ai_alerts 图层即可。

### 2.7 uav-task-schedule-mcp —— 任务排期与调度域（P0,平台调度能力全在这、我们只用了零头）

现有 create_task_plan 是**本地确定性排期**（优先级+就近合并+逐日装箱）;平台侧还有一整套
执行模式与调度动作没暴露：FlightTask.executionMode 支持 immediate/scheduled/recurring/continuous,
外加重排期/失败重试/断点续飞/航线连接优化四个高价值动作（均在 FlightTaskController）。

| 工具 | 平台依据 | 风险 |
|---|---|---|
| suggest_schedule(plot_ids/task_ids, constraints) | **排期建议**：综合天气窗口（FlightWeatherController 红黄绿逐日预判）+ 设备空闲档期（wayline-jobs 已排任务）+ 优先级/截止时间,输出建议排期表与理由;本地算法,复用 batch.py 装箱思路 | 读 |
| create_scheduled_task(route_id, drone_id, execution_time)🔒 | executionMode=scheduled 定时任务 | 高危🔒 |
| create_recurring_task(route_id, drone_id, cron/date_range)🔒 | executionMode=recurring 循环任务（每日巡查的正解,batch-patrol 逐日装箱可升级为平台原生循环） | 高危🔒 |
| list_scheduled_tasks(range) / cancel_scheduled_task(task_id)🔒 | /api/tasks/list + cancel | 读 / 高危🔒 |
| reschedule_task(task_id, new_time)🔒 | /api/tasks/planNewTask/{taskId} 重排期 | 高危🔒 |
| retry_failed_task(job_id)🔒 | /api/tasks/failTaskRetry/{jobId}（自动重新排期并下发） | 高危🔒 |
| resume_from_breakpoint(job_id)🔒 | /api/tasks/breakPointFlight/{jobId} 断点续飞 | 高危🔒 |
| optimize_route_connection(task_id) | /api/tasks/optimizeRoute/{taskId}（机场→航线起点安全连接优化） | 低危写 |
| sync_tasks_to_device(task_ids)🔒 | /api/tasks/sync/batch 批量同步 | 高危🔒 |
| get_schedule_conflicts(drone_id, range) | 设备档期冲突检测（wayline-jobs 时间窗求交,本地计算） | 读 |

**排期话术样例**："这 8 个图斑下周飞完,帮我排一下,避开下雨天""周三的任务挪到周四上午"
"失败的那趟明天重飞""D-07 这周还排得下吗"。
**安全注意**：凡产生"待执行"平台任务的动作都是高危——平台自动调度器会真执行
（UAV_CREATE_REAL_TASK 语境同款坑）;suggest_schedule 只算不写,确认后才落库。

### 2.8 uav-dock-debug-mcp —— 机场调试与远程运维域（P1,cloud-sdk 调试域的完整暴露）

cloud-sdk `AbstractDebugService` 有一整套机场远程调试指令,当前一个都没暴露。
运维画像刚需（"帮我开一下 XX 机场的舱盖看看""给机器充上电"）：

| 工具 | 平台依据（AbstractDebugService/DockController） | 风险 |
|---|---|---|
| debug_mode(dock_id, open/close)🔒 | debugModeOpen/Close（进调试模式才能做下列动作） | 高危🔒 |
| dock_cover(dock_id, open/close/force_close)🔒 | coverOpen/Close/ForceClose 舱盖 | 高危🔒 |
| dock_putter(dock_id, open/close)🔒 | putterOpen/Close 推杆 | 高危🔒 |
| drone_power(dock_id, on/off)🔒 | droneOpen/Close 舱内无人机开关机 | 高危🔒 |
| charge_control(dock_id, on/off)🔒 | chargeOpen/Close 充电 | 高危🔒 |
| air_conditioner(dock_id, mode) | airConditionerModeSwitch 空调（制冷/制热/除湿） | 中危 |
| supplement_light(dock_id, on/off) | supplementLightOpen/Close 补光灯 | 中危 |
| device_reboot(dock_id)🔒 | deviceReboot 重启机场 | 高危🔒 |
| battery_maintenance(dock_id, on/off)🔒 | batteryMaintenanceSwitch 电池保养 | 高危🔒 |
| get_dock_environment(dock_id) | 机场 OSD（温湿度/风速/雨量/舱内状态,DockOsdController） | 读 |

调试域动作有**顺序依赖**（先进 debug_mode → 开舱盖 → 无人机开机…）,天然适合 skill 化
（见 3.2 dock-maintenance）,工具描述里要写清前置条件,防止模型乱序调用。

### 2.9 uav-dispatch-order-mcp —— 派单协作域（P2,多人协同场景才需要）

| 工具 | 平台依据 | 风险 |
|---|---|---|
| create_dispatch_task(name, geometry, deadline) / split_subtasks | /task-dispatch/tasks、draft-and-split（MultiPolygon 自动拆分） | 中危写 |
| assign_order(subtask, unit/user)🔒 / broadcast_order🔒 | /assignments/assign·broadcast | 高危🔒 |
| list_orders / accept_order / reject_order | /assignments | 低危写 |
| review_result(result_id, approve/reject, opinion)🔒 | /results/approve·reject | 高危🔒 |
| get_dispatch_progress(task_id) | /process-logs + 状态机 | 读 |

### 2.10 uav-ops-mcp —— 固件与设备资产域（P2,管理员画像）

get_latest_firmware / start_firmware_upgrade🔒 / get_upgrade_progress（DeviceFirmwareController + Redis upgrading:*）、
list_device_logs / request_log_upload（DeviceLogsController）、
register_device🔒 / bind_device🔒（DeviceRegistrationController）。
（机场侧重启/充电/电池保养已归入 2.8 机场调试域,此域只留固件、日志、资产登记。）

### 2.11 uav-workflow-mcp —— 平台审批流域（P2,双层审批打通）

start_approval(business_type, business_id) / list_my_pending / approve🔒 / reject🔒 / delegate / get_process_logs
（ruoyi-workflow 自研引擎,DRONE_TASK_AUDIT_V1 已可用）。

**与 confirm_token 审批服务的关系**：平台工作流=业务级审批（这个任务该不该飞,审核员角色签核）;
confirm_token=操作级确认（这一下起飞由在场的人点）。两层不互替。M4 打通方向：
Agent 发起的任务先走平台工作流,工作流到 FLIGHT_EXECUTION 节点后,现场起飞仍走确认卡片。

## 3. Skills 规划

现有：plot-inspection（成熟,40 条评测双百）、batch-patrol（占位）、smart-recognition（占位）。

### 3.1 补全占位的两个

| skill | 编排（用到的域） | 关键纪律 |
|---|---|---|
| **batch-patrol 批量巡查** | create_task_plan 排期 → 逐日执行（现有）+ **失败重试/断点续飞**（flight-control 域 pause/resume + 平台 failTaskRetry/breakPointFlight）+ 每日完成后自动 get_task_report 汇总 | 排期确认=授权当日批次;跨天执行前重新 preflight;失败架次先报告再重试（重试要新确认） |
| **smart-recognition 智能识别** | 任务完成 → submit_inference（按场景选 scene）→ 轮询结果 → list_ai_alerts 落图 → 高危告警触发 emergency-response | 识别是异步长任务,提交后立即告知用户预计时长,不阻塞对话;告警只转述规则引擎结论,不自行判定 |

### 3.2 新增建议（按演示/业务价值排序）

1. **emergency-response 应急响应**（alert + flight-control + live 域,演示王牌）
   触发：AI 告警/人工告警/用户口述("XX 位置疑似违建有人施工")。
   编排：定位事发点 → find_nearby_drones → takeoff_to_point🔒（确认卡片）→ start_live（推流给用户看）→
   speaker_tts🔒 喊话驱离 → 拍照取证（相机控制）→ return_home → 生成处置记录。
   红线：全程每个高危动作独立确认;喊话内容需用户核准原文。

2. **evidence-report 成果举证报告**（media + recognition 域）
   任务完成 → get_task_report + list_media 照片墙 → 需要正射时 start_3d_modeling🔒 →
   成果链接归档 → （对接派单时）upload_result 回传。把现在报告里的"归档说明"变成真实交付物。

3. **airspace-guard 空域合规**（airspace 域）
   规划航线自动 check_route_conflict;"明天上午这片临时管制" → create_zone🔒(带过期时间) →
   受影响的已排期任务自动检出并提醒重排。

4. **daily-situation 每日态势/日报**（alert + recognition dashboard + task history）
   "今天飞得怎么样" → 任务完成率、告警趋势、热点告警 TOP、成果统计 → 生成结构化日报（可定时,DeerFlow scheduled-tasks 已有入口）。

5. **device-health-inspection 设备巡检**（alert HMS + ops 域）
   "给所有机场做个体检" → 逐台 get_device_health + 电量/固件版本盘点 → 异常项建议（升级/维保走确认）。

6. **live-observation 现场观察**（live 域）
   "让我看看 XX 现在的画面" → 就近机在飞则直接开流,不在飞则引导走 emergency-response 的派飞分支。

7. **smart-scheduling 智能排期**（task-schedule 域,create_task_plan 的进化形态）
   "这些图斑下周飞完,避开坏天气" → suggest_schedule（天气窗口×设备档期×优先级×截止时间,
   给排期表+理由）→ 用户确认 → 批量 create_scheduled_task/recurring_task 落库 →
   失败架次自动进 retry 建议。与现有 batch-patrol 的关系：batch-patrol 管"当下逐日执行",
   smart-scheduling 管"未来时间表落到平台定时/循环任务",共用装箱算法。
   纪律：suggest 只算不写;每个落库动作独立确认;改期/取消要复述影响面（连带的检查/派单）。

8. **dock-maintenance 机场维护**（dock-debug + ops 域）
   "检查一下 XX 机场""给无人机充电""开舱盖我看看" → get_dock_environment 体检 →
   按需 debug_mode→舱盖/充电/空调 顺序操作（每步确认）→ 操作完成必须退出调试模式并复位。
   纪律：**顺序依赖硬编码进流程**（进 debug → 动作 → 复位 → 退 debug）,禁止跳步;
   有飞行任务排期临近的机场拒绝进入调试模式。

### 3.3 Skill 工程纪律（血泪经验,新 skill 必须遵守）

- **allowed-tools 全局并集**：每个 skill 必须完整声明自用工具,否则被其他 skill 的白名单挤掉（坑①）;
- **单项追问映射**要写进流程（"电量够不够"→check_battery 那样的显式映射,否则模型凭记忆答）;
- **行动纪律段**：执行中禁止重复规划、检查通过直接行动不反问、高危动作生成确认单后停止等待;
- 工具描述改完,mcp-services 和 Gateway **都要重启**。

## 4. 安全设计要点

1. **风险三档**：读（放开,可进 router 长尾）/ 低中危写（审计,部分免 token）/ 高危写（confirm_token,
   全部集中在 flight-control、airspace、dispatch-order 的少数工具,拦截器白名单按域收口）。
2. **紧急动作白名单**（新概念）：return_home / emergency_stop 是止损动作,等待人工确认反而危险。
   建议：免 token 执行 + 执行即通知（播报机制已有）+ 审计强标记 + 事后需人工在平台关单确认。
   评测要加反向用例："紧急停止"必须秒执行、"起飞"必须走卡片——两种路径不能混。
3. **Agent 权限 ≤ 用户权限落地**（ruoyi-system 对接,M4）：
   前端登录态 token 透传 → BFF/DeerFlow context → MCP 拦截器把 token 注入请求头 →
   mcp-services 调平台时带用户身份,平台 dataScope 自动过滤;高危工具在 MCP 侧再按
   sys_menu 权限（如 `drone:task:publish`）做前置校验。当前的 X-API-Key 是服务间信任,不替代用户级授权。
4. **DRC 摇杆不做成工具**：LLM 不做实时闭环操纵;喊话/探照灯这类 DRC 下行"离散指令"可以做。

## 5. 落地优先级与依赖

| 批次 | 内容 | 依赖 | 粗估 |
|---|---|---|---|
| **P0（下一里程碑）** | uav-airspace-mcp（根治 preflight 空域占位）+ uav-alert-mcp + uav-media-mcp + **uav-task-schedule-mcp（suggest_schedule/定时循环/重排期/失败重试/断点续飞）**;evidence-report + smart-scheduling skill;评测集扩 ~20 条 | 平台现有接口,无新部署 | 5-6 天 |
| **P1** | uav-live-mcp（GIS show_live）+ uav-flight-control-mcp(含紧急白名单机制) + **uav-dock-debug-mcp（机场调试全套）**;emergency-response + dock-maintenance skill;batch-patrol 补全 | 直播拉流地址联调;喊话器/探照灯/舱盖需真机验证 | 6-8 天 |
| **P1.5** | uav-recognition-mcp + smart-recognition 补全 + daily-situation | AI 推理平台在现网可用性待确认（RabbitMQ 队列/模型服务） | 3-5 天 |
| **P2** | dispatch-order / ops(固件资产) / workflow 三域 + 权限透传（Agent≤用户） | 多用户/生产化诉求明确后 | 5-8 天 |

**建议 P0 先动 uav-airspace-mcp**：工作量最小（复用 flyWorkZone 客户端与 geo.py）、
直接消除现有评测中两个"⚠ 注意"占位、且"禁飞区冲突检测"在演示里是安全叙事的强素材。
**次优先 uav-task-schedule-mcp 的 suggest_schedule**："避开坏天气把这批图斑排完"是
业务方最常提的诉求,纯读不碰平台写面,风险为零、演示话术自然。
