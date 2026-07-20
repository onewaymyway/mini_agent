from __future__ import annotations

import copy
import re as _re
import threading
from typing import Optional

from mini_agent.config import AppConfig, SessionStats, build_system_prompt
from mini_agent.llm import (
    LLMClient, LLMConfig, LLMResponse, ToolSchema,
    LLMError,
)
import mini_agent.agent as _agent_pkg
from mini_agent.llm.retry import RetryPolicy, default_retry_policy, no_retry_policy, parse_backoff
from mini_agent.llm.client_pool import LLMClientPool
from mini_agent.permissions import PermissionGuard
from mini_agent.skills import SkillLoader
from mini_agent.tools import ToolRegistry, get_default_registry
from mini_agent.session import SessionManager, Session
import mini_agent.ui.renderer as R
from mini_agent.perception.token_counter import estimate_messages_tokens
from mini_agent.perception.project_scanner import ProjectScanner
from mini_agent.perception.file_watcher import FileWatcher
from mini_agent.perception.tool_cache import ToolResultCache
from mini_agent.perception.memory_base import MemoryBackend
from mini_agent.perception.memory_store import MemoryStore, MemoryEntry
from mini_agent.perception.memory_factory import create_memory_backend
from mini_agent.context_builder import ContextBuilder
from mini_agent.tool_executor import ToolExecutor
from mini_agent.history_manager import HistoryManager
from mini_agent.reminders import ReminderManager

from mini_agent.agent._helpers import (
    _term_write_lock_ctx, _NullCtx, _locked_print_info, _locked_print_warning,
    _is_tool_error, _clamp_confidence, _parse_lesson_candidates, _parse_timeline_summary,
)


