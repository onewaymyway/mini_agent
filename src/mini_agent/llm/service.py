"""
llm/service.py — 主对话循环之外场景专用的轻量 LLM 调用入口

背景（详见 next_doc/llm_helper_unification_plan.md）：
  在 agent 主对话循环之外（judge / ensemble 评审 / 目标拆解 / 摘要重写 /
  路由判定……），此前各处各写各的调用逻辑：有的裸调 client.chat()，
  有的每次重新读一份启动时的静态配置去 create_client()，还有两处传了
  chat() 根本不支持的 max_tokens= 参数（见 objective_executor.py /
  goal_backlog.py 的历史 bug）。

LLMHelper 统一这些调用：

  - 默认路径：只持有 LLMClientPool 的引用，不 copy 配置，因此天然
    跟随 Agent 当前正在用的 provider/model（包括 /model 切换），
    并复用 LLMClientPool 的多 key 轮转 + 多配置 fallback。
  - override 路径：显式传入 override_model / override_provider /
    override_temperature 中任意一个时，一次性基于 AppConfig 构造一个
    独立 client（不会污染/切换主 pool 的状态，也不会触发 pool 的
    fallback——这是"临时用一个特定配置问一次"的场景，不该跳到 fallback
    chain 的其它 entry），但仍然套用同一个 RetryPolicy。

调用方按场景选择 max_retries（无统一"一刀切"的重试次数，
见改造计划第 6.1 节的分场景取值表）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from .base import LLMConfig, LLMResponse, ToolSchema
from . import deadline as _deadline
from .retry import EmptyOutputCondition, RetryPolicy

if TYPE_CHECKING:
    from .client_pool import LLMClientPool


class LLMHelper:
    """
    供 Agent 主对话循环之外的场景复用的轻量 LLM 调用入口。

    Args:
        client_pool: Agent 当前的 LLMClientPool（只存引用，不 copy）。
        app_cfg:     AppConfig，仅在 override_* 分支里用于取
                     provider/model/api_key 等基础字段的默认值。
    """

    def __init__(self, client_pool: "LLMClientPool", app_cfg: Any) -> None:
        self._pool = client_pool
        self._cfg = app_cfg

    @staticmethod
    def _now() -> float:
        import time
        return time.time()

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        import time
        return int((time.time() - started_at) * 1000)

    def _record_call_stat(self, *, outcome: str, duration_ms: int = 0, usage=None) -> None:
        """[kanban_perception_gaps_improvement_plan.md 方向 B.2] 跟
        `agent/llm_control.py::LLMControlMixin._record_llm_call_stat()` 是
        平行实现（LLMHelper 不是 Agent 的成员方法，拿不到 self.cfg 之外的
        任何 Agent 状态），同样遵循"失败静默忽略，绝不影响主调用链路"的
        约定。"""
        try:
            project_root = getattr(self._cfg, "project_root", None)
            if not project_root:
                return
            from mini_agent.storage.paths import AgentPaths
            from mini_agent.llm import call_stats

            entry = self._pool.current_entry if self._pool else None
            provider = entry.config.provider if entry else getattr(self._cfg, "llm_provider", "")
            model = entry.config.model if entry else getattr(self._cfg, "model", "")
            call_stats.record_call(
                AgentPaths(project_root),
                provider=provider or "", model=model or "",
                input_tokens=getattr(usage, "input_tokens", 0) if usage else 0,
                output_tokens=getattr(usage, "output_tokens", 0) if usage else 0,
                duration_ms=duration_ms, outcome=outcome,
            )
        except Exception:
            pass

    @classmethod
    def from_config(cls, app_cfg: Any) -> "LLMHelper":
        """
        兜底构造：在没有活跃 Agent 实例可取（因而拿不到它的 client_pool）
        的场景下，从 AppConfig 现建一条单链 LLMClientPool。

        优先用 agent.llm_helper（跟随 /model 实时切换）；只有确实拿不到
        agent 引用时（如独立工具函数、无 agent 的后台任务）才用这个。
        """
        from .client_pool import LLMClientPool
        return cls(LLMClientPool.from_config(app_cfg), app_cfg)

    # ── 后台轻量调用的预算（tick_dispatch_only_execution_model_plan.md §6.6）──────────

    def background_kwargs(self) -> dict:
        """后台轻量判定调用（declare_paths / goal_relevance / novelty_judge 等）专用的
        ``ask()/chat()`` 关键字参数：``timeout`` / ``deadline`` / ``max_retries``。

        取值来自 ``AppConfig.retry.background_call_timeout_seconds``（默认 30）与
        ``background_call_max_retries``（默认 1）；``deadline`` 取
        ``timeout × (max_retries + 1)``，即“每次尝试最多 timeout 秒、最多 max_retries 次重试”
        的理论上限，再不含退避以外的任何额外等待。Agent 主对话与 step 执行不使用本方法。
        配置读不到时回退到默认值而不是关闭预算——后台调用宁可被截断也不该无限占用线程。
        回退开关：``background_call_timeout_seconds`` 显式设为 0（或负数）= 关闭后台预算，
        返回空 dict，``ask_background`` 退化为不带任何预算的普通 ``ask()``（旧行为）。
        """
        retry_cfg = getattr(self._cfg, "retry", None)
        raw_timeout = getattr(retry_cfg, "background_call_timeout_seconds", 30.0)
        if raw_timeout is None:
            raw_timeout = 30.0
        timeout = float(raw_timeout)
        if timeout <= 0:
            return {}
        retries = int(getattr(retry_cfg, "background_call_max_retries", 1))
        retries = max(0, retries)
        return {
            "timeout": timeout,
            "deadline": timeout * (retries + 1),
            "max_retries": retries,
        }

    # ── 便捷入口：单轮、无工具、只要文本 ──────────────────────────────────────

    def ask(
        self,
        prompt: str,
        *,
        system: str = "",
        max_retries: int = 3,
        retry_policy: Optional[RetryPolicy] = None,
        override_model: Optional[str] = None,
        override_provider: Optional[str] = None,
        override_temperature: Optional[float] = None,
        timeout: Optional[float] = None,
        deadline: Optional[float] = None,
    ) -> str:
        """
        最常见场景：单轮 user 消息、无工具、只要最终文本。

        timeout:  [tick_dispatch_only_execution_model_plan.md 阶段四] 单次请求超时上限（秒），
                  只会把 provider 配置的超时压得更小，不会放宽；None = 沿用配置。
        deadline: 本次调用的总墙钟上限（秒，含重试、退避、fallback、排队）；
                  None = 不限（旧行为）。后台轻量判定调用请用 ``background_kwargs()``。

        调用失败（重试预算耗尽后仍异常）时向上抛出 LLMError，
        由调用方决定是否要捕获降级——不同调用点的降级语义不一样
        （有的返回空串，有的返回 None/[]），不适合在这里统一吞掉。
        """
        resp = self.chat(
            messages=[{"role": "user", "content": prompt}],
            system=system,
            tools=None,
            max_retries=max_retries,
            retry_policy=retry_policy,
            override_model=override_model,
            override_provider=override_provider,
            override_temperature=override_temperature,
            timeout=timeout,
            deadline=deadline,
        )
        return (resp.text or "").strip()

    # ── 完整入口 ──────────────────────────────────────────────────────────────

    def chat(
        self,
        messages: list[dict],
        system: str = "",
        tools: Optional[list[ToolSchema]] = None,
        *,
        max_retries: int = 3,
        retry_policy: Optional[RetryPolicy] = None,
        override_model: Optional[str] = None,
        override_provider: Optional[str] = None,
        override_temperature: Optional[float] = None,
        timeout: Optional[float] = None,
        deadline: Optional[float] = None,
    ) -> LLMResponse:
        # [tick_dispatch_only_execution_model_plan.md 阶段四] 带 timeout/deadline 时整个调用
        # （含 pool fallback / 多轮 / 重试 / 并发槽位排队）都在同一个 deadline 作用域内，
        # 预算共享而不是每个 entry 各算一份。两者都为 None 时完全是旧路径。
        if (timeout is not None and timeout > 0) or (deadline is not None and deadline > 0):
            with _deadline.deadline_scope(total_seconds=deadline, request_timeout=timeout):
                return self._chat_impl(
                    messages, system, tools,
                    max_retries=max_retries, retry_policy=retry_policy,
                    override_model=override_model, override_provider=override_provider,
                    override_temperature=override_temperature,
                )
        return self._chat_impl(
            messages, system, tools,
            max_retries=max_retries, retry_policy=retry_policy,
            override_model=override_model, override_provider=override_provider,
            override_temperature=override_temperature,
        )

    def _chat_impl(
        self,
        messages: list[dict],
        system: str = "",
        tools: Optional[list[ToolSchema]] = None,
        *,
        max_retries: int = 3,
        retry_policy: Optional[RetryPolicy] = None,
        override_model: Optional[str] = None,
        override_provider: Optional[str] = None,
        override_temperature: Optional[float] = None,
    ) -> LLMResponse:
        policy = retry_policy or RetryPolicy(
            max_retries=max_retries,
            conditions=[EmptyOutputCondition()],
            retry_on_exception=True,
        )
        tools = tools or []

        if override_model is not None or override_provider is not None or override_temperature is not None:
            return self._chat_override(
                messages, system, tools, policy,
                override_model=override_model,
                override_provider=override_provider,
                override_temperature=override_temperature,
            )

        _call_started_at = self._now()
        try:
            response = self._pool.call_with_pool(
                call_fn=lambda client: client.chat(messages, system, tools),
                retry_policy=policy,
            )
        except Exception:
            self._record_call_stat(outcome="error", duration_ms=self._elapsed_ms(_call_started_at))
            raise
        self._record_call_stat(
            outcome="success", duration_ms=self._elapsed_ms(_call_started_at), usage=response.usage,
        )
        return response

    # ── override 分支：临时构造独立 client，不经过 fallback chain ─────────────

    def _chat_override(
        self,
        messages: list[dict],
        system: str,
        tools: list[ToolSchema],
        policy: RetryPolicy,
        *,
        override_model: Optional[str],
        override_provider: Optional[str],
        override_temperature: Optional[float],
    ) -> LLMResponse:
        from .factory import create_client

        base_cfg = LLMConfig.from_app_config(self._cfg)
        llm_cfg = LLMConfig(
            provider=override_provider or base_cfg.provider,
            model=override_model or base_cfg.model,
            api_key=base_cfg.api_key,
            base_url=base_cfg.base_url,
            max_tokens=base_cfg.max_tokens,
            temperature=base_cfg.temperature if override_temperature is None else override_temperature,
            timeout=base_cfg.timeout,
            requires_api_key=base_cfg.requires_api_key,
            use_system_tool_call=base_cfg.use_system_tool_call,
            system_message_format=base_cfg.system_message_format,
        )
        client = create_client(llm_cfg)
        return policy.call_with_retry(
            call_fn=lambda: client.chat(messages, system, tools),
        )


def ask_background(llm_helper: Any, prompt: str, **kwargs: Any) -> str:
    """后台轻量判定调用的统一入口（tick_dispatch_only_execution_model_plan.md §6.6）。

    若 ``llm_helper`` 是真正的 ``LLMHelper``（有 ``background_kwargs()``），自动套用
    ``retry.background_call_*`` 预算（单次超时 / 重试次数 / 总 deadline）；否则
    （测试替身、仅实现 ``ask(prompt)`` 的鸭子类型）原样透传，保持兼容。
    调用方显式传入的同名参数优先于默认预算。
    """
    getter = getattr(llm_helper, "background_kwargs", None)
    if callable(getter):
        try:
            merged = dict(getter())
        except Exception:
            merged = {}
        merged.update(kwargs)
        return llm_helper.ask(prompt, **merged)
    return llm_helper.ask(prompt, **kwargs)

