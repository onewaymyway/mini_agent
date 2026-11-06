"""
llm/client_pool.py — LLM 多配置故障转移 & 多 Key 轮转

提供两个独立的机制，可单独或组合使用：

─────────────────────────────────────────────────────────────────────────────
ApiKeyPool — 同一 provider 的多个 API Key 管理
─────────────────────────────────────────────────────────────────────────────

当某个 key 遇到 rate limit / 认证失败等错误时，自动切换到下一个可用 key。
支持两种轮转策略：

  "passive"（被动，默认）
      正常情况只用当前 key；遇到触发条件才切换。
      适合 key 数量少、请求不密集的场景。

  "round_robin"（主动轮询）
      每次请求自动轮转，均匀分摊 RPM 配额。
      适合 key 数量多、请求密集的场景。

切换触发条件（switch_on）可配置，是一组错误类名称字符串，命中其中任意一个
则立刻切换，不再等待退避。默认触发条件：["LLMRateLimitError"]。
可扩展为：["LLMRateLimitError", "LLMConfigError"]（认证失败也切换）。

─────────────────────────────────────────────────────────────────────────────
LLMClientPool — 多套 LLM 配置的故障转移链
─────────────────────────────────────────────────────────────────────────────

持有一个有序的配置列表（llm_fallback_chain），第一条是主配置。
当当前配置的所有重试（含多 key 切换）全部失败后，自动切换到下一条配置。

fallback 触发条件（fallback_on）同样可配置：
  默认：["LLMRateLimitError", "LLMTimeoutError", "LLMProviderError"]
        —— 可恢复的错误才 fallback；认证失败（LLMConfigError）不 fallback，
           因为换个配置不会解决代码配置问题。

使用示例：
    pool = LLMClientPool.from_config(cfg)
    response = pool.call(
        call_fn=lambda client: client.chat(messages, system, tools),
        retry_policy=my_retry_policy,
        stream=False,
    )
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TYPE_CHECKING

from .base import LLMClient, LLMConfig, LLMResponse, LLMRateLimitError, LLMError, resolve_trust_env, mask_api_key
from . import deadline as _deadline

if TYPE_CHECKING:
    from mini_agent.config import AppConfig
    from .retry import RetryPolicy

logger = logging.getLogger(__name__)


def _exc_matches(exc: Exception, names: "set[str]") -> bool:
    """异常类名（含其所有父类名）是否命中 names。

    原先只比较 type(exc).__name__，子类异常（如 LLMPermanentError）不会命中
    配置里写的父类名（如 "LLMProviderError"），导致 fallback / 换 key 被静默跳过。
    """
    from .base import LLMContextWindowError
    if isinstance(exc, LLMContextWindowError):
        # 上下文超限是确定性错误，必须原样抛给上层触发 compact，
        # 不能因为它继承自 LLMProviderError 就被 fallback / 换 key 吞掉。
        return type(exc).__name__ in names
    return any(cls.__name__ in names for cls in type(exc).__mro__)


# ─────────────────────────────────────────────────────────────────────────────
# ApiKeyPool
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class KeyState:
    """单个 API Key 的运行时状态。"""
    key: str
    cooldown_until: float = 0.0   # monotonic 时间戳，0 = 不在冷却
    fail_count: int = 0

    @property
    def is_available(self) -> bool:
        return time.monotonic() >= self.cooldown_until

    def cool_down(self, seconds: float) -> None:
        self.cooldown_until = time.monotonic() + seconds
        self.fail_count += 1

    def reset(self) -> None:
        self.cooldown_until = 0.0
        self.fail_count = 0


class ApiKeyPool:
    """
    同一 provider 下的多 API Key 管理器。

    Args:
        keys:            API key 列表（至少一个）
        rotation:        "passive"（遇错切换）或 "round_robin"（每次轮转）
        switch_on:       触发切换的错误类名称集合
        cooldown:        key 被切换后的冷却时间（秒），默认 60s
    """

    def __init__(
        self,
        keys: list[str],
        rotation: str = "passive",
        switch_on: Optional[set[str]] = None,
        cooldown: float = 60.0,
    ) -> None:
        if not keys:
            raise ValueError("ApiKeyPool requires at least one key")
        self._states: list[KeyState] = [KeyState(k) for k in keys]
        self._rotation = rotation
        self._switch_on: set[str] = switch_on or {"LLMRateLimitError", "LLMPermanentError"}
        self._cooldown = cooldown
        self._idx = 0          # 当前 key 索引（round_robin 用）
        self._lock = threading.Lock()

    @property
    def current_key(self) -> str:
        with self._lock:
            return self._states[self._idx].key

    def acquire_key(self) -> str:
        """
        获取当前应该使用的 key。
        round_robin 模式下自动推进到下一个可用 key。
        """
        with self._lock:
            if self._rotation == "round_robin":
                self._advance()
            return self._states[self._idx].key

    def on_error(self, key: str, exc: Exception) -> Optional[str]:
        """
        报告某个 key 出错。若错误命中 switch_on，则切换到下一个可用 key。

        Returns:
            切换后的新 key（已切换），或 None（错误不触发切换 / 无可用 key）
        """
        if not self._should_switch(exc):
            return None

        with self._lock:
            # 找到出错的 key，设置冷却
            for state in self._states:
                if state.key == key:
                    state.cool_down(self._cooldown)
                    logger.warning(
                        "ApiKeyPool: key %s cooled down for %.0fs (%s: %s)",
                        mask_api_key(key), self._cooldown, type(exc).__name__, exc,
                    )
                    break

            # 切换到下一个可用 key：从失败的 key 自己的位置往后找，而不是从共享指针往后找
            # （指针可能已被其它调用推进，否则会跳过或选错 key）
            next_key = self._find_available(after=key)
            if next_key is not None:
                logger.info("ApiKeyPool: switched to key %s", mask_api_key(next_key))
            return next_key

    def on_success(self, key: str) -> None:
        """key 调用成功，重置失败计数。"""
        with self._lock:
            for state in self._states:
                if state.key == key and state.fail_count > 0:
                    state.reset()
                    break

    def all_exhausted(self) -> bool:
        """所有 key 均在冷却中。"""
        with self._lock:
            return not any(s.is_available for s in self._states)

    def next_available_in(self) -> float:
        """返回最快一个 key 解除冷却还需等待的秒数。"""
        with self._lock:
            now = time.monotonic()
            waits = [max(0.0, s.cooldown_until - now) for s in self._states]
            return min(waits)

    def snapshot(self) -> list[dict]:
        """返回所有 key 状态快照（供日志/状态栏使用）。"""
        with self._lock:
            now = time.monotonic()
            return [
                {
                    "key_suffix": s.key[-8:],
                    "available": s.is_available,
                    "cooldown_remaining": max(0.0, round(s.cooldown_until - now, 1)),
                    "fail_count": s.fail_count,
                }
                for s in self._states
            ]

    # ── 内部辅助 ─────────────────────────────────────────────────────────────

    def _should_switch(self, exc: Exception) -> bool:
        return _exc_matches(exc, self._switch_on)

    def _advance(self) -> None:
        """round_robin：推进到下一个可用 key（已持锁时调用）。"""
        n = len(self._states)
        for delta in range(1, n + 1):
            nxt = (self._idx + delta) % n
            if self._states[nxt].is_available:
                self._idx = nxt
                return
        # 全部冷却中，保持当前索引（等待）

    def _find_available(self, after: Optional[str] = None) -> Optional[str]:
        """找到第一个可用 key 并更新 _idx（已持锁时调用）。
        after 为失败的 key：从它的位置往后找；为 None 时从当前指针开始。"""
        n = len(self._states)
        base = next((i for i, st in enumerate(self._states) if st.key == after), self._idx) if after is not None else self._idx
        for delta in range(1, n + 1):
            nxt = (base + delta) % n
            if self._states[nxt].is_available:
                self._idx = nxt
                return self._states[nxt].key
        return None


# ─────────────────────────────────────────────────────────────────────────────
# ProviderEntry — fallback chain 中的单条配置
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ProviderEntry:
    """
    llm_fallback_chain 中的一条记录，持有该配置对应的 client 和 key pool。
    """
    config: LLMConfig
    client: LLMClient
    key_pool: Optional[ApiKeyPool] = None   # None = 单 key，不做轮转

    @property
    def label(self) -> str:
        # 带上 key 后缀：同 provider/model、不同 key 的条目在日志里必须可区分，
        # 否则 fallback 切换会显示成 "a/m → a/m" 这种看似没切换的假象。
        base = f"{self.config.provider}/{self.config.model}"
        key = self.config.api_key or ""
        return f"{base}#{key[-4:]}" if key else base

    def rebuild_client_with_key(self, key: str) -> None:
        """切换到新 key 后重建 LLM client（用新 key 构造 SDK 客户端）。"""
        from .factory import create_client
        _owner = getattr(self.client, "_fallback_pool", None)
        new_cfg = LLMConfig(
            provider=self.config.provider,
            model=self.config.model,
            api_key=key,
            base_url=self.config.base_url,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            timeout=self.config.timeout,
            extra=self.config.extra,
            requires_api_key=self.config.requires_api_key,
            use_system_tool_call=self.config.use_system_tool_call,
            system_message_format=self.config.system_message_format,
            trust_env=self.config.trust_env,
        )
        self.config = new_cfg
        self.client = create_client(new_cfg)
        if _owner is not None:
            self.client._fallback_pool = _owner


# ─────────────────────────────────────────────────────────────────────────────
# LLMClientPool
# ─────────────────────────────────────────────────────────────────────────────

class LLMClientPool:
    """
    多套 LLM 配置的故障转移链 + 多 Key 轮转的统一调度器。

    llm_fallback_chain 中第一条是主配置；当当前配置全部失败后，
    按顺序切换到下一条配置继续执行。

    Args:
        entries:      ProviderEntry 列表，第一条为主配置
        fallback_on:  触发 fallback 到下一条配置的错误类名称集合
    """

    DEFAULT_FALLBACK_ON: set[str] = {
        "LLMRateLimitError",
        "LLMTimeoutError",
        "LLMProviderError",
        "LLMPermanentError",
    }

    def __init__(
        self,
        entries: list[ProviderEntry],
        fallback_on: Optional[set[str]] = None,
        max_rounds: int = 2,
        round_wait: float = 5.0,
    ) -> None:
        """
        Args:
            entries:      ProviderEntry 列表，第一条为主配置
            fallback_on:  触发 fallback 到下一条配置的错误类名称集合
            max_rounds:   fallback chain 最多整体轮询的轮数（默认 2）。
                          原实现只会把链条里的每个 entry 各试一次，全部
                          失败后立即抛出；这里改为可以从头再走一轮或多轮，
                          给限流/冷却中的 key 和配置一个恢复的机会。
                          设为 1 等价于旧行为（只走一轮）。
            round_wait:   每一轮整体失败后、开始下一轮之前的等待秒数
                          （默认 5s）。设为 0 表示不等待、立即开始下一轮。
        """
        if not entries:
            raise ValueError("LLMClientPool requires at least one entry")
        self._entries = entries
        for _e in self._entries:
            _e.client._fallback_pool = self
        self._current_idx = 0
        self._fallback_on: set[str] = fallback_on or self.DEFAULT_FALLBACK_ON
        self._max_rounds = max(1, max_rounds)
        self._round_wait = max(0.0, round_wait)
        self._lock = threading.Lock()
        # 调用计数（llm/call_stats.py）：默认不记录，由 configure_stats() 显式开启。
        # 不配置时（测试、嵌入场景）pool 行为与之前完全一致。
        self._stats_root: Optional[str] = None
        self._stats_kind: str = "agent"
        self._stats_origin: str = "main"

    # ── 对外主接口 ────────────────────────────────────────────────────────────

    @property
    def current_client(self) -> LLMClient:
        with self._lock:
            return self._entries[self._current_idx].client

    @property
    def current_entry(self) -> ProviderEntry:
        with self._lock:
            return self._entries[self._current_idx]

    def find_entry_index(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Optional[int]:
        """
        在 fallback chain 中查找匹配 provider/model 的条目索引。

        provider 和 model 均为可选过滤条件（大小写不敏感）：
          - 只给 model：按模型名匹配（忽略 provider）
          - 只给 provider：返回该 provider 的第一条条目（"默认模型"）
          - 都给：要求两者都匹配

        Returns:
            匹配的条目索引，未找到返回 None。
        """
        provider_l = provider.lower().strip() if provider else None
        model_l = model.lower().strip() if model else None
        with self._lock:
            for i, entry in enumerate(self._entries):
                if provider_l is not None and entry.config.provider.lower() != provider_l:
                    continue
                if model_l is not None and entry.config.model.lower() != model_l:
                    continue
                return i
        return None

    def switch_to_index(self, idx: int) -> ProviderEntry:
        """
        将 _current_idx 切换到已存在的条目（不重建 client，因为该条目本就
        持有一个就绪的 client）。

        Returns:
            切换后的 ProviderEntry。
        """
        with self._lock:
            if not (0 <= idx < len(self._entries)):
                raise IndexError(f"LLMClientPool: index {idx} out of range")
            self._current_idx = idx
            return self._entries[idx]

    def add_entry(self, entry: ProviderEntry, *, activate: bool = True) -> int:
        """
        向 fallback chain 追加一条新条目（不影响已有条目）。

        Returns:
            新条目的索引。
        """
        entry.client._fallback_pool = self
        with self._lock:
            self._entries.append(entry)
            new_idx = len(self._entries) - 1
            if activate:
                self._current_idx = new_idx
            return new_idx

    def configure_stats(
        self,
        root: Optional[str],
        *,
        kind: Optional[str] = None,
        origin: Optional[str] = None,
    ) -> None:
        """开启调用计数。

        root:   计数写入的项目根目录（主项目根）；None = 不记录。
        kind:   pool 的默认来源（调用点没有通过 call_source 标记时使用），如 agent / cron。
        origin: 调用来源项目，main 或 external:<项目名>。
        """
        self._stats_root = str(root) if root else None
        if kind:
            self._stats_kind = kind
        if origin:
            self._stats_origin = origin

    def _record_stat(
        self,
        outcome: str,
        *,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        duration_ms: int = 0,
        usage: Any = None,
    ) -> None:
        """记录一条调用计数。失败一律静默忽略——计数绝不能影响真正的 LLM 调用。"""
        root = self._stats_root
        if not root:
            return
        try:
            from mini_agent.storage.paths import AgentPaths
            from mini_agent.llm import call_stats

            if provider is None or model is None:
                entry = self.current_entry
                if provider is None:
                    provider = entry.config.provider
                if model is None:
                    model = entry.config.model
            call_stats.record_call(
                AgentPaths(root),
                provider=provider or "",
                model=model or "",
                input_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
                output_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
                duration_ms=duration_ms,
                outcome=outcome,
                source=call_stats.current_source(self._stats_kind),
                origin=self._stats_origin,
            )
        except Exception:
            pass

    def record_stat(
        self,
        outcome: str,
        *,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        duration_ms: int = 0,
        usage: Any = None,
    ) -> None:
        """公开入口：供 pool 之外、但需要按同一口径计数的调用点使用（如 override 临时 client）。"""
        self._record_stat(
            outcome, provider=provider, model=model, duration_ms=duration_ms, usage=usage,
        )

    def call_with_pool(
        self,
        call_fn: Callable[[LLMClient], LLMResponse],
        retry_policy: "RetryPolicy",
        on_switch_key: Optional[Callable[[str, str, Exception], None]] = None,
        on_switch_config: Optional[Callable[[str, str, Exception], None]] = None,
        max_rounds: Optional[int] = None,
        on_retry: Optional[Callable[[int, str], None]] = None,
    ) -> LLMResponse:
        """执行 call_fn 并统一记录调用计数（成功 / 最终失败）。

        所有 LLM 调用（主对话、LLMHelper、后台任务、子 Agent）最终都经过这里，
        因此计数只在这一处记录，不会重复计数。key 切换与配置切换的计数在
        ``_call_entry`` / ``_call_with_pool_impl`` 中就地记录。
        """
        started = time.monotonic()
        try:
            response = self._call_with_pool_impl(
                call_fn=call_fn,
                retry_policy=retry_policy,
                on_switch_key=on_switch_key,
                on_switch_config=on_switch_config,
                max_rounds=max_rounds,
                on_retry=on_retry,
            )
        except Exception:
            self._record_stat("error", duration_ms=int((time.monotonic() - started) * 1000))
            raise
        self._record_stat(
            "success",
            duration_ms=int((time.monotonic() - started) * 1000),
            usage=response.usage,
        )
        return response

    def _call_with_pool_impl(
        self,
        call_fn: Callable[[LLMClient], LLMResponse],
        retry_policy: "RetryPolicy",
        on_switch_key: Optional[Callable[[str, str, Exception], None]] = None,
        on_switch_config: Optional[Callable[[str, str, Exception], None]] = None,
        max_rounds: Optional[int] = None,
        on_retry: Optional[Callable[[int, str], None]] = None,
    ) -> LLMResponse:
        """
        执行 call_fn，自动处理 key 切换和配置 fallback。

        Args:
            call_fn:          接收 LLMClient，返回 LLMResponse 的函数
            retry_policy:     用于单个 provider entry 内的重试策略
            on_switch_key:    key 切换时的通知回调 (old_key_suffix, new_key_suffix, exc)
            on_switch_config: 配置切换时的通知回调 (old_label, new_label, exc)
            max_rounds:       本次调用覆盖实例默认的轮询轮数（不传则用
                              构造时的 max_rounds，默认 2）

        Returns:
            LLMResponse

        Raises:
            LLMError: 所有轮次、所有配置均已用尽仍然失败

        Note:
            修复说明：原实现里外层只有一层 for 循环，把 fallback chain 的
            每个 entry 各试一次；如果链条里所有 entry 在这一轮里恰好都
            触发了 fallback 条件（例如短时间内被同一波限流/临时封禁波及），
            会直接 raise last_exc，不会给"稍等一下、从头再试一轮"的机会。
            现在改成外层再套一层轮次循环：一轮所有 entry 都失败后，等待
            round_wait 秒（给限流/冷却中的 key 一点恢复时间），再从
            start_idx 重新开始新的一轮，最多尝试 max_rounds 轮。
        """
        last_exc: Optional[Exception] = None

        with self._lock:
            start_idx = self._current_idx
            total = len(self._entries)

        rounds = self._max_rounds if max_rounds is None else max(1, max_rounds)

        for round_num in range(1, rounds + 1):
            for config_attempt in range(total):
                with self._lock:
                    entry_idx = (start_idx + config_attempt) % total
                    entry = self._entries[entry_idx]

                # [tick_dispatch_only_execution_model_plan.md 阶段四] deadline 作用域内，
                # 切换 entry / 进入新一轮之前先确认预算还在；没有作用域时是空操作。
                if last_exc is not None:
                    _deadline.check_deadline("LLMClientPool fallback")

                try:
                    response = self._call_entry(
                        entry=entry,
                        call_fn=call_fn,
                        retry_policy=retry_policy,
                        on_switch_key=on_switch_key,
                        on_retry=on_retry,
                    )
                    # 成功：记录当前 idx 并返回
                    with self._lock:
                        self._current_idx = entry_idx
                    return response

                except Exception as exc:
                    last_exc = exc
                    # 判断是否触发 fallback；不触发的错误（如配置错误）
                    # 无论第几轮都应该立即抛出，多等/多轮没有意义。
                    if not self._should_fallback(exc):
                        raise
                    # deadline 已用尽：不再 fallback 到下一个 entry / 下一轮，直接抛出
                    if _deadline.expired():
                        raise

                    is_last_in_round = config_attempt == total - 1
                    if not is_last_in_round:
                        next_entry = self._entries[(entry_idx + 1) % total]
                        logger.warning(
                            "LLMClientPool: [%s] failed (%s: %s), falling back to [%s]",
                            entry.label, type(exc).__name__, exc, next_entry.label,
                        )
                        self._record_stat(
                            "config_switch",
                            provider=entry.config.provider, model=entry.config.model,
                        )
                        if on_switch_config:
                            on_switch_config(entry.label, next_entry.label, exc)
                    elif round_num < rounds:
                        # 本轮（第 round_num 轮）链条里所有 entry 都失败了，
                        # 但还有下一轮机会：等待一小段时间后从头开始重试，
                        # 给被限流/冷却的 key 和配置一点恢复时间。
                        first_entry = self._entries[start_idx]
                        logger.warning(
                            "LLMClientPool: 第 %d/%d 轮所有配置均已失败 (最后一个 [%s]: %s: %s)，"
                            "等待 %.1fs 后从头开始第 %d 轮重试",
                            round_num, rounds, entry.label, type(exc).__name__, exc,
                            self._round_wait, round_num + 1,
                        )
                        self._record_stat(
                            "config_switch",
                            provider=entry.config.provider, model=entry.config.model,
                        )
                        if on_switch_config:
                            on_switch_config(entry.label, first_entry.label, exc)
                        if self._round_wait > 0:
                            time.sleep(_deadline.clamp_wait(self._round_wait))
                    # else: 已经是最后一轮的最后一个 entry，跳出内层循环，
                    # 内层 for 结束、外层 for 也结束，落到函数末尾统一抛出

        raise last_exc  # 所有轮次、所有配置都已尝试失败

    def chat_with_retry(
        self,
        messages: list[dict],
        system: str,
        tools: Optional[list] = None,
        *,
        max_retries: int = 2,
        retry_delay: float = 5.0,
        retry_policy: "Optional[RetryPolicy]" = None,
        on_retry: Optional[Callable[[int, str], None]] = None,
    ) -> LLMResponse:
        """与 ``LLMClient.chat_with_retry`` 同签名的池化版本。

        后台摘要、画像、记忆回填、压缩等调用原先直接持有某个 provider client 调用
        ``chat_with_retry``，403/限流时既不切换 key 也不切换 provider。现在统一走
        这里：先按 retry_policy 在当前 entry 内重试，失败则按 fallback_on 切到链上
        下一条（含换 key），与主对话的 ``LLMHelper.chat`` 行为一致。
        """
        from .retry import RetryPolicy, EmptyOutputCondition

        policy = retry_policy or RetryPolicy(
            max_retries=max_retries,
            conditions=[EmptyOutputCondition()],
            retry_delay=retry_delay,
            retry_on_exception=True,
        )
        from mini_agent.llm import call_stats

        tools_list = tools or []
        # 未显式标记来源的 chat_with_retry 调用（摘要、画像、回填、压缩……）归为 background
        with call_stats.call_source_if_unset("background"):
            return self.call_with_pool(
                call_fn=lambda client: client.chat(messages, system, tools_list),
                retry_policy=policy,
                on_retry=on_retry,
            )

    def snapshot(self) -> dict:
        """返回当前状态快照（供状态栏/日志使用）。"""
        with self._lock:
            entries_info = []
            for i, e in enumerate(self._entries):
                info = {
                    "label": e.label,
                    "active": i == self._current_idx,
                }
                if e.key_pool:
                    info["keys"] = e.key_pool.snapshot()
                entries_info.append(info)
            return {"entries": entries_info, "current": self._current_idx}

    # ── 工厂方法 ──────────────────────────────────────────────────────────────

    @classmethod
    def from_config(cls, cfg: "AppConfig") -> "LLMClientPool":
        """从 AppConfig 构建 pool，并按 cfg 所属项目配置调用计数（见 configure_pool_stats）。"""
        pool = cls._from_config_impl(cfg)
        configure_pool_stats(pool, cfg)
        return pool

    @classmethod
    def _from_config_impl(cls, cfg: "AppConfig") -> "LLMClientPool":
        """
        从 AppConfig 构建 LLMClientPool。

        读取 cfg.llm_fallback_chain（列表，每项是 dict）。
        若为空，退化为只含主配置的单条链。

        每条配置 dict 支持以下字段：
            provider, model, api_key, api_keys, base_url,
            max_tokens, temperature, timeout,
            key_rotation, key_switch_on, key_cooldown
        """
        from .factory import create_client
        from mini_agent.config import AppConfig

        chain_cfg: list[dict] = getattr(cfg, "llm_fallback_chain", []) or []
        fallback_on_cfg: Optional[list] = getattr(cfg, "llm_fallback_on", None)
        fallback_on = set(fallback_on_cfg) if fallback_on_cfg else None
        # 可选：整条 fallback chain 失败后回头重试的轮数/等待秒数，
        # 配置文件里不写就用 __init__ 里的默认值（2 轮，每轮间隔 5s）。
        max_rounds = int(getattr(cfg, "llm_fallback_max_rounds", 2) or 2)
        round_wait = float(getattr(cfg, "llm_fallback_round_wait", 5.0) or 0.0)

        # 若未配置 fallback chain，退化为单条主配置
        if not chain_cfg:
            main_llm_cfg = LLMConfig.from_app_config(cfg)
            main_client = create_client(main_llm_cfg)
            entry = ProviderEntry(config=main_llm_cfg, client=main_client, key_pool=None)
            return cls(
                entries=[entry],
                fallback_on=fallback_on,
                max_rounds=max_rounds,
                round_wait=round_wait,
            )

        entries: list[ProviderEntry] = []
        seen_identity: set[tuple] = set()
        seen_model: dict[tuple, str] = {}
        for item in chain_cfg:
            llm_cfg = cls._build_llm_config(item, cfg)
            # 完全相同（provider/model/base_url/key）的条目是配置笔误，直接去掉，
            # 避免 fallback 时"切换"到一个和当前完全一样的连接。
            identity = (llm_cfg.provider, llm_cfg.model, llm_cfg.base_url, llm_cfg.api_key)
            if identity in seen_identity:
                logger.warning(
                    "llm_fallback_chain: 忽略重复条目 %s/%s（provider/model/base_url/key 完全相同）",
                    llm_cfg.provider, llm_cfg.model,
                )
                continue
            seen_identity.add(identity)
            # 同 provider/model 只换 key 的条目：账号级 403 / 封禁换 key 无效，给出提示。
            model_key = (llm_cfg.provider, llm_cfg.model, llm_cfg.base_url)
            if model_key in seen_model:
                logger.warning(
                    "llm_fallback_chain: %s/%s 出现多条（key 不同）。若失败原因是账号级限制"
                    "（如 403 unusual activity），换 key 无效，应改用不同 provider 的备用条目。",
                    llm_cfg.provider, llm_cfg.model,
                )
            else:
                seen_model[model_key] = llm_cfg.api_key or ""
            client = create_client(llm_cfg)

            # 构建 key pool（若配置了多个 key）
            key_pool: Optional[ApiKeyPool] = None
            api_keys = item.get("api_keys") or []
            if api_keys and len(api_keys) > 1:
                switch_on_raw = item.get("key_switch_on", ["LLMRateLimitError"])
                # LLMPermanentError（401/403：key 失效 / 账号被限制）无论配置如何都应换 key：
                # 同一把 key 重试不会成功，而池里还有别的 key 可用时，直接换 key 才是正解。
                switch_on_set = set(switch_on_raw) | {"LLMPermanentError"}
                key_pool = ApiKeyPool(
                    keys=api_keys,
                    rotation=item.get("key_rotation", "passive"),
                    switch_on=switch_on_set,
                    cooldown=float(item.get("key_cooldown", 60.0)),
                )
            elif api_keys and len(api_keys) == 1:
                # 单 key 写在 api_keys 里也兼容
                pass

            entries.append(ProviderEntry(config=llm_cfg, client=client, key_pool=key_pool))

        return cls(
            entries=entries,
            fallback_on=fallback_on,
            max_rounds=max_rounds,
            round_wait=round_wait,
        )

    # ── 内部辅助 ──────────────────────────────────────────────────────────────

    def _call_entry(
        self,
        entry: ProviderEntry,
        call_fn: Callable[[LLMClient], LLMResponse],
        retry_policy: "RetryPolicy",
        on_switch_key: Optional[Callable[[str, str, Exception], None]],
        on_retry: Optional[Callable[[int, str], None]] = None,
    ) -> LLMResponse:
        """
        在单个 ProviderEntry 内执行调用，处理 key 轮转和重试。
        """
        key_pool = entry.key_pool

        def _single_call() -> LLMResponse:
            # 修复说明（对应问题：key 轮转重试后若再次失败会绕过后续切换/fallback）：
            # 原实现里"换新 key 后立即重试"只裸调用一次 call_fn，且没有
            # try/except 包裹——如果这次重试仍然失败（例如新 key 同样被
            # 限流/封禁），异常会直接原样抛出，既不会再给 key_pool 一次
            # on_error() 的机会去尝试池子里的下一把 key，也会跳过
            # on_switch_key 的通知。
            #
            # 改成 while 循环后：只要 key_pool.on_error() 还能找到可用的
            # 下一把 key（未全部处于冷却中），就会不断切换、不断重试；
            # 只有当 on_error() 返回 None（错误类型不触发切换，或者所有
            # key 都已耗尽/冷却）时才会真正把异常向上抛出，交给
            # retry_policy / call_with_pool 的 fallback 逻辑处理。
            while True:
                # 获取当前 key（round_robin 模式下每次推进）
                if key_pool:
                    current_key = key_pool.acquire_key()
                    # 若当前 client 的 key 与 pool 给出的 key 不同，重建 client
                    if current_key != entry.config.api_key:
                        entry.rebuild_client_with_key(current_key)

                try:
                    resp = call_fn(entry.client)
                    if key_pool:
                        key_pool.on_success(entry.config.api_key)
                    return resp

                except Exception as exc:
                    if not key_pool:
                        raise
                    old_key = entry.config.api_key
                    new_key = key_pool.on_error(old_key, exc)
                    if new_key is None:
                        raise   # 错误不触发切换，或已无可用 key —— 真正抛出，交给上层 fallback

                    logger.info(
                        "ApiKeyPool: key %s → %s (%s)",
                        mask_api_key(old_key), mask_api_key(new_key), type(exc).__name__,
                    )
                    self._record_stat(
                        "key_switch",
                        provider=entry.config.provider, model=entry.config.model,
                    )
                    if on_switch_key:
                        on_switch_key(old_key[-8:], new_key[-8:], exc)
                    entry.rebuild_client_with_key(new_key)
                    # 不立即 return，而是回到循环开头用新 key 重新尝试；
                    # 若这次依然失败，会再次进入 except 分支，继续尝试
                    # 下一把 key（不算入 retry_policy 的退避等待）
                    continue

        return retry_policy.call_with_retry(call_fn=_single_call, on_retry=on_retry)

    def _should_fallback(self, exc: Exception) -> bool:
        return _exc_matches(exc, self._fallback_on)

    @staticmethod
    def _build_llm_config(item: dict, cfg: "AppConfig") -> LLMConfig:
        """从 chain 条目 dict 构建 LLMConfig。"""
        import os
        provider = item.get("provider") or getattr(cfg, "llm_provider", "anthropic")

        # api_key 优先级：条目显式指定 > api_keys[0] > 环境变量
        api_keys_list = item.get("api_keys") or []
        api_key = (
            item.get("api_key")
            or (api_keys_list[0] if api_keys_list else "")
            or _get_env_api_key(provider)
        )

        model = item.get("model") or getattr(cfg, "model", "")
        base_url = item.get("base_url") or getattr(cfg, "llm_base_url", "") or None

        return LLMConfig(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            max_tokens=int(item.get("max_tokens", getattr(cfg, "max_tokens", 8192))),
            temperature=float(item.get("temperature", 0.0)),
            timeout=int(item.get("timeout", 120)),
            requires_api_key=(provider not in ("ollama", "local")),
            use_system_tool_call=bool(
                item.get("use_system_tool_call",
                         getattr(cfg, "use_system_tool_call", False))
            ),
            system_message_format=item.get(
                "system_message_format",
                getattr(cfg, "system_message_format", "system_field"),
            ),
            # 条目显式写 trust_env 优先，否则沿用全局 llm_network.trust_env
            trust_env=bool(item.get("trust_env", resolve_trust_env(cfg))),
        )


def _get_env_api_key(provider: str) -> str:
    """根据 provider 名称读取对应的环境变量 API key。"""
    import os
    env_var = _PROVIDER_ENV_MAP.get(provider.lower())
    return os.environ.get(env_var, "") if env_var else ""


# provider 名称 → 环境变量名的映射表（供 _get_env_api_key 和 inject_env_from_providers 共用）
_PROVIDER_ENV_MAP: dict[str, str] = {
    "anthropic":  "ANTHROPIC_API_KEY",
    "claude":     "ANTHROPIC_API_KEY",
    "openai":     "OPENAI_API_KEY",
    "azure":      "OPENAI_API_KEY",
    "deepseek":   "DEEPSEEK_API_KEY",
    "moonshot":   "MOONSHOT_API_KEY",
    "qwen":       "DASHSCOPE_API_KEY",
    "groq":       "GROQ_API_KEY",
    "together":   "TOGETHER_API_KEY",
    "fireworks":  "FIREWORKS_API_KEY",
    "nvidia":     "NVIDIA_API_KEY",
    "nim":        "NVIDIA_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "agnes":      "AGNES_API_KEY",
}


def inject_env_from_providers(providers_cfg: dict) -> None:
    """
    从已解析的 providers.json 数据中，将 api_key / api_keys 自动注入为
    对应的标准环境变量（如 AGNES_API_KEY、NVIDIA_API_KEY）。

    **只注入当前进程环境中尚不存在的变量**，不覆盖用户已手动设置的值。
    注入范围：
      1. ``llm_fallback_chain`` 中每条条目的 ``api_key`` / ``api_keys``
      2. ``providers`` 块中每个 provider 的 ``api_key`` / ``api_keys``

    这样，各 provider 实现在初始化时读取 ``os.environ.get("AGNES_API_KEY")``
    等标准变量，就能找到 providers.json 里配置的 key，无需额外传参。

    若某个 provider 配置了多个 ``api_keys``，除了单数形式的环境变量（取
    第一个 key，供只认单 key 的代码路径兼容）外，还会额外注入复数形式的
    环境变量（如 ``AGNES_API_KEYS``，逗号分隔全部 key）。目前主要给
    `.claude/skills/` 下的 gen_image_with_text / gen_video_with_text /
    ask_image 三个 skill 用——它们是独立子进程，读不到 providers.json 时
    (比如工作目录不在项目内)，会退回读这个环境变量，从而也能用上多 key
    限流自动切换。只有一个 key 时不会注入复数形式变量，行为与之前一致。

    Args:
        providers_cfg: ``_load_providers_config()`` 的返回值（可能是 ``{}``）。
    """
    import os

    def _all_keys(entry: dict) -> list[str]:
        """从条目中提取全部有效 api_key（去重保序），api_key 字段优先排第一。"""
        result: list[str] = []
        single = entry.get("api_key", "")
        if single:
            result.append(single)
        for k in entry.get("api_keys", []) or []:
            if k and k not in result:
                result.append(k)
        return result

    def _inject(provider_name: str, keys: list[str]) -> None:
        """若 keys 非空，注入单数环境变量（第一个 key）；
        若有多个 key，再额外注入复数环境变量（逗号分隔全部 key）。
        两者都遵循"只注入尚未设置的变量"规则。"""
        if not keys:
            return
        env_var = _PROVIDER_ENV_MAP.get(provider_name.lower())
        if not env_var:
            return

        if not os.environ.get(env_var):
            os.environ[env_var] = keys[0]

        if len(keys) > 1:
            keys_env_var = f"{env_var}S"
            if not os.environ.get(keys_env_var):
                os.environ[keys_env_var] = ",".join(keys)

    if not providers_cfg:
        return

    # 1. 遍历 llm_fallback_chain
    for entry in providers_cfg.get("llm_fallback_chain", []):
        provider = entry.get("provider", "")
        if provider:
            _inject(provider, _all_keys(entry))

    # 2. 遍历 providers 块
    for provider_name, settings in providers_cfg.get("providers", {}).items():
        if isinstance(settings, dict):
            _inject(provider_name, _all_keys(settings))

def build_pool_with_primary(cfg: "AppConfig", primary: LLMConfig) -> "LLMClientPool":
    """构造一个“首选 = primary，其后接 llm_fallback_chain 其余条目”的 pool。

    cron / Objective / SubAgent 这些独立 Agent 原先只用 ``create_client(primary)``
    造单个 client，整条 ``llm_fallback_chain`` 被忽略——一旦主 provider 返回 403 /
    限流，任务直接失败，没有任何切换机会。这里保持 primary 不变（保留任务指定的
    model 等覆盖项），再把链上其余 provider 追加为备选。
    """
    from .factory import create_client

    base = LLMClientPool.from_config(cfg)
    primary_identity = (primary.provider, primary.model, primary.base_url, primary.api_key)
    primary_entry = ProviderEntry(config=primary, client=create_client(primary), key_pool=None)
    rest = [
        e for e in base._entries
        if (e.config.provider, e.config.model, e.config.base_url, e.config.api_key) != primary_identity
    ]
    pool = LLMClientPool(
        entries=[primary_entry] + rest,
        fallback_on=base._fallback_on,
        max_rounds=base._max_rounds,
        round_wait=base._round_wait,
    )
    configure_pool_stats(pool, cfg)
    return pool


def attach_fallback_pool(agent, cfg: "AppConfig", primary: LLMConfig, *, source: Optional[str] = None):
    """把子 Agent 的单链 pool 替换为带 fallback 链的 pool，并同步 ``_llm``。

    source: 调用计数里的来源标记（如 cron / objective / subagent），用于看板按来源拆分。
    """
    pool = build_pool_with_primary(cfg, primary)
    if source:
        configure_pool_stats(pool, cfg, kind=source)
    agent._client_pool = pool
    agent._llm = pool.current_client
    return agent


def _resolve_stats_scope(cfg: "AppConfig") -> tuple[Optional[str], str]:
    """决定调用计数写到哪个项目、标记为哪个来源项目。

    - 主项目：写入自己的根目录，origin = main。
    - 外部项目（存在 project.yaml）：优先写入主项目根目录（看板读的就是主项目），
      origin = external:<项目目录名>；主项目根解析不到时退回写入外部项目自己的目录，
      保证不丢数据（只是不会出现在主看板里）。
    """
    from pathlib import Path

    root = getattr(cfg, "project_root", None)
    if not root:
        return None, "main"
    root_path = Path(root)
    try:
        if (root_path / "project.yaml").exists():
            origin = f"external:{root_path.name}"
            from mini_agent.config.loader import _resolve_main_project_root

            main_root = _resolve_main_project_root(root_path)
            if main_root is not None:
                return str(main_root), origin
            return str(root_path), origin
    except Exception:
        pass
    return str(root_path), "main"


def configure_pool_stats(pool: "LLMClientPool", cfg: "AppConfig", *, kind: Optional[str] = None) -> None:
    """按 cfg 为 pool 配置调用计数。任何异常都静默忽略——计数不能影响 pool 构造。"""
    try:
        root, origin = _resolve_stats_scope(cfg)
        pool.configure_stats(root, kind=kind, origin=origin)
    except Exception:
        pass
