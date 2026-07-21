"""P1 三域安全机制单测（本地审批模式，不碰平台/网络）。

覆盖（docs/05 §4.2 紧急白名单防注入三件套 + §2.8 顺序依赖）：
1. 紧急动作前置条件：无活动飞行 → 拒绝（地面机注入 DoS 防护）
2. 紧急动作冷却窗：同机同动作 60s 内重复 → 拒绝
3. 紧急动作有活动飞行证据 → 免 token 执行 + emergency 审计标记
4. 高危飞控无 token → 只生成确认单
5. 调试域顺序闸：未进 debug_mode 直接动舱盖 → 拒绝
6. 调试域进入调试后动作放行至确认单阶段；退出后再拒
7. 临近排期的机场拒绝进调试
"""

from __future__ import annotations

import pytest

from uav_mcp import device_lock, dock_debug, flight_control
from uav_mcp.state import STATE


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr("uav_mcp.config.APPROVAL_BASE", "")
    STATE.reset()
    device_lock._locks.clear()
    flight_control._emergency_calls.clear()
    # 不碰平台：hydrate 无操作，设备直接灌 STATE
    monkeypatch.setattr("uav_mcp.drones.hydrate", lambda: None)
    STATE.drones["D-1"] = {"drone_id": "D-1", "device_sn": "SN001", "status": "idle",
                           "model": "M350", "battery_pct": 90, "payload": [],
                           "endurance_min": 40, "location": [113.0, 30.0]}
    yield
    STATE.reset()
    device_lock._locks.clear()
    flight_control._emergency_calls.clear()


class _FakeClient:
    def __init__(self):
        self.calls = []

    def dock_service_job(self, sn, service, param=None):
        self.calls.append(("job", sn, service))
        return {"ok": True}

    def emergency_stop(self, sn):
        self.calls.append(("estop", sn))
        return {"ok": True}

    def osd_latest(self, sn):
        return {"modeCode": 0}  # 待机

    def dock_debug(self, sn, path):
        self.calls.append(("debug", sn, path))
        return {"ok": True}

    def wayline_jobs_search(self, filters):
        return []


@pytest.fixture()
def fake_cli(monkeypatch):
    cli = _FakeClient()
    monkeypatch.setattr("uav_mcp.flight_control.get_client", lambda: cli)
    monkeypatch.setattr("uav_mcp.dock_debug.get_client", lambda: cli)
    return cli


# ── 紧急白名单 ─────────────────────────────────────────────

def test_emergency_rejected_when_not_flying(fake_cli):
    out = flight_control.return_home("D-1")
    assert out["status"] == "rejected" and "无活动飞行" in out["reason"]
    assert not fake_cli.calls  # 未下发


def test_emergency_executes_with_active_flight(fake_cli):
    STATE.drones["D-1"]["status"] = "flying"
    out = flight_control.return_home("D-1")
    assert out["status"] == "executed" and out["emergency"] is True
    assert ("job", "SN001", "return_home") in fake_cli.calls
    assert "notify" in out


def test_emergency_cooldown_blocks_repeat(fake_cli):
    STATE.drones["D-1"]["status"] = "flying"
    assert flight_control.emergency_stop("D-1")["status"] == "executed"
    out = flight_control.emergency_stop("D-1")
    assert out["status"] == "rejected" and "冷却" in out["reason"]
    assert len([c for c in fake_cli.calls if c[0] == "estop"]) == 1


def test_emergency_osd_evidence(fake_cli, monkeypatch):
    monkeypatch.setattr(fake_cli, "osd_latest", lambda sn: {"modeCode": 5})  # 航线飞行中
    out = flight_control.return_home("D-1")
    assert out["status"] == "executed" and "modeCode=5" in out["evidence"]


# ── 高危飞控确认流 ──────────────────────────────────────────

def test_takeoff_to_point_requires_confirmation(fake_cli):
    out = flight_control.takeoff_to_point("D-1", 113.5, 30.5, 100)
    assert out["status"] == "requires_confirmation" and out["action"] == "takeoff_to_point"


def test_speaker_tts_locks_text_in_summary(fake_cli):
    out = flight_control.speaker_tts("D-1", "请立即离开施工区域")
    assert out["status"] == "requires_confirmation"
    assert ["播放原文", "请立即离开施工区域"] in out["summary"]["rows"]


# ── 调试域顺序闸 ────────────────────────────────────────────

def test_dock_action_rejected_without_debug_mode(fake_cli):
    out = dock_debug.dock_cover("D-1", "open")
    assert out["status"] == "rejected" and "调试模式" in out["reason"]


def test_dock_action_allowed_after_debug_held(fake_cli):
    device_lock.acquire("SN001", "debug", "debug_mode")
    out = dock_debug.dock_cover("D-1", "open")
    assert out["status"] == "requires_confirmation"  # 进入确认单阶段
    device_lock.release("SN001", "debug")
    out2 = dock_debug.charge_control("D-1", True)
    assert out2["status"] == "rejected"


def test_debug_mode_rejected_with_upcoming_jobs(fake_cli, monkeypatch):
    monkeypatch.setattr(fake_cli, "wayline_jobs_search",
                        lambda f: [{"jobId": 9, "jobName": "汉川巡查0800"}])
    out = dock_debug.debug_mode("D-1", True)
    assert out["status"] == "rejected" and "排期任务" in out["reason"]
