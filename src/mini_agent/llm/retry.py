"""
llm/retry.py — LLM 调用重试条件框架

提供可扩展的重试条件机制。每个条件是一个独立的检查器，
只要任意一个条件判定"需要重试"，就触发重试。

内置条件：
  EmptyOutputCondition   — 模型没有任何文本输出且没有工具调用
  EmptyTextCondition     — 模型文本为空（即使有工具调用也重试）

退避策略（BackoffStrategy）：
  FixedBackoff(delay)            — 每次等待固定秒数（默认）
  LinearBackoff(initial, step)   — 每次线性递增：initial, initial+step, initial+2*step, …
  ExponentialBackoff(initial, multiplier, max_delay)
                                 — 每次指数递增：initial, initial*m, initial*m², …，上限 max_delay

扩展新条件：
  继承 RetryCondition，实现 should_retry(response) 方法即可，
  然后加入 RetryPolicy.conditions 列表。

示例：
  policy = RetryPolicy(
      max_retries=3,
      backoff=ExponentialBackoff(initial=5.0, multiplier=2.0, max_delay=120.0),
      conditions=[EmptyOutputCondition()],
  )
  response = policy.call_with_retry(call_fn, on_retry=on_retry_fn)
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Optional

from .base import LLMResponse, LLMConfigError, LLMContextWindowError

logger = logging.getLogger(__name__)


# ── 退避策略抽象基类 ──────────────────────────────────────────────────────────

class BackoffStrategy(ABC):
    """
    单次等待时长计算策略。

    每次触发重试时，call_with_retry 调用 delay_for(attempt)
    获取本次应等待的秒数（attempt 从 1 开始，表示"第几次重试"）。
    """

    @abstractmethod
    def delay_for(self, attempt: int) -> float:
        """
        返回第 attempt 次重试前应等待的秒数。

        Args:
            attempt: 当前重试次数，从 1 开始
        """
        ...

    @property
    def description(self) -> str:
        """人可读的策略描述，用于日志和启动信息。"""
        return self.__class__.__name__


# ── 内置退避策略 ──────────────────────────────────────────────────────────────

@dataclass
class FixedBackoff(BackoffStrategy):
    """
    固定等待时长策略：每次重试等待相同的秒数。

    适用场景：简单场景，或等待时间由外部因素决定（如 Retry-After 头）。

    示例（每次等待 10s）：
        FixedBackoff(delay=10.0)
        → 第1次: 10s, 第2次: 10s, 第3次: 10s
    """
    delay: float = 10.0

    def delay_for(self, attempt: int) -> float:
        return max(0.0, self.delay)

    @property
    def description(self) -> str:
        return f"fixed({self.delay}s)"


@dataclass
class LinearBackoff(BackoffStrategy):
    """
    线性递增等待策略：每次重试在上次基础上增加固定步长。

    计算公式：delay = initial + (attempt - 1) * step

    Args:
        initial:   第一次重试的等待时间（秒）
        step:      每次递增的秒数
        max_delay: 等待时间上限（0 = 不限制），防止等待时间无限增长

    示例（initial=10, step=60）：
        → 第1次: 10s, 第2次: 70s, 第3次: 130s, 第4次: 190s
    """
    initial: float = 10.0
    step: float = 60.0
    max_delay: float = 0.0   # 0 = 不限制

    def delay_for(self, attempt: int) -> float:
        d = self.initial + (attempt - 1) * self.step
        if self.max_delay > 0:
            d = min(d, self.max_delay)
        return max(0.0, d)

    @property
    def description(self) -> str:
        cap = f", max={self.max_delay}s" if self.max_delay > 0 else ""
        return f"linear(init={self.initial}s, step=+{self.step}s{cap})"


@dataclass
class ExponentialBackoff(BackoffStrategy):
    """
    指数递增等待策略：每次重试等待时间乘以固定倍数。

    计算公式：delay = min(initial * multiplier^(attempt-1), max_delay)

    Args:
        initial:    第一次重试的等待时间（秒）
        multiplier: 每次重试的倍数因子（必须 > 1.0）
        max_delay:  等待时间上限（秒，0 = 不限制）

    示例（initial=5, multiplier=2.0, max_delay=300）：
        → 第1次: 5s, 第2次: 10s, 第3次: 20s, 第4次: 40s, … 上限 300s
    示例（initial=10, multiplier=1.5）：
        → 第1次: 10s, 第2次: 15s, 第3次: 22.5s, 第4次: 33.75s
    """
    initial: float = 5.0
    multiplier: float = 2.0
    max_delay: float = 300.0   # 默认上限 5 分钟

    def __post_init__(self) -> None:
        if self.multiplier <= 1.0:
            raise ValueError(f"ExponentialBackoff.multiplier must be > 1.0, got {self.multiplier}")
        if self.initial <= 0:
            raise ValueError(f"ExponentialBackoff.initial must be > 0, got {self.initial}")

    def delay_for(self, attempt: int) -> float:
        d = self.initial * (self.multiplier ** (attempt - 1))
        if self.max_delay > 0:
            d = min(d, self.max_delay)
        return max(0.0, d)

    @property
    def description(self) -> str:
        cap = f", max={self.max_delay}s" if self.max_delay > 0 else ""
        return f"exponential(init={self.initial}s, x{self.multiplier}{cap})"


# ── 重试条件抽象基类 ──────────────────────────────────────────────────────────

class RetryCondition(ABC):
    """
    单个重试条件的抽象基类。

    每个条件只关注一个判断维度，保持职责单一。
    条件之间是 OR 关系：任意一个触发即重试。
    """

    @abstractmethod
    def should_retry(self, response: LLMResponse) -> bool:
        """
        判断是否需要重试。

        Args:
            response: LLM 返回的响应对象

        Returns:
            True  — 触发重试
            False — 本条件不触发重试
        """
        ...

    @property
    def name(self) -> str:
        """条件的可读名称，用于日志输出。"""
        return self.__class__.__name__

    @property
    def reason(self) -> str:
        """重试原因描述，打印到日志中。"""
        return f"[{self.name}] triggered retry"


# ── 内置重试条件 ──────────────────────────────────────────────────────────────

class EmptyOutputCondition(RetryCondition):
    """
    【内置】空输出条件：模型既没有文本输出，也没有工具调用。

    这是最常见的"无效响应"场景：模型返回了一个空响应，
    什么内容都没有，视为调用失败，应该重试。
    """

    def should_retry(self, response: LLMResponse) -> bool:
        has_text = bool(response.text and response.text.strip())
        has_tool_calls = bool(response.tool_calls)
        return not has_text and not has_tool_calls

    @property
    def reason(self) -> str:
        return "[EmptyOutputCondition] 模型无文本输出且无工具调用，视为空响应，触发重试"


class EmptyTextCondition(RetryCondition):
    """
    【内置】空文本条件：模型文本为空（即使有工具调用也重试）。

    比 EmptyOutputCondition 更严格：只要 text 字段为空就重试，
    适用于要求模型必须附带解释文本的场景。
    """

    def should_retry(self, response: LLMResponse) -> bool:
        return not bool(response.text and response.text.strip())

    @property
    def reason(self) -> str:
        return "[EmptyTextCondition] 模型文本输出为空，触发重试"


class StopReasonCondition(RetryCondition):
    """
    【内置】特定停止原因条件：当 stop_reason 命中指定值时重试。

    示例：重试 stop_reason == "max_tokens" 的截断响应。
        condition = StopReasonCondition(stop_reasons={"max_tokens"})
    """

    def __init__(self, stop_reasons: set[str]) -> None:
        self.stop_reasons = stop_reasons

    def should_retry(self, response: LLMResponse) -> bool:
        return response.stop_reason in self.stop_reasons

    @property
    def reason(self) -> str:
        return f"[StopReasonCondition] stop_reason={self.stop_reasons!r}，触发重试"


# ── 重试策略 ──────────────────────────────────────────────────────────────────

@dataclass
class RetryPolicy:
    """
    重试策略：持有一组重试条件和退避策略，执行带重试的 LLM 调用。

    Attributes:
        max_retries:  最多重试次数（不含首次调用），0 = 不重试
        conditions:   重试条件列表，任意一个触发即重试（OR 关系）
        backoff:      退避策略，控制每次重试前等待多久；
                      默认 FixedBackoff(10s)，即每次等待 10 秒。
                      传入 LinearBackoff / ExponentialBackoff 可实现递增等待。
        retry_delay:  【兼容旧接口】若传入且 backoff 未显式指定，
                      自动构造 FixedBackoff(retry_delay)。
                      新代码请直接使用 backoff= 参数。
        retry_on_exception:  call_fn() 抛出异常时是否重试（见下）
        non_retryable_exceptions: 即使 retry_on_exception=True，
                       命中这些异常类型时也直接抛出，不计入重试。
                       默认包含 LLMConfigError —— 配置错误（如缺少 api_key）
                       是确定性的，重试不会让它变成功。
        network_aware:  是否启用断网感知（默认 True）。异常触发重试前，
                       先用 mini_agent.network.connectivity 判断这个异常是否
                       "看起来像"网络层失败；如果是，且此刻确实探测不到网络，
                       就不按 backoff 策略盲目重试（断网时重试大概率还是失败，
                       纯粹浪费重试预算），而是阻塞等待网络恢复，恢复后立即
                       重新调用——这次等待不计入 max_retries 预算。
        network_check_interval: 断网等待期间的轮询间隔（秒），默认 5s。
        network_max_wait: 断网等待的最长时长（秒），默认 0 = 不限时长一直等
                       到网络恢复为止。设置为正数后，等待超时仍未恢复网络时
                       会退回正常的异常重试流程（消耗一次重试预算）。
    """

    max_retries: int = 2
    conditions: list[RetryCondition] = field(
        default_factory=lambda: [EmptyOutputCondition()]
    )
    backoff: BackoffStrategy = field(default_factory=lambda: FixedBackoff(10.0))
    # 向后兼容：旧代码传 retry_delay=N 等价于 backoff=FixedBackoff(N)
    retry_delay: float = field(default=0.0, repr=False)
    retry_on_exception: bool = False
    non_retryable_exceptions: tuple = field(
        default_factory=lambda: (LLMConfigError, LLMContextWindowError)
    )
    network_aware: bool = True
    network_check_interval: float = 5.0
    network_max_wait: float = 0.0

    def __post_init__(self) -> None:
        # 兼容旧接口：若外部只传了 retry_delay 而未显式设置 backoff，
        # 则用 retry_delay 构造 FixedBackoff 覆盖默认值
        if self.retry_delay > 0 and isinstance(self.backoff, FixedBackoff) and self.backoff.delay == 10.0:
            self.backoff = FixedBackoff(self.retry_delay)

    def call_with_retry(
        self,
        call_fn: Callable[[], LLMResponse],
        on_retry: Optional[Callable[[int, str], None]] = None,
    ) -> LLMResponse:
        """
        执行 call_fn，失败时按策略重试。

        Args:
            call_fn:   无参数的调用函数，每次调用返回 LLMResponse
            on_retry:  重试回调，签名为 on_retry(attempt: int, reason: str)
                       可用于打印警告、更新 UI 等，不传则静默重试

        Returns:
            最终的 LLMResponse（首次成功或最后一次重试的结果）

        Note:
            - "响应质量"重试（conditions）和"异常"重试（retry_on_exception）
              共用同一个 max_retries 预算。
            - 若达到 max_retries 上限仍触发质量条件，返回最后一次的响应而非抛出异常。
            - retry_on_exception=False 时，call_fn 抛出异常会直接向上传播。
            - non_retryable_exceptions 命中时，无论 retry_on_exception 取值，
              都立即向上抛出。
        """
        # 获取全局倒计时状态（可能为 None，如在测试中）
        try:
            from mini_agent.orchestrator.concurrency import get_retry_countdown_state
            _countdown = get_retry_countdown_state()
        except Exception:
            _countdown = None

        attempt = 0
        while True:
            try:
                response = call_fn()
            except self.non_retryable_exceptions:
                raise
            except Exception as e:
                if not self.retry_on_exception:
                    raise

                if self.network_aware and self._handle_if_offline(e, on_retry, attempt):
                    # 断网期间的等待不计入 attempt/max_retries 预算：
                    # 网络恢复后直接用同一次 attempt 计数重新调用 call_fn。
                    continue

                if attempt >= self.max_retries:
                    raise
                attempt += 1
                reason = f"[Exception] {type(e).__name__}: {e}"
                delay = self.backoff.delay_for(attempt)
                logger.warning(
                    "LLM retry %d/%d (wait %.1fs, %s) — %s",
                    attempt, self.max_retries, delay, self.backoff.description, reason,
                )
                if on_retry:
                    on_retry(attempt, reason)
                self._do_wait(delay, attempt, _countdown)
                continue

            triggered = self._check_conditions(response)
            if triggered is None or attempt >= self.max_retries:
                return response

            attempt += 1
            reason = triggered.reason
            delay = self.backoff.delay_for(attempt)
            logger.warning(
                "LLM retry %d/%d (wait %.1fs, %s) — %s",
                attempt, self.max_retries, delay, self.backoff.description, reason,
            )
            if on_retry:
                on_retry(attempt, reason)
            self._do_wait(delay, attempt, _countdown)

    def _handle_if_offline(
        self,
        exc: Exception,
        on_retry: Optional[Callable[[int, str], None]],
        attempt: int,
    ) -> bool:
        """
        检查这次异常是否"看起来像"网络层失败，并且此刻确实探测不到网络；
        如果是，阻塞等待网络恢复后返回 True（调用方应该直接重试同一次调用，
        不增加 attempt 计数——断网期间的等待不消耗重试预算）。

        不是网络问题，或网络等待超时仍未恢复（network_max_wait > 0 时），
        返回 False，交回调用方按正常异常重试逻辑处理（消耗一次重试预算）。
        """
        from mini_agent.network.connectivity import is_connectivity_exception, is_online, wait_until_online

        if not is_connectivity_exception(exc):
            return False
        if is_online(use_cache=False):
            # 异常文案/类型像网络错误，但探测下来其实联网正常——大概率是
            # 服务端偶发的连接重置之类，不是真的断网，交回正常重试逻辑，
            # 避免把这类情况也当成"断网"进而无限期阻塞。
            return False

        reason = f"[NetworkOffline] {type(exc).__name__}: {exc}"
        logger.warning("检测到疑似网络中断，暂停重试计数，等待网络恢复 — %s", reason)
        if on_retry:
            on_retry(attempt, reason + "（等待网络恢复中，不计入重试次数）")

        def _on_waiting(elapsed: float) -> None:
            logger.warning("网络仍未恢复，已等待 %.0fs …", elapsed)
            if on_retry:
                on_retry(attempt, f"[NetworkOffline] 已等待 {elapsed:.0f}s，仍未恢复网络…")

        recovered = wait_until_online(
            check_interval=self.network_check_interval,
            max_wait=self.network_max_wait,
            on_waiting=_on_waiting,
        )
        if recovered and on_retry:
            on_retry(attempt, "[NetworkOffline] 网络已恢复，重新发起请求")
        return recovered

    def _do_wait(self, delay: float, attempt: int, _countdown) -> None:
        """执行等待，同时维护倒计时状态供状态栏显示。"""
        if delay <= 0:
            return
        if _countdown:
            _countdown.start_wait(attempt, self.max_retries, delay, self.backoff.description)
        try:
            self._sleep_with_countdown(delay)
        finally:
            if _countdown:
                _countdown.stop_wait()

    def _sleep_with_countdown(self, total: float) -> None:
        """分片 sleep（每片 ≤0.2s），让状态栏有机会刷新倒计时显示。"""
        remaining = total
        while remaining > 0:
            time.sleep(min(remaining, 0.2))
            remaining -= 0.2

    def _check_conditions(
        self, response: LLMResponse
    ) -> Optional[RetryCondition]:
        """
        检查所有条件，返回第一个触发的条件；
        所有条件均未触发时返回 None。
        """
        for condition in self.conditions:
            if condition.should_retry(response):
                return condition
        return None

    def add_condition(self, condition: RetryCondition) -> "RetryPolicy":
        """链式添加条件，返回自身便于链式调用。"""
        self.conditions.append(condition)
        return self


# ── 便捷工厂 ──────────────────────────────────────────────────────────────────

def default_retry_policy(
    max_retries: int = 2,
    retry_delay: float = 10.0,
    retry_on_exception: bool = True,
    backoff: Optional[BackoffStrategy] = None,
) -> RetryPolicy:
    """
    默认重试策略：EmptyOutputCondition + 可配置退避。

    Args:
        max_retries:        最多重试次数，默认 2
        retry_delay:        固定等待秒数（兼容旧接口）；
                            若同时传入 backoff=，则 retry_delay 被忽略
        retry_on_exception: 异常是否计入重试，默认 True
        backoff:            退避策略；为 None 时使用 FixedBackoff(retry_delay)
    """
    _backoff = backoff if backoff is not None else FixedBackoff(retry_delay)
    return RetryPolicy(
        max_retries=max_retries,
        conditions=[EmptyOutputCondition()],
        backoff=_backoff,
        retry_on_exception=retry_on_exception,
    )


def no_retry_policy() -> RetryPolicy:
    """不重试策略（max_retries=0），用于禁用重试。"""
    return RetryPolicy(max_retries=0, conditions=[])


def background_retry_policy(
    max_retries: int = 10,
    retry_delay: float = 10.0,
    backoff: Optional[BackoffStrategy] = None,
) -> RetryPolicy:
    """
    后台任务重试策略：用于长期记忆/用户画像生成等场景。
    重试次数更多、间隔更长，适合非交互的后台任务。

    Args:
        max_retries: 最多重试次数，默认 10
        retry_delay: 固定等待秒数（兼容旧接口）
        backoff:     退避策略；为 None 时使用 FixedBackoff(retry_delay)
    """
    _backoff = backoff if backoff is not None else FixedBackoff(retry_delay)
    return RetryPolicy(
        max_retries=max_retries,
        conditions=[EmptyOutputCondition()],
        backoff=_backoff,
        retry_on_exception=True,
    )


def parse_backoff(
    mode: str,
    initial: float,
    step_or_multiplier: float,
    max_delay: float = 0.0,
) -> BackoffStrategy:
    """
    从字符串模式名称构造 BackoffStrategy，方便 CLI / 配置文件使用。

    Args:
        mode:                "fixed" | "linear" | "exponential"
        initial:             初始等待秒数（fixed 模式下即固定等待秒数）
        step_or_multiplier:  linear 模式下为步长（秒），exponential 模式下为倍数
        max_delay:           等待时间上限（0 = 不限制）

    Raises:
        ValueError: mode 不是已知策略名称时

    示例：
        parse_backoff("linear", 10, 60)          → LinearBackoff(10, 60)
        parse_backoff("exponential", 5, 1.5, 300) → ExponentialBackoff(5, 1.5, 300)
        parse_backoff("fixed", 30, 0)             → FixedBackoff(30)
    """
    mode = mode.lower().strip()
    if mode == "fixed":
        return FixedBackoff(delay=initial)
    elif mode == "linear":
        return LinearBackoff(initial=initial, step=step_or_multiplier, max_delay=max_delay)
    elif mode in ("exponential", "exp"):
        return ExponentialBackoff(initial=initial, multiplier=step_or_multiplier, max_delay=max_delay)
    else:
        raise ValueError(
            f"Unknown backoff mode: {mode!r}. "
            "Expected 'fixed', 'linear', or 'exponential'."
        )
