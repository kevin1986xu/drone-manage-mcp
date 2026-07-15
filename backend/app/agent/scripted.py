"""Scripted 模式：关键词兜底路由（《开发计划》L2 降级，同时是脱网演示保底）。

与 LLM 模式发出**完全相同的 AG-UI 事件流**，前端无感知。
工具真实调用业务原子层，话术模板用工具返回的真实数字填充。
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

from app.agent import tools as T
from app.agui import events as ag
from app.core.store import STORE

# 评测跑批用：SCRIPTED_FAST=1 跳过模拟延迟与打字机效果
_FAST = os.getenv("SCRIPTED_FAST") == "1"

# 每个会话的上下文（指代消解用）
_THREADS: dict[str, dict[str, Any]] = {}


def _ctx(thread_id: str) -> dict[str, Any]:
    return _THREADS.setdefault(
        thread_id, {"plots": [], "drone_id": None, "route_id": None, "plot_ids": []}
    )


def reset_threads() -> None:
    _THREADS.clear()


class _Emitter:
    """把「调工具 + 说话」封装成 AG-UI 事件序列。"""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def call_tool(self, tool, args: dict[str, Any], latency: float = 0.5) -> Any:
        call_id = uuid.uuid4().hex[:12]
        await self.queue.put(ag.tool_start(call_id, tool.name, args))
        await asyncio.sleep(0 if _FAST else latency)  # 模拟真实系统延迟，演示观感
        result = tool.func(**args)
        await self.queue.put(ag.tool_end(call_id, tool.name, result))
        for d in ag.directives_for(tool.name, result):
            await self.queue.put(d)
        return result

    async def say(self, text: str) -> None:
        msg_id = uuid.uuid4().hex[:12]
        await self.queue.put(ag.text_start(msg_id))
        if _FAST:
            await self.queue.put(ag.text_content(msg_id, text))
        else:
            for i in range(0, len(text), 3):
                await self.queue.put(ag.text_content(msg_id, text[i : i + 3]))
                await asyncio.sleep(0.02)
        await self.queue.put(ag.text_end(msg_id))


def _extract_plot_ids(text: str) -> list[str]:
    return [f"GM-{int(n):02d}" for n in re.findall(r"GM-?\s?(\d{1,2})", text, re.I)]


def _extract_drone(text: str) -> str | None:
    m = re.search(r"D-?\s?(\d{1,2})", text, re.I)
    return f"D-{int(m.group(1)):02d}" if m else None


_DISTRICTS = ["光明", "南山", "宝安", "龙华", "福田", "罗湖", "龙岗", "盐田", "坪山", "大鹏"]


def _extract_region(text: str) -> str | None:
    for d in _DISTRICTS:
        if d in text:
            return f"{d}区"
    m = re.search(r"([一-龥]{2})[市县区]", text)  # 汉川市/云梦县等两字地名
    if m:
        return m.group(0)
    return None  # 不过滤，查全部（真实/多区域数据下更稳）


async def _handle(em: _Emitter, ctx: dict[str, Any], msg: str) -> None:
    # ── 系统回传：人工确认 ───────────────────────────────
    if msg.startswith("[SYSTEM_CONFIRMATION]"):
        action = re.search(r"action=(\w+)", msg)
        token = re.search(r"confirm_token=([\w\-]+)", msg)
        action_id = re.search(r"action_id=([\w\-]+)", msg)
        if not (action and token and action_id):
            await em.say("确认信息不完整，无法执行。")
            return
        item = STORE.pending_actions.get(action_id.group(1))
        params = (item or {}).get("params", {})
        if action.group(1) == "take_off":
            r = await em.call_tool(
                T.take_off,
                {"drone_id": params.get("drone_id"), "route_id": params.get("route_id"), "confirm_token": token.group(1)},
                0.9,
            )
            if r.get("status") == "airborne":
                ctx["flight_task_id"] = r["flight_task_id"]
                await em.say(
                    f"{r['drone_id']} 已起飞，正沿 {r['route_id']} 执行核查任务（任务号 {r['flight_task_id']}，"
                    f"预计 {r['duration_min']} 分钟）。我会全程监控遥测，出现低电量、偏航或失联会立即提醒你；"
                    "任务完成后自动生成核查报告。"
                )
            else:
                await em.say(f"起飞指令未执行：{r.get('reason') or r.get('error')}")
        elif action.group(1) == "dispatch_drone":
            r = await em.call_tool(
                T.dispatch_drone,
                {
                    "drone_id": params.get("drone_id"),
                    "task_type": params.get("task_type"),
                    "plot_ids": params.get("plot_ids", []),
                    "confirm_token": token.group(1),
                },
                0.7,
            )
            if r.get("order_id"):
                ctx["drone_id"] = r["drone_id"]
                await em.say(
                    f"已锁定 {r['drone_id']} 执行{r['task_type']}（调度单 {r['order_id']}，"
                    f"关联图斑 {'、'.join(r['plot_ids'])}）。接下来可以说\"规划航线\"。"
                )
            else:
                await em.say(f"调度未执行：{r.get('reason') or r.get('error')}")
        elif action.group(1) == "create_task_plan":
            r = await em.call_tool(
                T.create_task_plan,
                {
                    "plot_ids": params.get("plot_ids", []),
                    "deadline_days": params.get("deadline_days", 5),
                    "max_sorties_per_day": params.get("max_sorties_per_day", 3),
                    "confirm_token": token.group(1),
                },
                1.0,
            )
            if r.get("status") == "plan_activated":
                ctx["plan_id"] = r["plan_id"]
                n_days = len(r["schedule"])
                await em.say(
                    f"计划 {r['plan_id']} 已生效。第 1 天 {r['day1_executed']} 个架次已完成航线规划并锁定无人机，"
                    f"后续 {n_days - 1} 天的架次已排期待执行。每完成一批我会向你汇报，你也可以随时问我进度。"
                )
            else:
                await em.say(f"计划未生效：{r.get('reason') or r.get('error')}")
        return

    if msg.startswith("[SYSTEM_CANCELLED]"):
        await em.say("好的，已取消该操作。需要调整方案的话直接告诉我。")
        return

    # ── 系统回传：任务完成 ───────────────────────────────
    if msg.startswith("[TASK_COMPLETED]"):
        tid = re.search(r"flight_task_id=([\w\-]+)", msg)
        r = STORE.flight_tasks.get(tid.group(1)) if tid else None
        covered = "、".join(r["covered_plots"]) if r else ""
        await em.say(
            f"任务完成：{r['drone_id'] if r else '无人机'} 已按航线完成飞行，"
            f"采集影像 214 张、点云 1 组，已自动入库并关联 {covered} 三个图斑。"
            "核查报告草稿已生成，需要我读一下要点吗？"
        )
        return

    # ── 系统回传：编辑器保存 ─────────────────────────────
    if msg.startswith("[EDITOR_SAVED]"):
        rid = (re.search(r"route_id=([\w\-]+)", msg) or [None, ctx.get("route_id")])[1]
        r = await em.call_tool(T.get_route_detail, {"route_id": rid}, 0.5)
        diff = r.get("diff_vs_prev") or {}
        moved = diff.get("moved_waypoints", [])
        if not moved and not diff.get("waypoint_count_delta"):
            await em.say(f"编辑器已回传，{r['route_id']} 的航点没有变化，仍为 rev.{r['version']} 之前的方案。可以直接说\"我要起飞\"。")
            return
        moved_txt = (
            f"{len(moved)} 个航点位置有调整（如 {moved[0]['seq']} 号航点移动约 {moved[0]['moved_m']} m）"
            if moved
            else f"航点数量{'增加' if diff.get('waypoint_count_delta', 0) > 0 else '减少'} {abs(diff.get('waypoint_count_delta', 0))} 个"
        )
        await em.say(
            f"收到你的调整（{r['route_id']} 已更新至 rev.{r['version']}）：{moved_txt}，"
            f"航程 {'+' if diff.get('length_km_delta', 0) >= 0 else ''}{diff.get('length_km_delta', 0)} km，"
            f"预计时长更新为 {r['duration_min']} 分钟，续航余量仍然充足。"
            "确认没问题就可以说\"我要起飞\"。"
        )
        return

    # ── 话术路由（演示主线五步 + 常见变体，评测集反向驱动扩展）──
    lower = msg.lower()

    # 航线解释（先于其他航线分支：为什么/怎么考虑/逻辑类提问）
    if any(k in msg for k in ("为什么", "为啥", "怎么考虑", "逻辑", "依据", "解释")) and (
        "规划" in msg or "航线" in msg
    ):
        if not ctx.get("route_id"):
            await em.say("当前还没有航线。先规划一条，我再给你讲规划逻辑。")
            return
        ex = await em.call_tool(T.explain_route, {"route_id": ctx["route_id"]}, 0.6)
        d = ex["decision"]
        merged = [m["plot_id"] for m in d["merged_candidates"]]
        bc = d["baseline_comparison"]
        rej = "；".join(f"{x['plot_id']}（{x['reason']}）" for x in d["rejected_candidates"][:2])
        await em.say(
            f"这条航线的决策依据：覆盖 {len(d['covered_plots'])} 个图斑，"
            f"{'其中 ' + '、'.join(merged) + ' 因处于同一航向带、顺带覆盖的增量航程小于单独起飞而合并；' if merged else ''}"
            f"未纳入的图斑：{rej}。与逐图斑单独起飞相比，节省约 {bc['saved_min']} 分钟"
            f"（{bc['separate_sorties']} 个架次 {bc['separate_total_min']} min → 1 个架次 {bc['merged_total_min']} min）。"
        )
        return

    # 批量任务编排（Plan-and-Execute，场景8）
    if any(k in msg for k in ("排期", "本周飞完", "批量", "都飞完", "全部飞完", "分几天")) or (
        "每天" in msg and "架次" in msg
    ):
        plot_ids = _extract_plot_ids(msg) or ctx.get("plot_ids") or [
            p["plot_id"] for p in (ctx.get("plots") or T.query_plots.func()["plots"])
        ]
        deadline = 7 if "本周" in msg else 5
        dm = re.search(r"(\d+)\s*天", msg)
        if dm:
            deadline = int(dm.group(1))
        maxs = 3
        sm = re.search(r"(?:每天|不超过)\D{0,4}(\d+)\s*架次", msg)
        if sm:
            maxs = int(sm.group(1))
        r = await em.call_tool(
            T.create_task_plan, {"plot_ids": plot_ids, "deadline_days": deadline, "max_sorties_per_day": maxs}, 1.3
        )
        if r.get("status") == "requires_confirmation":
            sched = r["schedule"]
            n_sorties = sum(len(d["sorties"]) for d in sched)
            feas = "可在期限内完成" if r["feasible"] else f"⚠ 按每天≤{maxs}架次会超出 {deadline} 天期限，建议放宽每日架次或延长截止"
            await em.say(
                f"已生成批量核查排期：{len(plot_ids)} 个图斑就近合并为 {n_sorties} 个架次，"
                f"分 {len(sched)} 天完成（每天≤{maxs} 架次），{feas}。"
                "计划已在右侧列出，请在下方卡片确认后开始执行第 1 天。"
            )
        else:
            await em.say(f"排期失败：{r.get('error')}")
        return

    # 计划进度
    if ctx.get("plan_id") and any(k in msg for k in ("进度", "怎么样了", "到哪了", "执行情况", "飞到哪")):
        r = await em.call_tool(T.get_plan_progress, {"plan_id": ctx["plan_id"]}, 0.6)
        await em.say(f"计划 {r['plan_id']} 当前进度：{r['executed_sorties']}/{r['total_sorties']} 个架次已执行（状态：{r['status']}）。")
        return

    # 单机状态（点名某架无人机 + 状态类问题）
    if (
        _extract_drone(msg)
        and any(k in msg for k in ("状态", "电量", "情况", "怎么样", "健康", "自检"))
        and not any(k in msg for k in ("规划", "航线", "起飞"))
    ):
        r = await em.call_tool(T.get_drone_status, {"drone_id": _extract_drone(msg)}, 0.6)
        if r.get("error"):
            await em.say(r["error"])
            return
        await em.say(
            f"{r['drone_id']}（{r['model']}）当前{r['status_cn']}：电量 {r['battery_pct']}%，"
            f"挂载{r['payload']}，避障{'在线' if r['obstacle_avoidance'] else '离线'}，健康自检{r['health_check']}。"
        )
        return

    if any(k in msg for k in ("无人机", "调度", "飞机")) and not any(k in msg for k in ("起飞", "航线")):
        r = await em.call_tool(T.find_nearby_drones, {"radius_km": 5.0}, 0.9)
        if not r["drones"]:
            await em.say("5 公里范围内暂无可用无人机。")
            return
        idle = [d for d in r["drones"] if d["status"] == "idle"]
        pool = idle or r["drones"]
        best = max(
            pool,
            key=lambda d: (d["battery_pct"] or 50) * 0.6
            - d["distance_km"] * 10
            + (30 if "激光雷达" in d["payload"] else 0),
        )
        ctx["drone_id"] = best["drone_id"]
        bat = f"电量 {best['battery_pct']}%" if best["battery_pct"] is not None else "电量待机场遥测确认"
        scope = (
            f"查询到的 {r['reference_plot_count']} 个图斑周边（每个图斑 {r['radius_km']:.0f} 公里内）"
            if r.get("reference_plot_count", 1) > 1
            else f"周边 {r['radius_km']:.0f} 公里内"
        )
        near = f"，离图斑 {best['nearest_plot']} 最近" if best.get("nearest_plot") else ""
        await em.say(
            f"{scope}共有 {r['count']} 架可用无人机，已标注在地图上。"
            f"综合状态、距离与挂载，{best['drone_id']}（{bat}，最近 {best['distance_km']} km{near}，"
            f"{best['status_cn']}，挂载{best['payload']}）最优，建议由它执行核查任务。"
        )
        return

    if ("图斑" in msg and any(k in msg for k in ("查", "哪些", "情况", "核查", "看", "有"))) or "图斑" == msg.strip():
        plot_ids = _extract_plot_ids(msg)
        region = _extract_region(msg)
        args: dict[str, Any] = {"plot_ids": plot_ids} if plot_ids else ({"region": region} if region else {})
        r = await em.call_tool(T.query_plots, args, 0.8)
        ctx["plots"] = r["plots"]
        ctx["plot_ids"] = [p["plot_id"] for p in r["plots"]]
        if not r["plots"]:
            await em.say("没有查到符合条件的图斑，可以换个区域或图斑编号再试。")
            return
        high = [p for p in r["plots"] if p["priority"] == "高"]
        shown = r["plots"][:6]
        names = "、".join(p["plot_id"] for p in shown) + ("等" if r["count"] > 6 else "")
        where = args.get("region") or "当前范围"
        await em.say(
            f"在{where}找到 {r['count']} 个待核查图斑（{names}，批次 {r['batch_no']}），"
            f"已在右侧地图标出。其中 {'、'.join(p['plot_id'] for p in high[:3])} 为高优先级"
            f"（{high[0]['plot_type']}等），建议优先核查。"
            if high
            else f"在{where}找到 {r['count']} 个待核查图斑（{names}），已在右侧地图标出。"
        )
        return

    if (
        ("航线" in msg and any(k in msg for k in ("规划", "生成", "覆盖")))
        or "重新规划" in msg
        or ("规划" in msg and ("图斑" in msg or _extract_plot_ids(msg)))
        or lower.strip() in {"规划航线", "生成航线"}
    ):
        drone_id = _extract_drone(msg) or ctx.get("drone_id") or "D-12"
        plot_ids = _extract_plot_ids(msg)
        if not plot_ids:
            # 默认给最高优先级的重点图斑规划，multi_cover 会自动合并同航向带图斑
            plots = ctx.get("plots") or T.query_plots.func(region="光明区")["plots"]
            focus = [p for p in plots if "重点" in p["plot_type"]] or [p for p in plots if p["priority"] == "高"] or plots
            plot_ids = [focus[0]["plot_id"]]
        ctx["drone_id"] = drone_id
        # "每个图斑拍 N 张 / 拍 N 个点" → photo_num（整条航线统一）
        pm = re.search(r"(?:拍摄?|拍照)?\s*(\d+)\s*(?:张|个点|个拍照点)", msg)
        args = {"drone_id": drone_id, "plot_ids": plot_ids, "strategy": "multi_cover"}
        if pm:
            args["photo_num"] = max(1, min(12, int(pm.group(1))))
        r = await em.call_tool(T.generate_route, args, 1.2)
        if r.get("error"):
            await em.say(f"航线生成失败：{r['error']}")
            return
        ctx["route_id"] = r["route_id"]
        ctx["route_plot_ids"] = plot_ids  # 原始核查目标，供后续软约束重规划保持范围
        ex = await em.call_tool(T.explain_route, {"route_id": r["route_id"]}, 0.6)
        d = ex["decision"]
        merged = [m["plot_id"] for m in d["merged_candidates"]]
        bc = d["baseline_comparison"]
        explain = (
            f"说明一下规划逻辑：你本次只需核查 {'、'.join(plot_ids)}，"
            f"但 {'、'.join(merged)} 恰好在同一航向带上，顺带覆盖的增量航程小于单独起飞的往返航程，"
            f"因此一条航线覆盖 {len(d['covered_plots'])} 个图斑——"
            f"比分 {bc['separate_sorties']} 次起飞节省约 {bc['saved_min']} 分钟。"
            if merged
            else "本次没有可经济合并的相邻图斑，航线只覆盖指定图斑。"
        )
        await em.say(
            f"航线 {r['route_id']} 已生成（右侧青色虚线）：由 {drone_id} 执行，"
            f"航程 {r['length_km']} km，预计 {r['duration_min']} 分钟。{explain}"
        )
        return

    # 软约束调参：对已有航线提参数化优化诉求（飞低/多拍/精度/只飞这块）→ 重规划替换
    _tune_kw = ("飞低", "飞高", "降低", "提高", "高度", "拍", "重叠", "精度", "清晰", "只飞", "单独这块", "不顺带")
    if ctx.get("route_id") and any(k in msg for k in _tune_kw) and not any(
        k in msg for k in ("手动", "编辑器", "我自己", "挪", "拖")
    ):
        args: dict[str, Any] = {
            "drone_id": ctx.get("drone_id") or "D-12",
            "plot_ids": ctx.get("route_plot_ids") or [c["plot_id"] for c in T.get_route_detail.func(route_id=ctx["route_id"])["covered_plots"]],
            "strategy": "single" if any(k in msg for k in ("只飞", "单独这块", "不顺带")) else "multi_cover",
            "replace_route_id": ctx["route_id"],
        }
        # 高度（平台安全带约 100~120m；低于下限会被顶回，故"飞低"取 100）
        alt = re.search(r"(?:降到|调到|高度)\s*(\d{2,3})\s*米?", msg)
        if alt:
            args["altitude_m"] = float(alt.group(1))
        elif any(k in msg for k in ("飞低", "低一点", "低点", "降低")):
            args["altitude_m"] = 100.0
        # 拍照点数（PLOT_INSPECTION 靠拍照点数控制采样密度，是最有效的杠杆）
        pm = re.search(r"(?:拍摄?|拍照)?\s*(\d+)\s*(?:张|个点|个拍照点)", msg)
        if pm:
            args["photo_num"] = max(1, min(12, int(pm.group(1))))
        elif any(k in msg for k in ("多拍", "拍密", "密一点", "精度", "清晰", "细一点")):
            args["photo_num"] = 6
        r = await em.call_tool(T.generate_route, args, 1.2)
        if r.get("error"):
            await em.say(f"重规划失败：{r['error']}")
            return
        ctx["route_id"] = r["route_id"]
        chg = r.get("change_vs_previous") or {}
        fb = r.get("feasibility") or {}
        parts = []
        if "altitude_m" in args:
            parts.append(f"高度 {chg.get('altitude_m', args['altitude_m'])} m")
        if "photo_num" in args:
            parts.append(f"每图斑拍照点 {chg.get('photo_num', args['photo_num'])}")
        if args["strategy"] == "single":
            parts.append("只覆盖指定图斑（不顺带合并）")
        changed = "；".join(parts) or "参数已调整"
        feas = (
            f"续航余量 {fb.get('margin_min')} 分钟，充足"
            if fb.get("within_budget")
            else f"⚠ 预计 {fb.get('duration_min')} 分钟已超续航预算 {fb.get('endurance_budget_min')} 分钟，建议减少拍照点或换电量更高的设备"
        )
        await em.say(
            f"已按你的要求重规划（{r['route_id']}）：{changed}。"
            f"当前航程 {r['length_km']} km、预计 {r['duration_min']} 分钟，{feas}。"
        )
        return

    if any(k in msg for k in ("手动", "编辑器", "我自己")) or (any(k in msg for k in ("调整", "编辑", "挪", "拖")) and "航" in msg) or ("改" in msg and "航点" in msg):
        if not ctx.get("route_id"):
            await em.say("当前还没有航线，先说\"规划航线\"生成一条。")
            return
        r = await em.call_tool(T.open_route_editor, {"route_id": ctx["route_id"]}, 0.6)
        await em.say(
            f"已在右侧打开航线编辑界面（免登录嵌入，链接 {r['token_ttl_min']} 分钟内有效）。"
            "拖动航点调整后点击「保存并回传」，结果会自动回传给我。"
        )
        return

    # 航线详情（多长/多久/航点列表类查询）
    if ("航线" in msg or "航点" in msg) and any(k in msg for k in ("多长", "多久", "详情", "列表", "情况", "看看")):
        if not ctx.get("route_id"):
            await em.say("当前还没有航线，先说\"规划航线\"。")
            return
        r = await em.call_tool(T.get_route_detail, {"route_id": ctx["route_id"]}, 0.5)
        await em.say(
            f"{r['route_id']}（rev.{r['version']}，{r['source']}）：航程 {r['length_km']} km，"
            f"预计 {r['duration_min']} 分钟，{len(r['waypoints'])} 个航点，"
            f"覆盖图斑 {'、'.join(c['plot_id'] for c in r['covered_plots'])}，已在右侧地图显示。"
        )
        return

    # 单项飞前检查（用户点名问某一项）
    if "天气" in msg or "风速" in msg or "能见度" in msg:
        r = await em.call_tool(T.check_weather, {"location": "光明区"}, 0.55)
        await em.say(f"{r['detail']}。")
        return
    if "空域" in msg or ("许可" in msg and "飞" not in msg):
        if not ctx.get("route_id"):
            await em.say("还没有航线，空域检查需要针对具体航线进行。先规划航线。")
            return
        r = await em.call_tool(T.check_airspace, {"route_id": ctx["route_id"]}, 0.55)
        await em.say(f"{r['detail']}。")
        return
    if "避障" in msg and "起飞" not in msg:
        drone_id = ctx.get("drone_id") or "D-12"
        r = await em.call_tool(T.check_drone_obstacle, {"drone_id": drone_id}, 0.55)
        if ctx.get("route_id"):
            r2 = await em.call_tool(T.check_route_obstacle, {"route_id": ctx["route_id"]}, 0.55)
            await em.say(f"机载避障：{r['detail']}；航线避障：{r2['detail']}。")
        else:
            await em.say(f"{r['detail']}。")
        return
    if ("电量" in msg or "续航" in msg) and ctx.get("route_id"):
        drone_id = ctx.get("drone_id") or "D-12"
        r = await em.call_tool(T.check_battery, {"drone_id": drone_id, "route_id": ctx["route_id"]}, 0.55)
        await em.say(f"{r['detail']}，{'满足任务需求' if r['status'] == 'pass' else '余量偏紧，注意监控'}。")
        return

    if any(k in msg for k in ("起飞", "可以飞", "飞前", "检查一下")):
        if not ctx.get("route_id"):
            await em.say("还没有可执行的航线，先说\"规划航线\"。")
            return
        drone_id, route_id = ctx.get("drone_id") or "D-12", ctx["route_id"]
        await em.say("起飞前先做安全检查：")
        results = []
        results.append(await em.call_tool(T.check_weather, {"location": "光明区"}, 0.55))
        results.append(await em.call_tool(T.check_battery, {"drone_id": drone_id, "route_id": route_id}, 0.55))
        results.append(await em.call_tool(T.check_route_obstacle, {"route_id": route_id}, 0.55))
        results.append(await em.call_tool(T.check_drone_obstacle, {"drone_id": drone_id}, 0.55))
        results.append(await em.call_tool(T.check_airspace, {"route_id": route_id}, 0.55))
        n_pass = sum(1 for c in results if c["status"] == "pass")
        warns = [c for c in results if c["status"] == "warn"]
        fails = [c for c in results if c["status"] == "fail"]
        if fails:
            await em.say(f"检查未通过：{fails[0]['detail']}。请先处理后再起飞。")
            return
        warn_txt = f"，一项提示——{warns[0]['detail']}，当前时段起飞无冲突" if warns else ""
        await em.say(f"五项检查完成：{n_pass} 项通过{warn_txt}。整体满足起飞条件，请在下方卡片确认。")
        await em.call_tool(T.take_off, {"drone_id": drone_id, "route_id": route_id}, 0.5)
        return

    await em.say(
        "我可以帮你完成飞行核查全流程：查图斑（\"帮我查一下光明区的图斑\"）→ 调度周边无人机 → "
        "规划航线 → 手动调整航线 → 飞前检查与起飞确认。从查图斑开始试试。"
    )


async def run_scripted(thread_id: str, message: str) -> AsyncIterator[dict[str, Any]]:
    em = _Emitter()
    run_id = uuid.uuid4().hex[:12]
    yield ag.run_started(run_id)

    async def _work() -> None:
        try:
            await _handle(em, _ctx(thread_id), message.strip())
        except Exception as exc:  # noqa: BLE001
            await em.queue.put(ag.run_error(f"执行异常：{exc}"))
        finally:
            await em.queue.put(None)

    task = asyncio.create_task(_work())
    while True:
        item = await em.queue.get()
        if item is None:
            break
        yield item
    await task
    yield ag.run_finished(run_id)
