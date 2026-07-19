"""
tools/workdir_knowledge.py — Workdir 知识层主动写入 + 检索工具（W2，Stage 4.3-4.5 +
检索侧补全）

对应 self_evolution_stage4plus_plan.md Stage 4.3/4.4/4.5：

  add_open_thread(title, type, priority, description, work_thread_ref)
      随时记录"任务途中发现但当时不便处理"的问题（4.4）。

  update_work_thread(thread_id, cumulative_progress, next_suggested, open_questions)
      供 agent 在长任务里主动维护 WorkThread（4.3，呼应 W1 update_task_progress
      的"主动写入"原则；新建 WorkThread 也走这个工具——thread_id 不存在时
      创建一条新记录，而不是另外开一个 create_work_thread 工具，理由见
      update_work_thread() 函数体注释）。

  update_knowledge(section, content)
      写入项目软知识 knowledge.md（4.5，T1，走 StateRepo.apply() 安全网）。

  search_knowledge(query, k, topic, include_content)
      检索侧补全：update_knowledge() 把 knowledge.md 写进去之后，原本没有
      任何工具能把它读出来——agent 只能靠自己想起"项目里有个 knowledge.md"
      然后用文件读取工具翻整份 Markdown，没有按相关性筛选的手段，导致积累
      的软知识在实践中几乎不会被后续 session 用上。这个工具对应设计文档
      8.4 节"knowledge.md 相关段落，按本次 session 意图检索后注入"那一项
      （此前只实现了 always-on 注入的几类，这一项一直空着）。检索基于
      knowledge_index.json 的 TF-IDF 关键词匹配（见
      perception/workdir_knowledge.py 的 search_knowledge_index()），
      不需要向量数据库。

设计取舍（与 tools/evolution.py 的 skill_propose 同构）：
  - 四个工具都是模块级 @tool 装饰器注册的无状态函数，没有直接 access 到
    调用它的 Agent 实例，需要通过 thread-local provider 读取"当前项目根目录"
    和"当前 session_id"——复用 Phase E（3.3）/ Phase C（3.1）已经建立的
    thread-local provider 模式（tools/orchestration.py 的
    set_active_skills_provider、tools/evolution.py 的
    set_project_root_provider 同款写法），而不是 fallback 到 Path.cwd()
    或空字符串。
  - add_open_thread / update_work_thread 直接调用
    perception/workdir_knowledge.py 的纯函数，不经过 StateRepo——这两个文件
    是"观察性数据"，定位与 task_manifest.json（W1）一致，不需要 git 历史。
  - update_knowledge 走 StateRepo.apply()，tier 固定 T1（不接受调用方传入
    tier，理由与 skill_propose 固定 T1 一致：这个动作本身的风险等级是
    确定的，不应该被 prompt injection 改变）。与 skill_propose 不同的是，
    knowledge.md 不属于"需要人工 merge 才生效"的提案——它是"认知积累"，
    直接 apply() 到当前 checkout 的分支（main/master），不开 evolve 分支、
    不用 EvolutionWorkspace。这与 cli/commands/evolution.py 里
    `StateRepo(agent.cfg.project_root)` 直接操作主仓库是同一个模式，
    区别于 skill_propose 那种"需要审核"的提案流程。
  - search_knowledge 是纯读取操作（不修改任何文件），requires_approval=False
    且不经过 StateRepo——和 add_open_thread / update_work_thread 一样，
    读取本身没有需要"可 git revert"的风险面。
"""

from __future__ import annotations

import json
import threading as _threading
from pathlib import Path
from typing import Callable, Optional

from . import tool

# ── 模块级"当前项目根目录" / "当前 session_id" 提供者 ─────────────────────
# thread-local，与 tools/evolution.py 的 set_project_root_provider 同款写法。

_project_root_local = _threading.local()
_session_id_local = _threading.local()


def set_project_root_provider(provider: Optional[Callable[[], Path]]) -> None:
    """由 Agent.__init__ 调用，为当前线程注册一个返回 cfg.project_root 的回调。

    与 tools/evolution.py 的同名函数职责相同，但各自维护独立的 thread-local
    存储（每个模块只负责自己工具需要的 provider，避免跨模块耦合）。
    Agent 侧需要同时调用两处注册——见 agent.py __init__ 中的接入点。
    """
    _project_root_local.provider = provider


