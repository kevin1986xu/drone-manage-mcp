# 06 - 无人机 MCP 功能明细（按业务域）

> 组织方式：**主线八域**（航线 → 任务 → 调度 → 飞行 → 直播 / 操控 → debug → 成果）
> + **三个支撑面**（安全面 / 智能面 / 管理面）。
> 这是功能字典;分域决策与优先级论证见 [05-MCP工具与Skills扩展规划](05-MCP工具与Skills扩展规划.md)。
>
> 状态：✅ 已上线 · 🔵 P0 · 🟡 P1 · 🟠 P1.5 · ⚪ P2
> 风险：读 / 写 / 🔒 confirm_token 人在环 / ⚡ 紧急白名单（免 token+强审计）
>
> 注：域是**逻辑分类**,与物理 server 的对应关系见文末附录（如"成果报告"物理上在
> uav-flight-task-mcp,逻辑上属成果域）。

---

## 主线一：航线域

**定位**：从图斑到可执行航线。物理 server：uav-route-planning-mcp（8202）。

| 细分功能 | 工具/接口 | 说明 | 状态 | 风险 |
|---|---|---|---|---|
| 航线生成 | generate_route | 平台图斑巡检算法（边界拍照点对中+中心高空）;multi_cover 自动合并同航向带邻近图斑 / single 单图斑 | ✅ | 写(平台建航线) |
| 软约束重规划 | generate_route(replace_route_id) | "飞低一点"→altitude_m、"拍密一点"→photo_num、"只飞这块"→plot_ids/single;返回前后对比 change_vs_previous | ✅ | 写 |
| 续航可行性校验 | generate_route 返回 feasibility | within_budget=false 给 hint（降拍照数/减图斑/换机）,禁止硬报可行 | ✅ | 读 |
| 航线详情与版本 | get_route_detail(version?) | 航程/时长/覆盖图斑/与上一版本 diff;include_waypoints 取航点坐标 | ✅ | 读 |
| 规划决策解释 | explain_route | 结构化依据：合并原因/放弃原因/架次对比/续航预算,只转述不编造 | ✅ | 读 |
| 人工可视化编辑 | open_route_editor + 编辑器 REST | 免登录临时链接(10min);编辑保存→新版本→按索引回写平台(保留拍照动作) | ✅ | 写 |
| KMZ 导入导出 | import_route_kmz / export_route_kmz | 平台 /api/routes/file/upload·download,对接大疆司空生态 | ⚪ | 写/读 |
| 多航线类型 | generate_route(route_type) 扩展 | 平台 FlightRoute.routeType：航点(0)/面状正射(1)/倾斜(2)/带状(3)/几何体(4)/贴近摄影(5)——当前只用图斑巡检,测绘场景解锁正射/倾斜 | ⚪ | 写 |
| 建线选项查询 | get_route_create_options | 机型/载荷/镜头可选项（RouteDeviceCapabilityController） | ⚪ | 读 |

## 主线二：任务域

**定位**：飞行任务对象的生命周期（创建/状态/取消）。物理 server：uav-flight-task-mcp（8204）。

| 细分功能 | 工具/接口 | 说明 | 状态 | 风险 |
|---|---|---|---|---|
| 任务创建（经起飞） | take_off 内部 | 确认后创建本地任务+可选平台 flighttask（UAV_CREATE_REAL_TASK 开关,默认只建不发） | ✅ | 🔒 |
| 任务状态查询 | get_task_status | 双源：有平台任务以平台状态为准,否则本地按时长估算进度 | ✅ | 读 |
| 历史任务查询 | list_task_history(status/drone/limit) | 倒序列表,支持过滤 | ✅ | 读 |
| 任务取消 | cancel_task | 平台 PUT /api/tasks/cancel/{taskId} | 🔵 | 🔒 |
| 任务详情/清单 | list_tasks / get_task_detail | 平台 /api/tasks/list(权限过滤版 authorityList)、/{taskId} | 🔵 | 读 |
| 任务下发/同步 | publish_task / sync_tasks_batch | 平台 publish/{taskId}、sync/batch——**产生"待执行"即可能被自动调度器执行,一律人在环** | ⚪ | 🔒 |
| 任务导出 | export_task_records | wayline-jobs Excel 导出 | ⚪ | 读 |
| 飞行日志 | list_flight_logs(task/drone/workspace) | FlightLogController 每次飞行的详细记录——与设备调试日志(管理面)是两回事,复盘/追责用 | 🔵 | 读 |

