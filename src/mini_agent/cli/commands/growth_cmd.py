"""
cli/commands/growth_cmd.py — /growth 命令处理（成长顾问，见
next_doc/growth_advisor_design.md）。

子命令：
  /growth                 — 展示候选队列概况（pending 列表 + 已生成报告数）
  /growth scan             — 手动触发一轮信号扫描 + 候选生成 + Top-N 调研报告
                              （等价于 sys:growth_advisor_daily 的内容，
                              不依赖那条 cron job 是否 enabled）
  /growth list              — 列出当前 pending 候选（含 id，便于 accept/dismiss）
  /growth dismiss <id> [reason]  — 忽略一个候选（标记 dismissed，进入冷却期）；
                              reason 可选，见下方枚举，不传则记为 unspecified，
                              行为与之前版本一致
  /growth accept <id>       — 采纳一个候选（标记 accepted，记入反馈台账）
  /growth report <id>       — 查看某候选已生成的调研报告正文
  /growth retrospective     — 生成/展示月度成长复盘摘要
  /growth align             — 查看兴趣方向 ⇄ Goal 对齐分析（见
                              next_doc/growth_advisor_goal_cron_integration_plan.md
                              阶段 A）：哪些方向有兴趣信号但没建目标、
                              哪些已建目标但停滞
  /growth adopt-goal <id>   — 把一个候选落地成一个 GoalBacklog 目标
                              （阶段 B，要求候选已有调研报告）

dismiss 的 reason 可选值：
  not_interested    — 这个方向我不关心（参与方向/类别置信度衰减）
  bad_timing        — 方向可以，但现在不是时候（参与衰减）
  report_not_useful — 方向没错，是报告没写好（不参与衰减，只计入报告
                       质量诊断，见月度复盘 top_report_quality_flags）
  unspecified       — 未指定（默认值，参与衰减，兼容旧行为）
"""

from __future__ import annotations

import mini_agent.ui.renderer as R


def _get_paths(agent):
    if agent is None:
        return None
    paths = getattr(agent, "_paths", None)
    if paths is not None:
        return paths
    cfg = getattr(agent, "cfg", None)
    if cfg is None:
        return None
    try:
        from mini_agent.storage.paths import AgentPaths
        return AgentPaths(cfg.project_root)
    except Exception:
        return None


def _get_cfg(agent):
    cfg = getattr(agent, "cfg", None) if agent else None
    growth_cfg = getattr(cfg, "growth_advisor", None) if cfg is not None else None
    if growth_cfg is None:
        from mini_agent.config.models import GrowthAdvisorConfig
        growth_cfg = GrowthAdvisorConfig()
    return growth_cfg


def _get_profile(paths):
    from mini_agent.profile import UserProfileManager
    mgr = UserProfileManager(paths)
    return mgr, mgr.load()


def _get_memory_store(paths):
    # [next_doc/growth_advisor_diagnostics_and_language_fix_plan.md
    # 方向一] 之前是 `MemoryStore(paths)`——把整个 AgentPaths 实例当路径
    # 传了进去，静默降级为空记忆列表，导致 `/growth scan` 之类命令在 0
    # 条记忆上跑。改用统一的工具函数。
    try:
        from mini_agent.perception.memory_factory import build_default_memory_store
        return build_default_memory_store(paths)
    except Exception:
        return None


def _get_goal_backlog(paths):
    """[growth_advisor_goal_cron_integration_plan.md] 尽力构造一个
    `GoalBacklog`，拿不到就返回 None——对齐分析/落地目标命令都要能在
    goals.json 暂不可用时给出明确报错，而不是让异常直接冒出去。
    """
    try:
        from mini_agent.perception.goal_backlog import GoalBacklog
        return GoalBacklog(paths)
    except Exception:
        return None


