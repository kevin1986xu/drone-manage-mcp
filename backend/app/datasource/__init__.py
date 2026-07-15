"""业务数据源层：真实 drone-manage API / mock 双模式。

- 未配置 DRONE_API_BASE：get_real() 返回 None，core 全走内存 mock。
- 配置后：core 各能力优先走真实接口；单次调用异常由 core 捕获并回落
  mock（对应《开发计划》L1 降级：单 tool 内部兜底，Agent 链路不变）。
"""

from __future__ import annotations

from functools import lru_cache

from app import config


@lru_cache(maxsize=1)
def get_real():
    if not config.DRONE_API_BASE:
        return None
    from app.datasource.real import DroneManageClient

    return DroneManageClient(config.DRONE_API_BASE)
