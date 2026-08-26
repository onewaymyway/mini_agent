"""
tools/external_projects.py — 大管家标准工具集（阶段 5 第二项）

对应 `next_doc/external_projects_workspace_plan.md` 阶段 5：
  "daemon 侧新增一组标准工具供大管家 agent 调用：list_projects
   inspect_project trigger_run propose_fix"

设计取舍：
  - 与 `tools/evolution.py::skill_propose` 不同，本模块的工具不需要
    thread-local "当前项目根目录" provider——它们操作的是"注册表里的
    某个外部项目"，与调用它们的 Agent 会话本身所在的 project_root
    无关（大管家 daemon 的会话本身通常没有、也不需要一个有意义的
    project_root）。每个工具函数按需自己构造 `ExternalProjectRegistry()`
    （默认路径 `~/.mini_agent/external_projects.json`）。
  - 全部返回 JSON 字符串（与 skill_propose 一致的既有约定），失败时
    `{"ok": false, "error": "..."}`，不抛异常给调用方（LLM tool_use
    的错误处理路径统一是"读返回值里的 ok 字段"，不是"catch 异常"）。
  - `trigger_run` 会真的执行外部项目自己的代码（`project.yaml` 里声明
    的 `cmd`），保留 `requires_approval=True`；`list_projects` /
    `inspect_project` 是纯只读操作；`propose_fix` 虽然会写文件，但
    落在独立分支上、不影响目标项目当前 checkout 的分支，且本身经过
    校验流水线把关（同 skill_propose 的取舍），不需要额外的人工确认
    才能"生成一个可审核的提案"。
"""

from __future__ import annotations

import json
from pathlib import Path

from . import tool


def _registry():
    from mini_agent.external_projects.registry import ExternalProjectRegistry

    return ExternalProjectRegistry()


@tool(
    name="list_projects",
    description=(
        "List all registered external projects (daemon's external-project registry): "
        "name, path, enabled state, registration time. Read-only."
    ),
    schema={"type": "object", "properties": {}, "required": []},
    requires_approval=False,
)
def list_projects() -> str:
    registry = _registry()
    projects = registry.list()
    return json.dumps(
        {
            "ok": True,
            "projects": [
                {
                    "name": p.name,
                    "path": p.path,
                    "enabled": p.enabled,
                    "registered_at": p.registered_at,
                }
                for p in projects
            ],
        },
        indent=2,
        ensure_ascii=False,
    )


@tool(
    name="inspect_project",
    description=(
        "Inspect one registered external project in depth: its parsed project.yaml manifest "
        "(entrypoints/health_check/resources), current health (health_check probe if declared, "
        "else degraded to its last run-ledger record), and its most recent run-ledger entries. "
        "Read-only — use this before trigger_run or propose_fix to understand what a project "
        "actually declares and how it has been doing."
    ),
    schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Registered external project name."},
            "recent_runs_limit": {
                "type": "integer",
                "description": "How many recent ledger records to include (default 5).",
            },
        },
        "required": ["name"],
    },
    requires_approval=False,
)
def inspect_project(name: str, recent_runs_limit: int = 5) -> str:
    from mini_agent.external_projects.ledger import read_ledger
    from mini_agent.external_projects.manifest import ProjectManifestError
    from mini_agent.external_projects.registry import ExternalProjectRegistryError
    from mini_agent.external_projects.status import project_status_snapshot

    registry = _registry()
    try:
        record = registry.get(name)
    except ExternalProjectRegistryError as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)

    manifest_payload = None
    manifest_error = None
    source_dir = None
    try:
        manifest = registry.load_manifest_for(name)
        source_dir = manifest.source_dir
        manifest_payload = {
            "entrypoints": {
                key: {"cmd": ep.cmd, "schedule": ep.schedule, "timeout_sec": ep.timeout_sec}
                for key, ep in manifest.entrypoints.items()
            },
            "health_check": manifest.health_check.cmd if manifest.health_check else None,
            "resources": {
                "allowed_domains": manifest.resources.allowed_domains,
                "max_concurrency": manifest.resources.max_concurrency,
            },
        }
    except ProjectManifestError as e:
        manifest_error = str(e)

    snap = project_status_snapshot(registry, name)
    recent = []
    if source_dir is not None:
        recent = [r.to_dict() for r in read_ledger(source_dir, limit=recent_runs_limit)]

    return json.dumps(
        {
            "ok": True,
            "name": name,
            "path": record.path,
            "enabled": record.enabled,
            "manifest": manifest_payload,
            "manifest_error": manifest_error,
            "health": snap.health,
            "health_source": snap.health_source,
            "recent_runs": recent,
        },
        indent=2,
        ensure_ascii=False,
    )