## 主线三：调度域

**定位**：空间上派谁（选机锁机）+ 时间上何时飞（排期）。物理 server：uav-drone-dispatch-mcp（8201）+ 规划中的 uav-task-schedule-mcp。

### 3.1 空间调度（✅ 已上线）

| 细分功能 | 工具 | 说明 | 风险 |
|---|---|---|---|
| 图斑查询 | query_plots | 区域/编号(含尾号)/类型/批次/日期过滤;include_geometry 供落图 | 读 |
| 就近选机 | find_nearby_drones(plot_ids) | 距离基准=目标图斑;返回距离/电量/状态 | 读 |
| 设备状态 | get_drone_status | 名称模糊匹配→SN;电量/位置/在线/任务中 | 读 |
| 设备总览视图 | list_drones_overview | 平台 area-tree 区划分组（含各区告警数）+ 在线设备清单——"全市机场什么情况"一屏答 | 读 |
| 锁定无人机 | dispatch_drone | 锁机绑定图斑,防并发抢占 | 🔒 |
| 批量排期计划 | create_task_plan | 确定性装箱：优先级排序+就近合并架次(≤3图斑/3km)+逐日装箱+截止校验;计划确认=授权 | 🔒 |
| 计划进度 | get_plan_progress | 排期表推进状态（前端 show_plan） | 读 |

### 3.2 时间调度（🔵 P0,uav-task-schedule-mcp）

| 细分功能 | 工具 | 说明 | 风险 |
|---|---|---|---|
| **排期建议** | suggest_schedule | 天气窗口(平台红黄绿逐日)×设备档期×优先级×截止时间→建议排期表+理由;**只算不写** | 读 |
| 定时任务 | create_scheduled_task | 平台 executionMode=scheduled,指定 executionTime | 🔒 |
| 循环任务 | create_recurring_task | executionMode=recurring,日期区间内每日执行（每日巡查正解） | 🔒 |
| 排期清单/取消 | list_scheduled_tasks / cancel_scheduled_task | 未来任务视图 | 读 / 🔒 |
| 重排期 | reschedule_task | 平台 planNewTask/{taskId}（"周三的挪到周四上午"） | 🔒 |
| 失败重试 | retry_failed_task | 平台 failTaskRetry/{jobId},自动重新排期下发 | 🔒 |
| 断点续飞 | resume_from_breakpoint | 平台 breakPointFlight/{jobId},中断处继续 | 🔒 |
| 航线连接优化 | optimize_route_connection | 平台 optimizeRoute/{taskId}:机场→航线起点安全连接 | 写 |
| 档期冲突检测 | get_schedule_conflicts(drone, range) | wayline-jobs 时间窗求交,本地计算 | 读 |
| 多机接力排期 | suggest_schedule(relay 模式) | 超大图斑（如现网 1472 亩规委会图斑）单机续航不够→多架次接力/多机分片,司空 2"跨区域多机统一调度"的对话化 | 读 |

## 主线四：飞行域

**定位**：一次飞行的执行管理（起飞、飞行中管理）。物理 server：uav-flight-task-mcp + 规划中的 uav-flight-control-mcp。

