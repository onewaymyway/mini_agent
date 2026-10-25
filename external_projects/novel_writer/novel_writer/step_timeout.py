"""novel_writer/step_timeout.py — 单步执行时限（默认 40 分钟，可配置）。

`run_step()` 在执行一个步骤前登记该步骤的截止时刻；步骤内部每一次调用 skill 执行器
（包括 python 步骤里嵌套的子 skill 调用，如修改循环、乱码守卫重生成）都共用同一个截止时刻，
所以「一个步骤最多 N 分钟」是整个步骤的总时限，不是单次调用的时限。

超时后由 `FrameworkInvoker` 把正在运行的子进程（含其下所有工具子进程）整树终止，
步骤记为失败（带 timed_out 标记），再由 worker 现有的自动重跑机制重新跑这个步骤。

配置（`_studio/settings.json`）：
    "step_timeout": {"minutes": 40, "overrides": {"<步骤id或类别>": <分钟，0 = 该步骤不限时>}}
    minutes 为 null 或 0 表示全局不限时；overrides 中步骤 id 优先于类别。
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

DEFAULT_STEP_TIMEOUT_MINUTES = 40

_local = threading.local()


def limit_minutes(settings: Dict[str, Any], step: Any) -> Optional[float]:
    """某步骤的时限（分钟）；None 表示不限时。"""
    cfg = (settings or {}).get("step_timeout") or {}
    overrides = cfg.get("overrides") or {}
    if step.id in overrides:
        value = overrides[step.id]
    elif getattr(step, "category", None) in overrides:
        value = overrides[step.category]
    elif "minutes" in cfg:
        value = cfg["minutes"]
    else:
        value = DEFAULT_STEP_TIMEOUT_MINUTES
    if not value:
        return None
    return float(value)


@contextmanager
def deadline_scope(limit_sec: Optional[float]) -> Iterator[None]:
    """登记当前线程正在执行的步骤的截止时刻；limit_sec 为 None 时不设时限。可嵌套（取最早的截止时刻）。"""
    previous = getattr(_local, "deadline", None)
    new = None if limit_sec is None else time.monotonic() + float(limit_sec)
    if previous is not None and (new is None or previous < new):
        new = previous
    _local.deadline = new
    try:
        yield
    finally:
        _local.deadline = previous


def remaining_sec() -> Optional[float]:
    """距当前步骤截止还剩多少秒（可能为负）；没有登记时限时返回 None。"""
    deadline = getattr(_local, "deadline", None)
    if deadline is None:
        return None
    return deadline - time.monotonic()


def describe(limit_min: float) -> str:
    return f"{limit_min:g} 分钟"
