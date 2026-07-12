"""
cli/commands/evolution.py — /evolution slash 命令处理（Stage 2.4）

对应 self_evolution_implementation_plan.md Stage 2.4：

/evolution log [N]            — StateRepo.log()，展示最近 N 条自我修改 commit（默认 10）
/evolution show <commit>      — 展示单条 commit 的完整结构化信息（含 diff）
/evolution diff <commit>      — StateRepo.diff()，展示某次 commit 的改动内容
/evolution revert <commit>    — StateRepo.revert()，按设计文档 4.3 节生成 revert commit，
                                  并自动写入一条 source="revert_record" 的 lesson

本命令组操作的 StateRepo 固定指向 `agent.cfg.project_root`——"自我修改"针对的
就是 agent 自己运行所在的项目仓库，不支持指定别的仓库路径（避免被误用为
通用 git 客户端，这不是本命令的定位）。
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

import mini_agent.ui.renderer as R

if TYPE_CHECKING:
    from mini_agent.evolution.state_repo import CommitInfo


def handle_evolution_cmd(args: list[str], agent=None) -> None:
    from mini_agent.evolution.state_repo import StateRepo, StateRepoError

    if agent is None:
        R.print_error("No active agent context for /evolution.")
        return

    sub = args[0] if args else "log"
    rest = args[1:]

    try:
        repo = StateRepo(agent.cfg.project_root)
    except StateRepoError as e:
        R.print_error(f"Failed to open StateRepo at {agent.cfg.project_root}: {e}")
        return

    if sub == "log":
        _handle_log(repo, rest)
    elif sub == "show":
        _handle_show(repo, rest)
    elif sub == "diff":
        _handle_diff(repo, rest)
    elif sub == "revert":
        _handle_revert(repo, rest, agent)
    elif sub == "outcomes":
        _handle_outcomes(rest, agent)
    elif sub in ("lessons-to-reminders", "lessons2reminders"):
        _handle_lessons_to_reminders(agent)
    else:
        R.print_error(
            "Usage: /evolution [log [N] | show <commit> | diff <commit> | "
            "revert <commit> | outcomes [--worsened] | lessons-to-reminders]"
        )


# ── /evolution log ───────────────────────────────────────────────────────────

def _handle_log(repo, rest: list[str]) -> None:
    limit = 10
    if rest:
        try:
            limit = max(1, int(rest[0]))
        except ValueError:
            R.print_error(f"Invalid count: {rest[0]!r} (expected an integer)")
            return

    commits = repo.log(limit=limit)
    if not commits:
        R.print_info("No self-evolution commits yet.")
        return

    from rich.table import Table
    from rich import box as rbox

    t = Table(box=rbox.SIMPLE, show_header=True, header_style="bold dim")
    t.add_column("Commit", style="cyan", min_width=8)
    t.add_column("Subject", min_width=30, max_width=60)
    t.add_column("Files", min_width=10, max_width=30)
    t.add_column("Date", min_width=12)

    for c in commits:
        files_preview = ", ".join(c.files[:2])
        if len(c.files) > 2:
            files_preview += f" (+{len(c.files) - 2} more)"
        t.add_row(c.commit[:8], c.subject, files_preview, c.date[:19])

    R.console.print("\n[bold]Self-Evolution History[/bold]  "
                     "[dim](/evolution show <commit> for details)[/dim]")
    R.console.print(t)
    R.console.print()


# ── /evolution show ──────────────────────────────────────────────────────────

def _handle_show(repo, rest: list[str]) -> None:
    if not rest:
        R.print_error("Usage: /evolution show <commit>")
        return
    commit_ref = rest[0]

    commits = repo.log(limit=200)
    match = _find_commit(commits, commit_ref)
    if match is None:
        R.print_error(f"Commit not found: {commit_ref} (try /evolution log to list recent commits)")
        return

    R.console.print(f"\n[bold cyan]{match.commit}[/bold cyan]")
    R.console.print(f"[bold]{match.subject}[/bold]")
    if match.body:
        R.console.print(f"[dim]{match.body}[/dim]")
    R.console.print(f"Author: {match.author}    Date: {match.date}")
    if match.files:
        R.console.print(f"Files: {', '.join(match.files)}")
    R.console.print()

    diff_text = repo.diff(f"{match.commit}~1", match.commit)
    if diff_text:
        R.print_diff(diff_text)
    else:
        R.print_info("(no diff available — likely the repository's first commit)")


# ── /evolution diff ───────────────────────────────────────────────────────────

def _handle_diff(repo, rest: list[str]) -> None:
    if not rest:
        R.print_error("Usage: /evolution diff <commit>")
        return
    commit_ref = rest[0]
    diff_text = repo.diff(f"{commit_ref}~1", commit_ref)
    if not diff_text:
        R.print_info(f"No diff found for {commit_ref} "
                      "(invalid commit, or it is the repository's first commit).")
        return
    R.print_diff(diff_text)


# ── /evolution revert ────────────────────────────────────────────────────────

def _handle_revert(repo, rest: list[str], agent) -> None:
    from mini_agent.evolution.state_repo import StateRepoError

    if not rest:
        R.print_error("Usage: /evolution revert <commit>")
        return
    commit_ref = rest[0]

    commits = repo.log(limit=200)
    match = _find_commit(commits, commit_ref)
    if match is None:
        R.print_error(f"Commit not found: {commit_ref} (try /evolution log to list recent commits)")
        return

    # T3 commit 的 revert 同样必须强制人审（设计文档 4.1 节："强制人审 + 不可被 agent 自我批准"）。
    # /evolution revert 是人类在 REPL 里主动敲下的命令，本身就是"人审"的体现，因此这里不再
    # 重复弹一次确认——但展示完整 diff 让人能在执行前看清楚要撤销什么，符合"diff 必须显式标红"
    # 的精神（即使该要求字面上是针对 T3 *写入*流程的，revert 同样是一次有影响的操作）。
    diff_text = repo.diff(f"{match.commit}~1", match.commit)
    if diff_text:
        R.console.print(f"\n[bold]About to revert:[/bold] {match.commit[:8]}  {match.subject}")
        R.print_diff(diff_text)

    try:
        revert_commit = repo.revert(match.commit)
    except StateRepoError as e:
        R.print_error(f"Revert failed: {e}")
        return

    R.print_success(f"Reverted {match.commit[:8]} → new commit {revert_commit[:8]}")

    _record_revert_lesson(agent, match, revert_commit)

    # [方案三] 若该 commit 正处于效果回填观察期，提前结束观察——
    # 继续观察一个已被撤销的 commit 没有意义。失败静默，不影响 revert 本身。
    try:
        from mini_agent.evolution import outcome_tracker
        from mini_agent.storage.paths import AgentPaths

        outcome_tracker.mark_reverted(AgentPaths(agent.cfg.project_root), match.commit)
    except Exception:
        pass


def _handle_outcomes(rest: list[str], agent) -> None:
    """
    [方案三] /evolution outcomes [--worsened]

    列出自我进化 commit 的效果回填记录（observing / improved / no_change /
    worsened / insufficient_data / reverted_by_user）。verdict == worsened
    的记录只是建议复核，不会自动 revert——最终决策权留给用户，与
    SoftGoalDeriver 推导出的 Goal 需要人工 accept/reject 是同一套设计哲学。
    """
    if agent is None:
        R.print_error("No active agent context for /evolution outcomes.")
        return

    from mini_agent.evolution import outcome_tracker
    from mini_agent.storage.paths import AgentPaths

    only_worsened = "--worsened" in rest
    paths = AgentPaths(agent.cfg.project_root)
    records = (
        outcome_tracker.get_revert_candidates(paths)
        if only_worsened else outcome_tracker.get_all(paths)
    )
    if not records:
        R.print_info(
            "No worsened outcome records." if only_worsened
            else "No outcome-tracking records yet (recorded automatically when "
                 "skill_propose commits are triggered by a lesson group)."
        )
        return

    from rich.table import Table
    from rich import box as rbox
    import time as _time

    verdict_style = {
        "improved": "green", "worsened": "bold red", "no_change": "yellow",
        "insufficient_data": "dim", "reverted_by_user": "dim",
    }

    t = Table(box=rbox.SIMPLE, show_header=True, header_style="bold dim")
    t.add_column("Commit", style="cyan", min_width=8)
    t.add_column("Lesson Group", min_width=12, max_width=30)
    t.add_column("Status", min_width=10)
    t.add_column("Baseline → Post", min_width=14)
    t.add_column("Verdict", min_width=14)
    t.add_column("Committed", min_width=12)

    records.sort(key=lambda r: -r.committed_at)
    for r in records:
        verdict_text = r.verdict or "-"
        style = verdict_style.get(r.verdict, "")
        post = "?" if r.post_trigger_count is None else str(r.post_trigger_count)
        date_str = _time.strftime("%Y-%m-%d", _time.localtime(r.committed_at))
        t.add_row(
            r.commit_id[:8],
            r.trigger_lesson_group_id[:30],
            r.status,
            f"{r.baseline_trigger_count} → {post}",
            f"[{style}]{verdict_text}[/{style}]" if style else verdict_text,
            date_str,
        )

    R.console.print(t)

    worsened = [r for r in records if r.verdict == "worsened"]
    if worsened and not only_worsened:
        R.print_warning(
            f"{len(worsened)} commit(s) judged 'worsened' after their observation window — "
            f"consider reviewing: /evolution show <commit> or /evolution revert <commit>"
        )



    """
    设计文档 4.3 节："回退记录反哺 lesson 库——每次 revert 生成一条
    source='revert_record' 的 lesson"。

    复用 Stage 1 已经打通的 MemoryEntry 写入路径（agent._memory.add() +
    agent._append_memory_delta()），与 SessionEnd 反思 / 规则触发 lesson
    走同一套存储，保证后续检索/剪枝/能力地图等机制对三种来源一视同仁。

    失败（memory 未启用、写入异常等）只警告，不影响 revert 本身已经成功这件事——
    revert 是一次 git 操作，已经完成；lesson 记录是锦上添花的审计产物。
    """
    if agent is None or getattr(agent, "_memory", None) is None:
        return
    try:
        from mini_agent.perception.memory_store import MemoryEntry

        trigger = f"曾提案改动 {reverted.commit[:8]}（{reverted.subject}），已通过 /evolution revert 撤销"
        outcome = f"该改动被判定不应保留，已生成 revert commit {revert_commit[:8]} 撤销其效果"
        entry = MemoryEntry(
            session_id=getattr(agent, "session_id", "") or "",
            summary="",
            key_outcomes=[],
            tags=["lesson", "revert_record"],
            model=getattr(agent.cfg, "model", ""),
            entry_type="lesson",
            trigger=trigger,
            outcome=outcome,
            root_cause="",
            suggested_action=f"不建议未经修改地重新尝试与 {reverted.commit[:8]} 同方向的改动",
            confidence=0.9,  # 人工明确执行的 revert，可信度高于规则触发与自由反思
            occurrence_count=1,
            source="revert_record",
        )
        if entry.scope == "global" and getattr(agent, "_global_memory", None):
            agent._global_memory.add(entry)
        else:
            agent._memory.add(entry)
        if hasattr(agent, "_append_memory_delta"):
            agent._append_memory_delta(entry)
    except Exception as e:
        R.print_warning(f"[evolution] failed to record revert lesson: {e}")


# ── /evolution lessons-to-reminders ──────────────────────────────────────────

def _record_revert_lesson(agent, match, revert_commit: str) -> None:
    """
    设计文档 4.3 节："回退记录反哺 lesson 库——每次 revert 生成一条
    source='revert_record' 的 lesson"。

    复用 Stage 1 已经打通的 MemoryEntry 写入路径（agent._memory.add() +
    agent._append_memory_delta()），与 SessionEnd 反思 / 规则触发 lesson
    走同一套存储，保证后续检索/剪枝/能力地图等机制对三种来源一视同仁。

    失败（memory 未启用、写入异常等）只警告，不影响 revert 本身已经成功这件事——
    revert 是一次 git 操作，已经完成；lesson 记录是锦上添花的审计产物。
    """
    if agent is None or getattr(agent, "_memory", None) is None:
        return
    try:
        from mini_agent.perception.memory_store import MemoryEntry

        trigger = f"曾提案改动 {match.commit[:8]}（{match.subject}），已通过 /evolution revert 撤销"
        outcome = f"该改动被判定不应保留，已生成 revert commit {revert_commit[:8]} 撤销其效果"
        entry = MemoryEntry(
            session_id=getattr(agent, "session_id", "") or "",
            summary="",
            key_outcomes=[],
            tags=["lesson", "revert_record"],
            model=getattr(agent.cfg, "model", ""),
            entry_type="lesson",
            trigger=trigger,
            outcome=outcome,
            root_cause="",
            suggested_action=f"不建议未经修改地重新尝试与 {match.commit[:8]} 同方向的改动",
            confidence=0.9,  # 人工明确执行的 revert，可信度高于规则触发与自由反思
            occurrence_count=1,
            source="revert_record",
        )
        if entry.scope == "global" and getattr(agent, "_global_memory", None):
            agent._global_memory.add(entry)
        else:
            agent._memory.add(entry)
        if hasattr(agent, "_append_memory_delta"):
            agent._append_memory_delta(entry)
    except Exception as e:
        R.print_warning(f"[evolution] failed to record revert lesson: {e}")


def _handle_lessons_to_reminders(agent) -> None:
    """
    [具身改进 B2] 扫描 lesson memory，把达到阈值的分组转化为 pre_tool reminder。

    - human_feedback 来源的分组：直接激活（写入 reminder 目录，立即生效）。
    - 仅 self_reflection 来源、达到 T1 聚合门槛（occurrence≥3 且来自≥2个
      session）的分组：写成草稿（drafts/ 子目录，enabled: false），需要
      用户手动审阅后提升（lesson_to_reminder.promote_draft()）。
    """
    if agent is None or getattr(agent, "_memory", None) is None:
        R.print_error("[evolution] 当前 agent 未启用 memory，无法扫描 lesson。")
        return

    from pathlib import Path
    from mini_agent.evolution.lesson_to_reminder import LessonToReminderBridge

    reminder_cfg = getattr(getattr(agent, "cfg", None), "reminder", None)
    custom_dir = getattr(reminder_cfg, "custom_dir", None) if reminder_cfg else None
    reminder_dir = Path(custom_dir) if custom_dir else (
        Path(agent.cfg.project_root) / ".agent" / "reminders"
    )

    entries = agent._memory.all_entries()
    bridge = LessonToReminderBridge(reminder_dir)
    generated = bridge.scan(entries)

    if not generated:
        R.print_info("[evolution] 没有达到阈值的 lesson 分组，未生成新的 reminder。")
        return

    written = bridge.write(generated)
    activated = [p for gr, p in zip(generated, written) if gr.activated]
    drafted = [p for gr, p in zip(generated, written) if not gr.activated]

    if activated:
        R.print_info(f"[evolution] 已激活 {len(activated)} 条 reminder（human_feedback 来源）：")
        for p in activated:
            R.print_info(f"  + {p}")
    if drafted:
        R.print_info(
            f"[evolution] 已生成 {len(drafted)} 条草稿（达到 T1 聚合门槛，待审阅）："
        )
        for p in drafted:
            R.print_info(f"  ~ {p}")

    # 新写入的 reminder 立即生效。若 cfg.reminder.custom_dir 此前未设置（默认
    # None），ReminderManager 内部的 loader 不知道 reminder_dir 这个新目录，
    # 需要先把它接上，reload() 才能真正扫到刚写的文件（drafts/ 不受影响，
    # 本来就不会被 ReminderLoader 扫描到）。
    if activated and getattr(agent, "_reminder_mgr", None) is not None:
        try:
            if reminder_cfg is not None and getattr(reminder_cfg, "custom_dir", None) is None:
                reminder_cfg.custom_dir = reminder_dir
                agent._reminder_mgr._loader._custom_dir = reminder_dir
            agent._reminder_mgr.reload()
            R.print_info("[evolution] ReminderManager 已热重载。")
        except Exception as e:
            R.print_warning(f"[evolution] ReminderManager 热重载失败: {e}")


# ── 辅助 ──────────────────────────────────────────────────────────────────────

def _find_commit(commits: list, ref: str) -> Optional["CommitInfo"]:
    """按完整 hash 或前缀匹配 commit。优先精确匹配，再退化为前缀匹配（取第一个命中）。"""
    for c in commits:
        if c.commit == ref:
            return c
    for c in commits:
        if c.commit.startswith(ref):
            return c
    return None
