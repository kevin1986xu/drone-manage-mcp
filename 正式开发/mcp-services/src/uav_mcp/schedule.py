"""任务排期与调度域（时间维度：何时飞）。

- suggest_schedule：**只算不写**。天气窗口（Open-Meteo 逐日预报）×设备档期
  （平台 wayline-jobs）×优先级/截止时间 → 建议排期表+理由；
- 凡产生平台"待执行"任务的动作（定时/循环/重排/重试/续飞/取消）一律
  confirm_token 人在环，且受 UAV_CREATE_REAL_TASK 开关约束——平台自动调度器
  对"待执行"任务是真执行；
- retry/breakpoint 的 job_id 是 wayline job 编号（非 FlightTask.taskId），
  从 list_scheduled_tasks / get_schedule_conflicts 的 jobs 里取。
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, timedelta
from typing import Any

import httpx

from uav_mcp import approval, config, geo
from uav_mcp import drones as drones_core
from uav_mcp import plots as plots_core
from uav_mcp import routes as routes_core
from uav_mcp.drone_manage import DroneManageError, get_client
from uav_mcp.state import STATE
from uav_mcp.weather import FAIL_CODES, WARN_CODES, WIND_LIMIT_MS, WMO

logger = logging.getLogger(__name__)

TASK_STATUS_CN = {1: "待执行", 2: "执行中", 3: "已完成", 4: "已取消", 5: "执行失败"}


# ── 天气窗口（Open-Meteo 逐日预报，7 天）─────────────────────

def fetch_daily_weather(lat: float, lon: float, days: int = 7) -> list[dict[str, Any]]:
    """逐日适飞预判：红(fail)/黄(warn)/绿(pass)。失败抛异常由调用方降级。"""
    resp = httpx.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": round(lat, 4), "longitude": round(lon, 4),
            "daily": "weather_code,wind_speed_10m_max,wind_gusts_10m_max,precipitation_sum",
            "wind_speed_unit": "ms", "timezone": "Asia/Shanghai",
            "forecast_days": max(1, min(days, 14)),
        },
        timeout=8,
    )
    resp.raise_for_status()
    daily = resp.json()["daily"]
    out = []
    for i, day in enumerate(daily["time"]):
        code = int(daily["weather_code"][i] or 0)
        wind = float(daily["wind_speed_10m_max"][i] or 0)
        precip = float(daily["precipitation_sum"][i] or 0)
        if code in FAIL_CODES or wind >= WIND_LIMIT_MS:
            status = "fail"
        elif code in WARN_CODES or wind >= WIND_LIMIT_MS * 0.7 or precip >= 1:
            status = "warn"
        else:
            status = "pass"
        out.append({
            "date": day, "status": status, "condition": WMO.get(code, f"代码{code}"),
            "wind_max_ms": wind, "precipitation_mm": precip,
        })
    return out


# ── 设备档期（平台 wayline-jobs）─────────────────────────────

def _device_jobs(device_sn: str, begin: str, end: str) -> list[dict[str, Any]]:
    rows = get_client().wayline_jobs_search(
        {"pageNum": 1, "pageSize": 200, "deviceSn": device_sn,
         "beginTimeStart": f"{begin} 00:00:00", "beginTimeEnd": f"{end} 23:59:59"}
    )
    return [
        {
            "job_id": r.get("jobId"),
            "job_name": r.get("jobName"),
            "begin_time": r.get("beginTime"),
            "end_time": r.get("endTime"),
            "status": r.get("status"),
            "task_id": r.get("taskId"),
        }
        for r in rows
    ]


def get_schedule_conflicts(drone_id: str, date_range: list[str] | None = None) -> dict[str, Any]:
    """查设备档期：时间窗内已排/已飞的 wayline 作业清单（判断"这天还排得下吗"）。"""
    try:
        drones_core.hydrate()
    except DroneManageError as exc:
        return {"error": f"无人机平台不可达：{exc}"}
    d = drones_core.find(drone_id)
    if not d or not d.get("device_sn"):
        return {"error": f"无人机 {drone_id} 不存在或缺少设备 SN"}
    today = date.today().isoformat()
    begin, end = (date_range if date_range and len(date_range) == 2
                  else (today, (date.today() + timedelta(days=7)).isoformat()))
    try:
        jobs = _device_jobs(d["device_sn"], begin, end)
    except DroneManageError as exc:
        return {"error": f"档期查询失败：{exc}"}
    return {
        "drone_id": d["drone_id"],
        "range": [begin, end],
        "job_count": len(jobs),
        "jobs": jobs[:30],
        "note": "job_id 可用于 retry_failed_task / resume_from_breakpoint",
    }


# ── 排期建议（只算不写）─────────────────────────────────────

def suggest_schedule(
    plot_ids: list[str],
    deadline_days: int = 7,
    max_sorties_per_day: int = 3,
) -> dict[str, Any]:
    """综合天气窗口×设备档期×优先级的排期建议表。只算不写；
    确认后由 create_scheduled_task 逐条落库。"""
    try:
        plots_core.hydrate()
        drones_core.hydrate()
    except DroneManageError as exc:
        return {"error": f"无人机平台不可达：{exc}"}
    keys = [plots_core.resolve_pid(p) for p in plot_ids]
    missing = [p for p, k in zip(plot_ids, keys) if not k]
    if missing:
        return {"error": f"图斑不存在：{', '.join(missing)}；请先 query_plots 获取有效编号"}
    infos = [STATE.plots[k] for k in keys]
    center = geo.centroid([p["centroid"] for p in infos] + [infos[0]["centroid"]])

    # 1) 天气窗口
    try:
        weather = fetch_daily_weather(center[1], center[0], deadline_days)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"逐日气象预报不可用（{exc}），无法给出避雨排期，请稍后重试"}
    flyable = [w for w in weather if w["status"] != "fail"]
    if not flyable:
        return {
            "status": "no_window",
            "weather": weather,
            "detail": f"未来 {deadline_days} 天没有适飞窗口，建议顺延截止时间",
        }

    # 2) 架次分组（复用批量域的就近合并算法）
    from uav_mcp.batch import _cluster_sorties

    sorties = _cluster_sorties([p["plot_id"] for p in infos])

    # 3) 设备档期：默认就近在线机场；逐适飞日装箱，避开已排作业的天
    online = [d for d in STATE.drones.values() if d.get("online") and d.get("device_sn")]
    chosen = None
    if online:
        chosen = min(online, key=lambda d: geo.dist_m(d["location"]["coordinates"], center))
    busy_days: set[str] = set()
    if chosen:
        try:
            jobs = _device_jobs(chosen["device_sn"], flyable[0]["date"], flyable[-1]["date"])
            for j in jobs:
                if j.get("begin_time"):
                    busy_days.add(str(j["begin_time"])[:10])
        except DroneManageError:
            pass  # 档期查不到不阻塞建议，注明即可

    days_plan: list[dict[str, Any]] = []
    queue = sorties[:]
    for w in flyable:
        if not queue:
            break
        cap = max_sorties_per_day - (1 if w["date"] in busy_days else 0)
        if cap <= 0:
            continue
        todays, queue = queue[:cap], queue[cap:]
        days_plan.append(
            {
                "date": w["date"],
                "weather": f"{w['condition']} · 最大风 {w['wind_max_ms']} m/s"
                + ("（边缘条件注意监控）" if w["status"] == "warn" else ""),
                "existing_jobs_note": "该日已有排班，容量已扣减" if w["date"] in busy_days else None,
                "sorties": [{"plots": s} for s in todays],
            }
        )
    return {
        "status": "ok" if not queue else "insufficient_window",
        "plots": [p["plot_id"] for p in infos],
        "suggested_drone": chosen["drone_id"] if chosen else None,
        "days": days_plan,
        "unscheduled": [s for s in queue],
        "weather_outlook": weather,
        "note": "本建议只算不写。用户确认后：先 generate_route 生成航线，"
        "再逐日 create_scheduled_task 落平台定时任务（每次落库独立确认）。",
    }


# ── 平台任务视图 ─────────────────────────────────────────────

def _task_view(t: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": t.get("taskId"),
        "task_name": t.get("taskName"),
        "execution_mode": t.get("executionMode"),
        "execution_time": t.get("executionTime") or t.get("plannedStartTime"),
        "status": TASK_STATUS_CN.get(t.get("taskStatus"), str(t.get("taskStatus"))),
        "device_sn": t.get("deviceSn"),
        "route_id": t.get("routeId"),
        "created_by": t.get("createBy"),
    }


def list_scheduled_tasks(
    status: str | None = None,
    date_range: list[str] | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    filters: dict[str, Any] = {"pageNum": 1, "pageSize": max(1, min(limit, 50))}
    status_param = {v: k for k, v in TASK_STATUS_CN.items()}
    if status:
        if status not in status_param:
            return {"error": f"status 须为：{list(status_param)}"}
        filters["taskStatus"] = status_param[status]
    if date_range and len(date_range) == 2:
        filters["startTimeBegin"] = f"{date_range[0]} 00:00:00"
        filters["startTimeEnd"] = f"{date_range[1]} 23:59:59"
    try:
        result = get_client().flight_tasks_query(filters)
    except DroneManageError as exc:
        return {"error": f"无人机平台不可达：{exc}", "tasks": [], "count": 0}
    return {
        "count": result["total"],
        "returned": len(result["rows"]),
        "tasks": [_task_view(t) for t in result["rows"]],
    }


# ── 高危写：定时/循环/重排/取消/重试/续飞（全部人在环）───────

def _guard_real_task() -> dict[str, Any] | None:
    if not config.UAV_CREATE_REAL_TASK:
        return {
            "status": "rejected",
            "reason": "服务端 UAV_CREATE_REAL_TASK 开关未开启：平台侧'待执行'任务会被"
            "自动调度器真实执行，当前环境禁止创建。请联系管理员开启后重试",
        }
    return None


def _resolve_route(route_id: str) -> tuple[dict[str, Any] | None, str | None]:
    r, rev = routes_core._rev(route_id)
    if not rev:
        return None, f"航线 {route_id} 不存在"
    if not rev.get("platform_route_id"):
        return None, f"航线 {route_id} 无平台航线 ID（本地降级航线不能用于平台排期）"
    return rev, None


def create_scheduled_task(
    route_id: str,
    drone_id: str,
    execution_time: str,
    task_name: str | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """【高危】平台定时任务（executionMode=scheduled，到点自动执行）。"""
    try:
        drones_core.hydrate()
    except DroneManageError as exc:
        return {"error": f"无人机平台不可达：{exc}"}
    d = drones_core.find(drone_id)
    if not d or not d.get("device_sn"):
        return {"error": f"无人机 {drone_id} 不存在或缺少设备 SN"}
    rev, err = _resolve_route(route_id)
    if err:
        return {"error": err}
    if confirm_token is None:
        item = approval.create_pending_action(
            "create_scheduled_task",
            {"route_id": route_id.upper(), "drone_id": d["drone_id"],
             "execution_time": execution_time, "task_name": task_name},
            {"title": f"定时任务 · {execution_time}",
             "rows": [
                 {"label": "执行无人机", "value": f"{d['drone_id']} · {d['model']}"},
                 {"label": "航线", "value": route_id.upper()},
                 {"label": "执行时间", "value": f"{execution_time}（到点平台自动起飞）"},
             ]},
        )
        return {"status": "requires_confirmation", "action_id": item["action_id"],
                "action": "create_scheduled_task",
                "message": "高危操作：定时任务到点会真实执行。已生成待确认单，等待人工确认"}
    item = approval.validate_and_consume("create_scheduled_task", confirm_token)
    if not item:
        return approval.refusal("create_scheduled_task")
    blocked = _guard_real_task()
    if blocked:
        return blocked
    p = item["params"]
    d = drones_core.find(p["drone_id"])
    rev, err = _resolve_route(p["route_id"])
    if err:
        return {"error": err}
    ptid = uuid.uuid4().hex
    try:
        created = get_client().create_scheduled_flight_task(
            {
                "taskId": ptid,
                "taskName": p.get("task_name") or f"{config.ROUTE_NAME_PREFIX}定时-{p['route_id']}",
                "routeId": rev["platform_route_id"],
                "deviceSn": d["device_sn"],
                "workspaceId": config.DRONE_WORKSPACE_ID,
                "taskType": "PLOT_INSPECTION",
                "executionMode": "scheduled",
                "executionTime": p["execution_time"],
                "description": "低空智察智能体创建的定时任务（人在环确认后）",
            }
        )
    except DroneManageError as exc:
        return {"error": f"平台创建定时任务失败：{exc}"}
    return {"status": "scheduled", "platform_task_id": ptid,
            "execution_time": p["execution_time"], "platform_status": created.get("taskStatus")}


def create_recurring_task(
    route_id: str,
    drone_id: str,
    start_date: str,
    end_date: str,
    execute_times: list[str] | None = None,
    task_name: str | None = None,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """【高危】平台循环任务（executionMode=recurring，日期区间内每日按时执行）。
    execute_times 为每日执行时刻列表（如 ["09:00"]），默认 09:00。"""
    try:
        drones_core.hydrate()
    except DroneManageError as exc:
        return {"error": f"无人机平台不可达：{exc}"}
    d = drones_core.find(drone_id)
    if not d or not d.get("device_sn"):
        return {"error": f"无人机 {drone_id} 不存在或缺少设备 SN"}
    rev, err = _resolve_route(route_id)
    if err:
        return {"error": err}
    times = execute_times or ["09:00"]
    if confirm_token is None:
        item = approval.create_pending_action(
            "create_recurring_task",
            {"route_id": route_id.upper(), "drone_id": d["drone_id"], "start_date": start_date,
             "end_date": end_date, "execute_times": times, "task_name": task_name},
            {"title": f"循环任务 · {start_date} ~ {end_date}",
             "rows": [
                 {"label": "执行无人机", "value": f"{d['drone_id']} · {d['model']}"},
                 {"label": "航线", "value": route_id.upper()},
                 {"label": "执行周期", "value": f"{start_date} ~ {end_date} 每日 {'/'.join(times)}"},
                 {"label": "风险提示", "value": "区间内每天都会真实起飞，请确认周期与时刻"},
             ]},
        )
        return {"status": "requires_confirmation", "action_id": item["action_id"],
                "action": "create_recurring_task",
                "message": "高危操作：循环任务区间内每日真实执行。已生成待确认单，等待人工确认"}
    item = approval.validate_and_consume("create_recurring_task", confirm_token)
    if not item:
        return approval.refusal("create_recurring_task")
    blocked = _guard_real_task()
    if blocked:
        return blocked
    p = item["params"]
    d = drones_core.find(p["drone_id"])
    rev, err = _resolve_route(p["route_id"])
    if err:
        return {"error": err}
    ptid = uuid.uuid4().hex
    try:
        created = get_client().create_scheduled_flight_task(
            {
                "taskId": ptid,
                "taskName": p.get("task_name") or f"{config.ROUTE_NAME_PREFIX}循环-{p['route_id']}",
                "routeId": rev["platform_route_id"],
                "deviceSn": d["device_sn"],
                "workspaceId": config.DRONE_WORKSPACE_ID,
                "taskType": "PLOT_INSPECTION",
                "executionMode": "recurring",
                "startDate": p["start_date"],
                "endDate": p["end_date"],
                "cycleConfig": {"executeType": "daily", "executeFrequency": 1,
                                "executeTimes": p["execute_times"]},
                "description": "低空智察智能体创建的循环任务（人在环确认后）",
            }
        )
    except DroneManageError as exc:
        return {"error": f"平台创建循环任务失败：{exc}"}
    return {"status": "recurring_created", "platform_task_id": ptid,
            "period": [p["start_date"], p["end_date"]], "execute_times": p["execute_times"],
            "platform_status": created.get("taskStatus")}


def _simple_confirm_action(
    action: str, params: dict[str, Any], title: str, rows: list[dict[str, str]],
    confirm_token: str | None, executor,
) -> dict[str, Any]:
    """取消/重排/重试/续飞共用的"确认单→执行"骨架。"""
    if confirm_token is None:
        item = approval.create_pending_action(action, params, {"title": title, "rows": rows})
        return {"status": "requires_confirmation", "action_id": item["action_id"], "action": action,
                "message": "高危操作：已生成待确认单，等待人工确认后执行"}
    item = approval.validate_and_consume(action, confirm_token)
    if not item:
        return approval.refusal(action)
    try:
        return executor(item["params"])
    except DroneManageError as exc:
        return {"error": f"平台操作失败：{exc}"}


def cancel_scheduled_task(task_id: str, confirm_token: str | None = None) -> dict[str, Any]:
    """【高危】取消平台任务（含定时/循环）。"""
    try:
        t = get_client().get_flight_task(task_id)
    except DroneManageError as exc:
        return {"error": f"无人机平台不可达：{exc}"}
    if not t:
        return {"error": f"任务 {task_id} 不存在"}
    return _simple_confirm_action(
        "cancel_scheduled_task", {"task_id": task_id},
        f"取消任务 · {t.get('taskName') or task_id}",
        [{"label": "任务", "value": t.get("taskName") or task_id},
         {"label": "当前状态", "value": TASK_STATUS_CN.get(t.get("taskStatus"), "-")}],
        confirm_token,
        lambda p: (get_client().cancel_flight_task(p["task_id"]),
                   {"status": "cancelled", "task_id": p["task_id"]})[1],
    )


def reschedule_task(task_id: str, new_time: str | None = None,
                    confirm_token: str | None = None) -> dict[str, Any]:
    """【高危】重排期：给 new_time 则改到指定时间；不给则由平台自动重排（planNewTask）。"""
    try:
        t = get_client().get_flight_task(task_id)
    except DroneManageError as exc:
        return {"error": f"无人机平台不可达：{exc}"}
    if not t:
        return {"error": f"任务 {task_id} 不存在"}

    def _exec(p: dict[str, Any]) -> dict[str, Any]:
        blocked = _guard_real_task()
        if blocked:
            return blocked
        if p.get("new_time"):
            get_client().update_flight_task(
                p["task_id"], {"executionMode": "scheduled", "executionTime": p["new_time"]}
            )
            return {"status": "rescheduled", "task_id": p["task_id"], "new_time": p["new_time"]}
        data = get_client().plan_new_task(p["task_id"])
        return {"status": "rescheduled_auto", "task_id": p["task_id"], "platform_result": data}

    return _simple_confirm_action(
        "reschedule_task", {"task_id": task_id, "new_time": new_time},
        f"重排期 · {t.get('taskName') or task_id}",
        [{"label": "任务", "value": t.get("taskName") or task_id},
         {"label": "原定时间", "value": str(t.get("executionTime") or t.get("plannedStartTime") or "-")},
         {"label": "新时间", "value": new_time or "平台自动计算最近可行窗口"}],
        confirm_token, _exec,
    )


def retry_failed_task(job_id: str, confirm_token: str | None = None) -> dict[str, Any]:
    """【高危】失败架次重试（平台自动重排期并下发）。job_id 为 wayline 作业号。"""
    def _exec(p: dict[str, Any]) -> dict[str, Any]:
        blocked = _guard_real_task()
        if blocked:
            return blocked
        data = get_client().fail_task_retry(p["job_id"])
        return {"status": "retry_submitted", "job_id": p["job_id"], "platform_result": data}

    return _simple_confirm_action(
        "retry_failed_task", {"job_id": job_id},
        f"失败重试 · 作业 {job_id}",
        [{"label": "作业", "value": job_id},
         {"label": "动作", "value": "平台自动重新排期并下发执行"}],
        confirm_token, _exec,
    )


def resume_from_breakpoint(job_id: str, confirm_token: str | None = None) -> dict[str, Any]:
    """【高危】断点续飞（从中断航点继续执行）。job_id 为 wayline 作业号。"""
    def _exec(p: dict[str, Any]) -> dict[str, Any]:
        blocked = _guard_real_task()
        if blocked:
            return blocked
        data = get_client().breakpoint_flight(p["job_id"])
        return {"status": "resume_submitted", "job_id": p["job_id"], "platform_result": data}

    return _simple_confirm_action(
        "resume_from_breakpoint", {"job_id": job_id},
        f"断点续飞 · 作业 {job_id}",
        [{"label": "作业", "value": job_id},
         {"label": "动作", "value": "从中断航点继续执行（真实起飞）"}],
        confirm_token, _exec,
    )


def optimize_route_connection(task_id: str, min_height_above_terrain: float = 120.0) -> dict[str, Any]:
    """航线连接优化（机场→航线起点安全连接，低危写：只改连接段不触发执行）。"""
    try:
        data = get_client().optimize_route(task_id, min_height_above_terrain)
    except DroneManageError as exc:
        return {"error": f"平台优化失败：{exc}"}
    return {"status": "optimized", "task_id": task_id, "platform_result": data}
