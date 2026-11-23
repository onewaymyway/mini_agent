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
    """写入停止请求（带时间戳）。只影响在此时间之后开始、仍在运行的任务。"""
    store.write_text_atomic(_stop_path(ctx), "stop\n")


def clear_stop(ctx) -> None:
    p = _stop_path(ctx)
    if p.is_file():
        p.unlink()


def stop_requested(ctx, since: Optional[float] = None) -> bool:
    """since 为任务开始的时间（epoch 秒）：只有在它之后写入的停止请求才生效。
    不传 since 时，只要存在停止请求即为真（看板与命令行用于显示状态）。"""
    p = _stop_path(ctx)
    if not p.is_file():
        return False
    if since is None:
        return True
    # 以文件修改时间为准，不解析内容：旧版本写入的内容格式不同，也不能被误判为停止
    try:
        return p.stat().st_mtime >= since
    except OSError:
        return False


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
            msg = (f"本库今日 LLM 调用已达上限：已用 {d['calls']} / 上限 {dl}。"
                   f"该上限在「设置 → 本库」的「每日最多 LLM 调用」中修改。")
            joblog.log(ctx.root, "budget", msg, level="error")
            raise BudgetStop(msg)
        if g["calls"] >= gl:
            msg = (f"全局今日 LLM 调用已达上限：所有文章库合计已用 {g['calls']} / 上限 {gl}。"
                   f"该上限只能在「设置 → 全局默认」的「全局每日最多 LLM 调用」中修改"
                   f"（保存位置：{paths.global_settings_path()}）。")
            joblog.log(ctx.root, "budget", msg, level="error")
            raise BudgetStop(msg)
        d["calls"] += 1
        g["calls"] += 1
        store.write_json_atomic(_domain_path(ctx), d)
        store.write_json_atomic(_global_path(), g)


# ------------------------------------------------------------------ 包装

class GuardedLLM:
    """每次模型调用前：停止信号 → 记账 → 单次调用超时。调用在辅助线程执行，超时或停止时立即返回。

    超时是“单次模型调用”的最长等待（不是整批任务的时长）；停止只影响 since 之后发出的请求。"""

    def __init__(self, inner: Any, ctx, timeout_sec: Optional[float] = None, since: Optional[float] = None):
        self.inner = inner
        self.ctx = ctx
        self.timeout_sec = timeout_sec
        self.since = time.time() if since is None else since
        self.name = getattr(inner, "name", "guarded")

    def ask(self, prompt: str, system: str = "") -> str:
        cancel.check()
        if stop_requested(self.ctx, self.since):
            raise cancel.Cancelled("用户请求停止")
        deadline = time.monotonic() + self.timeout_sec if self.timeout_sec else None
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
            if stop_requested(self.ctx, self.since):
                raise cancel.Cancelled("用户请求停止")
            if deadline is not None and time.monotonic() > deadline:
                raise StepTimeout(f"单次模型调用超过 {self.timeout_sec / 60:.1f} 分钟未返回")
        if "error" in box:
            raise box["error"]
        return box.get("value", "")


def guard(ctx, inner: Any, *, timeout_min: Optional[float] = None, since: Optional[float] = None) -> GuardedLLM:
    return GuardedLLM(inner, ctx, timeout_sec=float(timeout_min) * 60 if timeout_min else None, since=since)


def stop_pending_for(ctx, run_starts) -> bool:
    """是否有停止请求作用于正在运行的任务：run_starts 为各运行中任务的开始时间（epoch 秒）。"""
    return any(stop_requested(ctx, since=t) for t in run_starts)
