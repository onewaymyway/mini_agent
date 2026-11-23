"""budget.py — 预算与停止（方案 §9）：每日 LLM 调用上限（本库 + 全局总闸）、单步超时、停止信号。

* GuardedLLM 包住真正的模型调用：每次调用前检查停止信号、单步超时，并记入当日用量。
* 超限与超时使用 BaseException 子类（BudgetStop / StepTimeout），流水线里的 `except Exception`
  不会把它们当作单篇失败吞掉，必须整体停止。
* 0 表示上限为 0（不允许调用），而不是“不限制”。
* 停止信号是 logs/stop.flag（跨进程可见）；看板与命令行都可以写入；下一轮 cycle 开始时清除。
* 已知限制：每日计数只在本进程内加锁，多个进程同时运行时计数可能略有偏差（方案 §20.3 的串行策略可避免）。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any, Optional

from . import cancel, joblog, paths, settings, store

STOP_FILE = "logs/stop.flag"
_LOCK = threading.Lock()


class BudgetStop(BaseException):
    """今日调用预算已用尽（本库或全局）。"""


class StepTimeout(BaseException):
    """单步超时。"""


# ------------------------------------------------------------------ 停止信号

def _stop_path(ctx):
    return ctx.path("logs", "stop.flag")


def request_stop(ctx) -> None:
    store.write_text_atomic(_stop_path(ctx), datetime.now().isoformat(timespec="seconds"))
    cancel.request(ctx.root)


def clear_stop(ctx) -> None:
    p = _stop_path(ctx)
    if p.is_file():
        p.unlink()


def stop_requested(ctx) -> bool:
    return _stop_path(ctx).is_file() or cancel.is_requested(ctx.root)


# ------------------------------------------------------------------ 用量

def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _read(path) -> dict:
    try:
        d = store.read_json(path, default=None)
    except ValueError:
        d = None
    if not isinstance(d, dict) or d.get("date") != _today():
        return {"date": _today(), "calls": 0}
    return d


def _domain_path(ctx):
    return ctx.path("logs", "budget.json")


def _global_path():
    return paths.data_root() / "logs" / "budget_global.json"


def usage(ctx) -> dict:
    eff, _ = settings.effective_settings(ctx)
    d = _read(_domain_path(ctx))
    g = _read(_global_path())
    return {"domain": {"calls": d["calls"], "limit": eff["budget"]["max_llm_calls_per_day"]},
            "global": {"calls": g["calls"], "limit": eff["budget"]["global_max_llm_calls_per_day"]}}


def charge(ctx) -> None:
    """记入一次调用；超限则抛 BudgetStop（不计数）。"""
    eff, _ = settings.effective_settings(ctx)
    dl = int(eff["budget"]["max_llm_calls_per_day"])
    gl = int(eff["budget"]["global_max_llm_calls_per_day"])
    with _LOCK:
        d = _read(_domain_path(ctx))
        g = _read(_global_path())
        if d["calls"] >= dl:
            joblog.log(ctx.root, "budget", f"本库今日 LLM 调用已达上限 {dl}，已停止", level="error")
            raise BudgetStop(f"本库今日 LLM 调用已达上限 {dl}")
        if g["calls"] >= gl:
            joblog.log(ctx.root, "budget", f"全局今日 LLM 调用已达上限 {gl}，已停止", level="error")
            raise BudgetStop(f"全局今日 LLM 调用已达上限 {gl}")
        d["calls"] += 1
        g["calls"] += 1
        store.write_json_atomic(_domain_path(ctx), d)
        store.write_json_atomic(_global_path(), g)


# ------------------------------------------------------------------ 包装

class GuardedLLM:
    """调用前：停止信号 → 单步超时 → 记账。调用在辅助线程执行，超时或停止时立即返回。"""

    def __init__(self, inner: Any, ctx, timeout_sec: Optional[float] = None):
        self.inner = inner
        self.ctx = ctx
        self.deadline = time.monotonic() + timeout_sec if timeout_sec else None
        self.name = getattr(inner, "name", "guarded")

    def ask(self, prompt: str, system: str = "") -> str:
        cancel.check()
        if stop_requested(self.ctx):
            raise cancel.Cancelled("用户请求停止")
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise StepTimeout("单步超时")
        charge(self.ctx)
        box: dict = {}

        def run():
            try:
                box["value"] = self.inner.ask(prompt, system=system)
            except BaseException as exc:  # noqa: BLE001 — 原样转交调用线程
                box["error"] = exc

        t = threading.Thread(target=run, daemon=True)
        t.start()
        while t.is_alive():
            t.join(0.2)
            if stop_requested(self.ctx):
                raise cancel.Cancelled("用户请求停止")
            if self.deadline is not None and time.monotonic() > self.deadline:
                raise StepTimeout("单步超时（模型调用未在时限内返回）")
        if "error" in box:
            raise box["error"]
        return box.get("value", "")


def guard(ctx, inner: Any, *, timeout_min: Optional[float] = None) -> GuardedLLM:
    return GuardedLLM(inner, ctx, timeout_sec=float(timeout_min) * 60 if timeout_min else None)
