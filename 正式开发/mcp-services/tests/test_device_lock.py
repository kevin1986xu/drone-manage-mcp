"""设备级操作锁单测（docs/05 §4.5）：互斥/TTL/幂等续期/类别隔离。"""

from __future__ import annotations

import time

from uav_mcp import device_lock


def setup_function(_):
    device_lock._locks.clear()


def test_mutual_exclusion_same_category():
    ok, _ = device_lock.acquire("DOCK-1", "debug", "debug_mode")
    assert ok
    ok2, holder = device_lock.acquire("DOCK-1", "debug", "dock_cover")
    assert not ok2 and holder == "debug_mode"


def test_category_isolation():
    assert device_lock.acquire("DOCK-1", "debug", "debug_mode")[0]
    assert device_lock.acquire("DOCK-1", "flight", "return_home")[0]


def test_same_action_reacquire_is_renewal():
    assert device_lock.acquire("D-1", "flight", "fly_to_point")[0]
    assert device_lock.acquire("D-1", "flight", "fly_to_point")[0]


def test_ttl_expiry_releases_lock():
    device_lock.acquire("D-1", "debug", "debug_mode", ttl_s=1)
    device_lock._locks[("D-1", "debug")]["expires_at"] = time.time() - 1
    ok, _ = device_lock.acquire("D-1", "debug", "dock_cover")
    assert ok


def test_release_and_holder():
    device_lock.acquire("D-1", "debug", "debug_mode")
    assert device_lock.holder("D-1", "debug") == "debug_mode"
    device_lock.release("D-1", "debug")
    assert device_lock.holder("D-1", "debug") is None
