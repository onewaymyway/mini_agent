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
    - Workdir 知识层（W2，4.6）：project.json 身份信息 / active WorkThread
      进度 / 高优先级 open_threads，均为 always-on 注入
    - Global 知识层（W3，5.5）：self_profile.self_assessment 精简注入 /
      evolution_state.pending_evolve_branches，均 always-on；
      projects_index + activity_log 最近几条仅在 workdir 变化时注入
    """

    def __init__(
        self,
        cfg: "AppConfig",
        skill_loader: Optional["SkillLoader"] = None,
        memory: Optional["MemoryStore"] = None,
        global_memory: Optional["MemoryStore"] = None,
        project_snapshot_getter=None,   # Callable[[], Optional[str]]
        profile_text_getter=None,       # Callable[[], str]
        self_model_getter=None,         # Callable[[], Optional[str]]  [具身改进 C1]
        persona_getter=None,            # Callable[[], Optional[str]]  当前激活的 persona name
        notepad_getter=None,            # Callable[[], Optional[str]]  记事本渲染文本，每轮读取
    ) -> None:
        self.cfg = cfg
        self.skill_loader = skill_loader
        self.memory = memory
        self.global_memory = global_memory
        self._project_snapshot_getter = project_snapshot_getter
        self._profile_text_getter = profile_text_getter
        # [具身改进 C1] AgentSelfModel：session 级实时聚合视图（每轮读取）
        self._self_model_getter = self_model_getter
        # 角色扮演（Persona）系统：每轮读取当前激活的 persona name（可能为 None）
        self._persona_getter = persona_getter
        # 记事本：每轮读取当前 session 的记事本渲染文本（persist across compact）
        self._notepad_getter = notepad_getter

        # ── Turn 级缓存 ──────────────────────────────────────────────────────
        # 每次 run_turn 开始时由 refresh_turn_context() 填充，
        # 整个 turn 内的多次 LLM 调用共享，不重复检索。
        self._cached_memory_snippet: str = ""
        self._cached_turn_query: str = ""
        # 改进5：记录本 turn 实际注入到上下文里的记忆 entry_id，供
        # correction_detector 检测到人类纠正时定位"刚才用到的知识是不是过时了"
        # （agent.py::_detect_and_record_correction 会读取这个列表）。
        self.last_injected_memory_ids: list[str] = []

        # ── Skill 目录缓存 ───────────────────────────────────────────────────
        # 只在 skill 集合变化时重建，避免每次 build() 重新生成字符串。
        self._cached_skill_dir: str = ""
        self._cached_skill_dir_key: tuple = ()   # (frozenset(active), frozenset(available))

        # ── Global 知识层（W3，5.5）：workdir 切换检测 ───────────────────────
        # 记录上一次 build() 时的 project_root，用于判断"projects_index +
        # activity_log 最近几条"是否需要注入（8.4 节表格：仅在 workdir 变化
        # 时注入，不是每次 build() 都重复注入，否则会持续占用 context）。
        # 初始为 None：进程内第一次 build() 视为"刚切换到当前 workdir"，
        # 因此也会注入一次（与"agent 启动时看到自己上次在哪干了什么"的
        # 设计意图一致，不需要特殊处理第一次的情况）。
        self._last_seen_project_root: Optional[str] = None

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
            memories = None
            # 图书馆式两步检索：先定位书架（分类号），再只在书架范围内精排。
            # 只对 project 级记忆生效（global 记忆的分类体系是另一棵独立的树，
            # merge_search 本身已经做了两级合并，这里不重复处理 global 侧）。
            if getattr(self.cfg.memory, "library_shelf_search_enabled", True):
                library = getattr(self.memory, "library", None)
                if library is not None:
                    try:
                        memories = library.shelf_search(
                            self.memory, query, k=self.cfg.memory_top_k
                        )
                    except Exception:
                        memories = None
            if not memories:
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
                self.last_injected_memory_ids = [
                    m.entry_id for m in memories if getattr(m, "entry_id", None)
                ]
            else:
                self.last_injected_memory_ids = []

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

        # ── 角色扮演（Persona）注入 ──────────────────────────────────────────
        # 单独成段，不与 skill/tool 使用规范混排：便于 /role exit 时整段摘除，
        # 也便于 /debug system 直接定位查看。安全边界声明由
        # render_persona_prompt() 强制追加，不受 persona 文件内容影响。
        persona_name = self._persona_getter() if self._persona_getter else None
        if persona_name:
            try:
                from mini_agent.orchestrator.persona_profiles import (
                    get_persona_loader, render_persona_prompt,
                )
                loader = get_persona_loader()
                persona = loader.get(persona_name) if loader else None
                if persona is not None:
                    base += "\n\n" + render_persona_prompt(persona)
            except Exception:
                pass  # persona 系统失败不应阻断 system prompt 组装

        # ── Skill 目录注入（带缓存）────────────────────────────────────────
        if self.skill_loader and self.skill_loader.available:
            base += "\n\n" + self._get_skill_directory()

        # ── 记事本（persist across compact）──────────────────────────────
        # 固定位置注入，每次 build() 都重新读取最新记事本内容，天然不受
        # history compact 影响（system prompt 每轮都会重新组装）。
        if self._notepad_getter is not None:
            try:
                notepad_content = self._notepad_getter()
            except Exception:
                notepad_content = None
            if notepad_content is not None:
                from mini_agent.prompts import pm as _pm
                base += "\n\n" + _pm.render("system/notepad", notepad_content=notepad_content)

        # ── 项目结构快照 ──────────────────────────────────────────────────
        snapshot = (
            self._project_snapshot_getter()
            if self._project_snapshot_getter is not None
            else None
        )
        if snapshot:
            base += "\n\n" + snapshot

        # ── Workdir 知识层（W2，4.6）：身份信息 / active WorkThread / 高优先级 open_threads ──
        wk_block = self._build_workdir_knowledge_block()
        if wk_block:
            base += "\n\n" + wk_block

        # ── Global 知识层（W3，5.5）：self_assessment / pending_evolve_branches
        #    always-on；projects_index + activity_log 仅在 workdir 变化时注入 ──
        gk_block = self._build_global_knowledge_block()
        if gk_block:
            base += "\n\n" + gk_block

        # ── AgentSelfModel（具身改进 C1）：session 级实时聚合视图 ─────────
        # 注入在 global knowledge 块（SelfAssessment 跨 session 历史评估）
        # 之后，补充 SelfAssessment 没有的实时维度：
        #   当前 workdir 能力分布 / 当前余裕摘要 / 当前内部感受
        if self._self_model_getter is not None:
            try:
                sm_fragment = self._self_model_getter()
                if sm_fragment:
                    base += "\n\n" + sm_fragment
            except Exception:
                pass  # 感知层失败不阻断 system prompt 组装

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
                    self.last_injected_memory_ids = [
                        m.entry_id for m in memories if getattr(m, "entry_id", None)
                    ]

        return base

    # ── 内部辅助 ──────────────────────────────────────────────────────────────

    def _build_workdir_knowledge_block(self) -> str:
        """
        组装 Workdir 知识层 always-on 注入块（设计文档 8.4 节）：
          - project.json 身份信息
          - work_index 里 status=active 的 WorkThread 的 cumulative_progress + next_suggested
          - open_threads 里 priority=high 的条目（限制最多 N 条）

        三部分均为纯本地小文件读取（无 LLM、无网络），开销可忽略，因此不做
        额外的跨 build() 调用缓存——每个 turn 调用一次 build()，多读几次
        几 KB 的 JSON 文件不构成性能问题，换来的是"数据写入后立即在下一次
        system prompt 里可见"，不需要额外的缓存失效逻辑。

        cfg.workdir_knowledge_enabled=False 时整体跳过；任何单项读取失败
        都不应该影响其余两项或 system prompt 的其他部分。
        """
        if not getattr(self.cfg, "workdir_knowledge_enabled", True):
            return ""

        try:
            from mini_agent.storage.paths import AgentPaths
            from mini_agent.perception import workdir_knowledge as wk
        except Exception:
            return ""

        try:
            paths = AgentPaths(self.cfg.project_root)
        except Exception:
            return ""

        lines: list[str] = []

        # project.json 身份信息
        try:
            meta = wk.load_project_meta(paths)
            if meta is not None:
                block = meta.to_prompt_block()
                if block:
                    lines.append(block)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.context_builder')
            pass

        # active WorkThread 进度
        try:
            active_threads = wk.get_active_work_threads(paths)
        except Exception:
            active_threads = []
        if active_threads:
            thread_lines = ["## Active work threads (cross-session)"]
            for t in active_threads:
                entry = f"- **{t.title}** (`{t.id}`)"
                if t.cumulative_progress:
                    entry += f"\n  Progress so far: {t.cumulative_progress}"
                if t.next_suggested:
                    entry += f"\n  Suggested next step: {t.next_suggested}"
                if t.open_questions:
                    entry += f"\n  Open questions: {'; '.join(t.open_questions[:3])}"
                thread_lines.append(entry)
            lines.append("\n".join(thread_lines))

        # 高优先级 open_threads
        try:
            limit = getattr(self.cfg.workdir_knowledge, "open_threads_inject_limit", 5)
            high_priority = wk.get_high_priority_open_threads(paths, limit=limit)
        except Exception:
            high_priority = []
        if high_priority:
            ot_lines = ["## High-priority open threads"]
            for item in high_priority:
                ot_lines.append(f"- [{item.type}] {item.title} (discovered in {item.discovered_in})")
            lines.append("\n".join(ot_lines))

        return "\n\n".join(lines)

    def _build_global_knowledge_block(self) -> str:
        """
        组装 Global 知识层 context 注入块（设计文档 8.4 节）：
          - self_profile.self_assessment（always-on，精简注入）
          - evolution_state.pending_evolve_branches（always-on）
          - projects_index + activity_log 最近几条（仅在 workdir 变化时注入）

        "workdir 变化"判定：与上一次 build() 调用时记录的 project_root 比较——
        同一个 ContextBuilder 实例在整个 Agent 生命周期内通常对应同一个
        project_root（一个进程一个 workdir），因此对绝大多数 session 而言
        这一项只在第一次 build() 时注入一次；只有在未来支持"同进程内切换
        workdir"的场景下才会再次触发，目前代码里没有这种调用路径，但判定
        逻辑本身不依赖这个假设，按 cfg.project_root 实时比较，保持正确性。

        cfg.global_knowledge_enabled=False 时整体跳过；任何单项读取失败
        都不应该影响其余部分或 system prompt 的其他部分（与 Workdir 块
        的容错策略一致）。
        """
        if not getattr(self.cfg, "global_knowledge_enabled", True):
            return ""

        try:
            from mini_agent.storage.paths import AgentPaths
            from mini_agent.perception import global_knowledge as gk
        except Exception:
            return ""

        try:
            paths = AgentPaths(self.cfg.project_root)
        except Exception:
            return ""

        lines: list[str] = []

        # self_assessment（always-on，精简注入）+ pending_evolve_branches
        try:
            profile = gk.load_self_profile(paths)
        except Exception:
            profile = None
        if profile is not None:
            try:
                assessment_block = profile.self_assessment.to_prompt_block()
            except Exception:
                assessment_block = ""
            if assessment_block:
                lines.append(assessment_block)

            pending = profile.evolution_state.pending_evolve_branches
            if pending:
                lines.append(
                    "## Pending evolve branches (awaiting human review)\n"
                    + "\n".join(f"- {b}" for b in pending[:10])
                )

        # projects_index + activity_log 最近几条：仅在 workdir 变化时注入
        current_root = str(self.cfg.project_root.resolve()) if self.cfg.project_root else ""
        workdir_changed = current_root != self._last_seen_project_root
        if workdir_changed:
            try:
                limit = getattr(self.cfg.global_knowledge, "activity_log_inject_limit", 5)
                recent_activity = gk.load_recent_activity(paths, limit=limit)
            except Exception:
                recent_activity = []
            try:
                index = gk.load_projects_index(paths)
                total_projects = len(index.projects)
            except Exception:
                total_projects = 0

            if recent_activity or total_projects:
                switch_lines = ["## Recent cross-project activity (workdir just changed)"]
                if total_projects:
                    switch_lines.append(f"- You have worked on {total_projects} project(s) so far.")
                for rec in recent_activity:
                    theme = rec.get("theme") or "(no theme recorded)"
                    pid = rec.get("project_id", "")
                    switch_lines.append(f"- [{pid}] {theme}")
                lines.append("\n".join(switch_lines))

            self._last_seen_project_root = current_root

        return "\n\n".join(lines)


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
    """从历史中提取最近一条真实用户输入文本。

    使用 _type=user_input 精确识别，跳过 tool_result / skill_context /
    reminder 等注入条目（向后兼容：无 _type 时用 is_real_user_input 字符串前缀判断）。
    """
    from mini_agent.history.entry import is_real_user_input
    for m in reversed(history):
        if is_real_user_input(m) and isinstance(m.get("content"), str):
            return m["content"]
    return ""