def _get_llm_helper(agent):
    """把 `agent.llm_helper`（`LLMHelper` 实例，见 `llm/service.py`）包成
    `growth_advisor` 期望的 `Callable[[str], str]` 约定（`llm_helper(prompt)
    -> str`）——`LLMHelper` 本身不可直接调用，只暴露 `.ask(prompt)` 等
    方法，这里统一收敛成一个闭包，任何一步拿不到就返回 None（各调用点
    的 LLM 增强/生成本来就要求"拿不到就走无 LLM 的默认路径"）。
    """
    if agent is None:
        return None
    helper = getattr(agent, "llm_helper", None)
    if helper is None:
        return None
    return lambda prompt: helper.ask(prompt)


def handle_growth_cmd(args: list[str], agent=None) -> None:
    paths = _get_paths(agent)
    if paths is None:
        R.print_error("Cannot access project paths (agent not initialized).")
        return

    from mini_agent.evolution import growth_advisor as ga

    sub = args[0] if args else ""

    if sub == "scan":
        cfg = _get_cfg(agent)
        if not cfg.enabled:
            R.print_warning("成长顾问当前已在配置中关闭（growth_advisor.enabled=False），跳过。")
            return
        mgr, profile = _get_profile(paths)
        store = _get_memory_store(paths)
        result = ga.run_daily_cycle(paths, cfg, profile, store, llm_helper=_get_llm_helper(agent))
        mgr.save()
        if result.get("skipped"):
            R.print_info(f"跳过：{result.get('reason')}")
            return
        n_cand = len(result.get("new_candidates", []))
        n_rep = len(result.get("reports", []))
        R.print_info(f"本轮扫描完成：新增/更新候选 {n_cand} 条，生成调研报告 {n_rep} 份。")
        return

    if sub == "list" or sub == "":
        backlog = ga.GrowthBacklog(paths)
        pending = backlog.pending()
        if not pending:
            R.print_info("当前没有待处理的成长方向候选。可执行 /growth scan 手动触发一轮扫描。")
            return
        R.console.print(f"[bold]待处理的成长方向候选（{len(pending)} 条）：[/bold]")
        for c in sorted(pending, key=lambda x: -x.confidence):
            report_mark = "（已有调研报告）" if c.report_id else ""
            R.console.print(
                f"  [{c.candidate_id}] {c.title}  confidence={c.confidence}"
                f"  evidence={c.evidence_count}{report_mark}"
            )
            R.console.print(f"      {c.rationale}")
        return

    if sub in ("accept", "dismiss"):
        if len(args) < 2:
            R.print_error(f"用法：/growth {sub} <candidate_id>" + ("  [reason]" if sub == "dismiss" else ""))
            if sub == "dismiss":
                R.print_info(
                    "可选 reason（不传则记为 unspecified，行为等价于此前版本）："
                    "not_interested(不感兴趣) | bad_timing(时机不对) | "
                    "report_not_useful(方向没错，报告没写好，不会压低该方向今后的置信度)"
                )
            return
        cid = args[1]
        backlog = ga.GrowthBacklog(paths)
        status = ga.STATUS_ACCEPTED if sub == "accept" else ga.STATUS_DISMISSED
        cand = backlog.set_status(cid, status)
        if cand is None:
            R.print_error(f"未找到候选：{cid}")
            return
        reason = None
        if sub == "dismiss":
            reason = args[2] if len(args) >= 3 else None
            if reason is not None and reason not in ga._VALID_DISMISS_REASONS:
                R.print_error(
                    f"未知 reason：{reason}，可选值：not_interested | bad_timing | "
                    "report_not_useful | unspecified"
                )
                return
        ga.GrowthFeedbackLedger(paths).record(cid, status, reason=reason)
        verb = "已采纳" if sub == "accept" else "已忽略"
        R.print_info(f"{verb}：{cand.title}")
        return

    if sub == "report":
        if len(args) < 2:
            R.print_error("用法：/growth report <candidate_id>")
            return
        cid = args[1]
        backlog = ga.GrowthBacklog(paths)
        cand = backlog.get(cid)
        if cand is None:
            R.print_error(f"未找到候选：{cid}")
            return
        if not cand.report_id:
            cfg = _get_cfg(agent)
            report = ga.generate_growth_report(paths, cand, llm_helper=_get_llm_helper(agent))
            R.print_info(f"已生成调研报告：{report.body_path}")
        else:
            report = ga.get_report_by_id(paths, cand.report_id)
        if report is None:
            R.print_error("报告索引缺失，请重新执行 /growth report 生成。")
            return
        from pathlib import Path
        body_path = Path(report.body_path)
        if body_path.exists():
            R.console.print(body_path.read_text(encoding="utf-8"))
        else:
            R.print_error(f"报告文件缺失：{body_path}")
        return

    if sub == "retrospective":
        summary = ga.monthly_retrospective_summary(paths)
        R.console.print("[bold]成长顾问月度复盘：[/bold]")
        for k, v in summary.items():
            R.console.print(f"  {k}: {v}")
        return

    if sub == "align":
        cfg = _get_cfg(agent)
        mgr, profile = _get_profile(paths)
        goal_backlog = _get_goal_backlog(paths)
        llm_helper = _get_llm_helper(agent) if getattr(cfg, "goal_alignment_llm_enabled", False) else None
        alignment = ga.goal_growth_alignment(
            paths, profile, cfg=cfg, goal_backlog=goal_backlog, llm_helper=llm_helper
        )
        if not alignment.get("enabled", True):
            R.print_info("对齐分析当前已关闭（growth_advisor.goal_alignment_enabled=False）。")
            return
        unmatched = alignment.get("unmatched_interests", [])
        linked = alignment.get("linked_goals", [])
        suggested = alignment.get("llm_suggested_matches", [])
        R.console.print("[bold]兴趣方向 ⇄ 目标 对齐分析：[/bold]")
        if unmatched:
            R.console.print(f"  有兴趣信号但还没建目标（{len(unmatched)} 个）：")
            for row in unmatched:
                cid_mark = f"  [{row['candidate_id']}]" if row.get("candidate_id") else ""
                R.console.print(
                    f"    - {row['topic']}{cid_mark}  证据数={row.get('evidence_count')}"
                )
        else:
            R.console.print("  没有找到\"有兴趣但没建目标\"的方向。")
        if suggested:
            R.console.print(f"  LLM 建议关注的潜在配对（{len(suggested)} 个，字面不完全一致，仅供参考）：")
            for row in suggested:
                R.console.print(
                    f"    - {row['topic']} ≈ 目标 [{row['goal_id']}] {row['goal_title']}"
                )
        if linked:
            R.console.print(f"  已关联目标的方向（{len(linked)} 个）：")
            for row in linked:
                mark = "  ⚠️ 停滞" if row["stalled"] else ""
                R.console.print(
                    f"    - {row['topic']} → 目标 [{row['goal_id']}] {row['goal_title']}"
                    f"  状态={row['goal_status']}{mark}"
                )
        else:
            R.console.print("  当前没有已关联目标的方向。")
        return

    if sub == "adopt-goal":
        if len(args) < 2:
            R.print_error("用法：/growth adopt-goal <candidate_id>")
            return
        cid = args[1]
        backlog = ga.GrowthBacklog(paths)
        cand = backlog.get(cid)
        if cand is None:
            R.print_error(f"未找到候选：{cid}")
            return
        goal_backlog = _get_goal_backlog(paths)
        try:
            goal = ga.adopt_candidate_as_goal(paths, cand, goal_backlog=goal_backlog)
        except ValueError as exc:
            R.print_error(str(exc))
            return
        except RuntimeError as exc:
            R.print_error(str(exc))
            return
        R.print_info(f"已创建目标 [{goal.id}] {goal.title}，并关联到候选 {cid}。")
        return

    R.print_error(
        "未知子命令。可用：/growth [list] | scan | accept <id> | dismiss <id> | report <id> | "
        "retrospective | align | adopt-goal <id>"
    )