def set_session_id_provider(provider: Optional[Callable[[], str]]) -> None:
    """由 Agent.__init__ 调用，为当前线程注册一个返回当前 session_id 的回调。

    用 lambda 懒读取（例如 `lambda: self._session.id if self._session else ""`），
    不在注册时固化值——session_id 在 Agent 生命周期内可能因 load_session() /
    new_session() 变化，工具调用时应该读到"当时"的 session_id。
    """
    _session_id_local.provider = provider


def _get_project_root() -> Optional[Path]:
    provider = getattr(_project_root_local, "provider", None)
    if provider is None:
        return None
    try:
        return provider()
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.tools.workdir_knowledge._get_project_root')
        return None


def _get_session_id() -> str:
    provider = getattr(_session_id_local, "provider", None)
    if provider is None:
        return ""
    try:
        return provider() or ""
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.tools.workdir_knowledge._get_session_id')
        return ""


def _error(msg: str) -> str:
    return json.dumps({"ok": False, "error": msg}, ensure_ascii=False)


# ════════════════════════════════════════════════════════════════════════════
# add_open_thread（4.4）
# ════════════════════════════════════════════════════════════════════════════

@tool(
    name="add_open_thread",
    description=(
        "Record a cross-session open item discovered while working — a bug, piece "
        "of tech debt, feature idea, open question, or blocker that you noticed but "
        "couldn't (or shouldn't) address right now. Stored in open_threads.json so "
        "future sessions (including yourself) can pick it up. Use this the moment "
        "you notice something worth tracking, rather than letting it get lost when "
        "the current task finishes."
    ),
    schema={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Short title for the open thread (shown in summaries).",
            },
            "type": {
                "type": "string",
                "enum": ["bug", "tech_debt", "feature", "question", "blocker"],
                "description": "Category of this open thread.",
            },
            "priority": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "How urgent this is. 'high' priority items get surfaced "
                                "automatically at the start of future sessions.",
            },
            "description": {
                "type": "string",
                "description": "Fuller description with enough context for a future "
                                "session (or yourself) to act on this without re-discovering it.",
            },
            "work_thread_ref": {
                "type": "string",
                "description": "Optional id of a related WorkThread (from work_index.json), "
                                "if this open thread belongs to a larger ongoing effort.",
            },
        },
        "required": ["title"],
    },
    requires_approval=False,
)
def add_open_thread(
    title: str,
    type: str = "question",
    priority: str = "medium",
    description: str = "",
    work_thread_ref: Optional[str] = None,
) -> str:
    project_root = _get_project_root()
    if project_root is None:
        return _error(
            "project_root provider not registered (add_open_thread must be called "
            "from within an Agent session)."
        )

    from mini_agent.storage.paths import AgentPaths
    from mini_agent.perception.workdir_knowledge import add_open_thread as _add

    paths = AgentPaths(project_root)
    item = _add(
        paths,
        title=title,
        discovered_in=_get_session_id(),
        type=type,
        priority=priority,
        description=description,
        work_thread_ref=work_thread_ref,
    )
    return json.dumps({"ok": True, "item": item.to_dict()}, ensure_ascii=False)


# ════════════════════════════════════════════════════════════════════════════
# update_work_thread（4.3）
# ════════════════════════════════════════════════════════════════════════════

