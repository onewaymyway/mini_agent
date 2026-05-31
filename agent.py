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
from session import SessionManager, Session
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
        # Session 持久化
        self._session_mgr: Optional[SessionManager] = None
        self._session: Optional[Session] = None
        self._init_session()

    # ── Session 管理 ──────────────────────────────────────────────────────────────

    def _init_session(self) -> None:
        """初始化 SessionManager，创建新 Session（尚未写文件）。"""
        if not getattr(self.cfg, "auto_save_session", True):
            return
        try:
            session_dir = getattr(self.cfg, "session_dir", None)
            if session_dir is None:
                session_dir = self.cfg.project_root / "sessions"
            fmt = getattr(self.cfg, "session_fmt", "json")
            self._session_mgr = SessionManager(session_dir=session_dir, fmt=fmt)
            self._session = self._session_mgr.new_session(
                provider=getattr(self.cfg, "llm_provider", "unknown"),
                model=self.cfg.model,
            )
        except Exception as e:
            R.print_warning(f"Session init failed: {e}")

    def save_session(self) -> Optional[str]:
        """保存当前对话历史到 Session 文件，返回路径，失败返回 None。"""
        if not self._session_mgr or not self._session:
            return None
        try:
            stats = {
                "turns":         self.stats.turns,
                "input_tokens":  self.stats.input_tokens,
                "output_tokens": self.stats.output_tokens,
                "tool_calls":    self.stats.tool_calls,
            }
            path = self._session_mgr.save(
                self._session,
                history=self._history,
                stats=stats,
            )
            return str(path)
        except Exception as e:
            R.print_warning(f"Session save failed: {e}")
            return None

    def load_session(self, session_id: str) -> bool:
        """按 session_id（或其前缀）加载历史到当前 agent，返回是否成功。"""
        if not self._session_mgr:
            return False
        session = self._session_mgr.load(session_id)
        if session is None:
            return False
        self._session = session
        self._history = list(session.history)
        self.stats.turns         = session.stats.get("turns", 0)
        self.stats.input_tokens  = session.stats.get("input_tokens", 0)
        self.stats.output_tokens = session.stats.get("output_tokens", 0)
        self.stats.tool_calls    = session.stats.get("tool_calls", 0)
        return True

    @property
    def session_id(self) -> Optional[str]:
        return self._session.id if self._session else None

    @property
    def session_file(self) -> Optional[str]:
        return self._session.file_path if self._session else None

    @property
    def session_manager(self) -> Optional[SessionManager]:
        return self._session_mgr

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
        try:
            if self.skill_loader:
                newly = self.skill_loader.auto_activate(user_message)
                for name in newly:
                    R.print_skill_loaded(name)

            self._history.append({"role": "user", "content": user_message})
            self.stats.turns += 1

            return self._agentic_loop()
        finally:
            # 每轮对话后自动保存 session
            if getattr(self.cfg, 'auto_save_session', True) and self._history:
                self.save_session()

    # ── Agentic loop ───────────────────────────────────────────────────────────

    def _agentic_loop(self) -> str:
        """Keep calling the LLM until it produces a final text response (no tool calls)."""
        final_text = ""
        loop_count = 0

        while loop_count < self.cfg.max_turns:
            loop_count += 1
            response = self._call_llm()
            final_text = response.text
            self.stats.input_tokens += response.usage.input_tokens
            self.stats.output_tokens += response.usage.output_tokens

            # 将 LLMResponse 写入对话历史（provider 无关格式）
            self._append_assistant_response(response)

            if not response.has_tool_calls:
                break

            # 执行工具调用，结果写回历史
            tool_results, result_strs = self._execute_tools(response)
            self._history.append(
                self._build_tool_result_message(response.tool_calls, result_strs)
            )

        if loop_count >= self.cfg.max_turns:
            R.print_warning(f"Reached max turns ({self.cfg.max_turns}).")

        return final_text

    # ── LLM 调用 ───────────────────────────────────────────────────────────────

    def _call_llm(self) -> LLMResponse:
        """调用 LLMClient，根据 cfg.stream 选择流式或非流式。"""
        system = self._build_system()
        tools = self._build_tool_schemas()

        # 思维链回调：对支持 on_reasoning 参数的 provider（如 NVIDIA）启用流式 reasoning
        import inspect as _inspect
        _stream_sig = _inspect.signature(self._llm.stream)
        _supports_on_reasoning = "on_reasoning" in _stream_sig.parameters

        # 流式 reasoning 开关：已有内容时显示 header
        _reasoning_started = [False]

        def _on_reasoning(token: str) -> None:
            if not _reasoning_started[0]:
                R.print_reasoning_header()
                _reasoning_started[0] = True
            R.print_reasoning(token)

        # 转换消息：将 tool_use 类型转换为 text 类型（用于不支持 tool_use 的模型）
        from llm.system_tool_call import convert_tool_use_to_text
        messages_for_llm = convert_tool_use_to_text(self._history)

        try:
            if self.cfg.stream:
                R.print_assistant_prefix(agent_name=self.cfg.agent_name)
                writer = R.StreamWriter()
                stream_kwargs: dict = dict(
                    messages=messages_for_llm,
                    system=system,
                    tools=tools,
                    on_token=writer.write,
                )
                if _supports_on_reasoning:
                    stream_kwargs["on_reasoning"] = _on_reasoning
                response = self._llm.stream(**stream_kwargs)
                # postprocess 已提取 <thinking> 块，非流式 reasoning 在这里显示
                if not _reasoning_started[0] and response.reasoning:
                    R.print_reasoning_header()
                    R.console.print(response.reasoning, style="dim")
                if _reasoning_started[0] or response.reasoning:
                    R.print_reasoning_footer()
                writer.flush()
            else:
                response = self._llm.chat(
                    messages=messages_for_llm,
                    system=system,
                    tools=tools,
                )
                # postprocess 已提取 <thinking> 块，统一在此显示
                if response.reasoning:
                    R.print_reasoning_header()
                    R.console.print(response.reasoning, style="dim")
                    R.print_reasoning_footer()
                if response.text:
                    R.print_assistant_prefix(agent_name=self.cfg.agent_name)
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

    def _execute_tools(self, response: LLMResponse) -> tuple[list, list[str]]:
        """运行所有工具调用，返回 (tool_calls列表, result字符串列表)。"""
        result_strs: list[str] = []

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

            result_strs.append(result_str)

        return response.tool_calls, result_strs

    def _build_tool_result_message(self, tool_calls, results: list[str]) -> dict:
        """
        构造回注工具结果的 user 消息。
        统一使用 <tool_result> 文本格式（与 tool_call_protocol.md 对应）。
        """
        from llm.system_tool_call import render_tool_results
        content = render_tool_results(tool_calls, results)
        return {"role": "user", "content": content}

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