| 细分功能 | 工具 | 说明 | 状态 | 风险 |
|---|---|---|---|---|
| 人在环起飞 | take_off | 无 token→登记确认单(不飞);确认卡片批准→携一次性 token 执行;伪造/文本授权一律拒绝 | ✅ | 🔒 |
| 任务暂停/恢复 | pause_task / resume_task | cloud-sdk flighttaskPause/Recovery | 🟡 | 🔒 |
| 飞行限高 | set_height_limit | 平台 setDroneHeightLimit/{droneSn} | 🟡 | 🔒 |
| 控制权抢占 | grab_authority(flight/payload) | cloud-sdk flightAuthorityGrab / payloadAuthorityGrab（多控制端夺权——慎用,人在环） | ⚪ | 🔒 |
| 完成播报 | （BFF watcher,非工具） | airborne 后盯任务终态,下一轮对话自动先播报成果 | ✅ | — |
| 实时事件订阅 | （架构升级,非工具） | MQTT flighttaskProgress/flighttaskReady/flyToPointProgress 事件流替代轮询——播报机制的下一形态,IM 通道(M4)的推送数据源 | ⚪ | — |

## 主线五：直播域

**定位**：看得见的飞行。规划 server：uav-live-mcp（🟡 P1）。

| 细分功能 | 工具 | 说明 | 风险 |
|---|---|---|---|
| 直播能力查询 | get_live_capacity | 设备可推流的相机/镜头/清晰度清单 | 读 |
| 开流/停流 | start_live / stop_live | 大疆走 MQTT services;吉威机型走 other-drone-manage HTTP+ZLM 代理;返回拉流地址（GIS 前端 show_live 内嵌） | 写(审计) |
| 切镜头/画质 | switch_camera / set_live_quality | 广角/变焦/红外切换,分辨率码率调节 | 写 |
| 历史视频 | list_flight_videos | 按 missionId/deviceSn 归档的录像（FlightHistoryVideo,MinIO） | 读 |
| 遥测历史 | get_telemetry_history | InfluxDB OSD_DRONE/OSD_DOCK 时间范围查询 | 读 |
| 轨迹回放 | get_flight_trajectory | FLIGHT_TRAJECTORY 按任务取轨迹,落图回放 | 读 |

## 主线六：操控域

**定位**：飞行中的主动干预与载荷操作。规划 server：uav-flight-control-mcp（🟡 P1）。

| 细分功能 | 工具 | 说明 | 风险 |
|---|---|---|---|
| 一键返航 | return_home | 止损动作:免 token 秒执行+强审计+事后播报 | ⚡ |
| 紧急停止 | emergency_stop | 同上;评测需反向用例（急停必须秒执行,起飞必须走卡片） | ⚡ |
| 指点飞行 | fly_to_point / fly_to_point_update / fly_to_point_stop | 飞向坐标/中途改点/停止悬停（DockController jobs） | 🔒 |
| 起飞至点位 | takeoff_to_point | 应急响应第一动作:从机场直接起飞到事发点 | 🔒 |
| 就地降落 | start_landing | DockController service=start_landing——有返航必须有降落（返航不可行时的备选） | 🔒 |
| 接管前限飞检查 | check_takeover_no_fly_zone | 平台 /api/tasks/takeover/no-fly-zone/check:人工/Agent 接管设备前查限飞告警状态 | 读 |
| 喊话器 | speaker_tts / speaker_play_control | TTS 文本下发+音量/模式/播放控制(cloud-sdk DRC 下行);**喊话文本需用户核准原文** | 🔒 |
| 探照灯 | light_control | 开关/亮度/模式（夜间作业、警示） | 写 |
| 拍照取证 | camera_take_photo | cloud-sdk cameraPhotoTake,应急现场单拍 | 写(审计) |
| 录像控制 | camera_recording(start/stop) | 现场录像 | 写 |
| 变焦/对焦 | camera_zoom / camera_focus | focalLengthSet、focusModeSet/Value、点对焦 | 写 |
| 拍摄参数 | camera_settings | 曝光值/曝光模式(cameraExposure*)、分屏(screen_split)、精准点击(cameraAim) | 写 |
| 云台控制 | gimbal_control | 回中/lookAt 朝向目标坐标 | 写 |
| 热成像测光 | ir_metering | 点测温/区域测温(irMeteringPoint/Area)——夜间/火情场景 | 读 |
| POI 环绕 | poi_circle(enter/exit/speed) | 绕目标点环绕观察（M30 系列） | 🔒 |
| DRC 通道管理 | drc_enter / drc_exit | 为**人工**摇杆操纵开通道;LLM 不做 stick_control 闭环操纵（设计红线） | 🔒 |

