"""
context_builder.py — System prompt 组装器

职责：将所有上下文来源（skill、memory、project snapshot、plan 等）
组合成最终的 system prompt 字符串。

从 Agent 中拆出，Agent 只需持有一个 ContextBuilder 实例并调用 build()。
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
    - 跨 session 长期记忆（MemoryStore）
    """

    def __init__(
        self,
        cfg: "AppConfig",
        skill_loader: Optional["SkillLoader"] = None,
        memory: Optional["MemoryStore"] = None,
        project_snapshot_getter=None,   # Callable[[], Optional[str]]
    ) -> None:
        self.cfg = cfg
        self.skill_loader = skill_loader
        self.memory = memory
        # 允许外部提供一个 getter 而不是直接传字符串，
        # 这样懒加载的 ProjectScanner 完成后 ContextBuilder 能自动获取最新结果
        self._project_snapshot_getter = project_snapshot_getter

    def build(self, history: list[dict]) -> str:
        """
        根据当前 history 组装完整 system prompt。

        Args:
            history: agent 当前的对话历史（用于提取最近用户消息，做 skill chunking 和 memory 搜索）
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
        base = build_system_prompt(cfg, active, skill_context=skill_ctx)

        # ── Skill 目录注入 ────────────────────────────────────────────────
        if self.skill_loader and self.skill_loader.available:
            base += "\n\n" + self._build_skill_directory()

        # ── 项目结构快照 ──────────────────────────────────────────────────
        snapshot = (
            self._project_snapshot_getter()
            if self._project_snapshot_getter is not None
            else None
        )
        if snapshot:
            base += "\n\n" + snapshot

        # ── 长期记忆 ──────────────────────────────────────────────────────
        if self.memory and history:
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

    def _build_skill_directory(self) -> str:
        """构建 skill 目录块（注入可用 skill 列表 + 使用追踪约定）。"""
        catalog = self.skill_loader.get_catalog()
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

        # 使用追踪约定（只在有激活 skill 时注入）
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
