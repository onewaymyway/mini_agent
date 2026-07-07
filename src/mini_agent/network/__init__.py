"""
mini_agent.network — 通用网络能力模块。

目前只有连通性检测一个子模块，未来如果有更多"和网络环境相关但不绑定具体
业务场景"的能力（比如代理可用性探测、延迟测量），可以继续加到这个包下。
"""
from .connectivity import (
    DEFAULT_PROBE_TARGETS,
    is_online,
    wait_until_online,
    is_connectivity_exception,
)

__all__ = [
    "DEFAULT_PROBE_TARGETS",
    "is_online",
    "wait_until_online",
    "is_connectivity_exception",
]
