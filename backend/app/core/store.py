"""内存世界状态。演示期用内存即可；生产期换持久化实现，接口不变。"""

from __future__ import annotations

import copy
from typing import Any

from app.data import mock_data


class Store:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.plots: dict[str, dict[str, Any]] = {
            p["plot_id"]: copy.deepcopy(p) for p in mock_data.PLOTS_SEED
        }
        self.drones: dict[str, dict[str, Any]] = {
            d["drone_id"]: copy.deepcopy(d) for d in mock_data.DRONES_SEED
        }
        # route_id -> {versions: [route_rev, ...], ...}；route_rev 含 waypoints/统计/决策依据
        self.routes: dict[str, dict[str, Any]] = {}
        # 调度单
        self.dispatch_orders: dict[str, dict[str, Any]] = {}
        # 待人工确认的高危动作：action_id -> {action, params, summary, token, status, expires_at}
        self.pending_actions: dict[str, dict[str, Any]] = {}
        # 编辑器免登录 token：token -> {route_id, expires_at}
        self.editor_tokens: dict[str, dict[str, Any]] = {}
        # 飞行任务
        self.flight_tasks: dict[str, dict[str, Any]] = {}
        # 批量排期计划：plan_id -> {constraints, days:[{day, sorties:[...]}], status, ...}
        self.task_plans: dict[str, dict[str, Any]] = {}
        # 自增序号
        self.seq: dict[str, int] = {}

    def next_id(self, prefix: str, width: int = 4) -> str:
        n = self.seq.get(prefix, 0) + 1
        self.seq[prefix] = n
        return f"{prefix}-{n:0{width}d}"


STORE = Store()