@tool(
    name="trigger_run",
    description=(
        "Immediately trigger one entrypoint of a registered external project: runs the cmd "
        "declared in that project's project.yaml as a subprocess with cwd = the project's own "
        "root, and records the outcome in that project's own run ledger (trigger='manual'). "
        "Does NOT require a daemon scheduling cycle or the entrypoint's own schedule to be due."
    ),
    schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Registered external project name."},
            "entrypoint": {
                "type": "string",
                "description": "Entrypoint key declared under 'entrypoints' in that project's project.yaml.",
            },
        },
        "required": ["name", "entrypoint"],
    },
    requires_approval=True,  # 会真的跑外部项目自己的代码，保留人工确认
)
def trigger_run(name: str, entrypoint: str) -> str:
    from mini_agent.external_projects.scheduler import trigger_run as _scheduler_trigger_run

    registry = _registry()
    try:
        result = _scheduler_trigger_run(registry, name, entrypoint, trigger="manual")
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)

    return json.dumps(
        {
            "ok": result.returncode == 0,
            "project": result.project_name,
            "entrypoint": result.entrypoint_key,
            "returncode": result.returncode,
            "trigger": result.trigger,
        },
        indent=2,
        ensure_ascii=False,
    )


@tool(
    name="propose_fix",
    description=(
        "Propose a fix to a registered external project's own files (e.g. a scraper script broken "
        "by a website redesign). Commits the change to a NEW dedicated evolve/<date>-fix-<slug> "
        "branch inside that project's OWN git repository (auto git-init'd if it doesn't have one "
        "yet) — NEVER the project's currently checked-out branch. Goes through the same "
        "self-evolution safety net used for mini_agent's own skill proposals (StateRepo.apply(), "
        "tier=T2 by default: lint the changed .py files + run that project's own tests/ if "
        "present). If validation fails, nothing is written or committed. The proposal is NOT "
        "active until a human reviews and merges that branch — this tool only produces a "
        "reviewable branch, it never auto-merges."
    ),
    schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Registered external project name."},
            "changes": {
                "type": "object",
                "description": (
                    "Mapping of path (relative to the project's root) -> new full file content. "
                    "A null value deletes that file."
                ),
            },
            "message": {"type": "string", "description": "Commit message summarizing the fix."},
            "reason": {
                "type": "string",
                "description": "Why this fix is being proposed (recorded in the commit message).",
            },
            "tier": {
                "type": "string",
                "description": "Risk tier T0-T3 (see evolution/validators.py). Default T2.",
            },
            "change_type": {
                "type": "string",
                "description": (
                    "'fix' (default — corrects a hard failure, verifiable by health_check/exit "
                    "code) or 'enhancement' (no hard failure signal; whether it's actually "
                    "better is a subjective judgment call). Passing validation never implies an "
                    "enhancement should be auto-landed — only a human may land one, after "
                    "reviewing the evidence."
                ),
            },
        },
        "required": ["name", "changes", "message"],
    },
    requires_approval=False,  # 落在独立分支，不影响目标项目当前分支；把关在校验流水线 + 人工 merge
)
def propose_fix(
    name: str,
    changes: dict,
    message: str,
    reason: str = "",
    tier: str = "T2",
    change_type: str = "fix",
) -> str:
    from mini_agent.external_projects.maintenance import MaintenanceError, propose_maintenance_fix
    from mini_agent.external_projects.registry import ExternalProjectRegistryError

    registry = _registry()
    try:
        record = registry.get(name)
    except ExternalProjectRegistryError as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)

    try:
        result = propose_maintenance_fix(
            Path(record.path),
            dict(changes or {}),
            message,
            slug=name,
            reason=reason,
            tier=tier,
            change_type=change_type,
        )
    except MaintenanceError as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)

    if not result.ok:
        return json.dumps(
            {
                "ok": False,
                "error": result.error,
                "tier": result.tier,
                "forced_tier": result.forced_tier,
                "validation_errors": result.validation_errors,
                "change_type": result.change_type,
            },
            indent=2,
            ensure_ascii=False,
        )

    land_note = (
        "This is an ENHANCEMENT proposal (no hard failure signal — passing tests only means "
        "no known regression, not that it's actually better). Do not land it yourself; surface "
        "the branch and evidence to the user and let them decide."
        if result.change_type == "enhancement"
        else "Review (e.g. git -C <path> diff <base>..<branch>) and merge manually to apply it, "
        "or delete the branch to discard."
    )
    return json.dumps(
        {
            "ok": True,
            "project": name,
            "branch": result.branch,
            "commit": result.commit,
            "tier": result.tier,
            "change_type": result.change_type,
            "message": (
                f"{'Fix' if result.change_type == 'fix' else 'Enhancement'} proposed on branch "
                f"'{result.branch}' ({result.commit[:8]}) inside {record.path}. {land_note}"
            ),
        },
        indent=2,
        ensure_ascii=False,
    )


