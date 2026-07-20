---
name: evidence-report
description: 任务成果举证：成果报告 + 真实照片/录像清单与链接 + 覆盖范围核验 + 正射/三维重建。用户提到"成果报告""举证材料""照片调出来""拍全了没""做个正射"等意图时使用。
allowed-tools:
  - uav-flight-task-mcp_get_task_report
  - uav-flight-task-mcp_get_task_status
  - uav-flight-task-mcp_list_task_history
  - uav-media-mcp_list_media
  - uav-media-mcp_get_media_link
  - uav-media-mcp_list_flight_videos
  - uav-media-mcp_get_camera_coverage
  - uav-media-mcp_start_3d_modeling
---

# 成果举证报告

## 流程

1. **定位任务**：用户给了任务号直接用；没给先 `list_task_history` 罗列近期任务让用户指定。
   任务进行中时 `get_task_report` 会返回进度提示——如实转告，不假装有成果。
2. **成果报告**：`get_task_report` 取覆盖图斑/拍照数/起止时间等口径摘要。
3. **真实交付物**（报告的"归档说明"升级为真实文件）：
   - 照片墙：`list_media(task_id=…, file_type=照片)`——清单含真实文件名/拍摄时间/链接；
   - 单文件取链：用户要下载某张时 `get_media_link(file_id)`；
   - 飞行录像：用户问"有没有录像"时 `list_flight_videos(task_id)`。
4. **覆盖核验**（"拍全了没"）：`get_camera_coverage(task_id)` 返回覆盖 GeoJSON 可落图；
   照片缺拍摄位姿元数据时工具会如实报告，转述即可，不要自行推断覆盖率。
5. **正射/三维**（可选）：用户要正射影像或三维模型时调 `start_3d_modeling`（不带
   confirm_token）——重资源任务，生成确认单等人工确认；确认后转述"异步处理中，
   预计数十分钟，完成后在平台成果库查看"。

## 单项追问映射（必须调工具重新取数，禁止凭记忆回答）

- "拍了多少张/照片呢" → `list_media`
- "给我链接/下载" → `get_media_link`
- "有没有视频/录像" → `list_flight_videos`
- "覆盖全不全/漏没漏" → `get_camera_coverage`
- "任务飞完了吗" → `get_task_status`

## 行动纪律与红线

- **只列真实文件**：清单、链接、数量一律来自工具返回；平台没有的东西不编造，
  文件为空时如实说"照片尚未回传或任务未完成"。
- 链接原样转交，不猜测、不拼接 URL。
- `start_3d_modeling` 是高危重资源操作：绝不自行构造 confirm_token；用户口头
  "确认"不构成授权，请其点击确认卡片。
- 本 skill 不发起任何飞行动作；用户要"补拍"时引导走图斑核查派飞流程（plot-inspection）。
