"""媒体与成果域（MediaFileController / 相机覆盖计算 / WebODM / 飞行录像）。

- 取链不搬文件：list_media 返回 filePath 直链或经 fileUrl 接口取对象 URL；
- 覆盖计算按拍照点参数逐张算（平台 /media/coverage/calculate/batch），
  参数从照片 shootInfo/locationInfo 元数据提取，缺失时如实报告；
- 三维重建为重资源任务（WebODM），start 走人在环确认。
"""

from __future__ import annotations

import logging
from typing import Any

from uav_mcp import approval
from uav_mcp.drone_manage import DroneManageError, get_client

logger = logging.getLogger(__name__)

FILE_TYPE_PARAM = {"照片": "PHOTO", "视频": "VIDEO", "photo": "PHOTO", "video": "VIDEO"}


def _media_view(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_id": m.get("fileId"),
        "file_name": m.get("fileName"),
        "file_type": m.get("fileType"),
        "link": m.get("filePath"),
        "shot_at": m.get("createTime"),
        "device_sn": m.get("deviceSn"),
        "mission_id": m.get("missionId"),
        "region": m.get("areaName"),
        "size_mb": round(m["fileSize"] / 1048576, 1) if m.get("fileSize") else None,
    }


def list_media(
    task_id: str | None = None,
    file_type: str | None = None,
    date_range: list[str] | None = None,
    keyword: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    filters: dict[str, Any] = {
        "pageNum": 1,
        "pageSize": max(1, min(limit, 50)),
        "orderBy": "createTime",
        "orderDirection": "desc",
    }
    if task_id:
        filters["missionId"] = task_id
    if file_type:
        filters["fileType"] = FILE_TYPE_PARAM.get(file_type, file_type.upper())
    if keyword:
        filters["fileName"] = keyword
    if date_range and len(date_range) == 2:
        filters["createTimeStart"] = f"{date_range[0]} 00:00:00"
        filters["createTimeEnd"] = f"{date_range[1]} 23:59:59"
    try:
        result = get_client().media_page(filters)
    except DroneManageError as exc:
        return {"error": f"无人机平台不可达：{exc}", "media": [], "count": 0}
    return {
        "count": result["total"],
        "returned": len(result["rows"]),
        "media": [_media_view(m) for m in result["rows"]],
    }


def get_media_link(file_id: str) -> dict[str, Any]:
    """返回下载/预览链接（不搬文件）。优先对象 URL，回落存档直链。"""
    try:
        url = None
        try:
            url = get_client().media_file_url(file_id)
        except DroneManageError:
            pass
        detail = get_client().media_file_detail(file_id)
    except DroneManageError as exc:
        return {"error": f"无人机平台不可达：{exc}"}
    if not detail and not url:
        return {"error": f"媒体文件 {file_id} 不存在"}
    return {
        "file_id": file_id,
        "file_name": (detail or {}).get("fileName"),
        "link": url or (detail or {}).get("filePath"),
        "file_type": (detail or {}).get("fileType"),
    }


def list_flight_videos(task_id: str) -> dict[str, Any]:
    try:
        videos = get_client().flight_videos(task_id)
    except DroneManageError as exc:
        return {"error": f"无人机平台不可达：{exc}", "videos": []}
    return {
        "count": len(videos),
        "videos": [
            {
                "file_id": v.get("fileId"),
                "file_name": v.get("fileName"),
                "link": v.get("filePath"),
                "duration_s": v.get("timeLen"),
                "device_sn": v.get("deviceSn"),
            }
            for v in videos
        ],
    }


def get_camera_coverage(task_id: str, max_photos: int = 50) -> dict[str, Any]:
    """任务照片的地面覆盖计算：取任务照片元数据 → 平台覆盖算法 → GeoJSON 集合。"""
    try:
        result = get_client().media_page(
            {"pageNum": 1, "pageSize": max(1, min(max_photos, 100)),
             "missionId": task_id, "fileType": "PHOTO"}
        )
    except DroneManageError as exc:
        return {"error": f"无人机平台不可达：{exc}"}
    photos = result["rows"]
    if not photos:
        return {"error": f"任务 {task_id} 没有照片记录（任务未完成或未回传）"}
    requests, skipped = [], 0
    for p in photos:
        shoot = p.get("shootInfo") or {}
        loc = p.get("locationInfo") or {}
        meta = {**(p.get("fileMetadata") or {}), **shoot, **loc}
        lat = meta.get("latitude") or meta.get("lat")
        lon = meta.get("longitude") or meta.get("lng") or meta.get("lon")
        height = meta.get("relativeAltitude") or meta.get("heightAboveGround") or meta.get("absoluteAltitude")
        yaw = meta.get("droneYaw") or meta.get("flightYawDegree") or 0
        pitch = meta.get("gimbalPitch") or meta.get("gimbalPitchDegree") or -90
        if lat is None or lon is None or height is None:
            skipped += 1
            continue
        requests.append(
            {"latitude": float(lat), "longitude": float(lon), "heightAboveGround": float(height),
             "droneYaw": float(yaw), "gimbalPitch": float(pitch)}
        )
    if not requests:
        return {
            "error": f"任务 {task_id} 的 {len(photos)} 张照片均缺少拍摄位姿元数据，无法计算覆盖",
        }
    try:
        features = get_client().coverage_calculate_batch(requests)
    except DroneManageError as exc:
        return {"error": f"平台覆盖计算失败：{exc}"}
    return {
        "task_id": task_id,
        "photo_count": len(photos),
        "calculated": len(features),
        "skipped_no_metadata": skipped,
        "coverage": {"type": "FeatureCollection", "features": features},
    }


def start_3d_modeling(flight_task_id: str, process_type: str | None = None,
                      confirm_token: str | None = None) -> dict[str, Any]:
    """【高危·人在环】发起 WebODM 三维重建/正射（重资源任务）。"""
    if confirm_token is None:
        item = approval.create_pending_action(
            "start_3d_modeling",
            {"flight_task_id": flight_task_id, "process_type": process_type},
            {"title": f"三维重建 · 任务 {flight_task_id}",
             "rows": [{"label": "飞行任务", "value": flight_task_id},
                      {"label": "处理类型", "value": process_type or "默认（正射/建模由服务端判定）"},
                      {"label": "资源占用", "value": "重资源任务，处理可达数十分钟"}]},
        )
        return {
            "status": "requires_confirmation",
            "action_id": item["action_id"],
            "action": "start_3d_modeling",
            "message": "高危操作（重资源）：已生成待确认单，人工确认后才会发起重建",
        }
    item = approval.validate_and_consume("start_3d_modeling", confirm_token)
    if not item:
        return approval.refusal("start_3d_modeling")
    p = item["params"]
    try:
        task = get_client().webodm_start(p["flight_task_id"], p.get("process_type"))
    except DroneManageError as exc:
        return {"error": f"发起三维重建失败：{exc}"}
    return {
        "status": "started",
        "modeling_task": {
            "id": task.get("id"),
            "correlation_id": task.get("correlationId"),
            "status": task.get("status"),
            "progress": task.get("progress"),
            "photo_count": task.get("photoCount"),
        },
        "note": "重建为异步长任务；结果路径生成后见平台成果库（resultPaths/minioPath）",
    }