@tool(
    name="list_backlog",
    description=(
        "List improvement-backlog items for one registered external project (soft-quality "
        "issues without a hard failure signal — outcome-review findings, user feedback, "
        "health trends). Read-only. Optionally filter by status "
        "(open/proposed/landed/dismissed)."
    ),
    schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Registered external project name."},
            "status": {
                "type": "string",
                "description": "Optional status filter: open, proposed, landed, or dismissed.",
            },
        },
        "required": ["name"],
    },
    requires_approval=False,
)
def list_backlog(name: str, status: str = "") -> str:
    from mini_agent.external_projects.backlog import read_backlog
    from mini_agent.external_projects.registry import ExternalProjectRegistryError

    registry = _registry()
    try:
        record = registry.get(name)
    except ExternalProjectRegistryError as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)

    items = read_backlog(Path(record.path), status=status or None)
    return json.dumps(
        {"ok": True, "items": [i.to_dict() for i in items]},
        indent=2,
        ensure_ascii=False,
    )


@tool(
    name="append_backlog_item",
    description=(
        "Append one improvement-backlog item to a registered external project — use this to "
        "durably record a soft-quality issue or a piece of user feedback (e.g. 'this week's "
        "candidate-pool report missed an obvious hot stock') so a future review pass can find "
        "it, instead of letting it disappear at the end of this conversation. This only writes "
        "a to-do record — it does not execute or change any project code."
    ),
    schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Registered external project name."},
            "source": {
                "type": "string",
                "description": "One of: outcome_review, user_feedback, health_trend.",
            },
            "summary": {"type": "string", "description": "One-sentence description of the issue."},
            "evidence_ref": {
                "type": "string",
                "description": "Optional pointer to supporting evidence (a file path, report, etc.).",
            },
        },
        "required": ["name", "source", "summary"],
    },
    requires_approval=False,
)
def append_backlog_item(name: str, source: str, summary: str, evidence_ref: str = "") -> str:
    from mini_agent.external_projects.backlog import BacklogError, append_item
    from mini_agent.external_projects.registry import ExternalProjectRegistryError

    registry = _registry()
    try:
        record = registry.get(name)
    except ExternalProjectRegistryError as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)

    try:
        item = append_item(
            Path(record.path),
            source=source,
            summary=summary,
            evidence_ref=evidence_ref or None,
        )
    except BacklogError as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)

    return json.dumps({"ok": True, "item": item.to_dict()}, indent=2, ensure_ascii=False)


__all__ = [
    "list_projects",
    "inspect_project",
    "trigger_run",
    "propose_fix",
    "list_backlog",
    "append_backlog_item",
]