## 主线七：debug 域（机场调试与维护）

**定位**：不飞的时候照顾好机器。规划 server：uav-dock-debug-mcp（🟡 P1）。
**顺序依赖**：进 debug_mode → 操作 → 复位 → 退 debug,skill 硬编码流程;临近排期任务的机场拒绝进调试。

| 细分功能 | 工具 | 说明 | 风险 |
|---|---|---|---|
| 调试模式开关 | debug_mode(open/close) | 后续所有调试动作的前置 | 🔒 |
| 舱盖控制 | dock_cover(open/close/force_close) | 远程开盖巡检、异物处理后强制关闭 | 🔒 |
| 推杆控制 | dock_putter(open/close) | 归中机构 | 🔒 |
| 舱内无人机电源 | drone_power(on/off) | 远程开关机 | 🔒 |
| 充电控制 | charge_control(on/off) | "给 XX 机场的无人机充上电" | 🔒 |
| 空调模式 | air_conditioner(mode) | 制冷/制热/除湿——高低温天气预处理 | 写 |
| 补光灯 | supplement_light(on/off) | 舱内查看辅助 | 写 |
| 机场重启 | device_reboot | 故障恢复终极手段 | 🔒 |
| 电池保养 | battery_maintenance(on/off) | 长期驻场电池健康 | 🔒 |
| 机场环境读数 | get_dock_environment | 温湿度/风速/雨量/舱内状态(DockOsd)——巡检体检第一步 | 读 |

## 主线八：成果域

**定位**：飞完之后交付什么。物理:uav-flight-task-mcp(报告) + 规划 uav-media-mcp（🔵 P0）。

| 细分功能 | 工具 | 说明 | 状态 | 风险 |
|---|---|---|---|---|
| 任务成果报告 | get_task_report | 覆盖图斑/拍照数/起止时间/归档说明;进行中返回进度提示 | ✅ | 读 |
| 媒体清单 | list_media(task/plot/type/time) | 平台 /media/page,照片墙数据源 | 🔵 | 读 |
| 媒体取链 | get_media_link | 返回下载/预览链接,不搬文件 | 🔵 | 读 |
| 相机覆盖计算 | get_camera_coverage | 平台按飞行参数算地面覆盖 GeoJSON——"这一趟拍全了没"直接落图 | 🔵 | 读 |
| **举证有效性校验** | verify_evidence_coverage | 覆盖 GeoJSON×目标图斑求交（覆盖率%）+ 照片 EXIF 坐标/时间完整性——图斑举证规范要求照片带地理坐标与时间;覆盖不足自动建议补拍航线 | 🔵 | 读 |
| 三维重建 | start_3d_modeling / get_modeling_status / get_modeling_result_link | WebODM:正射影像/三维模型;重资源任务要确认 | 🔵 | 🔒/读 |
| 飞行录像归档 | list_flight_videos | 与直播域共用（missionId 维度） | 🟡 | 读 |
| 媒体归档目录 | list_media_folders | MediaFolder 树状归档（按任务/图斑组织照片墙的目录骨架） | ⚪ | 读 |
| 媒体清单导出 | export_media_list | /media/export Excel | ⚪ | 读 |
| 成果回传派单 | upload_dispatch_result | 对接 task-dispatch /results/upload（管理面联动） | ⚪ | 写 |

---