class LLMControlMixin:
    """LLM 客户端与 Provider/模型切换、底层调用封装。"""

    @property
    def llm_client(self) -> LLMClient:
        return self._llm

    @property
    def llm_helper(self) -> "LLMHelper":
        """
        主对话循环之外场景（judge / ensemble / 目标拆解 / 摘要重写……）
        统一使用的轻量 LLM 调用入口，详见 llm/service.py 顶部说明。

        每次访问都基于 self._client_pool（懒取，不缓存），因此 /model
        切换后下一次调用自动跟随，语义与 llm_client 属性一致。
        """
        from mini_agent.llm.service import LLMHelper
        return LLMHelper(self._client_pool, self.cfg)

    def switch_provider(self, llm_config: LLMConfig) -> None:
        """
        运行时切换 LLM provider，不影响对话历史。
        同时重建 LLMClientPool 为单条链（新 config）。

        Example:
            agent.switch_provider(LLMConfig(provider="openai", model="gpt-4o", api_key="..."))
        """
        from mini_agent.llm.client_pool import ProviderEntry
        new_client = _agent_pkg.create_client(llm_config)
        self._llm = new_client
        entry = ProviderEntry(config=llm_config, client=new_client, key_pool=None)
        self._client_pool = LLMClientPool(entries=[entry])
        self.cfg.model = llm_config.model
        self.cfg.llm_provider = llm_config.provider
        self.cfg.api_key = llm_config.api_key or ""
        self.cfg.llm_base_url = llm_config.base_url or ""
        R.print_info(f"Switched to {self._llm}")

    def switch_model(self, model: str) -> "ProviderEntry":  # noqa: F821 (前向引用，运行时从 client_pool 导入)
        """
        运行时切换模型（保持当前 provider 不变，除非该模型属于 fallback chain
        中的另一个 provider）。

        行为：
          1. 先在当前 LLMClientPool 的 fallback chain 中查找匹配该 model 名称
             的已配置条目——若找到，直接切换 _current_idx 指向它（该条目早已
             持有一个就绪的 client，无需重建，API key/provider 也随之带过去）。
          2. 若 fallback chain 中没有这个模型，则在**当前 provider**下用新的
             model 名构造一个新的 LLMConfig（沿用当前 api_key/base_url 等），
             创建新 client，作为新条目追加进 fallback chain 并激活。

        这保证了 /model <name> 不再只是改一个不会被实际使用的字符串，而是
        真正让后续的 LLM 调用使用新模型对应的 client。

        Returns:
            切换后激活的 ProviderEntry。
        """
        from mini_agent.llm.client_pool import ProviderEntry

        idx = self._client_pool.find_entry_index(model=model)
        if idx is not None:
            entry = self._client_pool.switch_to_index(idx)
        else:
            current = self._client_pool.current_entry
            new_cfg = LLMConfig(
                provider=current.config.provider,
                model=model,
                api_key=current.config.api_key,
                base_url=current.config.base_url,
                max_tokens=current.config.max_tokens,
                temperature=current.config.temperature,
                timeout=current.config.timeout,
                extra=current.config.extra,
                requires_api_key=current.config.requires_api_key,
                use_system_tool_call=current.config.use_system_tool_call,
                system_message_format=current.config.system_message_format,
            )
            new_client = _agent_pkg.create_client(new_cfg)
            entry = ProviderEntry(config=new_cfg, client=new_client, key_pool=None)
            self._client_pool.add_entry(entry, activate=True)

        self._llm = entry.client
        self.cfg.model = entry.config.model
        self.cfg.llm_provider = entry.config.provider
        self.cfg.api_key = entry.config.api_key or ""
        self.cfg.llm_base_url = entry.config.base_url or ""
        return entry

    def switch_to_provider_default(
        self, provider: str, model: Optional[str] = None,
    ) -> "ProviderEntry":  # noqa: F821
        """
        运行时切换到指定 provider（供 `/provider switch <name> [model]` 使用）。

        行为：
          1. 若 fallback chain 中已有该 provider 的条目：
             - 给了 model：要求 provider+model 都匹配；
             - 没给 model：使用该 provider 在 chain 中出现的**第一条**（即
               "默认模型"）。
             命中后直接切换 _current_idx，复用已就绪的 client。
          2. 若 fallback chain 中完全没有该 provider：构造一条全新配置
             （model 用调用方传入的值；若也没传，退回当前 model），从标准
             环境变量解析 api_key，创建 client 并作为新条目追加进 chain。

        Returns:
            切换后激活的 ProviderEntry。
        """
        from mini_agent.llm.client_pool import ProviderEntry, _get_env_api_key

        idx = self._client_pool.find_entry_index(provider=provider, model=model)
        if idx is not None:
            entry = self._client_pool.switch_to_index(idx)
        else:
            resolved_model = model or self.cfg.model
            api_key = _get_env_api_key(provider)
            new_cfg = LLMConfig(
                provider=provider,
                model=resolved_model,
                api_key=api_key,
                requires_api_key=(provider not in ("ollama", "local")),
            )
            new_client = _agent_pkg.create_client(new_cfg)
            entry = ProviderEntry(config=new_cfg, client=new_client, key_pool=None)
            self._client_pool.add_entry(entry, activate=True)

        self._llm = entry.client
        self.cfg.model = entry.config.model
        self.cfg.llm_provider = entry.config.provider
        self.cfg.api_key = entry.config.api_key or ""
        self.cfg.llm_base_url = entry.config.base_url or ""
        return entry

    # ── [SYS-ROLE-AGENT] 角色 Agent 触发 ────────────────────────────────────

    def _call_llm(self) -> LLMResponse:
        """
        调用 LLMClient，根据 cfg.stream 选择流式或非流式。
        通过 LLMClientPool 支持多 key 轮转和多配置故障转移。
        """
        system = self._build_system()
        tools = self._build_tool_schemas()

        import inspect as _inspect
        from mini_agent.llm.system_tool_call import convert_tool_use_to_text
        from mini_agent.history.entry import to_llm_messages
        messages_for_llm = convert_tool_use_to_text(to_llm_messages(self._history))

        # [SYS-PRIVACY] 发送前：屏蔽隐私值
        _guard = self._privacy_guard
        if _guard.active:
            messages_for_llm = _guard.redact_messages(messages_for_llm)
            system = _guard.redact_system(system)

        def _do_single_call(client: LLMClient) -> LLMResponse:
            """单次 LLM 调用（流式/非流式），接受 client 参数供 pool 切换。"""
            _stream_sig = _inspect.signature(client.stream)
            _supports_on_reasoning = "on_reasoning" in _stream_sig.parameters
            _reasoning_started = [False]

            def _on_reasoning(token: str) -> None:
                if not self.cfg.show_reasoning:
                    return
                if not _reasoning_started[0]:
                    R.print_reasoning_header()
                    _reasoning_started[0] = True
                R.print_reasoning(token)

            # [SYS-PRIVACY] 流式打印时，占位符可能被拆成多个 token
            # （如 "{{SECRET_" 和 "1}}" 分两次到达）。
            # 用一个小缓冲区：遇到 "{{" 开头但还没有 "}}" 闭合时暂缓打印，
            # 等完整占位符到齐后 restore 再输出。
            # 注意：_make_guarded_write(w) 在 writer 实例化之后调用，避免前向引用。
            def _make_guarded_write(w: "R.StreamWriter"):
                _ph_buf: list[str] = []

                def _guarded_write(token: str) -> None:
                    if _ph_buf:
                        _ph_buf.append(token)
                        combined = "".join(_ph_buf)
                        if "}}" in combined:
                            _ph_buf.clear()
                            w.write(_guard.restore(combined))
                        elif len(combined) > 40:
                            # 超长未闭合，不是合法占位符，直接输出
                            _ph_buf.clear()
                            w.write(combined)
                    else:
                        if "{{" in token:
                            idx = token.rfind("{{")
                            before, after = token[:idx], token[idx:]
                            if "}}" in after:
                                w.write((before + _guard.restore(after)) if before else _guard.restore(after))
                            else:
                                if before:
                                    w.write(before)
                                _ph_buf.append(after)
                        else:
                            w.write(token)

                return _guarded_write

            try:
                if self.cfg.stream:
                    R.print_assistant_prefix(agent_name=self.cfg.agent_name)
                    writer = R.StreamWriter()
                    _on_token_fn = _make_guarded_write(writer) if _guard.active else writer.write
                    stream_kwargs: dict = dict(
                        messages=messages_for_llm,
                        system=system,
                        tools=tools,
                        on_token=_on_token_fn,
                    )
                    if _supports_on_reasoning:
                        stream_kwargs["on_reasoning"] = _on_reasoning
                    resp = client.stream(**stream_kwargs)
                    if self.cfg.show_reasoning:
                        if not _reasoning_started[0] and resp.reasoning:
                            R.print_reasoning_header()
                            R.console.print(resp.reasoning, style="dim")
                        if _reasoning_started[0] or resp.reasoning:
                            R.print_reasoning_footer()
                    writer.flush()
                else:
                    resp = client.chat(
                        messages=messages_for_llm,
                        system=system,
                        tools=tools,
                    )
                    if resp.reasoning and self.cfg.show_reasoning:
                        R.print_reasoning_header()
                        R.console.print(resp.reasoning, style="dim")
                        R.print_reasoning_footer()
                    if resp.text:
                        R.print_assistant_prefix(agent_name=self.cfg.agent_name)
                        R.print_markdown(resp.text)
            except LLMError:
                raise
            except Exception as e:
                from mini_agent.llm.base import LLMProviderError
                raise LLMProviderError(f"Unexpected LLM error: {e}") from e

            return resp

        def _on_retry(attempt: int, reason: str) -> None:
            if getattr(self.cfg, "llm_retry_verbose", True):
                R.print_warning(
                    f"[retry {attempt}/{self._retry_policy.max_retries}] {reason}"
                )

        def _on_switch_key(old_suffix: str, new_suffix: str, exc: Exception) -> None:
            R.print_warning(
                f"[key-switch] ...{old_suffix} → ...{new_suffix} "
                f"({type(exc).__name__})"
            )

        def _on_switch_config(old_label: str, new_label: str, exc: Exception) -> None:
            R.print_warning(
                f"[llm-fallback] {old_label} → {new_label} "
                f"({type(exc).__name__}: {str(exc)[:80]})"
            )
            self._llm = self._client_pool.current_client
            # [BUGFIX] 自动 fallback 切换 provider/model 后，之前只更新了
            # self._llm，没有同步 self.cfg.model / self.cfg.llm_provider——
            # 导致 self.cfg 里的 model 字段停留在配置文件写死的 chain[0]，
            # 与实际正在使用的模型不一致。这会让所有读取 base_cfg.model 做
            # "复用主 Agent 当前模型"兜底的地方（如
            # role_agents/model_resolution.py 的 resolve_role_model）拿到
            # 过期的、可能已经确认不可用的模型名（例如 TurnJudge 重新踩一遍
            # 已经 403 的模型）。这里补上同步，行为与显式 /model、/provider
            # switch（switch_model / switch_provider）保持一致。
            _entry = self._client_pool.current_entry
            self.cfg.model = _entry.config.model
            self.cfg.llm_provider = _entry.config.provider

        response = self._client_pool.call_with_pool(
            call_fn=_do_single_call,
            retry_policy=self._retry_policy,
            on_switch_key=_on_switch_key,
            on_switch_config=_on_switch_config,
        )
        self._llm = self._client_pool.current_client

        # [SYS-PRIVACY] 收到回复后：还原占位符 → 真实值
        if _guard.active:
            from dataclasses import replace as _dc_replace
            import json as _json
            if response.text:
                response = _dc_replace(response, text=_guard.restore(response.text))
            if response.tool_calls:
                restored_calls = []
                for tc in response.tool_calls:
                    raw = _json.dumps(tc.input)
                    restored_raw = _guard.restore(raw)
                    if restored_raw != raw:
                        from mini_agent.llm.base import ToolCall as _ToolCall
                        tc = _ToolCall(id=tc.id, name=tc.name, input=_json.loads(restored_raw))
                    restored_calls.append(tc)
                response = _dc_replace(response, tool_calls=restored_calls)

        return response

    # ── History management ─────────────────────────────────────────────────────

