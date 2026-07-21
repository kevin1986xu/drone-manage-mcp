"""平台接口契约冒烟（05 文档 §6.3）：调真实平台验响应 schema 关键字段。

平台迭代会让 MCP 工具静默坏掉——本脚本在 CI/演示前跑一遍，
每个新域至少覆盖一条读链路的关键字段断言。只读，不产生任何平台写面。

用法：cd mcp-services && PYTHONPATH=src .venv/bin/python3 scripts/contract_smoke.py
"""

from __future__ import annotations

import sys

sys.path.insert(0, "src")

from uav_mcp.drone_manage import get_client  # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, note: str = "") -> None:
    print(f"  {'✓' if cond else '✗'} {name}" + (f"  {note}" if note else ""))
    if not cond:
        FAILURES.append(name)


def main() -> int:
    c = get_client()

    print("[图斑/围栏 flyWorkZone]")
    plots = c.list_plots()
    check("图斑非空", len(plots) > 0, f"{len(plots)} 条")
    p = plots[0]
    check("图斑关键字段", all(k in p for k in ("plot_id", "region", "area_code", "geometry", "centroid")))
    zones = c.list_zones(["禁飞区"])
    check("禁飞区非空", len(zones) > 0, f"{len(zones)} 条")
    z = zones[0]
    check("围栏关键字段", all(k in z for k in ("zoneId", "zoneName", "zoneType", "status")))
    check("围栏几何可用", bool(z.get("zoneGeometryJson") or z.get("zoneGeometry")))

    print("[告警 alerts]")
    alerts = c.list_alerts({"pageNum": 1, "pageSize": 1})
    check("告警 rows/total", isinstance(alerts["rows"], list) and alerts["total"] >= 0, f"total={alerts['total']}")
    if alerts["rows"]:
        a = alerts["rows"][0]
        check("告警关键字段", all(k in a for k in ("alertId", "alertLevel", "alertStatus", "alertTime")))
        check("告警枚举为整数", isinstance(a["alertLevel"], int) and isinstance(a["alertStatus"], int))
    n = c.alerts_unhandled_count()
    check("未处理计数", isinstance(n, int), f"{n} 条")

    print("[媒体 media]")
    media = c.media_page({"pageNum": 1, "pageSize": 1})
    check("媒体 rows/total", isinstance(media["rows"], list) and media["total"] >= 0, f"total={media['total']}")
    if media["rows"]:
        m = media["rows"][0]
        check("媒体关键字段", all(k in m for k in ("fileId", "fileName", "fileType", "filePath")))
        url = c.media_file_url(m["fileId"])
        check("取链可用", bool(url), (url or "")[:50])

    print("[任务调度 tasks]")
    tasks = c.flight_tasks_query({"pageNum": 1, "pageSize": 1})
    check("任务 rows/total", isinstance(tasks["rows"], list) and tasks["total"] >= 0, f"total={tasks['total']}")
    if tasks["rows"]:
        t = tasks["rows"][0]
        check("任务关键字段", all(k in t for k in ("taskId", "taskName", "executionMode", "taskStatus")))
    jobs = c.wayline_jobs_search({"pageNum": 1, "pageSize": 1})
    check("wayline-jobs 可查", isinstance(jobs, list), f"{len(jobs)} 条(首页)")
    if jobs:
        check("作业关键字段", all(k in jobs[0] for k in ("jobId", "beginTime", "status")))

    print("[直播/遥测 live]（P1，只读链路）")
    docks_pre = c.list_docks()
    online = next((d for d in docks_pre if d.get("online") and d.get("device_sn")), None)
    if online:
        sn = online["device_sn"]
        try:
            cap = c.live_capacity(sn)
            check("直播能力可查", True, f"{online['drone_id']} capacity={'有' if cap else '空'}")
        except Exception as exc:  # noqa: BLE001 —— 契约面：接口可达但设备可能不支持
            check("直播能力可查", "404" in str(exc) or "不" in str(exc), f"{exc}")
        osd = c.osd_latest(sn)
        check("OSD latest 可查", osd is None or isinstance(osd, dict),
              "有数据" if osd else "无数据（未飞过）")
        dosd = c.dock_osd_latest(sn)
        check("机场 OSD（环境读数）", dosd is None or isinstance(dosd, dict),
              f"温度={dosd.get('temperature') if dosd else '—'}")
    else:
        check("在线机场存在（live 前置）", False, "全部离线，直播契约跳过")

    print("[飞控 flight-control]（P1，只读链路；写面真机联调）")
    try:
        nfz = c.takeover_no_fly_zone_check(113.42, 30.65, 100)
        check("接管限飞检查可用", True, f"{str(nfz)[:60]}")
    except Exception as exc:  # noqa: BLE001
        check("接管限飞检查可用", False, f"{exc}")
    print("  ↳ 写面（返航/急停/指点/喊话/舱盖/充电…）为真机联调项，本冒烟不触发")

    print("[设备 device]")
    docks = c.list_docks()
    check("机场设备非空", len(docks) > 0, f"{len(docks)} 台")
    check("设备关键字段", all(k in docks[0] for k in ("drone_id", "device_sn", "online", "location")))

    print()
    if FAILURES:
        print(f"✗ 契约冒烟失败 {len(FAILURES)} 项：{FAILURES}")
        return 1
    print("✓ 契约冒烟全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
