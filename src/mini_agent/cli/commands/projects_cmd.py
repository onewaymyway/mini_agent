"""
cli/commands/projects_cmd.py — `mini-agent projects` 独立命令行入口

对应 `next_doc/external_projects_workspace_plan.md` 阶段 3 第四项。

用法（与 `mini-agent workflow` / `mini-agent daemon` 短路方式完全一致，
见 `cli/app.py::main()` 里的对应分支）：
    mini-agent projects list
    mini-agent projects status <name>
    mini-agent projects run <name> <entrypoint>
    mini-agent projects ledger <name> [limit]
    mini-agent projects register <path> [--name <name>]
    mini-agent projects unregister <name>
    mini-agent projects enable <name>
    mini-agent projects disable <name>

这一层只是注册表 + manifest 解析 + 触发一次执行的薄封装，不构造 Agent、
不依赖 daemon 是否在运行——`list`/`status`/`register`/`unregister` 直接
操作本地注册表文件，`run` 直接以子进程方式执行对应 entrypoint 的 `cmd`，
与 daemon 内部调度器（`external_projects/scheduler.py::run_due_entrypoints`）
触发同一个 entrypoint 时走的是同一条 `_run_entrypoint` 路径，结果完全
等价（呼应原则二：daemon 只是可选加成）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional


def _print(msg: str) -> None:
    print(msg)


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _cmd_list(_args: List[str]) -> int:
    from mini_agent.external_projects import ExternalProjectRegistry
    from mini_agent.external_projects.ledger import last_record
    from mini_agent.external_projects.manifest import ProjectManifestError

    registry = ExternalProjectRegistry()
    projects = registry.list()
    if not projects:
        _print("尚未注册任何外部项目。用 `mini-agent projects register <path>` 注册一个。")
        return 0
    _print(f"{'NAME':<20}{'ENABLED':<10}{'LAST_RUN':<12}{'PATH'}")
    for p in projects:
        # 只读账本，不主动探测 health_check——`list` 应该是纯被动、瞬时
        # 完成的操作（原则三），真正想探测健康检查用 `projects status`。
        try:
            manifest = registry.load_manifest_for(p.name)
            last = last_record(manifest.source_dir) if manifest.source_dir else None
            last_run = ("OK" if last.success else "FAIL") if last else "(none)"
        except ProjectManifestError:
            last_run = "(bad manifest)"
        _print(f"{p.name:<20}{str(p.enabled):<10}{last_run:<12}{p.path}")
    return 0


def _cmd_status(args: List[str]) -> int:
    from mini_agent.external_projects import ExternalProjectRegistry
    from mini_agent.external_projects.manifest import ProjectManifestError
    from mini_agent.external_projects.registry import ExternalProjectRegistryError

    if not args:
        _err("用法: mini-agent projects status <name>")
        return 1
    name = args[0]
    registry = ExternalProjectRegistry()
    try:
        record = registry.get(name)
    except ExternalProjectRegistryError as exc:
        _err(str(exc))
        return 1

    _print(f"name:      {record.name}")
    _print(f"path:      {record.path}")
    _print(f"enabled:   {record.enabled}")
    _print(f"registered_at: {record.registered_at}")

    try:
        manifest = registry.load_manifest_for(name)
    except ProjectManifestError as exc:
        _err(f"警告: project.yaml 当前无法解析: {exc}")
        return 1

    _print("entrypoints:")
    for key, ep in manifest.entrypoints.items():
        schedule = ep.schedule or "(手动/外部触发)"
        _print(f"  - {key}: cmd={ep.cmd!r} schedule={schedule}")
    if manifest.health_check:
        _print(f"health_check: {manifest.health_check.cmd!r}")
    if manifest.resources.allowed_domains:
        _print(f"resources.allowed_domains: {manifest.resources.allowed_domains}")
    _print(f"resources.max_concurrency: {manifest.resources.max_concurrency}")

    from mini_agent.external_projects.status import project_status_snapshot
    from mini_agent.external_projects.ledger import read_ledger

    snap = project_status_snapshot(registry, name)
    _print(f"\nhealth: {snap.health} (source={snap.health_source})")
    if snap.last_run:
        lr = snap.last_run
        _print(
            f"last_run: entrypoint={lr.entrypoint} exit_code={lr.exit_code} "
            f"trigger={lr.trigger} finished_at={lr.finished_at}"
        )
        if lr.error_summary:
            _print(f"  error: {lr.error_summary}")
        if lr.detail:
            _print(f"  detail:\n    " + lr.detail.replace("\n", "\n    "))
    else:
        _print("last_run: (账本为空，该项目还没有任何执行记录)")

    recent = read_ledger(manifest.source_dir, limit=5) if manifest.source_dir else []
    if len(recent) > 1:
        _print("recent runs:")
        for rec in reversed(recent):
            mark = "OK" if rec.success else "FAIL"
            _print(f"  [{mark}] {rec.entrypoint} @ {rec.finished_at} (trigger={rec.trigger})")

    # [A3] next_doc/hybrid_exec_improvement_directions.md §3.A3 最小改动：
    # 附带打印一眼该外部项目的 hybrid_exec 用量摘要，不要求用户额外
    # `cd` 过去用 `mini-agent hybrid-exec list` 才能看到。纯只读、只在
    # `.agent/hybrid_exec/` 目录确实存在时才探测，不存在（这个外部项目
    # 从未用过 hybrid_exec）时完全不打印这部分，不制造噪音；探测本身
    # 失败也只打印一句警告，不影响上面已经打印完的其它信息、不让整个
    # `status` 命令因为这一项失败。
    if manifest.source_dir is not None:
        _print(_hybrid_exec_summary_line(manifest.source_dir))
    return 0


def _hybrid_exec_summary_line(project_dir: Path) -> str:
    """返回一行 hybrid_exec 用量摘要，供 `_cmd_status` 附带打印。

    对应 next_doc/hybrid_exec_improvement_directions.md A3 最小改动路径：
    只扫一眼 `<project_dir>/.agent/hybrid_exec/` 是否存在，存在则调用
    `build_kanban_summary()` 汇总打印一行"N 个 hybrid_exec 任务，当前
    命中率 xx%"。单独抽成函数方便单测覆盖三种分支（未使用/已使用/
    探测失败）而不用每次都走完整的 `_cmd_status`。
    """
    hybrid_exec_dir = Path(project_dir) / ".agent" / "hybrid_exec"
    if not hybrid_exec_dir.exists():
        return "hybrid_exec: 未使用（.agent/hybrid_exec/ 不存在）"

    try:
        from mini_agent.hybrid_exec import build_kanban_summary

        summary = build_kanban_summary(project_dir)
        tasks = summary.get("tasks", [])
        if not tasks:
            return "hybrid_exec: 目录存在但暂无已归档任务"

        active_count = sum(1 for t in tasks if t.get("active_status") == "active")
        total_success = sum(t.get("active_success_count", 0) or 0 for t in tasks)
        total_fail = sum(t.get("active_fail_count", 0) or 0 for t in tasks)
        total_runs = total_success + total_fail
        hit_rate = f"{total_success / total_runs * 100:.0f}%" if total_runs else "N/A"
        return (
            f"hybrid_exec: {len(tasks)} 个任务（{active_count} 个 active），"
            f"当前脚本命中率 {hit_rate}（{total_success}/{total_runs}）"
        )
    except Exception as exc:  # noqa: BLE001 — 摘要探测失败不应影响 status 命令其它部分
        return f"hybrid_exec: 摘要读取失败（{exc}）"


def _cmd_run(args: List[str]) -> int:
    from mini_agent.external_projects import ExternalProjectRegistry
    from mini_agent.external_projects.manifest import ProjectManifestError
    from mini_agent.external_projects.registry import ExternalProjectRegistryError
    from mini_agent.external_projects.scheduler import trigger_run

    if len(args) < 2:
        _err("用法: mini-agent projects run <name> <entrypoint>")
        return 1
    name, entrypoint_key = args[0], args[1]
    registry = ExternalProjectRegistry()
    try:
        result = trigger_run(registry, name, entrypoint_key, trigger="manual")
    except (ExternalProjectRegistryError, ProjectManifestError, ValueError) as exc:
        _err(str(exc))
        return 1

    _print(
        f"[{result.project_name}/{result.entrypoint_key}] "
        f"exit_code={result.returncode} trigger={result.trigger}"
    )
    if result.detail:
        _print("detail:\n  " + result.detail.replace("\n", "\n  "))
    return 0 if result.returncode == 0 else result.returncode


def _cmd_register(args: List[str]) -> int:
    from mini_agent.external_projects import ExternalProjectRegistry
    from mini_agent.external_projects.registry import ExternalProjectRegistryError

    if not args:
        _err("用法: mini-agent projects register <path> [--name <name>] [--no-validate]")
        return 1

    path_str = args[0]
    name: Optional[str] = None
    validate = True
    i = 1
    while i < len(args):
        if args[i] == "--name" and i + 1 < len(args):
            name = args[i + 1]
            i += 2
        elif args[i] == "--no-validate":
            validate = False
            i += 1
        else:
            i += 1

    path = Path(path_str).expanduser().resolve()
    if name is None:
        name = path.name

    registry = ExternalProjectRegistry()
    try:
        record = registry.register(name, path, validate=validate)
    except ExternalProjectRegistryError as exc:
        _err(str(exc))
        return 1

    _print(f"已注册外部项目 '{record.name}' -> {record.path}")
    return 0


def _cmd_unregister(args: List[str]) -> int:
    from mini_agent.external_projects import ExternalProjectRegistry
    from mini_agent.external_projects.registry import ExternalProjectRegistryError

    if not args:
        _err("用法: mini-agent projects unregister <name>")
        return 1
    name = args[0]
    registry = ExternalProjectRegistry()
    try:
        registry.unregister(name)
    except ExternalProjectRegistryError as exc:
        _err(str(exc))
        return 1
    _print(f"已移除外部项目 '{name}'（其自身代码/数据不受影响，只是取消注册）。")
    return 0


def _cmd_set_enabled(args: List[str], enabled: bool) -> int:
    from mini_agent.external_projects import ExternalProjectRegistry
    from mini_agent.external_projects.registry import ExternalProjectRegistryError

    verb = "enable" if enabled else "disable"
    if not args:
        _err(f"用法: mini-agent projects {verb} <name>")
        return 1
    name = args[0]
    registry = ExternalProjectRegistry()
    try:
        registry.set_enabled(name, enabled)
    except ExternalProjectRegistryError as exc:
        _err(str(exc))
        return 1
    _print(f"'{name}' 已{'启用' if enabled else '禁用'}"
           f"（禁用只影响 daemon 侧调度器是否会自动触发它，不影响该项目"
           f"被 OS cron / 手动直接执行）。")
    return 0


def _cmd_ledger(args: List[str]) -> int:
    from mini_agent.external_projects import ExternalProjectRegistry
    from mini_agent.external_projects.manifest import ProjectManifestError
    from mini_agent.external_projects.registry import ExternalProjectRegistryError
    from mini_agent.external_projects.ledger import read_ledger

    if not args:
        _err("用法: mini-agent projects ledger <name> [limit]")
        return 1
    name = args[0]
    limit = int(args[1]) if len(args) > 1 else 20

    registry = ExternalProjectRegistry()
    try:
        manifest = registry.load_manifest_for(name)
    except (ExternalProjectRegistryError, ProjectManifestError) as exc:
        _err(str(exc))
        return 1

    if manifest.source_dir is None:
        _err("无法定位该项目的 workspace 根目录")
        return 1

    records = read_ledger(manifest.source_dir, limit=limit)
    if not records:
        _print("账本为空，该项目还没有任何执行记录。")
        return 0
    for rec in records:
        mark = "OK" if rec.success else "FAIL"
        line = (
            f"[{mark}] {rec.entrypoint} exit_code={rec.exit_code} "
            f"trigger={rec.trigger} started_at={rec.started_at} "
            f"finished_at={rec.finished_at}"
        )
        if rec.error_summary:
            line += f" error={rec.error_summary!r}"
        _print(line)
        if rec.detail:
            _print("    detail:\n      " + rec.detail.replace("\n", "\n      "))
    return 0


def _cmd_backlog(args: List[str]) -> int:
    """`mini-agent projects backlog <name> [list|add <summary> [source] [evidence_ref]]`

    对应 `next_doc/stock_watch_continuous_improvement_plan.md` 阶段 1
    第 4 项：人工不必进 agent 对话，也能直接查看/写入某个外部项目的
    改进积压账本。不带子命令或带 `list` 时列出全部条目（可选状态
    过滤），带 `add` 时追加一条状态为 `open` 的新条目，`source` 默认
    `user_feedback`（人工直接敲命令行录入的绝大多数场景就是这一类）。
    """
    from mini_agent.external_projects import ExternalProjectRegistry
    from mini_agent.external_projects.backlog import BacklogError, append_item, read_backlog
    from mini_agent.external_projects.manifest import ProjectManifestError
    from mini_agent.external_projects.registry import ExternalProjectRegistryError

    if not args:
        _err("用法: mini-agent projects backlog <name> [list [status]|add <summary> [source] [evidence_ref]]")
        return 1
    name = args[0]
    rest = args[1:]

    registry = ExternalProjectRegistry()
    try:
        manifest = registry.load_manifest_for(name)
    except (ExternalProjectRegistryError, ProjectManifestError) as exc:
        _err(str(exc))
        return 1
    if manifest.source_dir is None:
        _err("无法定位该项目的 workspace 根目录")
        return 1
    root = manifest.source_dir

    if not rest or rest[0] == "list":
        status_filter = rest[1] if len(rest) > 1 else None
        items = read_backlog(root, status=status_filter)
        if not items:
            _print("改进积压账本为空。")
            return 0
        for item in items:
            line = f"[{item.status}] {item.id} ({item.source}) {item.summary}"
            if item.evidence_ref:
                line += f" | evidence={item.evidence_ref}"
            _print(line)
        return 0

    if rest[0] == "add":
        if len(rest) < 2:
            _err("用法: mini-agent projects backlog <name> add <summary> [source] [evidence_ref]")
            return 1
        summary = rest[1]
        source = rest[2] if len(rest) > 2 else "user_feedback"
        evidence_ref = rest[3] if len(rest) > 3 else None
        try:
            item = append_item(root, source=source, summary=summary, evidence_ref=evidence_ref)
        except BacklogError as exc:
            _err(str(exc))
            return 1
        _print(f"已记录待办 {item.id}: {item.summary}")
        return 0

    _err(f"未知子命令 'backlog {rest[0]}'。可用: list, add")
    return 1


def _cmd_review(args: List[str]) -> int:
    """`mini-agent projects review <name>` — 生成一次周期性 review 的
    任务模板文本并打印到标准输出。

    对应 `next_doc/stock_watch_continuous_improvement_plan.md` 阶段 4。
    这条命令本身**不**发起真实的 agent 会话，也**不**把生成的任务模板
    注册进任何正在运行的 daemon 调度循环——那部分（接入
    `CronScheduler.add_job`）是该文档明确标注的"待接线"部分，本命令
    只负责把"这次 review 该带着什么材料、交代什么任务边界"这件事做
    对、且可以离线验证（不需要真的跑起一个 daemon）。当前用法：把输出
    的文本手动粘贴进一次 mini_agent 对话，或者存起来供未来接线时直接
    作为 `task_template` 使用。
    """
    from mini_agent.external_projects import ExternalProjectRegistry
    from mini_agent.external_projects.manifest import ProjectManifestError
    from mini_agent.external_projects.registry import ExternalProjectRegistryError
    from mini_agent.external_projects.review import build_review_task_template_for

    if not args:
        _err("用法: mini-agent projects review <n>")
        return 1
    name = args[0]

    registry = ExternalProjectRegistry()
    try:
        manifest = registry.load_manifest_for(name)
    except (ExternalProjectRegistryError, ProjectManifestError) as exc:
        _err(str(exc))
        return 1

    if not manifest.review.enabled:
        _err(
            f"项目 '{name}' 未开启 review（project.yaml 需要 "
            "`review: {enabled: true}`），但仍然生成一次任务模板供预览。"
        )
        # 不 return——未开启只是"daemon 不会自动周期性触发"，不妨碍手动
        # 预览一次任务模板长什么样，方便用户先看看再决定要不要开启。

    template = build_review_task_template_for(manifest)
    _print(template)
    return 0


_SUBCOMMANDS = {
    "list": _cmd_list,
    "status": _cmd_status,
    "run": _cmd_run,
    "register": _cmd_register,
    "unregister": _cmd_unregister,
    "enable": lambda args: _cmd_set_enabled(args, True),
    "disable": lambda args: _cmd_set_enabled(args, False),
    "ledger": _cmd_ledger,
    "backlog": _cmd_backlog,
    "review": _cmd_review,
}


def run_projects_cli(argv: List[str], project_root: Optional[Path] = None) -> int:
    """
    `mini-agent projects <sub> ...` 的入口，由 `cli/app.py::main()` 短路调用。

    `project_root` 参数与 `workflow`/`daemon` 等其它短路子命令保持同样
    的签名（来自 `_extract_project_root` 解析出的 `--project`/`--workspace`），
    但 `projects` 子命令的注册表本身是用户级、与任何单个项目根无关，
    因此当前不使用这个参数，只是保持调用签名一致，方便未来如果需要
    "只看某个 project_root 下相关的外部项目"这类过滤能力时不用改
    `app.py` 的调用点。
    """
    del project_root  # 当前未使用，见上方说明

    if not argv or argv[0] in ("-h", "--help"):
        _print(__doc__ or "")
        return 0

    sub, rest = argv[0], argv[1:]
    handler = _SUBCOMMANDS.get(sub)
    if handler is None:
        _err(f"未知子命令 '{sub}'。可用: {', '.join(sorted(_SUBCOMMANDS))}")
        return 1
    return handler(rest)