## 支撑面 A：安全面（每次起飞的门禁,横切主线）

**定位**：不安全就不让飞;高危就要人点头。物理:uav-preflight-mcp(✅) + 规划 uav-airspace-mcp(🔵)、uav-alert-mcp(🔵) + 审批服务(✅,非 MCP)。

### A.1 飞前检查（✅ 已上线,uav-preflight-mcp）

| 细分功能 | 工具 | 检查内容 | 状态 |
|---|---|---|---|
| 气象检查 | check_weather | Open-Meteo 自查→平台气象兜底;风速(限 12m/s)/降水/温度→适飞结论 | ✅ |
| 电量续航 | check_battery | OSD 实时电量、续航预算 vs 任务时长、余量结论 | ✅ |
| 航线避障 | check_route_obstacle | 仿地飞行/地形抬升/安全高度校验（平台算法已含,复核口径） | ✅ |
| 机载避障 | check_drone_obstacle | 全向视觉避障自检（依赖机场在线） | ✅ |
| 空域许可 | check_airspace | **当前为人工核实占位→A.2 上线后接真实围栏冲突** | ✅→🔵 |
| 五项聚合 | preflight_check | 一次全查,overall 结论;有 fail 禁止发起起飞 | ✅ |
| 机场自检项 | check_dock_readiness | 行业标准第六项（大疆机场起飞前自检同款）：舱内温湿度/风速雨量传感器/通信状态/舱盖状态——数据源 get_dock_environment,纳入 preflight_check 聚合 | 🟡 |
| 平台天气红黄绿 | check_platform_weather | 平台 FlightWeatherController:红禁飞/黄人工确认/绿放行,含 confirm-and-publish——与我们人在环同构,可对接为第二气象源 | ⚪ |

### A.2 空域与电子围栏（🔵 P0,uav-airspace-mcp）

| 细分功能 | 工具 | 说明 | 风险 |
|---|---|---|---|
| 围栏查询 | list_zones(type) | flyWorkZone 四类:禁飞区/限高区/限速区/警告区（与图斑同表不同 zoneType） | 读 |
| 航线合规检测 | check_route_conflict(route_id) | 航线 WKT×围栏求交:穿禁飞区→fail+冲突多边形落图;超限高→顶回 | 读 |
| 临时管制区 | create_zone(带过期) / delete_zone | "明天上午这片临时管制"→建区+受影响排期任务检出提醒 | 🔒 |
| **围栏下发设备** | sync_zone_to_devices | cloud-sdk flightAreasUpdate/Delete:围栏推到设备侧才真正生效（机上强制,不只平台记录）——create_zone 的必要后半步 | 🔒 |
| 官方限飞区查询 | query_tsa_zones | cloud-sdk TSA 区域(大疆官方禁飞库),与自建围栏叠加检测 | 读 |
| 围栏文件导入 | import_zone_file | GeoJSON/KML 批量导入 | 写 |

### A.3 告警与设备健康（🔵 P0,uav-alert-mcp）

| 细分功能 | 工具 | 说明 | 风险 |
|---|---|---|---|
| 告警清单/详情 | list_alerts / get_alert_detail | 类型/等级(1普通2警告3严重)/时间/设备过滤 | 读 |
| 告警处置 | handle_alert / ignore_alert | 处理/忽略+备注 | 写 |
| 未处理计数 | get_unhandled_count | 值班视图入口 | 读 |
| 设备健康 HMS | get_device_health | 大疆健康管理系统消息+在线状态+心跳+**电池循环次数/健康度**（OSD 电池信息）+UOM 登记状态——"这台机能不能飞"的依据 | 读 |
| 失控行为查询/设置 | get/set_lost_action | 断联预案（4G 断联是无人值守场景高频故障）：失控时返航/悬停/降落,cloud-sdk property set;set 为高危 | 读/🔒 |

### A.4 监管合规（新增,《无人驾驶航空器飞行管理暂行条例》2024.1 施行）

