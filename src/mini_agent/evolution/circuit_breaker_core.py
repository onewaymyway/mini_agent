"""
evolution/circuit_breaker_core.py — 通用看护/熔断内核
[daemon_stability_and_ux_improvement_plan.md 第 1 项 / P2-1]

背景：
  `workflow/watchdog.py` 已经有一套相对成熟的"连续同类失败提前熔断"
  （report_attempt_failure，同一 step 连续 N 次同 error_type 失败即提前
  判 NEEDS_FIX，不必等重试预算耗尽）与"跨 step 系统性故障熔断"
  （report_workflow_level_failure，同一 error_type 在多个不同 step 上
  出现即触发）。但这套能力此前只服务于 Workflow 执行路径——
  ObjectiveExecutor 侧完全没有"跨多个不同 Objective 因同一 error_type
  失败"这种广度信号；CronJobExecutor 侧的 StuckDetector/reap_stale_jobs
  也是另一套独立实现。三条链路各自维护相似但不完全一致的阈值和状态
  字典，同一类 bug 需要在三处分别修一次。

本模块把"连续同类失败提前熔断"与"跨 scope 广度性熔断"两段判定逻辑
抽成与执行路径无关的通用组件 `CircuitBreakerCore`，`WorkflowWatchdog`、
`ObjectiveExecutor`、`CronJobRunner`/`CronJobExecutor` 都可以各自持有
一个实例，接入同一套判定规则，而不是三处分别实现：

  - `scope_id`：一次"连续失败"追踪的粒度——workflow 里是 step_id，
    Objective 里是 execution_id，cron 里是 job_id。
  - "广度"计数的粒度是"距离 scope_id 集合"——同一个 error_type 在多少
    个**不同** scope_id 上出现过，达到阈值即触发熔断（跨 step / 跨
    Objective / 跨 cron job，语义完全一致，只是 scope_id 的含义不同）。

不负责：
  - 触发熔断后具体要做什么（取消/暂停/仅记录不阻断）——通过构造函数传入
    的 `on_trip` 回调决定，本模块只负责判定与状态维护，保持调用方可以
    按自己的语义决定熔断后果（workflow 是主动 cancel；Objective/cron 现
    阶段先只做"记录 + 主动告警"，不阻断新任务提交，见各自接入点的说明）。
  - 事件落盘——调用方通过 `log_fn` 回调自行决定写到哪个 jsonl。
"""

from __future__ import annotations

import threading
from typing import Callable, Optional


# 粗粒度错误分类：Objective/cron 侧的失败原因是纯文本（不像 workflow 那样
# 能拿到异常对象类型），这里按关键词做一个足够用于"广度信号"判断的粗分类
# ——不追求精确，只追求"同一类问题能落到同一个 bucket 里"，误差可以接受，
# 因为熔断判断本身就是模糊的系统性信号，不是精确诊断。
_ERROR_TYPE_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("timeout", ("超时", "timeout", "timed out", "deadline")),
    ("rate_limit", ("rate limit", "429", "限流", "too many requests")),
    ("auth", ("401", "403", "unauthorized", "forbidden", "认证", "权限")),
    ("connection", ("connection", "连接", "网络", "network", "dns")),
    ("tool_protocol", ("tool_use", "工具调用", "协议残留", "健全性校验")),
    ("stuck", ("原地打转", "give_up", "give up", "stuck", "无实质进展")),
]


def classify_error_type(message: str) -> str:
    """把一条自由文本的失败原因粗分类为一个稳定的 error_type 标签，供
    `CircuitBreakerCore` 的广度计数使用。未命中任何已知关键词时归为
    "other"（仍然是一个有效 bucket，只是不细分）。"""
    text = (message or "").lower()
    for label, keywords in _ERROR_TYPE_KEYWORDS:
        if any(kw.lower() in text for kw in keywords):
            return label
    return "other"


