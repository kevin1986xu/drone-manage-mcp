"""业务数据源层：真实 drone-manage API / mock 双模式。

- 未配置 DRONE_API_BASE：get_real() 返回 None，core 全走内存 mock（纯演示模式）。
- 配置后：core 各能力优先走真实接口。降级规则（防止同一会话 mock/真实两个世界混用）：
  - 从未连通过平台：回落 mock，但结果必须带 data_source=mock 标注（L1 降级仅此一处）；
  - 连通过平台后再失败：沿用最近一次真实快照（data_source=real_cached），绝不切回 mock。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app import config


@lru_cache(maxsize=1)
def get_real():
    if not config.DRONE_API_BASE:
        return None
    from app.datasource.real import DroneManageClient

    return DroneManageClient(config.DRONE_API_BASE)


# 本进程是否成功连通过平台（图斑/设备任一 hydrate 成功即置位）
_real_ok_once = False


def note_real_success() -> None:
    global _real_ok_once
    _real_ok_once = True


def real_succeeded_before() -> bool:
    return _real_ok_once


SOURCE_RANK = {"mock": 0, "cached": 1, "real": 2}


def source_meta(*sources: str) -> dict[str, Any]:
    """hydrate 状态 → 工具返回的数据源标注（多个来源取最差的）。"""
    source = min(sources, key=lambda s: SOURCE_RANK.get(s, 0))
    if source == "real":
        return {"data_source": "real"}
    if source == "cached":
        return {
            "data_source": "real_cached",
            "notice": "平台瞬时不可达，以下为最近一次成功获取的真实数据（可能略有延迟）",
        }
    if get_real():
        return {
            "data_source": "mock",
            "notice": "⚠ 无人机平台暂时不可达，以下为内置演示数据（非真实图斑/设备）。"
            "必须向用户明确说明当前是演示数据，并建议稍后重试",
        }
    return {"data_source": "mock", "notice": "当前为脱网演示模式（内置演示数据）"}
