"""
evolution/tick_thread_guard.py — tick 线程内慢调用检测（tick 只派发、不执行 阶段 4）

背景（next_doc/tick_dispatch_only_execution_model_plan.md §6.4）：
  "tick() 只做决策+提交，不做耗时调用"这条约束此前只写在注释里，没有机制保证，
  新代码很容易再次违反。本模块把它变成可检测的运行时事实：

    - 调度 tick 的入口（SchedulerHeartbeat._maybe_tick / AgentRunner 兜底 tick）
      用 ``tick_thread_scope()`` 包住 ``AutonomousLoop.tick()``，
      在当前线程上设置 thread-local 标记；
    - LLM provider 在发起请求前调用 ``note_llm_call_in_tick_thread()``：
      若当前线程正处于 tick 内，则记录带调用栈的 warning、累加计数；
      严格模式下直接抛 ``TickThreadBlockingError``。

线程语义：
  标记是 thread-local 的——被派发到 TickDispatcher worker 线程里执行的 LLM 调用
  不是 tick 线程，不会被误报；这正是"派发出去就算合规"的判定依据。

严格模式：
  默认只告警不抛（直接抛会让部分降级路径产生副作用，见方案 §6.4）。
  ``set_strict(True)`` 由配置 ``scheduler.tick_thread_llm_strict`` 或测试夹具打开。
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from contextlib import contextmanager
from typing import Iterator, Optional

log = logging.getLogger(__name__)


class TickThreadBlockingError(RuntimeError):
    """在 tick 线程内发起了会阻塞的 LLM 调用（严格模式）。"""


_local = threading.local()
_state_lock = threading.Lock()
_strict: bool = False
_total_calls: int = 0
_last_call_at: float = 0.0
_last_call_label: str = ""
_last_call_stack: str = ""
# 同一个调用点的 warning 节流：避免异常循环时刷屏。key = 调用栈尾部指纹。
_warned_fingerprints: dict[str, float] = {}
_WARN_REPEAT_SECONDS = 300.0
_STACK_LIMIT = 14


# ── 标记 ──────────────────────────────────────────────────────────────────────

def in_tick_thread() -> bool:
    """当前线程是否正处于 AutonomousLoop.tick() 之内。"""
    return int(getattr(_local, "depth", 0)) > 0


@contextmanager
def tick_thread_scope() -> Iterator[None]:
    """包住 tick() 调用。可重入（用深度计数），异常时也保证清除。"""
    _local.depth = int(getattr(_local, "depth", 0)) + 1
    try:
        yield
    finally:
        _local.depth = max(0, int(getattr(_local, "depth", 1)) - 1)


@contextmanager
def tick_thread_exempt() -> Iterator[None]:
    """临时豁免：极少数确实需要在 tick 线程里做的受控调用可以显式标注。

    使用即意味着\"我知道这会阻塞 tick\"，应当有注释说明理由；目前存量代码无需使用。
    """
    saved = int(getattr(_local, "depth", 0))
    _local.depth = 0
    try:
        yield
    finally:
        _local.depth = saved


# ── 严格模式 / 统计 ──────────────────────────────────────────────────────────

def set_strict(enabled: bool) -> None:
    global _strict
    with _state_lock:
        _strict = bool(enabled)


def is_strict() -> bool:
    with _state_lock:
        return _strict


def reset_stats() -> None:
    """测试用：清零计数与节流表。"""
    global _total_calls, _last_call_at, _last_call_label, _last_call_stack
    with _state_lock:
        _total_calls = 0
        _last_call_at = 0.0
        _last_call_label = ""
        _last_call_stack = ""
        _warned_fingerprints.clear()


def stats() -> dict:
    """供 heartbeat 状态文件 / execution_model_status 读取。"""
    with _state_lock:
        return {
            "tick_thread_llm_calls": _total_calls,
            "last_call_at": _last_call_at,
            "last_call_label": _last_call_label,
            "last_call_stack": _last_call_stack,
            "strict": _strict,
        }


def tick_thread_llm_calls() -> int:
    with _state_lock:
        return _total_calls


def note_llm_call_in_tick_thread(label: str = "") -> bool:
    """LLM provider 发起请求前调用。

    返回 True 表示本次调用发生在 tick 线程内（已计数/告警）；False 表示无事发生。
    严格模式下抛 ``TickThreadBlockingError``（计数与告警先于抛出完成）。
    本函数自身绝不因为统计失败而影响 LLM 调用——除严格模式的有意抛出外，一律吞掉异常。
    """
    if not in_tick_thread():
        return False

    global _total_calls, _last_call_at, _last_call_label, _last_call_stack
    strict = False
    try:
        frames = traceback.format_stack(limit=_STACK_LIMIT)[:-1]
        stack_text = "".join(frames)
        fingerprint = "".join(frames[-4:])
        now = time.time()
        should_warn = False
        with _state_lock:
            _total_calls += 1
            _last_call_at = now
            _last_call_label = label
            _last_call_stack = stack_text
            strict = _strict
            last = _warned_fingerprints.get(fingerprint, 0.0)
            if now - last >= _WARN_REPEAT_SECONDS:
                _warned_fingerprints[fingerprint] = now
                should_warn = True
                if len(_warned_fingerprints) > 200:
                    _warned_fingerprints.clear()
        if should_warn:
            log.warning(
                "tick 线程内发起了 LLM 调用（会阻塞调度心跳，违反\"tick 只派发、不执行\"）"
                " label=%s\n%s", label, stack_text,
            )
    except Exception:  # pragma: no cover - 统计失败不能影响主调用
        pass

    if strict:
        raise TickThreadBlockingError(
            f"tick 线程内禁止同步 LLM 调用（scheduler.tick_thread_llm_strict=true）: {label}"
        )
    return True


def configure_from_config(cfg: Optional[object]) -> None:
    """按 AppConfig.scheduler.tick_thread_llm_strict 设置严格模式。"""
    sched = getattr(cfg, "scheduler", None) if cfg is not None else None
    if sched is not None:
        set_strict(bool(getattr(sched, "tick_thread_llm_strict", False)))
