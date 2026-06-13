"""
context_builder.py — System prompt 组装器

职责：将所有上下文来源（skill、memory、project snapshot、plan 等）
组合成最终的 system prompt 字符串。

从 Agent 中拆出，Agent 只需持有一个 ContextBuilder 实例并调用 build()。

修复（v2）：
  1. 记忆检索缓存：每次 run_turn 开始时调用 refresh_turn_context(query) 缓存检索结果，
     整个 turn 内（多次 LLM 调用）复用，避免对同一 query 重复遍历所有记忆条目。
  2. Skill 目录缓存：只在 skill 集合变化时重建目录字符串，而非每次 build() 都重新生成。
  3. 新增 clear_turn_cache() 供 run_turn 结束时清理。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.config import AppConfig
    from mini_agent.skills import SkillLoader
    from mini_agent.perception.memory_store import MemoryStore


class ContextBuilder:
    """
    组装 system prompt。

    所有上下文来源都在这里汇聚：
    - 基础 system prompt（由 config.build_system_prompt 生成）
    - Skill 目录 + 使用追踪约定
    - 项目结构快照（ProjectScanner）
    - 跨 session 长期记忆（project + global 两级）
    """

    def __init__(
        self,
        cfg: "AppConfig",
        skill_loader: Optional["SkillLoader"] = None,
        memory: Optional["MemoryStore"] = None,
        global_memory: Optional["MemoryStore"] = None,
        project_snapshot_getter=None,   # Callable[[], Optional[str]]
        profile_text_getter=None,       # Callable[[], str]
    ) -> None:
        self.cfg = cfg
        self.skill_loader = skill_loader
        self.memory = memory
        self.global_memory = global_memory
        self._project_snapshot_getter = project_snapshot_getter
        self._profile_text_getter = profile_text_getter

        # ── Turn 级缓存 ──────────────────────────────────────────────────────
        # 每次 run_turn 开始时由 refresh_turn_context() 填充，
        # 整个 turn 内的多次 LLM 调用共享，不重复检索。
        self._cached_memory_snippet: str = ""
        self._cached_turn_query: str = ""

        # ── Skill 目录缓存 ───────────────────────────────────────────────────
        # 只在 skill 集合变化时重建，避免每次 build() 重新生成字符串。
        self._cached_skill_dir: str = ""
        self._cached_skill_dir_key: tuple = ()   # (frozenset(active), frozenset(available))

    # ── Turn 生命周期 API ─────────────────────────────────────────────────────

    def refresh_turn_context(self, query: str) -> None:
        """
        在每次 run_turn 开始时调用，缓存当前 turn 的记忆检索结果。
        整个 turn 内的多次 _call_llm() 会复用这份缓存，不重复检索。

        使用 merge_search 合并 project + global 两级记忆（与 agent.py 原逻辑对齐）。

        Args:
            query: 当前用户消息（用于记忆检索）
        """
        self._cached_turn_query = query
        self._cached_memory_snippet = ""

        if self.memory and query:
            try:
                from mini_agent.perception.memory_factory import merge_search
                memories = merge_search(
                    self.memory, self.global_memory, query,
                    k=self.cfg.memory_top_k,
                )
            except Exception:
                memories = self.memory.search(query, k=self.cfg.memory_top_k)
            if memories:
                snippets = "\n".join(
                    f"- [{m.session_id[:6]}] {m.summary}"
                    for m in memories
                )
                self._cached_memory_snippet = (
                    f"\n\n## Relevant past experience\n{snippets}"
                )

    def clear_turn_cache(self) -> None:
        """在 run_turn 结束时调用，清理 turn 级缓存。"""
        self._cached_memory_snippet = ""
        self._cached_turn_query = ""

    # ── 组装 ──────────────────────────────────────────────────────────────────

    def build(self, history: list[dict]) -> str:
        """
        根据当前 history 组装完整 system prompt。

        记忆检索结果来自 turn 级缓存（由 refresh_turn_context 预填充），
        不在此处执行检索，避免每次 LLM 调用都重复遍历记忆条目。

        Args:
            history: agent 当前的对话历史（用于 skill chunking）
        """
        cfg = self.cfg
        active = self.skill_loader.active if self.skill_loader else []

        # ── Skill 上下文 ──────────────────────────────────────────────────
        if cfg.skill_chunking_enabled and self.skill_loader and history:
            last_user = _last_user_msg(history)
            skill_ctx = self.skill_loader.build_context(query=last_user)
        else:
            skill_ctx = self.skill_loader.build_context() if self.skill_loader else ""

        from mini_agent.config import build_system_prompt
        user_profile = self._profile_text_getter() if self._profile_text_getter else ""
        base = build_system_prompt(cfg, active, skill_context=skill_ctx, user_profile=user_profile)

        # ── Skill 目录注入（带缓存）────────────────────────────────────────
        if self.skill_loader and self.skill_loader.available:
            base += "\n\n" + self._get_skill_directory()

        # ── 项目结构快照 ──────────────────────────────────────────────────
        snapshot = (
            self._project_snapshot_getter()
            if self._project_snapshot_getter is not None
            else None
        )
        if snapshot:
            base += "\n\n" + snapshot

        # ── 长期记忆（使用 turn 级缓存，不重复检索）──────────────────────
        if self._cached_memory_snippet:
            base += self._cached_memory_snippet
        elif self.memory and history and not self._cached_turn_query:
            # 兜底：若 refresh_turn_context 未被调用（如直接调用 build），
            # 仍执行一次检索，但只做一次。
            last_user = _last_user_msg(history)
            if last_user:
                memories = self.memory.search(last_user, k=cfg.memory_top_k)
                if memories:
                    snippets = "\n".join(
                        f"- [{m.session_id[:6]}] {m.summary}"
                        for m in memories
                    )
                    base += f"\n\n## Relevant past experience\n{snippets}"

        return base

    # ── 内部辅助 ──────────────────────────────────────────────────────────────

    def _get_skill_directory(self) -> str:
        """
        获取 skill 目录块，带缓存：只在 skill 集合变化时重建。
        """
        if not self.skill_loader:
            return ""
        catalog = self.skill_loader.get_catalog()
        active_names = frozenset(s["name"] for s in catalog if s["active"])
        avail_names  = frozenset(s["name"] for s in catalog)
        key = (active_names, avail_names)
        if key != self._cached_skill_dir_key:
            self._cached_skill_dir = self._build_skill_directory(catalog)
            self._cached_skill_dir_key = key
        return self._cached_skill_dir

    def _build_skill_directory(self, catalog: list[dict]) -> str:
        """构建 skill 目录块（注入可用 skill 列表 + 使用追踪约定）。"""
        inactive = [s for s in catalog if not s["active"]]
        active_catalog = [s for s in catalog if s["active"]]

        lines = ["## Available Skills (Tool-Managed)\n"]
        lines.append(
            "You can call `skill_list`, `skill_activate`, `skill_deactivate` tools "
            "to manage skills dynamically.\n"
        )

        if active_catalog:
            lines.append("**Currently active:**")
            for s in active_catalog:
                lines.append(f"  - `{s['name']}`: {s['description']}")

        if inactive:
            lines.append("\n**Available (not yet loaded):**")
            for s in inactive:
                lines.append(f"  - `{s['name']}`: {s['description']}")

        lines.append(
            "\n> Activate a skill when its domain is relevant to the current task. "
            "Deactivate it once the task phase is complete to keep context lean."
        )

        if active_catalog:
            active_names = [s["name"] for s in active_catalog]
            lines.append(
                f"\n**Skill usage tracking:** When your response draws on guidance "
                f"from one or more active skills ({', '.join(active_names)}), "
                f"append a `<skill_used>name</skill_used>` tag at the very end of your reply "
                f"(after all content). Use comma-separation for multiple skills: "
                f"`<skill_used>docx,pdf</skill_used>`. "
                f"Only declare skills whose guidance you actually applied — "
                f"not every skill that is merely loaded."
            )

        return "\n".join(lines)


def _last_user_msg(history: list[dict]) -> str:
    """从历史中提取最近一条用户文本消息。"""
    for m in reversed(history):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            return m["content"]
    return ""