政务客户必问项。UOM（民航局综合管理平台）无公开 API,工具形态是**登记/核对**而非自动申报：

| 细分功能 | 工具 | 说明 | 状态 | 风险 |
|---|---|---|---|---|
| 适飞空域判断 | check_flight_clearance | 真高≤120m + 非管制空域 = 适飞（免申报）;管制空域需前 1 日 12 时前经 UOM 申请——纳入 check_airspace 结论口径。**我们 altitude 硬限 ≤120m 的设计被条例直接印证** | 🔵 随 A.2 | 读 |
| 飞行活动申报登记 | register_flight_clearance | 人工在 UOM 申报后,把申报单号/有效期录入任务;起飞确认卡片展示申报状态（已批准飞行需起飞前 1h 向空管报告——写进 SKILL 流程提示） | 🔵 | 写 |
| 实名登记核对 | （并入 get_device_health/资产域） | 设备 UOM 登记标志状态字段,未登记设备拒绝排期 | ⚪ | 读 |

### A.5 人在环与授权体系（✅ 机制已上线,非 MCP 工具）

| 机制 | 说明 | 状态 |
|---|---|---|
| confirm_token | 审批服务(8205)唯一签发;一次性/动作绑定/TTL 10min/重放拒绝;对话文本"我确认"不构成授权 | ✅ |
| 确认卡片双前端 | GIS 前端(BFF)与 DeerFlow 原生 UI 均有卡片→批准→token 回传 | ✅ |
| 紧急动作白名单⚡ | 返航/急停免 token+强审计+执行即播报（等确认反而危险） | 🟡 随操控域 |
| 拦截器 | guard 硬白名单+audit 审计落盘(token 打码) | ✅ |
| Agent≤用户权限 | 登录态透传→平台 dataScope 过滤+菜单权限前置校验（ruoyi-system 对接） | ⚪ P2 |

## 支撑面 B：智能面（🟠 P1.5,uav-recognition-mcp）

| 细分功能 | 工具 | 说明 | 风险 |
|---|---|---|---|
| 推理提交 | submit_inference(type, scene) | 三种:video 逐帧/image_batch 离线批量/stream 实时流;按场景路由模型 | 写 |
| 推理状态/结果 | get_inference_status / list_inference_results | 结果含类别/置信度/像素框+**反投影地理坐标**(OSD+相机内参),可直接落图 | 读 |
| AI 告警 | list_ai_alerts / mark_ai_alert_read / rebuild_alerts | 规则引擎产物,coord_hash 去重聚合+触发计数 | 读/写 |
| 告警规则 | get_alert_rules / update_alert_rule | 5 算子:单类置信度/多类任一/同图相交/IoU/数量统计;改规则人在环 | 读/🔒 |
| 态势统计 | get_situation_stats(dimension) | 大屏 10+ 接口按需聚合:趋势/等级分布/热点/地图点位/业务闭环 | 读 |

## 支撑面 C：管理面（⚪ P2）

| 域 | 细分功能 | 说明 |
|---|---|---|
| 派单协作(uav-dispatch-order-mcp) | 建任务/MultiPolygon 自动拆子任务/指派🔒/群发抢单🔒/接拒单/成果上传/审核🔒/评价/流程日志 | task-dispatch 模块,多承接单位协同 |
| 平台审批流(uav-workflow-mcp) | 发起(businessType=DRONE_TASK)/我的待办/批准🔒/驳回🔒/转办/加签/撤回/流程日志 | 自研引擎,DRONE_TASK_AUDIT_V1 已初始化;业务级审批,与操作级 confirm_token 双层不互替 |
| 固件与资产(uav-ops-mcp) | 最新固件/发起升级🔒/升级进度/设备日志/设备注册🔒/绑定🔒 | 管理员画像 |
| 消息通知(ruoyi-system) | 站内公告查询/发布🔒(SysNotice) | "给所有飞手发个通知";短信/邮件框架未接,IM 通道 M4 补 |

