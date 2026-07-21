"""设备级操作锁（docs/05 §4.5：dispatch_drone 锁机模式的推广）。

背景：平台自动调度器与 Agent 是两个并发写方，且同一会话/多会话可能同时对
同一设备发写动作（重排期时任务恰好开始执行、两个会话同时对同一机场进
debug_mode、retry 被重复调用）。本模块提供**同设备同类写动作互斥**：

- 锁粒度 = (device, category)：category 是动作类别（flight/debug/schedule），
  同设备不同类别不互斥（排期改动不挡飞行干预）；
- 锁带 TTL 防死锁（持有方崩溃/忘释放后自动过期）；
- 进程内实现（八域单进程共享 STATE，与现有 lock_drone 同口径）；跨进程
  部署时需换分布式锁——接口不变，换 backend 即可。

用法（写动作核心函数开头）：
    ok, holder = device_lock.acquire(dock_id, "debug", "debug_mode", ttl_s=120)
    if not ok:
        return {"error": f"设备正被其他操作占用（{holder}），请稍后重试"}
    try:
        ... 调平台 ...
    finally:
        device_lock.release(dock_id, "debug")
持续型操作（进 debug 模式后一系列动作）可不立即释放，靠 TTL 或显式退出释放。
"""

from __future__ import annotations

import threading
import time

_locks: dict[tuple[str, str], dict] = {}
_mu = threading.Lock()


def acquire(device: str, category: str, action: str, ttl_s: int = 120) -> tuple[bool, str]:
    """尝试获取 (device, category) 锁。返回 (成功?, 当前持有动作描述)。

    幂等续期：同 action 重复 acquire 视为续期成功（重试类接口重复调用只生效一次
    的配套——上层还应有业务级去重，这里保证至少不自锁）。
    """
    key = (str(device), category)
    now = time.time()
    with _mu:
        cur = _locks.get(key)
        if cur and cur["expires_at"] > now and cur["action"] != action:
            return False, cur["action"]
        _locks[key] = {"action": action, "expires_at": now + ttl_s, "acquired_at": now}
        return True, action


def release(device: str, category: str) -> None:
    with _mu:
        _locks.pop((str(device), category), None)


def holder(device: str, category: str) -> str | None:
    """当前有效持有者的动作名；无锁或已过期返回 None。"""
    key = (str(device), category)
    now = time.time()
    with _mu:
        cur = _locks.get(key)
        if cur and cur["expires_at"] > now:
            return cur["action"]
        return None
