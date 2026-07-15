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

from .base import LLMClient, LLMConfig, LLMResponse, LLMRateLimitError, LLMError

if TYPE_CHECKING:
    from mini_agent.config import AppConfig
    from .retry import RetryPolicy

logger = logging.getLogger(__name__)


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
                        "ApiKeyPool: key ...%s cooled down for %.0fs (%s: %s)",
                        key[-8:], self._cooldown, type(exc).__name__, exc,
                    )
                    break

            # 切换到下一个可用 key
            next_key = self._find_available()
            if next_key is not None:
                logger.info("ApiKeyPool: switched to key ...%s", next_key[-8:])
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
        return type(exc).__name__ in self._switch_on

    def _advance(self) -> None:
        """round_robin：推进到下一个可用 key（已持锁时调用）。"""
        n = len(self._states)
        for delta in range(1, n + 1):
            nxt = (self._idx + delta) % n
            if self._states[nxt].is_available:
                self._idx = nxt
                return
        # 全部冷却中，保持当前索引（等待）

    def _find_available(self) -> Optional[str]:
        """找到第一个可用 key 并更新 _idx（已持锁时调用）。"""
        n = len(self._states)
        for delta in range(1, n + 1):
            nxt = (self._idx + delta) % n
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
        return f"{self.config.provider}/{self.config.model}"

    def rebuild_client_with_key(self, key: str) -> None:
        """切换到新 key 后重建 LLM client（用新 key 构造 SDK 客户端）。"""
        from .factory import create_client
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
        )
        self.config = new_cfg
        self.client = create_client(new_cfg)


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
        self._current_idx = 0
        self._fallback_on: set[str] = fallback_on or self.DEFAULT_FALLBACK_ON
        self._max_rounds = max(1, max_rounds)
        self._round_wait = max(0.0, round_wait)
        self._lock = threading.Lock()

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
        with self._lock:
            self._entries.append(entry)
            new_idx = len(self._entries) - 1
            if activate:
                self._current_idx = new_idx
            return new_idx

    def call_with_pool(
        self,
        call_fn: Callable[[LLMClient], LLMResponse],
        retry_policy: "RetryPolicy",
        on_switch_key: Optional[Callable[[str, str, Exception], None]] = None,
        on_switch_config: Optional[Callable[[str, str, Exception], None]] = None,
        max_rounds: Optional[int] = None,
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

                try:
                    response = self._call_entry(
                        entry=entry,
                        call_fn=call_fn,
                        retry_policy=retry_policy,
                        on_switch_key=on_switch_key,
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

                    is_last_in_round = config_attempt == total - 1
                    if not is_last_in_round:
                        next_entry = self._entries[(entry_idx + 1) % total]
                        logger.warning(
                            "LLMClientPool: [%s] failed (%s: %s), falling back to [%s]",
                            entry.label, type(exc).__name__, exc, next_entry.label,
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
                        if on_switch_config:
                            on_switch_config(entry.label, first_entry.label, exc)
                        if self._round_wait > 0:
                            time.sleep(self._round_wait)
                    # else: 已经是最后一轮的最后一个 entry，跳出内层循环，
                    # 内层 for 结束、外层 for 也结束，落到函数末尾统一抛出

        raise last_exc  # 所有轮次、所有配置都已尝试失败

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
        for item in chain_cfg:
            llm_cfg = cls._build_llm_config(item, cfg)
            client = create_client(llm_cfg)

            # 构建 key pool（若配置了多个 key）
            key_pool: Optional[ApiKeyPool] = None
            api_keys = item.get("api_keys") or []
            if api_keys and len(api_keys) > 1:
                switch_on_raw = item.get("key_switch_on", ["LLMRateLimitError"])
                key_pool = ApiKeyPool(
                    keys=api_keys,
                    rotation=item.get("key_rotation", "passive"),
                    switch_on=set(switch_on_raw),
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
                        "ApiKeyPool: key ...%s → ...%s (%s)",
                        old_key[-8:], new_key[-8:], type(exc).__name__,
                    )
                    if on_switch_key:
                        on_switch_key(old_key[-8:], new_key[-8:], exc)
                    entry.rebuild_client_with_key(new_key)
                    # 不立即 return，而是回到循环开头用新 key 重新尝试；
                    # 若这次依然失败，会再次进入 except 分支，继续尝试
                    # 下一把 key（不算入 retry_policy 的退避等待）
                    continue

        return retry_policy.call_with_retry(call_fn=_single_call)

    def _should_fallback(self, exc: Exception) -> bool:
        return type(exc).__name__ in self._fallback_on

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
    从已解析的 providers.json 数据中，将 api_key / api_keys[0] 自动注入为
    对应的标准环境变量（如 AGNES_API_KEY、NVIDIA_API_KEY）。

    **只注入当前进程环境中尚不存在的变量**，不覆盖用户已手动设置的值。
    注入范围：
      1. ``llm_fallback_chain`` 中每条条目的 ``api_key`` / ``api_keys``
      2. ``providers`` 块中每个 provider 的 ``api_key`` / ``api_keys``

    这样，各 provider 实现在初始化时读取 ``os.environ.get("AGNES_API_KEY")``
    等标准变量，就能找到 providers.json 里配置的 key，无需额外传参。

    Args:
        providers_cfg: ``_load_providers_config()`` 的返回值（可能是 ``{}``）。
    """
    import os

    def _first_key(entry: dict) -> str:
        """从条目中提取第一个有效 api_key 字符串。"""
        k = entry.get("api_key", "")
        if k and not k.startswith("sk-"):
            # 非占位符（providers.json.example 里的 "sk-ant-key-1-..." 仍然是字符串，
            # 此处不做过滤，由调用方保证 providers.json 中填写的是真实 key）
            pass
        if k:
            return k
        keys = entry.get("api_keys", [])
        return keys[0] if keys else ""

    def _inject(provider_name: str, key_value: str) -> None:
        """若 key_value 非空且对应环境变量未设置，则注入。"""
        if not key_value:
            return
        env_var = _PROVIDER_ENV_MAP.get(provider_name.lower())
        if not env_var:
            return
        if not os.environ.get(env_var):
            os.environ[env_var] = key_value

    if not providers_cfg:
        return

    # 1. 遍历 llm_fallback_chain
    for entry in providers_cfg.get("llm_fallback_chain", []):
        provider = entry.get("provider", "")
        if provider:
            _inject(provider, _first_key(entry))

    # 2. 遍历 providers 块
    for provider_name, settings in providers_cfg.get("providers", {}).items():
        if isinstance(settings, dict):
            _inject(provider_name, _first_key(settings))