---

## 附录：逻辑域 ↔ 物理 server 对照

| 物理 server | 端口 | 承载的逻辑域 | 状态 |
|---|---|---|---|
| uav-drone-dispatch-mcp | 8201 | 调度(空间) | ✅ |
| uav-route-planning-mcp | 8202 | 航线 | ✅ |
| uav-preflight-mcp | 8203 | 安全面 A.1 | ✅ |
| uav-flight-task-mcp | 8204 | 任务 + 飞行(起飞) + 成果(报告) | ✅ |
| uav-airspace-mcp | 规划 | 安全面 A.2 | 🔵 |
| uav-alert-mcp | 规划 | 安全面 A.3 | 🔵 |
| uav-media-mcp | 规划 | 成果 | 🔵 |
| uav-task-schedule-mcp | 规划 | 调度(时间) | 🔵 |
| uav-live-mcp | 规划 | 直播 | 🟡 |
| uav-flight-control-mcp | 规划 | 操控 + 飞行(干预) | 🟡 |
| uav-dock-debug-mcp | 规划 | debug | 🟡 |
| uav-recognition-mcp | 规划 | 智能面 | 🟠 |
| uav-dispatch-order-mcp / uav-workflow-mcp / uav-ops-mcp | 规划 | 管理面 | ⚪ |

统计：细分功能共 **约 100 项**,已上线 24 项;高危(🔒)约 35 项全部人在环,紧急白名单(⚡)仅 2 项。

## 附录二：复核确认的边界（有意不做/暂不做）

- **DRC 摇杆闭环操纵**（stick_control）：LLM 不做实时闭环,只开通道给人;
- **失败重试的两条平台路径**：failTaskRetry(重排期下发) 与 retryPublishJobNewTime(改立即执行重发),
  工具层收敛为 retry_failed_task 一个入口,参数区分;
- **平台内部生命周期**：媒体 STS 直传/指纹去重、MQTT ACL、组织绑定、离线地图、PSDK 数据透传
  ——设备↔平台的内部协议,Agent 无需触达;
- **OSD 数据清理**（/drone/*/osd/clean）：运维危险操作,不给 Agent;
- **短信/邮件**：ruoyi-system 只有框架无实现,通知走站内公告 + M4 IM 通道;
- **备降点管理**：平台无备降点数据模型（机场任务失败即返航/降落）,如客户提出需先在平台建模型;
- **UOM 自动申报**：民航局平台无公开 API,只做申报登记核对,不承诺自动申报。

## 附录三：外部对照结论（2026-07 调研）

1. **与大疆司空 2 / 机场 3 功能域对齐度高**：航线类型(正射/倾斜/贴近)、无人值守自检、直播"指哪看哪"
   (cameraAim)、AI 检测、三维重建,我方清单均有对应;司空 2 的"机场起飞前自检(风速≤8m/s、雨量、
   舱温)"启发新增 check_dock_readiness。我们的差异化在**对话入口+人在环 token 体系**——行业仍以
   平台按钮操作为主,大模型指挥调度尚无成熟对标产品。
2. **法规硬约束**（《无人驾驶航空器飞行管理暂行条例》2024.1）：适飞空域=真高 120m 以下非管制区
   （我们 altitude≤120m 硬限被条例印证）;管制空域飞行需前 1 日 12 时前 UOM 申报、起飞前 1h 报告
   ——新增 A.4 监管合规小节;所有机型需 UOM 实名登记。
3. **图斑举证业务规范**：举证照片须带地理坐标与时间、按规范拍摄一次举证到位——新增
   verify_evidence_coverage（覆盖率×EXIF 校验),把"拍了"升级为"拍对了"。
4. **无人值守运营实际痛点**：4G 断联/失联预案（新增 get/set_lost_action）、电池循环寿命
   （并入 get_device_health）、极端温湿度（空调控制已有）。
