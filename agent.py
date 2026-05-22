"""
Agent loop.
Orchestrates the conversation with Claude, dispatches tool calls,
manages history, and streams responses.

与具体 LLM 的交互全部通过 llm.LLMClient 接口进行，
切换 provider 只需修改 LLMConfig.provider，不改动 Agent 代码。
"""

from __future__ import annotations

from typing import Optional

from config import AppConfig, SessionStats, build_system_prompt
from llm import (
    LLMClient, LLMConfig, LLMResponse, ToolSchema,
    create_client, LLMError,
)
from permissions import PermissionGuard
from skills import SkillLoader
from tools import ToolRegistry, get_default_registry
import renderer as R


class Agent:
    """
    Stateful agent that maintains conversation history and runs the agentic loop.

    Typical usage:
        agent = Agent(cfg, registry, skill_loader, guard)
        agent.run_turn("Fix the bug in app.py")
    """

    def __init__(
        self,
        cfg: AppConfig,
        registry: Optional[ToolRegistry] = None,
        skill_loader: Optional[SkillLoader] = None,
        guard: Optional[PermissionGuard] = None,
        llm_client: Optional[LLMClient] = None,
    ) -> None:
        self.cfg = cfg
        self.registry = registry or get_default_registry()
        self.skill_loader = skill_loader
        self.guard = guard or PermissionGuard(
            auto_approve=cfg.auto_approve,
            sandbox=cfg.sandbox,
            project_root=cfg.project_root,
        )
        self.stats = SessionStats()
        self._history: list[dict] = []
        # LLMClient 可从外部注入（便于测试），否则从 AppConfig 自动创建
        self._llm: LLMClient = llm_client or create_client(
            LLMConfig.from_app_config(cfg)
        )

    # ── Public interface ───────────────────────────────────────────────────────

    @property
    def history(self) -> list[dict]:
        return list(self._history)

    @property
    def llm_client(self) -> LLMClient:
        return self._llm

    def clear_history(self) -> None:
        self._history.clear()

    def switch_provider(self, llm_config: LLMConfig) -> None:
        """
        运行时切换 LLM provider，不影响对话历史。

        Example:
            agent.switch_provider(LLMConfig(provider="openai", model="gpt-4o", api_key="..."))
        """
        self._llm = create_client(llm_config)
        R.print_info(f"Switched to {self._llm}")

    def run_turn(self, user_message: str) -> str:
        """
        Run one user turn. May make multiple API calls (tool loops).
        Returns the final assistant text.
        """
        if self.skill_loader:
            newly = self.skill_loader.auto_activate(user_message)
            for name in newly:
                R.print_skill_loaded(name)

        self._history.append({"role": "user", "content": user_message})
        self.stats.turns += 1

        return self._agentic_loop()

    # ── Agentic loop ───────────────────────────────────────────────────────────

    def _agentic_loop(self) -> str:
        """Keep calling the LLM until it produces a final text response (no tool calls)."""
        final_text = ""
        loop_count = 0

        while loop_count < self.cfg.max_turns:
            loop_count += 1
            response = self._call_llm()
            # print("response:",response)
            final_text = response.text
            self.stats.input_tokens += response.usage.input_tokens
            self.stats.output_tokens += response.usage.output_tokens

            # 将 LLMResponse 写入对话历史（provider 无关格式）
            self._append_assistant_response(response)

            if not response.has_tool_calls:
                break

            # 执行工具调用，结果写回历史
            tool_results = self._execute_tools(response)
            self._history.append({"role": "user", "content": tool_results})

        if loop_count >= self.cfg.max_turns:
            R.print_warning(f"Reached max turns ({self.cfg.max_turns}).")

        return final_text

    # ── LLM 调用 ───────────────────────────────────────────────────────────────

    def _call_llm(self) -> LLMResponse:
        """调用 LLMClient，根据 cfg.stream 选择流式或非流式。"""
        system = self._build_system()
        tools = self._build_tool_schemas()

        # 思维链回调：仅对支持 on_reasoning 参数的 provider 传入
        import inspect as _inspect
        _stream_sig = _inspect.signature(self._llm.stream)
        _supports_reasoning = "on_reasoning" in _stream_sig.parameters

        def _on_reasoning(token: str) -> None:
            R.print_reasoning(token)

        try:
            if self.cfg.stream:
                R.print_assistant_prefix()
                writer = R.StreamWriter()
                stream_kwargs: dict = dict(
                    messages=self._history,
                    system=system,
                    tools=tools,
                    on_token=writer.write,
                )
                if _supports_reasoning:
                    R.print_reasoning_header()
                    stream_kwargs["on_reasoning"] = _on_reasoning
                # print(self._llm)
                response = self._llm.stream(**stream_kwargs)
                if _supports_reasoning and response.reasoning:
                    R.print_reasoning_footer()
                writer.flush()
            else:
                chat_kwargs: dict = dict(
                    messages=self._history,
                    system=system,
                    tools=tools,
                )
                response = self._llm.chat(**chat_kwargs)
                # 非流式时若有推理内容，先渲染思维链再渲染正文
                if response.reasoning:
                    R.print_reasoning_header()
                    console_dim = __import__("rich.console", fromlist=["Console"]).Console()
                    console_dim.print(response.reasoning, style="dim")
                    R.print_reasoning_footer()
                if response.text:
                    R.print_assistant_prefix()
                    R.print_markdown(response.text)
        except LLMError:
            raise
        except Exception as e:
            from llm import LLMProviderError
            raise LLMProviderError(f"Unexpected LLM error: {e}") from e

        return response

    # ── History management ─────────────────────────────────────────────────────

    def _append_assistant_response(self, response: LLMResponse) -> None:
        """
        将 LLMResponse 转换为对话历史条目。
        使用 provider 无关的通用格式（Anthropic/OpenAI 均可接受）。
        """
        content: list[dict] = []
        if response.text:
            content.append({"type": "text", "text": response.text})
        for tc in response.tool_calls:
            content.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "input": tc.input,
            })
        self._history.append({"role": "assistant", "content": content})

    # ── Tool execution ─────────────────────────────────────────────────────────

    def _execute_tools(self, response: LLMResponse) -> list[dict]:
        """运行所有工具调用，返回 tool_result 内容块列表。"""
        results: list[dict] = []

        for tc in response.tool_calls:
            R.print_tool_call(tc.name, tc.input, verbose=self.cfg.verbose)
            self.stats.tool_calls += 1

            allowed = self.guard.check(tc.name, tc.input)
            if not allowed:
                result_str = "[Tool call denied by user]"
                R.print_tool_error(tc.name, "denied by user")
            else:
                try:
                    result = self.registry.call(tc.name, tc.input)
                    result_str = str(result) if not isinstance(result, str) else result
                    R.print_tool_result(tc.name, result_str)
                except Exception as e:
                    result_str = f"[tool error: {e}]"
                    R.print_tool_error(tc.name, str(e))

            results.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": result_str,
            })

        return results

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _build_system(self) -> str:
        active = self.skill_loader.active if self.skill_loader else []
        skill_ctx = self.skill_loader.build_context() if self.skill_loader else ""
        return build_system_prompt(self.cfg, active, skill_context=skill_ctx)

    def _build_tool_schemas(self) -> list[ToolSchema]:
        """将 ToolRegistry 的工具定义转换为 provider 无关的 ToolSchema 列表。"""
        return [
            ToolSchema(
                name=td.name,
                description=td.description,
                input_schema=td.input_schema,
            )
            for td in (self.registry.get(n) for n in self.registry.names)
            if td is not None
        ]