class CircuitBreakerCore:
    """通用连续失败追踪 + 跨 scope 广度熔断内核，线程安全。"""

    def __init__(
        self,
        distinct_scope_threshold: Optional[int] = None,
        on_trip: Optional[Callable[[str, list[str]], None]] = None,
        log_fn: Optional[Callable[[str, dict], None]] = None,
    ) -> None:
        """
        distinct_scope_threshold: 同一 error_type 累计出现在多少个不同
            scope_id 上即触发广度熔断；None/0 表示不启用广度熔断（仍可
            使用连续失败追踪）。
        on_trip(error_type, distinct_scope_ids): 广度熔断触发时的回调，
            由调用方决定后续动作（cancel/notify/仅记录）。异常会被吞掉，
            不影响熔断状态本身已经翻转的事实。
        log_fn(event_type, data): 可选的事件落盘回调。
        """
        self._distinct_scope_threshold = distinct_scope_threshold
        self._on_trip = on_trip
        self._log_fn = log_fn

        self._lock = threading.Lock()
        # scope_id -> (last_error_type, consecutive_count)
        self._consecutive: dict[str, tuple[str, int]] = {}
        # error_type -> 曾经因该 error_type 失败过至少一次的 scope_id 集合
        # （全程累计，不做滑动窗口，理由同 workflow/watchdog.py 的原始实现：
        # 熔断关心"这类问题出现的广度"，不是"当前还有多少 scope 处于失败态"）。
        self._distinct_scopes: dict[str, set[str]] = {}
        self._tripped = False
        self._trip_reason: Optional[str] = None

    # ── 连续同类失败（单 scope 内）─────────────────────────────────────

    def report_attempt_failure(self, scope_id: str, error_type: str, threshold: int = 2) -> bool:
        """记录一次 scope_id 的失败尝试。与上一次记录的 error_type 相同则
        连续计数 +1，否则重置为 1。返回 True 表示已达 threshold，调用方
        应据此提前判定失败、不再消耗剩余重试预算。"""
        with self._lock:
            prev_type, prev_count = self._consecutive.get(scope_id, (None, 0))
            count = prev_count + 1 if prev_type == error_type else 1
            self._consecutive[scope_id] = (error_type, count)
            escalate = count >= max(1, threshold)
        if escalate:
            self._log("consecutive_failure_escalated", {
                "scope_id": scope_id, "error_type": error_type,
                "count": count, "threshold": threshold,
            })
        return escalate

    def reset_scope_failures(self, scope_id: str) -> None:
        """scope 成功或最终结束后清空连续失败计数。"""
        with self._lock:
            self._consecutive.pop(scope_id, None)

    # ── 跨 scope 广度熔断 ────────────────────────────────────────────

    @property
    def tripped(self) -> bool:
        with self._lock:
            return self._tripped

    @property
    def trip_reason(self) -> Optional[str]:
        with self._lock:
            return self._trip_reason

    def report_breadth_failure(self, scope_id: str, error_type: str) -> bool:
        """记录一次"某个 scope 因 error_type 失败"，用于跨 scope 的系统性
        故障识别。累计因同一 error_type 失败过的**不同** scope_id 数达到
        `distinct_scope_threshold` 时触发熔断（只触发一次），返回 True 表示
        本次调用触发了熔断；未启用/未达阈值/已触发过，返回 False。"""
        if not self._distinct_scope_threshold:
            return False
        with self._lock:
            if self._tripped:
                return False
            ids = self._distinct_scopes.setdefault(error_type, set())
            ids.add(scope_id)
            distinct_count = len(ids)
            trip = distinct_count >= max(1, self._distinct_scope_threshold)
            if trip:
                self._tripped = True
                self._trip_reason = (
                    f"error_type={error_type!r} 已在 {distinct_count} 个不同任务"
                    f"（{sorted(ids)}）上失败，达到熔断阈值 "
                    f"{self._distinct_scope_threshold}，判定为系统性问题"
                )
                snapshot = sorted(ids)
        if trip:
            self._log("circuit_breaker_tripped", {
                "error_type": error_type,
                "distinct_scope_ids": snapshot,
                "threshold": self._distinct_scope_threshold,
            })
            if self._on_trip is not None:
                try:
                    self._on_trip(error_type, snapshot)
                except Exception:
                    pass
        return trip

    def reset_trip(self) -> None:
        """允许调用方在人工介入/环境恢复后重新启用熔断判定（不清空历史
        distinct_scopes 计数，避免"重置后立刻因为旧记录再次触发"——
        distinct_scopes 本身按 error_type 隔离，reset 后只是允许再次判定，
        真正再次达到阈值仍然需要新的失败继续累积，因为
        `_distinct_scopes` 在 reset 时一并清空）。"""
        with self._lock:
            self._tripped = False
            self._trip_reason = None
            self._distinct_scopes.clear()

    # ── 日志 ────────────────────────────────────────────────────────

    def _log(self, event_type: str, data: dict) -> None:
        if self._log_fn is None:
            return
        try:
            self._log_fn(event_type, data)
        except Exception:
            pass
