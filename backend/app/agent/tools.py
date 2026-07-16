"""P0 全部 15 个工具（LangChain @tool 形式，产品链路使用）。

与 app/mcp_servers/* 共用同一层业务原子化代码（app/core）——
换编排框架/换模型时工具层原封不动。

tool 的 name/description 是 Agent 命中率的第一杠杆，按
"给一个不了解系统的新同事看"的标准撰写；调优顺序：先改描述，再改代码。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool


def _as_list(v: Any) -> Any:
    """把模型偶发的"字符串化列表"强制还原为列表。

    qwen 等模型有时把数组参数传成 JSON 字符串（plot_ids='["0005"]'）甚至裸串
    （plot_ids='0005'），若不还原，下游 `for i in plot_ids` 会按字符遍历导致
    子串误匹配、Agent 反复重试。这里统一在工具边界归一化。
    """
    if v is None or isinstance(v, list):
        return v
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("["):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return [str(x) for x in parsed]
            except (json.JSONDecodeError, ValueError):
                pass
        return [v]  # 裸串当作单个编号
    return v

from app.core import batch as batch_core
from app.core import drones as drones_core
from app.core import plots as plots_core
from app.core import preflight as preflight_core
from app.core import routes as routes_core
from app.core import tasks as tasks_core


def _lean_plots(result: dict[str, Any]) -> dict[str, Any]:
    """给 LLM 的图斑结果剔除大体积 geometry（占 ~86% token，几何前端另从 STORE 取），
    保留编号/类型/优先级/面积/中心等推理必需字段，避免多次查询撑爆上下文。"""
    if not isinstance(result, dict) or "plots" not in result:
        return result
    lean = dict(result)
    lean["plots"] = [{k: v for k, v in p.items() if k != "geometry"} for p in result["plots"]]
    return lean


def _lean_route(result: dict[str, Any]) -> dict[str, Any]:
    """给 LLM 的航线结果剔除 waypoints/geometry（几十个航点坐标），保留统计与决策字段；
    航点数量以标量 waypoint_count 保留（供复述用，不占 token）。"""
    if not isinstance(result, dict) or result.get("error"):
        return result
    lean = {k: v for k, v in result.items() if k not in ("waypoints", "geometry")}
    if isinstance(result.get("waypoints"), list):
        lean["waypoint_count"] = len(result["waypoints"])
    return lean

# ── drone-dispatch（调度域）──────────────────────────────────


@tool
def query_plots(
    region: str | None = None,
    plot_ids: list[str] | str | None = None,
    plot_type: str | None = None,
    date_range: list[str] | str | None = None,
    batch_no: str | None = None,
) -> dict[str, Any]:
    """查询自然资源核查图斑（下发的疑似变化地块）。

    用户说"查一下XX区的图斑 / 这周要核查哪些图斑 / GM-03 的情况"时用本工具。
    所有参数均可选：region 为行政区名（如"光明区"）；plot_ids 为图斑编号列表
    （如 ["GM-03"]）；plot_type 为疑似变化类型关键词；date_range 为
    ["YYYY-MM-DD","YYYY-MM-DD"]；batch_no 为下发批次号。
    返回图斑列表：编号、类型、面积（亩）、优先级、中心坐标、下发时间（几何边界已在地图显示，无需返回给你）。
    """
    return _lean_plots(
        plots_core.query_plots(region, _as_list(plot_ids), plot_type, _as_list(date_range), batch_no)
    )


@tool
def find_nearby_drones(
    plot_id: str | None = None,
    location: dict[str, Any] | None = None,
    radius_km: float = 5.0,
    plot_ids: list[str] | str | None = None,
) -> dict[str, Any]:
    """查询图斑周边可用的无人机。

    两种用法，务必区分：
    ① **为某批图斑选执行设备**（准备规划航线/派任务时）：必须传 plot_ids=
      本次要执行任务的目标图斑——距离按这些目标图斑计算。**选机的距离基准
      必须是要飞的图斑**；绝不能用"全部图斑盘点"里的距离给某批图斑选机
      （那个距离是到任意最近图斑的，可能与本次目标无关）。
    ② **泛盘点**（"这些图斑附近有哪些无人机"）：不传参照，以当前查询到的
      全部待核查图斑为参照集，结果 nearest_plot 标明各机离哪个图斑最近。
    radius_km 默认 5。返回编号、机型、电量、挂载、状态、距离，按距离升序。
    调度建议由你综合电量/距离/挂载推理给出；一旦确定了要飞哪批图斑，
    推荐设备前应按用法①重新查询，不要沿用泛盘点的旧距离。
    """
    return drones_core.find_nearby_drones(plot_id, location, radius_km, _as_list(plot_ids))


@tool
def get_drone_status(drone_id: str) -> dict[str, Any]:
    """查询单架无人机/机场的实时详细状态：电量、位置、固件、避障开关、健康自检。

    用户点名问某架设备的情况就用本工具，例如"D-12 现在什么状态"、
    "XX 机场怎么样了"、"D-07 电量还剩多少"、"它还能不能飞"。
    状态是实时数据，必须调用本工具获取，不得凭之前的对话内容回答。
    """
    return drones_core.get_drone_status(drone_id)


@tool
def dispatch_drone(
    drone_id: str,
    task_type: str,
    plot_ids: list[str] | str,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """【高危·需人工确认】锁定一架无人机执行核查任务。

    第一次调用时**不要**传 confirm_token：系统会生成待确认单并在界面
    弹出确认卡片，等待人工确认。人工确认后你会收到带 confirm_token 的
    后续指令，此时携带 token 再次调用才会真正锁定。
    task_type 如"图斑核查"。plot_ids 为本次任务关联的图斑编号。
    """
    return tasks_core.dispatch_drone(drone_id, task_type, _as_list(plot_ids), confirm_token)


# ── route-planning（航线域）──────────────────────────────────


@tool
def generate_route(
    drone_id: str,
    plot_ids: list[str] | str,
    strategy: str = "multi_cover",
    altitude_m: float = 120.0,
    overlap_rate: float = 0.7,
    photo_num: int = 4,
    replace_route_id: str | None = None,
) -> dict[str, Any]:
    """生成 / 重规划覆盖指定图斑的核查航线（也是自然语言优化航线的工具）。

    首次规划：用户说"规划航线 / 用 D-12 给这几个图斑规划航线"时调用。

    ── 自然语言软约束优化（把用户诉求翻译成下列参数，重新调用本工具，
       并传 replace_route_id=当前航线 route_id 以替换旧航线并拿到前后对比）──
      · "飞低一点更清晰/降低高度"   → altitude_m（平台安全带约 100~120m，低于下限会被顶回；实际值以返回为准）
      · "每块多拍几张/拍 N 张/拍密一点/精度高些" → photo_num（每图斑拍照点数，PLOT_INSPECTION 靠它控制采样密度，是最有效的清晰度/精度杠杆）
      · "只飞这一块/别顺带其他图斑"             → strategy="single"
      · "尽量一趟飞完/顺带把周边也覆盖"          → strategy="multi_cover"
      · "把旁边那块也加进来/去掉某块"            → 调整 plot_ids
    这些都是**整条航线统一**的参数（平台算法不支持给不同图斑设不同拍照数/高度）。

    ── 优化闭环 ──
    返回的 feasibility.within_budget=false 表示超出续航，按 feasibility.hint
    放宽参数（降 photo_num / 减图斑 / 换电量更高设备）后再规划，别硬报可行。
    change_vs_previous 给出与被替换航线的前后对比，用它向用户复述变化。

    ── 做不到的约束，如实说明、别硬凑 ──
    "避开村庄/水域上空""只给某一个图斑单独多拍"这类平台算法无法自动满足的，
    明确告知用户，并引导用 open_route_editor 在编辑器里手动调整该图斑航点。

    返回 route_id、航程、时长、覆盖图斑、feasibility、change_vs_previous。
    首次规划成功后建议立即调用 explain_route 主动解释规划逻辑。
    """
    return _lean_route(routes_core.generate_route(
        drone_id, _as_list(plot_ids), strategy, altitude_m, overlap_rate, photo_num, replace_route_id
    ))


@tool
def get_route_detail(route_id: str, version: int | None = None) -> dict[str, Any]:
    """查询航线的最新全量详情：航点列表、航程、时长、覆盖图斑、与上一版本的 diff。

    只要用户询问航线的任何信息——"航线多长"、"要飞多久"、"航点列表给我看看"、
    "现在这条航线什么情况"——都必须调用本工具取最新版本再回答；
    航线可能已被人工编辑更新，禁止引用对话历史里的旧数据。
    编辑器回传（[EDITOR_SAVED]）后也用本工具，并根据 diff_vs_prev 复述变更影响。
    """
    return _lean_route(routes_core.get_route_detail(route_id, version))


@tool
def explain_route(route_id: str) -> dict[str, Any]:
    """获取航线规划算法的结构化决策依据，用于向用户解释"为什么这么规划"。

    用户**每次**追问规划原因都必须重新调用本工具——"为什么这么规划"、
    "这条航线是怎么考虑的"、"为啥要覆盖这几个图斑"、"解释一下航线逻辑"——
    即使你之前已经解释过，也要重新调用取最新决策数据（航线可能已变更），
    禁止凭对话记忆复述。
    返回：覆盖图斑及覆盖率、同航向带合并原因与增量航程对比、被放弃图斑
    及原因、避让要素、与逐图斑单独起飞的架次/耗时对比。
    你必须只基于返回的数据转述，不得编造数据之外的理由。
    """
    return routes_core.explain_route(route_id)


@tool
def open_route_editor(route_id: str) -> dict[str, Any]:
    """生成航线可视化编辑界面的免登录链接（临时 token，10 分钟有效）。

    用户说"我手动调整一下航线 / 我要改航点"时用本工具。
    返回的 url 会自动嵌入右侧效果区，用户编辑完成后结果自动回传。
    """
    return routes_core.open_route_editor(route_id)


# ── preflight（飞前检查域）───────────────────────────────────


@tool
def check_weather(location: str = "光明区", time_window: str | None = None) -> dict[str, Any]:
    """飞前检查①：查询作业区域气象（风速/能见度/降水/温度）并给出适飞结论。"""
    return preflight_core.check_weather(location, time_window)


@tool
def check_battery(drone_id: str, route_id: str) -> dict[str, Any]:
    """飞前检查②：核对无人机当前电量、预计续航与任务时长，给出余量结论。"""
    return preflight_core.check_battery(drone_id, route_id)


@tool
def check_route_obstacle(route_id: str) -> dict[str, Any]:
    """飞前检查③：航线净空分析——仿地飞行开关、净空高度、避让要素清单。"""
    return preflight_core.check_route_obstacle(route_id)


@tool
def check_drone_obstacle(drone_id: str) -> dict[str, Any]:
    """飞前检查④：机载视觉/雷达避障系统自检状态。"""
    return preflight_core.check_drone_obstacle(drone_id)


@tool
def check_airspace(route_id: str, time_window: str | None = None) -> dict[str, Any]:
    """飞前检查⑤：空域许可有效期与冲突活动提醒。"""
    return preflight_core.check_airspace(route_id, time_window)


@tool
def preflight_check(drone_id: str, route_id: str) -> dict[str, Any]:
    """飞前检查（聚合）：一次返回气象/电量/航线避障/机载避障/空域五项结果。

    快速链路使用。当用户说"我要起飞 / 可以飞了吗"，若希望逐项展示
    检查过程（推荐，体验更好），请依次调用 check_weather → check_battery →
    check_route_obstacle → check_drone_obstacle → check_airspace 五个单项工具，
    而不是本聚合工具。全部通过后调用 take_off（不带 confirm_token）发起确认。
    """
    return preflight_core.preflight_check(drone_id, route_id)


# ── flight-task（飞行任务域）─────────────────────────────────


@tool
def take_off(drone_id: str, route_id: str, confirm_token: str | None = None) -> dict[str, Any]:
    """【高危·需人工确认】下发起飞指令，开始执行航线任务。

    重要：不带 confirm_token 调用本工具**不会起飞**，只是生成待确认单、
    在界面弹出确认卡片——这正是标准流程的一部分，无需先征求用户同意。
    因此：五项飞前检查完成且没有 fail 项时，应**立即**调用本工具
    （不传 confirm_token）发起人工确认，不要停下来询问"是否要起飞"。
    人工点击确认后你会收到带 confirm_token 的后续指令，携带 token 再次
    调用才会真正起飞；无有效 token 的调用一律被拒绝。
    """
    return tasks_core.take_off(drone_id, route_id, confirm_token)


# ── batch（批量任务编排域，P1 场景8：Plan-and-Execute）──────


@tool
def create_task_plan(
    plot_ids: list[str] | str,
    deadline_days: int = 5,
    max_sorties_per_day: int = 3,
    priority_first: bool = True,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """【高危·需人工确认】把一批图斑排成逐日核查计划（批量任务编排）。

    用户说"把这些图斑排个期本周飞完/每天不超过 N 架次/按优先级批量安排"时用本工具。
    算法按优先级排序、就近合并成架次、按每日架次上限装箱到各天，并校验截止约束。
    与 take_off 一样人在环：首次调用**不要**传 confirm_token，系统弹出计划确认卡片；
    人工确认整份计划即授权后续执行——收到带 confirm_token 的后续指令后携带 token
    再次调用，计划生效并自动执行第 1 天批次（逐架次规划航线 + 锁定无人机），
    后续天次保持排期待执行。返回 schedule（逐日架次表）供你向用户复述。
    """
    return batch_core.create_task_plan(
        _as_list(plot_ids), deadline_days, max_sorties_per_day, priority_first, confirm_token
    )


@tool
def get_plan_progress(plan_id: str) -> dict[str, Any]:
    """查询批量核查计划的执行进度：各天各架次的状态（scheduled/dispatched/completed）。"""
    return batch_core.get_plan_progress(plan_id)


ALL_TOOLS = [
    query_plots,
    find_nearby_drones,
    get_drone_status,
    dispatch_drone,
    generate_route,
    get_route_detail,
    explain_route,
    open_route_editor,
    check_weather,
    check_battery,
    check_route_obstacle,
    check_drone_obstacle,
    check_airspace,
    preflight_check,
    take_off,
]

# P1 批量编排工具（与 P0 分开，不计入 P0 契约测试的 15 个）
BATCH_TOOLS = [create_task_plan, get_plan_progress]