@tool(
    name="update_work_thread",
    description=(
        "Create or update a WorkThread in work_index.json — a cross-session line of "
        "work that may span multiple sessions with gaps in between (e.g. a multi-step "
        "feature or refactor). If thread_id does not exist yet, this creates a new "
        "WorkThread with that id. Call this when you make meaningful progress on a "
        "multi-session effort, or when starting one worth tracking across sessions — "
        "this is what lets a future session pick up exactly where you left off instead "
        "of re-deriving context from scratch."
    ),
    schema={
        "type": "object",
        "properties": {
            "thread_id": {
                "type": "string",
                "description": "Stable identifier for this WorkThread (e.g. 'wt_self_evolution'). "
                                "Use the same id across sessions to keep updating the same thread.",
            },
            "title": {
                "type": "string",
                "description": "Short title describing the work thread. Required when creating "
                                "a new thread; if omitted on an update, the existing title is kept.",
            },
            "status": {
                "type": "string",
                "enum": ["active", "done", "paused"],
                "description": "Current status of this work thread.",
            },
            "cumulative_progress": {
                "type": "string",
                "description": "Up-to-date summary of what has been accomplished so far across "
                                "all sessions on this thread (replaces the previous value).",
            },
            "next_suggested": {
                "type": "string",
                "description": "What should be done next, for whoever (or whatever session) "
                                "picks this up.",
            },
            "open_questions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Unresolved questions related to this work thread "
                                "(replaces the previous list if provided).",
            },
            "related_goal_id": {
                "type": "string",
                "description": "Optional id linking this thread to a Goal Backlog objective "
                                "(Phase H, not yet implemented — safe to omit).",
            },
        },
        "required": ["thread_id"],
    },
    requires_approval=False,
)
def update_work_thread(
    thread_id: str,
    title: str = "",
    status: str = "",
    cumulative_progress: str = "",
    next_suggested: str = "",
    open_questions: Optional[list] = None,
    related_goal_id: Optional[str] = None,
) -> str:
    project_root = _get_project_root()
    if project_root is None:
        return _error(
            "project_root provider not registered (update_work_thread must be called "
            "from within an Agent session)."
        )

    from mini_agent.storage.paths import AgentPaths
    from mini_agent.perception.workdir_knowledge import (
        WorkThread, find_work_thread, upsert_work_thread,
    )

    paths = AgentPaths(project_root)
    existing = find_work_thread(paths, thread_id)

    if existing is None:
        # 新建：thread_id 不存在时直接创建，而不是要求先调用一个独立的
        # create_work_thread 工具——长任务里"发现这值得长期追踪"和
        # "记录当前进度"往往是同一个时刻的判断，拆成两个工具调用只会增加
        # agent 选错工具的概率，对调用方没有实际收益。
        if not title:
            return _error(
                f"WorkThread '{thread_id}' does not exist yet; 'title' is required "
                "when creating a new WorkThread."
            )
        thread = WorkThread(
            id=thread_id,
            title=title,
            status=status or "active",
            cumulative_progress=cumulative_progress,
            next_suggested=next_suggested,
            open_questions=list(open_questions or []),
            related_goal_id=related_goal_id,
        )
    else:
        thread = existing
        if title:
            thread.title = title
        if status:
            thread.status = status
        if cumulative_progress:
            thread.cumulative_progress = cumulative_progress
        if next_suggested:
            thread.next_suggested = next_suggested
        if open_questions is not None:
            thread.open_questions = list(open_questions)
        if related_goal_id is not None:
            thread.related_goal_id = related_goal_id

    upsert_work_thread(paths, thread)
    return json.dumps({"ok": True, "work_thread": thread.to_dict()}, ensure_ascii=False)


# ════════════════════════════════════════════════════════════════════════════
# update_knowledge（4.5，T1，走 StateRepo.apply()）
# ════════════════════════════════════════════════════════════════════════════

