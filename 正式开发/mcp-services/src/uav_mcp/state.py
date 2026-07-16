"""进程内世界状态。

与演示版的关键差异：**没有 mock 种子**——plots/drones 只能来自
drone-manage 平台灌注；平台不可达时相关工具返回明确错误。
四个业务域在同一进程内共享本状态（航线/确认单/任务跨域可见）。
"""

from __future__ import annotations

from typing import Any


class State:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.plots: dict[str, dict[str, Any]] = {}
        self.drones: dict[str, dict[str, Any]] = {}
        # route_id -> {route_id, drone_id, versions: [rev, ...]}；rev 含航点/统计/决策依据
        self.routes: dict[str, dict[str, Any]] = {}
        self.dispatch_orders: dict[str, dict[str, Any]] = {}
        self.flight_tasks: dict[str, dict[str, Any]] = {}
        self.task_plans: dict[str, dict[str, Any]] = {}
        # 本地审批兜底模式的待确认单（配置 APPROVAL_BASE 后不使用）
        self.pending_actions: dict[str, dict[str, Any]] = {}
        # 航线编辑器免登录 token：token -> {route_id, expires_at}
        self.editor_tokens: dict[str, dict[str, Any]] = {}
        self.seq: dict[str, int] = {}

    def next_id(self, prefix: str, width: int = 4) -> str:
        n = self.seq.get(prefix, 0) + 1
        self.seq[prefix] = n
        return f"{prefix}-{n:0{width}d}"


STATE = State()
