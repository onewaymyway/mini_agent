"""
tools/skill_manager.py — Skill 动态管理工具

让 agent 自主决定何时激活、何时卸载技能，而不依赖关键词匹配。

工具列表：
  skill_list()                  — 查看所有可用技能和当前激活状态
  skill_activate(names, reason) — 激活一个或多个技能
  skill_deactivate(names, reason) — 卸载一个或多个不再需要的技能

设计原则：
  - 工具以闭包方式绑定 SkillLoader 实例，不依赖全局状态
  - 激活/卸载都要求 agent 说明原因（reason），便于追踪和调试
  - 工具结果以 JSON 格式返回，方便 agent 解析后续操作

注册方式（在 agent 初始化时调用）：
  from mini_agent.tools.skill_manager import register_skill_tools
  register_skill_tools(registry, skill_loader)
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import mini_agent.ui.renderer as R
from . import ToolRegistry

if TYPE_CHECKING:
    from skills import SkillLoader


def register_skill_tools(registry: ToolRegistry, skill_loader: "SkillLoader") -> None:
    """
    将 skill 管理工具注册到指定 registry。
    在 Agent.__init__ 中调用，将 skill_loader 绑定到闭包内。
    """
    # ── skill_list ─────────────────────────────────────────────────────────────

    def skill_list() -> str:
        """
        List all available skills and their current activation status.
        Returns each skill's name, description, and whether it is currently active.
        Call this before activating or deactivating skills to understand what is available.
        """
        catalog = skill_loader.get_catalog()
        if not catalog:
            return json.dumps({"skills": [], "message": "No skills available."})

        active_names   = [s["name"] for s in catalog if s["active"]]
        inactive_names = [s["name"] for s in catalog if not s["active"]]

        return json.dumps(
            {
                "skills":   catalog,
                "summary": {
                    "total":    len(catalog),
                    "active":   len(active_names),
                    "inactive": len(inactive_names),
                    "active_names":   active_names,
                    "inactive_names": inactive_names,
                },
            },
            ensure_ascii=False,
            indent=2,
        )

    registry.register_fn(
        fn=skill_list,
        name="skill_list",
        description=(
            "List all available skills with their name, description, and activation status. "
            "Use this to discover what skills exist and which are currently active "
            "before calling skill_activate or skill_deactivate."
        ),
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
        requires_approval=False,
        override=True,  # [Phase E / 3.3 修复] 见 sub_agent.py _build_agent 注释：SubAgent 持有独立 skill_loader 时会拿到 filtered() 出来的独立 registry 副本，该副本已含从全局 registry 复制来的同名占位条目，必须允许覆盖
    )

    # ── skill_activate ─────────────────────────────────────────────────────────

    def skill_activate(names: list, reason: str) -> str:
        """
        Activate one or more skills by name to load their guidance into the system context.
        Activated skill content will be injected into subsequent LLM calls.
        Always call skill_list first if you are unsure which skills are available.
        """
        if not names:
            return json.dumps({"error": "names list is empty"})

        results = []
        for name in names:
            ok = skill_loader.activate(name)
            if ok:
                desc = skill_loader.describe(name)
                results.append({"name": name, "status": "activated", "description": desc})
                R.print_skill_loaded(name)
            elif name not in skill_loader.available:
                results.append({"name": name, "status": "not_found",
                                "available": skill_loader.available})
            else:
                results.append({"name": name, "status": "already_active"})

        activated = [r["name"] for r in results if r["status"] == "activated"]
        return json.dumps(
            {
                "results":          results,
                "activated":        activated,
                "reason":           reason,
                "now_active":       skill_loader.active,
            },
            ensure_ascii=False,
            indent=2,
        )

    registry.register_fn(
        fn=skill_activate,
        name="skill_activate",
        description=(
            "Activate one or more skills by name. "
            "Skill content will be injected into the system prompt for subsequent turns, "
            "providing domain-specific guidance (e.g. how to create Word documents, "
            "how to write PDFs, coding conventions, etc.). "
            "Only activate skills that are relevant to the current task — "
            "unnecessary active skills waste context window space. "
            "Call skill_list first if you need to see available skill names."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of skill names to activate (exact match required).",
                    "minItems": 1,
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "Brief explanation of why these skills are needed for the current task. "
                        "Example: 'User asked to create a Word report, need docx skill.'"
                    ),
                },
            },
            "required": ["names", "reason"],
        },
        requires_approval=False,
        override=True,  # [Phase E / 3.3 修复] 见 sub_agent.py _build_agent 注释：SubAgent 持有独立 skill_loader 时会拿到 filtered() 出来的独立 registry 副本，该副本已含从全局 registry 复制来的同名占位条目，必须允许覆盖
    )

    # ── skill_deactivate ───────────────────────────────────────────────────────

    def skill_deactivate(names: list, reason: str) -> str:
        """
        Deactivate one or more skills to free up context window space.
        Call this when a skill's task phase is complete and its guidance is no longer needed.
        For example, after finishing a Word document, deactivate the docx skill.
        """
        if not names:
            return json.dumps({"error": "names list is empty"})

        results = []
        for name in names:
            ok = skill_loader.deactivate(name)
            if ok:
                results.append({"name": name, "status": "deactivated"})
                R.print_info(f"📤 Skill unloaded: {name}")
            elif name not in skill_loader.available:
                results.append({"name": name, "status": "not_found"})
            else:
                results.append({"name": name, "status": "not_active"})

        deactivated = [r["name"] for r in results if r["status"] == "deactivated"]
        return json.dumps(
            {
                "results":      results,
                "deactivated":  deactivated,
                "reason":       reason,
                "now_active":   skill_loader.active,
            },
            ensure_ascii=False,
            indent=2,
        )

    registry.register_fn(
        fn=skill_deactivate,
        name="skill_deactivate",
        description=(
            "Deactivate one or more skills that are no longer needed. "
            "This removes their content from the system prompt, keeping the context lean. "
            "You SHOULD deactivate a skill once its associated task phase is done. "
            "For example: after creating a PDF, deactivate the pdf skill; "
            "after finishing Excel work, deactivate the xlsx skill. "
            "Keeping irrelevant skills active wastes context tokens."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of skill names to deactivate.",
                    "minItems": 1,
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "Brief explanation of why these skills are no longer needed. "
                        "Example: 'Word document creation is complete, docx skill no longer needed.'"
                    ),
                },
            },
            "required": ["names", "reason"],
        },
        requires_approval=False,
        override=True,  # [Phase E / 3.3 修复] 见 sub_agent.py _build_agent 注释：SubAgent 持有独立 skill_loader 时会拿到 filtered() 出来的独立 registry 副本，该副本已含从全局 registry 复制来的同名占位条目，必须允许覆盖
    )


def register_compact_tool(registry: ToolRegistry, agent: "object") -> None:
    """
    注册 compact_history 工具，让 agent 可以主动触发带 skill 重附的压缩。
    在 Agent.__init__ 尾部调用（需要 agent 实例）。
    """

    def compact_history(reason: str = "") -> str:
        """
        Compress conversation history to free up context window, then re-attach
        recently-used skill content in LRU order within the configured token budget.
        Call this proactively when the conversation is getting long or when you
        anticipate needing more context space for upcoming tasks.
        """
        import json as _json
        try:
            result_text = agent.compact_with_skills()
            tracker = getattr(getattr(agent, "skill_loader", None), "tracker", None)
            included = tracker.recent_names()[:5] if tracker else []
            return _json.dumps({
                "status":   "compacted",
                "reason":   reason,
                "summary":  result_text[:200] + "…" if len(result_text) > 200 else result_text,
                "skill_context_reattached": included,
            }, ensure_ascii=False)
        except Exception as e:
            return _json.dumps({"status": "error", "message": str(e)})

    registry.register_fn(
        fn=compact_history,
        name="compact_history",
        description=(
            "Compress the conversation history to free up context window space. "
            "After compressing, the most recently used skills are automatically "
            "re-attached in LRU order, respecting the configured token budget "
            "(so older/less-used skills may be dropped). "
            "Call this when the conversation is getting very long or when you need "
            "to reclaim context space before a large task."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Optional: why you are compressing now.",
                },
            },
            "required": [],
        },
        requires_approval=False,
        override=True,  # [Phase E / 3.3 修复] 见 sub_agent.py _build_agent 注释：SubAgent 持有独立 skill_loader 时会拿到 filtered() 出来的独立 registry 副本，该副本已含从全局 registry 复制来的同名占位条目，必须允许覆盖
    )


def register_skill_stats_tool(registry: ToolRegistry, skill_loader: "SkillLoader") -> None:
    """注册 skill_usage_stats 工具，让 agent 查看 skill 调用追踪信息。"""

    def skill_usage_stats() -> str:
        """
        Show skill usage tracking: which skills have been called, how many times,
        and when they were last used. The LRU order determines priority during
        context compression — recently used skills survive; older ones may be dropped.
        """
        import json as _json
        tracker = skill_loader.tracker
        records = tracker.records
        if not records:
            return _json.dumps({
                "message": "No skills have been used yet.",
                "records": [],
                "budget":  {
                    "total_tokens":    tracker.total_budget,
                    "per_skill_tokens": tracker.per_skill_tokens,
                },
            })

        import time as _time
        rec_list = [
            {
                "name":        r.name,
                "call_count":  r.call_count,
                "last_called": _time.strftime("%H:%M:%S", _time.localtime(r.last_called)),
                "lru_rank":    i + 1,
            }
            for i, r in enumerate(records)
        ]
        return _json.dumps({
            "records": rec_list,
            "budget":  {
                "total_tokens":     tracker.total_budget,
                "per_skill_tokens": tracker.per_skill_tokens,
            },
            "note": "LRU rank 1 = most recently used (highest priority in compression).",
        }, ensure_ascii=False, indent=2)

    registry.register_fn(
        fn=skill_usage_stats,
        name="skill_usage_stats",
        description=(
            "Show skill usage statistics: call counts, last-used timestamps, "
            "and LRU rank for each skill. "
            "LRU rank determines priority during context compression — "
            "rank 1 (most recently used) survives first; "
            "lower-priority skills may be dropped when the budget is tight. "
            "Use this to understand which skills are at risk of being evicted."
        ),
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
        requires_approval=False,
        override=True,  # [Phase E / 3.3 修复] 见 sub_agent.py _build_agent 注释：SubAgent 持有独立 skill_loader 时会拿到 filtered() 出来的独立 registry 副本，该副本已含从全局 registry 复制来的同名占位条目，必须允许覆盖
    )
