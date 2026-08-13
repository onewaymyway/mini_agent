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
  /growth material <id>     — 查看某候选已生成的学习素材（学习路径 +
                              资源清单 + 第一个可执行任务；跟报告是
                              两份独立产物，见 growth_advisor_autonomous_
                              search_and_material_improvement_plan.md
                              "报告与学习素材分层"）
  /growth retrospective     — 生成/展示月度成长复盘摘要
  /growth align             — 查看兴趣方向 ⇄ Goal 对齐分析（见
                              next_doc/growth_advisor_goal_cron_integration_plan.md
                              阶段 A）：哪些方向有兴趣信号但没建目标、
                              哪些已建目标但停滞
  /growth align --adopt-all — [方向 A3] 批量落地"有兴趣但没建目标"的
                              方向，复用 auto_pursue_candidate() 整条
                              链路（生成报告 → 落地成 Goal → 确认执行
                              规范 → 绑定周期性）；单次最多处理
                              growth_advisor.goal_alignment_adopt_all_
                              max_batch（默认 3）条，避免一次性触发过多
                              LLM 调用，剩余条目下次调用继续处理
  /growth adopt-goal <id>   — 把一个候选落地成一个 GoalBacklog 目标
                              （阶段 B，要求候选已有调研报告）
  /growth timeline <id>     — 查看某方向的完整成长轨迹时间线（发现 →
                              生成报告 → 采纳/忽略 → 落地成目标 → 目标
                              当前状态），见
                              next_doc/growth_advisor_active_search_and_
                              lifecycle_plan.md 方向二

dismiss 的 reason 可选值：
  not_interested    — 这个方向我不关心（参与方向/类别置信度衰减）
  bad_timing        — 方向可以，但现在不是时候（参与衰减）
  report_not_useful — 方向没错，是报告没写好（不参与衰减，只计入报告
                       质量诊断，见月度复盘 top_report_quality_flags）
  unspecified       — 未指定（默认值，参与衰减，兼容旧行为）