@tool(
    name="update_knowledge",
    description=(
        "Append or update a section in knowledge.md — the project's accumulated "
        "'soft knowledge': architectural decisions and their背景, gotchas encountered, "
        "or 'why this way and not that way' rationale that doesn't fit into structured "
        "JSON. Different from CLAUDE.md (operating rules for the agent): knowledge.md "
        "is accumulated understanding ABOUT the project. Goes through the "
        "self-evolution safety net (StateRepo.apply(), tier=T1), creating a git commit "
        "so changes are tracked and reversible. If a section with the same heading "
        "already exists, its content is replaced; otherwise a new section is appended. "
        "Also maintains a structured knowledge_index.json entry for this section "
        "(for faster lookup by topic/module without reading the full Markdown)."
    ),
    schema={
        "type": "object",
        "properties": {
            "section": {
                "type": "string",
                "description": "Section heading (e.g. 'Why we chose SQLite over Postgres'). "
                                "Rendered as a markdown '## ' heading. Also used as the key "
                                "for the structured index entry — calling this again with the "
                                "same section updates both the Markdown and the index entry.",
            },
            "content": {
                "type": "string",
                "description": "Markdown body content for this section.",
            },
            "summary": {
                "type": "string",
                "description": "Optional one-sentence summary of this section, stored in "
                                "knowledge_index.json for quick scanning without reading the "
                                "full content. Defaults to a truncated copy of content if omitted.",
            },
            "topic": {
                "type": "string",
                "description": "Optional short topic tag (e.g. 'mcp', 'auth', 'storage') for "
                                "the structured index, to make this entry filterable by area.",
            },
            "decision_type": {
                "type": "string",
                "description": "Optional category of this knowledge entry, e.g. 'architecture', "
                                "'gotcha', 'tradeoff', 'convention'.",
            },
            "affected_modules": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of file/module paths this knowledge concerns "
                                "(e.g. ['mcp/manager.py']), for the structured index.",
            },
        },
        "required": ["section", "content"],
    },
    requires_approval=False,  # 把关在 StateRepo 的 T1 校验流水线（T0 schema 校验对纯文本必过，
                              # 真正的安全网是"写入必须 commit、必须可 git revert"）
)
def update_knowledge(
    section: str,
    content: str,
    summary: str = "",
    topic: str = "",
    decision_type: str = "",
    affected_modules: Optional[list] = None,
) -> str:
    project_root = _get_project_root()
    if project_root is None:
        return _error(
            "project_root provider not registered (update_knowledge must be called "
            "from within an Agent session)."
        )

    from mini_agent.evolution.state_repo import StateRepo, StateRepoError
    from mini_agent.storage.paths import AgentPaths

    paths = AgentPaths(project_root)
    knowledge_path = paths.workdir_knowledge_md

    existing = ""
    if knowledge_path.is_file():
        try:
            existing = knowledge_path.read_text(encoding="utf-8")
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.tools.workdir_knowledge.update_knowledge')
            existing = ""

    new_text = _upsert_markdown_section(existing, section, content)

    try:
        repo = StateRepo(project_root)
    except StateRepoError as e:
        return _error(f"failed to open StateRepo: {e}")

    try:
        repo.ensure_initial_commit()
        rel_path = knowledge_path.relative_to(project_root)
        result = repo.apply(
            changes={str(rel_path): new_text},
            message=f"Update knowledge.md: {section}",
            meta={
                "source": "update_knowledge",
                "session_id": _get_session_id(),
            },
            tier="T1",
            auto_validators=True,
        )
    except StateRepoError as e:
        return _error(f"StateRepo.apply() failed: {e}")

    if not result.ok:
        return json.dumps({
            "ok": False,
            "error": "validation failed",
            "validation_errors": result.validation_errors,
        }, ensure_ascii=False)

    # ── 14.1 横向加固：与 Markdown 写入同一次调用里顺手维护结构化索引 ──────
    # apply() 已经成功（git commit 已落地）之后才更新索引——若索引更新本身
    # 失败，不应该回滚已经成功的 knowledge.md 写入（索引是衍生数据，可随时
    # 重建；Markdown 才是权威数据源）。
    index_entry = None
    try:
        from mini_agent.perception.workdir_knowledge import upsert_knowledge_index_entry
        index_entry = upsert_knowledge_index_entry(
            paths,
            heading=section,
            summary=summary or content[:200],
            topic=topic,
            decision_type=decision_type,
            affected_modules=affected_modules,
        )
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.tools.workdir_knowledge')
        pass

    return json.dumps({
        "ok": True,
        "commit": result.commit,
        "tier": result.tier,
        "section": section,
        "index_entry": index_entry.to_dict() if index_entry else None,
    }, ensure_ascii=False)


# ════════════════════════════════════════════════════════════════════════════
# search_knowledge（检索侧补全，对应设计文档 8.4 节"按意图检索注入"）
# ════════════════════════════════════════════════════════════════════════════