"""

from __future__ import annotations

import time

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


def _get_web_search_fn(agent):
    """[growth_advisor_active_search_and_lifecycle_plan.md 方向一]
    把 Agent 已注册的 `tools/builtin.py::web_search()` 包成
    `generate_growth_report()` 期望的 `web_search_fn(query, max_results)
    -> str` 约定。拿不到 agent/cfg 时返回 `None`——调用方据此判断"调用
    方是否具备检索工具"，不引入新的检索通道。
    """
    if agent is None:
        return None
    cfg = getattr(agent, "cfg", None)
    if cfg is None:
        return None
    try:
        from mini_agent.tools.builtin import web_search as _web_search_tool
    except Exception:
        return None
    return lambda query, max_results=5: _web_search_tool(query, max_results=max_results)


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
        # [next_doc/growth_advisor_cron_search_and_status_history_plan.md
        # 方向一] `web_search_fn` 直接复用 tools/builtin.py 的模块级函数
        # （跟 tech_radar_search.py 默认使用的是同一个实现），是否真正
        # 触发仍然由 cfg.cron_triggered_active_search_enabled 这个显式
        # opt-in 开关决定——传入本身不代表一定会调用检索。
        from mini_agent.tools.builtin import web_search as _web_search_fn
        result = ga.run_daily_cycle(
            paths, cfg, profile, store,
            llm_helper=_get_llm_helper(agent), web_search_fn=_web_search_fn,
        )
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
            _, profile = _get_profile(paths)
            report = ga.generate_growth_report(
                paths, cand, llm_helper=_get_llm_helper(agent),
                profile=profile, cfg=cfg, web_search_fn=_get_web_search_fn(agent),
            )
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
        # [growth_advisor_autonomous_search_and_material_improvement_
        # plan.md 阶段三后续：生成后自检结果的展示] 只有这份报告真的
        # 做过引用自检（开了外部背景 + 拿到摘录 + LLM 生成）才展示，
        # 其余情况保持原样不打印任何额外内容，不干扰既有输出。
        cc = getattr(report, "citation_check", None)
        if cc:
            hallucinated = cc.get("hallucinated_refs") or []
            R.print_info(
                f"[自检] 引用了 {cc.get('cited_count', 0)}/{cc.get('excerpts_total', 0)} "
                f"条摘录"
                + (f"，检测到 {len(hallucinated)} 处疑似编造引用：{'、'.join(hallucinated)}"
                   if hallucinated else "，未检测到编造引用")
            )
        return

    if sub == "material":
        if len(args) < 2:
            R.print_error("用法：/growth material <candidate_id>")
            return
        cid = args[1]
        backlog = ga.GrowthBacklog(paths)
        cand = backlog.get(cid)
        if cand is None:
            R.print_error(f"未找到候选：{cid}")
            return
        if not cand.material_id:
            # 学习素材不强制要求先有报告，但如果这个候选已经有报告，
            # 复用报告的一句话摘要作为素材开头的背景（见
            # `generate_learning_material()` 的 `report` 参数说明）。
            report = ga.get_report_by_id(paths, cand.report_id) if cand.report_id else None
            material = ga.generate_learning_material(
                paths, cand, llm_helper=_get_llm_helper(agent), report=report,
            )
            R.print_info(f"已生成学习素材：{material.body_path}")
        else:
            material = ga.get_material_by_id(paths, cand.material_id)
        if material is None:
            R.print_error("学习素材索引缺失，请重新执行 /growth material 生成。")
            return
        from pathlib import Path
        body_path = Path(material.body_path)
        if body_path.exists():
            R.console.print(body_path.read_text(encoding="utf-8"))
        else:
            R.print_error(f"学习素材文件缺失：{body_path}")
        return

    if sub == "timeline":
        if len(args) < 2:
            R.print_error("用法：/growth timeline <candidate_id>")
            return
        cid = args[1]
        backlog = ga.GrowthBacklog(paths)
        cand = backlog.get(cid)
        if cand is None:
            R.print_error(f"未找到候选：{cid}")
            return
        goal_backlog = _get_goal_backlog(paths)
        events = ga.growth_topic_lifecycle(paths, cand.dedupe_key(), goal_backlog=goal_backlog)
        if not events:
            R.print_info("暂无可展示的成长轨迹。")
            return
        R.console.print(f"[bold]{cand.title} 的成长轨迹：[/bold]")
        for e in events:
            ts_str = time.strftime("%Y-%m-%d", time.localtime(e["ts"])) if e.get("ts") else "?"
            R.console.print(f"  [{ts_str}] {e['label']}")
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

        if len(args) >= 2 and args[1] == "--adopt-all":
            # [growth_advisor_autonomy_deepening_plan.md 方向 A3] 批量
            # 落地"有兴趣但没建目标"的方向，复用 auto_pursue_candidate()
            # 整条链路，单次最多处理 cfg.goal_alignment_adopt_all_max_batch
            # 条，避免一次性触发过多 LLM 调用。
            # CLI 场景没有 daemon 的 CronScheduler 实例可用，绑定周期性
            # 这一步会在 `auto_pursue_candidate()` 内部优雅退化（记一条
            # errors 提示"当前上下文拿不到 CronScheduler"，Goal 本身仍会
            # 创建成功），跟单条 `/growth accept` 在非 daemon 场景下的
            # 行为一致。
            cron_scheduler = None
            result = ga.batch_adopt_unmatched_interests(
                paths, cfg, profile, goal_backlog=goal_backlog,
                cron_scheduler=cron_scheduler, llm_helper=llm_helper,
            )
            processed = result.get("processed", [])
            skipped = result.get("skipped", [])
            if not processed and not skipped:
                R.print_info("没有找到\"有兴趣但没建目标\"的方向，无需批量落地。")
                return
            R.console.print(f"[bold]批量落地结果（{len(processed)} 条已处理）：[/bold]")
            for entry in processed:
                if entry.get("goal_id"):
                    R.print_info(f"  ✅ {entry['topic']} → 目标 [{entry['goal_id']}]")
                else:
                    R.print_error(f"  ❌ {entry['topic']}：{'；'.join(entry.get('errors') or ['未知原因'])}")
                for err in (entry.get("errors") or []):
                    if entry.get("goal_id"):
                        R.print_warning(f"      ⚠️ {err}")
            if skipped:
                R.console.print(f"  跳过（无对应候选记录，{len(skipped)} 条）：")
                for row in skipped:
                    R.console.print(f"    - {row['topic']}")
            remaining = result.get("remaining_count", 0)
            if remaining:
                remaining_topics = result.get("remaining_topics", [])
                R.print_info(f"还有 {remaining} 条未处理（本次批量上限已用完），可再次执行 /growth align --adopt-all 继续。")
                if remaining_topics:
                    R.console.print("  待处理：" + "、".join(remaining_topics))
            return

        if len(args) >= 2 and args[1] == "--confirm-match":
            # [growth_advisor_autonomy_deepening_plan_v2.md 方向 2] 把一条
            # `llm_suggested_matches` 里的建议确认成正式关联：
            # /growth align --confirm-match "<topic>" <goal_id>
            if len(args) < 4:
                R.print_error("用法：/growth align --confirm-match \"<兴趣方向>\" <goal_id>")
                return
            topic = args[2]
            goal_id = args[3]
            result = ga.confirm_llm_suggested_match(paths, topic, goal_id, goal_backlog=goal_backlog)
            if result.get("ok"):
                R.print_info(f"✅ 已将「{topic}」关联到目标 [{result['goal_id']}] {result.get('goal_title')}")
            else:
                R.print_error(f"关联失败：{result.get('reason')}")
            return

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
                    f"  （确认：/growth align --confirm-match \"{row['topic']}\" {row['goal_id']}）"
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
        "retrospective | align [--adopt-all] | adopt-goal <id> | timeline <id>"
    )