@tool(
    name="search_knowledge",
    description=(
        "Search this project's accumulated knowledge.md for entries relevant to a "
        "query — use this BEFORE re-deriving an architectural decision, debugging a "
        "'why is this built this way' question, or starting work in an area the "
        "project may have already documented a gotcha or tradeoff for. Searches "
        "structured summaries (fast, keyword-based) and optionally returns the full "
        "Markdown section content for the best matches. Call this proactively at the "
        "start of non-trivial tasks, the same way you would check open_threads or "
        "work_index — knowledge.md only helps future sessions if it's actually read."
    ),
    schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural-language or keyword query describing what you're "
                                "looking for (e.g. 'why SQLite instead of Postgres', "
                                "'auth token refresh gotchas').",
            },
            "k": {
                "type": "integer",
                "description": "Max number of matching entries to return, ranked by "
                                "relevance (default 5).",
            },
            "topic": {
                "type": "string",
                "description": "Optional exact-match topic filter (e.g. 'mcp', 'auth') to "
                                "narrow the search to entries tagged with that topic before "
                                "ranking — use when you already know the area and want to "
                                "avoid cross-topic noise.",
            },
            "include_content": {
                "type": "boolean",
                "description": "If true, also fetch and return the full Markdown section "
                                "content for each match (not just the summary). Defaults to "
                                "false to save tokens — most of the time the summary is "
                                "enough to decide whether the entry is relevant; turn this on "
                                "once you've confirmed a match is what you need.",
            },
        },
        "required": ["query"],
    },
    requires_approval=False,
)
def search_knowledge(
    query: str,
    k: int = 5,
    topic: Optional[str] = None,
    include_content: bool = False,
) -> str:
    project_root = _get_project_root()
    if project_root is None:
        return _error(
            "project_root provider not registered (search_knowledge must be called "
            "from within an Agent session)."
        )

    from mini_agent.storage.paths import AgentPaths
    from mini_agent.perception.workdir_knowledge import (
        search_knowledge_index, read_knowledge_section,
    )

    paths = AgentPaths(project_root)
    try:
        ranked = search_knowledge_index(paths, query, k=k, topic=topic or None)
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.tools.workdir_knowledge.search_knowledge')
        return _error(f"search failed: {e}")

    results = []
    for entry, score in ranked:
        item = entry.to_dict()
        item["score"] = round(score, 4)
        if include_content:
            try:
                item["content"] = read_knowledge_section(paths, entry.heading)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.tools.workdir_knowledge.search_knowledge')
                item["content"] = None
        results.append(item)

    return json.dumps({
        "ok": True,
        "query": query,
        "count": len(results),
        "results": results,
    }, ensure_ascii=False)


def _upsert_markdown_section(existing: str, section: str, content: str) -> str:
    """
    在现有 Markdown 文本中插入/替换一个 '## <section>' 二级标题段落。

    - 若标题已存在：替换该标题到下一个同级或更高级标题之前的全部内容
    - 若标题不存在：追加到文末（前面补一个空行分隔）

    用简单的逐行扫描实现，不引入 Markdown 解析依赖——knowledge.md 是
    agent 自己写自己读的内部文件，不需要处理任意第三方 Markdown 的边界情况。
    """
    heading = f"## {section}"
    lines = existing.splitlines()

    start_idx: Optional[int] = None
    end_idx: Optional[int] = None
    for i, line in enumerate(lines):
        if line.strip() == heading.strip():
            start_idx = i
            continue
        if start_idx is not None and end_idx is None:
            stripped = line.strip()
            if stripped.startswith("# ") or stripped.startswith("## "):
                end_idx = i
                break
    if start_idx is not None and end_idx is None:
        end_idx = len(lines)

    new_section_lines = [heading, "", content.rstrip(), ""]

    if start_idx is None:
        # 追加到文末
        prefix = lines + ([""] if lines and lines[-1].strip() else [])
        result_lines = prefix + new_section_lines
    else:
        result_lines = lines[:start_idx] + new_section_lines + lines[end_idx:]

    text = "\n".join(result_lines)
    # 折叠多余的连续空行（最多保留一行空行分隔），保持文件整洁
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip() + "\n"


__all__ = [
    "set_project_root_provider",
    "set_session_id_provider",
    "add_open_thread",
    "update_work_thread",
    "update_knowledge",
    "search_knowledge",
]
