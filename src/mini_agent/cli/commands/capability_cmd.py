"""
cli/commands/capability_cmd.py — /capability 命令处理（人设能力自主学习，见
next_doc/persona_capability_learning_design.md）。

补齐设计文档 §4 里发现的那一层缺失：`cron_scheduler.py` 的 `sys:` 内置任务
是"生成一段 task_template 文本交给 Agent 带着工具执行"的模式（参照
`sys:growth_advisor_daily` 引用 `/growth scan`），不是直接调用 Python 函数。
本模块提供的 `/capability cycle` 就是 `sys:capability_learning_cycle`
引用的中间层命令（该 cron job 已注册，默认 enabled=True，见
cron_scheduler.py SYSTEM_JOBS 里对应条目的说明）。

子命令：
  /capability                    — 展示所有 Track 概况（标题/状态/覆盖率）
  /capability list                — 同上（显式别名，风格对齐 /growth list）
  /capability create <title> | <persona_desc> [--llm-draft]
                                   — 创建一个 knowledge 型 Track。默认空
                                     大纲，之后可在看板手动补充子主题；
                                     加 --llm-draft 时用 agent 的
                                     llm_helper 起草一份初始大纲（§14 P2，
                                     拿不到 LLM 或起草失败时静默退回
                                     空大纲，不报错）
  /capability cycle                — 手动触发一轮学习循环（等价于
                                     sys:capability_learning_cycle 的内容，
                                     不依赖那条 cron job 是否已注册/enabled）。
                                     是否使用真实检索由
                                     capability_learning.retriever_enabled
                                     配置项控制（默认 True）：关闭时安全
                                     跳过需要检索的子主题并记台账；默认打开时
                                     具体走哪种检索实现由
                                     capability_learning.retriever_mode 决定
                                     （[v0.22 §14.4] "web_search"=默认，
                                     make_web_search_retriever 接
                                     web_search；"agent"=make_agent_retriever，
                                     受限工具集 SubAgent 自主调研，可用
                                     skill_list/skill_activate 等），检索结果
                                     写入前仍会经过 §13.3-g 合规过滤
                                     （make_wiki_writer 里已经接好，不受这个
                                     开关影响）
  /capability questions [track_id] — 列出 pending 状态的待回答问题
  /capability questions --sweep-expired
                                   — 清理超过 TTL 未回答的问题，标记为
                                     expired（§3.3），供
                                     sys:capability_question_sweep 引用
  /capability answer <question_id> <answer text>
                                   — 提交一条问题的回答（下一轮 cycle 会消费）
  /capability suggestions [track_id]
                                   — 列出 pending 状态的大纲动态生长建议
                                     （v0.21 §13.2-f，消费已回答问题时由
                                     llm_helper 提炼产生，见
                                     generate_outline_suggestion_from_answer）
  /capability suggestions accept <suggestion_id>
                                   — 采纳一条建议，追加为大纲新子主题
  /capability suggestions dismiss <suggestion_id>
                                   — 忽略一条建议
  /capability migrate-volatility  — [next_doc/capability_wiki_freshness_
                                     improvement_plan.md 阶段 2] 一次性把
                                     存量子主题里 volatility=="stable"（永
                                     不过期）的批量改成 "periodic"（30 天
                                     刷新周期）。幂等，可重复执行；不影响
                                     新建子主题（默认值已改为 periodic）。
  /capability refresh-all [track_id]
                                   — 把已判定 covered 的子主题批量重置为
                                     partial，立刻重新进入下一轮检索候选池
                                     （不用等 volatility 的周期性刷新窗口）。
                                     不传 track_id 时对所有 Track 生效；
                                     不清空已有 wiki 页面，重新检索到新内容
                                     前旧内容仍可读。幂等，可重复执行。
  /capability adopt-goal <track_id> <topic_id>
                                   — [next_doc/initiative_systems_
                                     unification_plan.md §4.2 阶段二] 把
                                     指定 Track 下的一个子主题落地成
                                     GoalBacklog 里的一个 Goal，交给目标树
                                     执行引擎 + ResourceArbiter 推进（与
                                     /growth adopt-goal 对称）。幂等：已
                                     落地过的子主题直接返回已有 Goal。
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


def _fmt_coverage(track) -> str:
    total = len(track.outline)
    if total == 0:
        return "0/0"
    covered = sum(1 for t in track.outline if t.coverage_state == "covered")
    return f"{covered}/{total}"


def _get_llm_helper(agent):
    """把 `agent.llm_helper` 包成 `Callable[[str], str]`，与
    `growth_cmd.py::_get_llm_helper` 完全同款约定，直接复用同一种
    "拿不到就返回 None，调用方退回无 LLM 默认路径"的克制。不共用同一份
    代码（而是各自 cli/commands 模块内一份小函数）是这个代码库里已有的
    既成模式（growth_cmd.py/goal_mode_cmd.py 等都是各自内联），这里跟随
    既有约定，不引入新的共享工具模块。"""
    if agent is None:
        return None
    helper = getattr(agent, "llm_helper", None)
    if helper is None:
        return None
    return lambda prompt: helper.ask(prompt)


def _print_tracks(tracks) -> None:
    if not tracks:
        R.print_info("暂无 CapabilityTrack，使用 /capability create <title> | <persona_desc> 创建一个。")
        return
    for t in tracks:
        R.print_info(
            f"[{t.track_id}] {t.title}  status={t.status}  target_type={t.target_type}  "
            f"覆盖率={_fmt_coverage(t)}  wiki_tag={t.wiki_tag}"
        )


def handle_capability_cmd(args: list[str], agent=None) -> None:
    paths = _get_paths(agent)
    if paths is None:
        R.print_error("Cannot access project paths (agent not initialized).")
        return

    from mini_agent.evolution.capability_learning import (
        CapabilityQuestionStore,
        CapabilityTrackStore,
        make_wiki_writer,
        run_capability_learning_cycle,
    )

    sub = args[0] if args else ""

    if sub in ("", "list"):
        store = CapabilityTrackStore(paths)
        _print_tracks(store.list_tracks())
        return

    if sub == "create":
        rest = " ".join(a for a in args[1:] if a not in ("--llm-draft", "--persona")).strip()
        use_llm_draft = "--llm-draft" in args
        target_type = "persona" if "--persona" in args else "knowledge"
        if not rest:
            R.print_error("用法：/capability create <title> | <persona_desc> [--llm-draft] [--persona]")
            return
        if "|" in rest:
            title, _, persona_desc = rest.partition("|")
        else:
            title, persona_desc = rest, rest
        title = title.strip()
        persona_desc = persona_desc.strip() or title
        if not title:
            R.print_error("用法：/capability create <title> | <persona_desc> [--llm-draft] [--persona]")
            return
        store = CapabilityTrackStore(paths)
        # --llm-draft：用 draft_outline_with_llm() 起草初始大纲（§14 P2，
        # opt-in）。拿不到 llm_helper（agent 未初始化/无 LLM 上下文）时
        # 静默退回空大纲，不报错——这跟 growth_cmd.py 里同款 LLM 增强
        # 开关的容错方式一致。
        llm_helper = _get_llm_helper(agent) if use_llm_draft else None
        track = store.create(
            title=title, persona_desc=persona_desc,
            target_type=target_type, llm_helper=llm_helper,
        )
        draft_note = ""
        if use_llm_draft:
            draft_note = (
                "（LLM 起草成功）" if track.outline
                else "（LLM 起草失败或不可用，已创建空大纲，可在看板手动补充）"
            )
        type_note = "persona 型" if target_type == "persona" else "knowledge 型"
        R.print_success(
            f"已创建 {type_note} Track [{track.track_id}] {track.title}，"
            f"大纲 {len(track.outline)} 个子主题{draft_note}，wiki_tag={track.wiki_tag}"
        )
        return

    if sub == "migrate-volatility":
        store = CapabilityTrackStore(paths)
        result = store.migrate_stable_volatility_to_periodic()
        if result["topics_migrated"] == 0:
            R.print_info("没有找到 volatility=\"stable\" 的子主题，无需迁移。")
        else:
            R.print_success(
                f"已迁移 {result['topics_migrated']} 个子主题（涉及 "
                f"{result['tracks_affected']} 个 Track），volatility 从 "
                f"\"stable\" 改为 \"periodic\"（30 天刷新周期）。"
            )
        return

    if sub == "refresh-all":
        store = CapabilityTrackStore(paths)
        target_track_id = args[1] if len(args) > 1 else None
        result = store.force_refresh_all_topics(track_id=target_track_id)
        if result["topics_reset"] == 0:
            R.print_info("没有找到 coverage_state==\"covered\" 的子主题，无需刷新。")
        else:
            scope_note = f"Track [{target_track_id}]" if target_track_id else "所有 Track"
            R.print_success(
                f"已把 {scope_note} 里 {result['topics_reset']} 个已覆盖子主题重置为 "
                f"partial（涉及 {result['tracks_affected']} 个 Track），"
                f"下一轮 /capability cycle 或 sys:capability_learning_cycle 会重新检索。"
            )
        return

    if sub == "cycle":
        # 是否使用真实检索由 CapabilityLearningConfig.retriever_enabled
        # 控制（默认 True，opt-out）。关闭时保持 P1 原有安全兜底行为：
        # retriever=None，需要检索的子主题记 skipped 台账，不产生网络
        # 请求。开启时具体走哪种检索实现由 retriever_mode 决定（[v0.22
        # §14.4]）：默认 "web_search" 用 make_web_search_retriever(cfg)
        # 接真实 web_search；"agent" 用 make_agent_retriever(cfg)，改由
        # 受限工具集的 SubAgent 自主调研（可用 skill 生态，不止关键词
        # 搜索）。两种检索结果在写入前都仍会经过 make_wiki_writer 里已经
        # 接好的 §13.3-g 合规过滤，不会绕过。
        cfg = getattr(agent, "cfg", None)
        retriever = None
        retriever_enabled = bool(getattr(getattr(cfg, "capability_learning", None), "retriever_enabled", False))
        retriever_mode = str(getattr(getattr(cfg, "capability_learning", None), "retriever_mode", "web_search") or "web_search")
        if retriever_enabled and cfg is not None:
            if retriever_mode == "agent":
                from mini_agent.evolution.capability_learning import make_agent_retriever
                retriever = make_agent_retriever(cfg)
            else:
                from mini_agent.evolution.capability_learning import make_web_search_retriever
                retriever = make_web_search_retriever(cfg)
        # [v0.23] wiki 写入实现档位：默认仍是固定模板渲染
        # （make_wiki_writer），"agent" 时改由 SubAgent 直接调用
        # capability_wiki_write 工具写入（工具集受
        # agent_retriever_tool_mode 控制），失败时自动退回固定模板兜底，
        # 见 make_agent_wiki_writer() 的说明。
        wiki_write_mode = str(getattr(getattr(cfg, "capability_learning", None), "wiki_write_mode", "callback") or "callback")
        if wiki_write_mode == "agent" and cfg is not None:
            from mini_agent.evolution.capability_learning import make_agent_wiki_writer
            wiki_writer = make_agent_wiki_writer(cfg, paths)
        else:
            wiki_writer = make_wiki_writer(paths)
        # v0.21 §13.2-f：拿得到 llm_helper 就顺带在消费已回答问题时生成
        # 大纲动态生长建议；拿不到（agent 未初始化/无 LLM 上下文）时这一步
        # 在 run_capability_learning_cycle 内部整体跳过，不影响循环本身。
        llm_helper = _get_llm_helper(agent)
        # [next_doc/outline_revision_and_suggestion_improvement_plan.md §三]
        # 自动大纲建议三个新来源的开关/阈值，从 CapabilityLearningConfig
        # 读取；拿不到 cfg 时全部退回函数默认值（miss_counts 开、其余关）。
        cap_cfg = getattr(cfg, "capability_learning", None)
        result = run_capability_learning_cycle(
            paths, retriever=retriever, wiki_writer=wiki_writer,
            llm_helper=llm_helper,
            outline_suggestion_miss_count_enabled=bool(
                getattr(cap_cfg, "outline_suggestion_miss_count_enabled", True)),
            outline_suggestion_miss_count_threshold=int(
                getattr(cap_cfg, "outline_suggestion_miss_count_threshold", 3)),
            outline_suggestion_research_enabled=bool(
                getattr(cap_cfg, "outline_suggestion_research_enabled", False)),
            outline_suggestion_milestone_enabled=bool(
                getattr(cap_cfg, "outline_suggestion_milestone_enabled", False)),
            outline_suggestion_milestone_threshold=float(
                getattr(cap_cfg, "outline_suggestion_milestone_threshold", 0.8)),
            # [next_doc/empty_outline_auto_draft_plan.md]
            empty_outline_auto_draft_enabled=bool(
                getattr(cap_cfg, "empty_outline_auto_draft_enabled", False)),
            empty_outline_auto_draft_after_hours=float(
                getattr(cap_cfg, "empty_outline_auto_draft_after_hours", 24.0)),
        )
        # v0.21 §8：本轮跑完后尝试推送一条按天节流的摘要通知（空轮/被
        # 节流/关闭时静默不发，不影响本命令的正常输出）。
        try:
            from mini_agent.evolution.capability_learning import (
                CapabilityQuestionStore as _QStore,
                maybe_dispatch_capability_notification,
            )
            pending_count = len(_QStore(paths).list_questions(status="pending"))
            maybe_dispatch_capability_notification(
                paths, getattr(cfg, "capability_learning", None), result, pending_count,
            )
        except Exception:
            pass
        skip_note = (
            f"（真实检索已开启，检索方式={retriever_mode}，写入方式={wiki_write_mode}，"
            f"检索失败/无结果的子主题仍会被跳过并记台账）"
            if retriever_enabled
            else "（未开启真实检索，需要检索的子主题会被跳过并记台账；"
                 "在 agent_config.json 里设置 capability_learning.retriever_enabled=true 可开启，"
                 "并可用 capability_learning.retriever_mode 选择 web_search/agent、"
                 "capability_learning.wiki_write_mode 选择 callback/agent）"
        )
        R.print_info(
            "本轮学习循环完成："
            f"处理 Track {result['tracks_processed']} 个，"
            f"检索并写入 {result['topics_researched']} 个子主题"
            f"（其中 {result.get('topics_research_empty', 0)} 个检索未获得有效结果，"
            f"标记为待重试，不计入已覆盖），"
            f"生成问题 {result['questions_raised']} 条，"
            f"消费已回答问题 {result['questions_consumed']} 条，"
            f"跳过 {result['topics_skipped']} 个子主题，"
            f"生成大纲建议 {result.get('outline_suggestions_generated', 0)} 条"
            f"{skip_note}"
        )
        return

    if sub == "questions":
        # --sweep-expired：清理长期未回答的问题（§3.3 TTL 过期规则），
        # 供 sys:capability_question_sweep cron job 的 task_template 引用。
        # 和"列出 pending 问题"是两件不冲突的事，放在同一个子命令下按参数
        # 分流，不新开一个子命令，风格对齐 /session cleanup 的做法。
        if "--sweep-expired" in args:
            store = CapabilityQuestionStore(paths)
            n = store.sweep_expired()
            R.print_info(f"已清理 {n} 条超期未回答的问题（标记为 expired）。")
            return
        track_id = args[1] if len(args) > 1 else None
        store = CapabilityQuestionStore(paths)
        pending = store.list_questions(status="pending", track_id=track_id)
        if not pending:
            R.print_info("暂无待回答问题。")
            return
        for q in pending:
            hint = f"（提示：{q.hint}）" if q.hint else ""
            R.print_info(f"[{q.question_id}] track={q.track_id} topic={q.topic_id}\n  {q.question}{hint}")
        return

    if sub == "answer":
        if len(args) < 3:
            R.print_error("用法：/capability answer <question_id> <answer text>")
            return
        question_id = args[1]
        answer_text = " ".join(args[2:])
        store = CapabilityQuestionStore(paths)
        q = store.answer(question_id, answer_text)
        if q is None:
            R.print_error(f"未找到待回答问题：{question_id}（可能已回答/已过期/id 有误）")
            return
        R.print_success(f"已记录回答，下一轮 /capability cycle 会消费这条问题。")
        return

    if sub == "persona":
        # persona 型 Track 专属子命令组（§10.3）：draft 生成/刷新草稿预览，
        # show 展示上一次落盘的草稿，publish 显式发布到正式 personas 目录。
        # 三个动作互相独立、都要求用户显式触发，任何一步都不会被
        # /capability cycle 自动带出（见设计文档 §10.3 第 4 点）。
        from mini_agent.evolution.capability_learning import (
            CapabilityTrackStore as _TrackStore,
            draft_persona_markdown,
            load_persona_draft,
            persona_draft_completeness,
            publish_persona_draft,
            save_persona_draft,
        )

        persona_sub = args[1] if len(args) > 1 else ""
        track_id = args[2] if len(args) > 2 else ""
        if persona_sub not in ("draft", "show", "publish") or not track_id:
            R.print_error(
                "用法：/capability persona draft <track_id> | "
                "/capability persona show <track_id> | "
                "/capability persona publish <track_id>"
            )
            return

        track_store = _TrackStore(paths)
        track = track_store.get(track_id)
        if track is None:
            R.print_error(f"未找到 Track：{track_id}")
            return
        if track.target_type != "persona":
            R.print_error(
                f"Track [{track_id}] 是 knowledge 型，不是 persona 型，"
                f"不支持人设草稿相关操作。"
            )
            return

        if persona_sub == "draft":
            questions = CapabilityQuestionStore(paths).list_questions(track_id=track_id)
            md = draft_persona_markdown(track, questions)
            save_persona_draft(paths, track_id, md)
            completeness = persona_draft_completeness(track, questions)
            missing_note = (
                f"，尚缺维度：{', '.join(completeness['missing_topic_names'])}"
                if completeness["missing_topic_names"] else "，各维度均已有信息"
            )
            R.print_success(
                f"已生成/刷新 Track [{track_id}] 的人设草稿"
                f"（{completeness['answered']}/{completeness['total']} 个维度已有信息{missing_note}）。"
                f"用 /capability persona show {track_id} 预览，确认无误后用 "
                f"/capability persona publish {track_id} 发布。"
            )
            return

        if persona_sub == "show":
            text = load_persona_draft(paths, track_id)
            if text is None:
                R.print_error(
                    f"Track [{track_id}] 还没有草稿，请先执行 "
                    f"/capability persona draft {track_id}。"
                )
                return
            R.print_info(text)
            return

        if persona_sub == "publish":
            try:
                target_path = publish_persona_draft(paths, track_id)
            except ValueError as e:
                R.print_error(str(e))
                return
            R.print_success(
                f"已发布到 {target_path}，可用 /role use <name> 激活"
                f"（角色名以草稿 frontmatter 里的 name 字段为准）。"
            )
            return

    if sub == "suggestions":
        from mini_agent.evolution.capability_learning import (
            CapabilityOutlineSuggestionStore,
            accept_outline_suggestion,
        )

        action = args[1] if len(args) > 1 else ""
        if action in ("accept", "dismiss"):
            if len(args) < 3:
                R.print_error(f"用法：/capability suggestions {action} <suggestion_id>")
                return
            suggestion_id = args[2]
            if action == "accept":
                topic = accept_outline_suggestion(paths, suggestion_id)
                if topic is None:
                    R.print_error(
                        f"未找到待处理建议：{suggestion_id}（可能已处理过/id 有误，"
                        f"或对应 Track 已被删除）"
                    )
                    return
                R.print_success(f"已采纳，新增子主题「{topic.name}」（topic_id={topic.topic_id}）。")
                return
            else:
                store = CapabilityOutlineSuggestionStore(paths)
                ok = store.dismiss(suggestion_id)
                if not ok:
                    R.print_error(f"未找到建议：{suggestion_id}")
                    return
                R.print_success("已忽略该建议。")
                return

        track_id = args[1] if len(args) > 1 else None
        store = CapabilityOutlineSuggestionStore(paths)
        pending = store.list_suggestions(status="pending", track_id=track_id)
        if not pending:
            R.print_info("暂无待处理的大纲建议。")
            return
        for s in pending:
            R.print_info(
                f"[{s.suggestion_id}] track={s.track_id}  建议新增子主题：「{s.suggested_name}」\n"
                f"  {s.rationale}"
            )
        return

    if sub == "persona_candidates":
        from mini_agent.evolution.persona_candidates import (
            PersonaCandidateStore,
            accept_candidate,
            dismiss_candidate,
            scan_persona_candidates,
        )

        action = args[1] if len(args) > 1 else "list"

        if action == "scan":
            cfg = getattr(agent.cfg, "persona_candidates", None) if agent is not None else None
            if cfg is None:
                from mini_agent.config.models import PersonaCandidateConfig
                cfg = PersonaCandidateConfig()
            if not getattr(cfg, "enabled", False):
                R.print_info("persona_candidates.enabled=False，跳过扫描（未发起 LLM 调用）。")
                return
            from mini_agent.profile import UserProfileManager

            profile = UserProfileManager(paths).load()
            created = scan_persona_candidates(paths, cfg, profile, _get_llm_helper(agent))
            if not created:
                R.print_info("本轮扫描没有产生新候选。")
                return
            for c in created:
                R.print_success(f"[{c.candidate_id}] {c.title} —— {c.rationale}")
            return

        if action in ("accept", "dismiss"):
            if len(args) < 3:
                R.print_error(f"用法：/capability persona_candidates {action} <candidate_id>")
                return
            candidate_id = args[2]
            if action == "accept":
                result = accept_candidate(paths, candidate_id)
                if result is None:
                    R.print_error(f"未找到待处理候选：{candidate_id}（可能已处理过/id 有误）")
                    return
                track = result["track"]
                R.print_success(
                    f"已采纳，新建 Track [{track['track_id']}] 「{track['title']}」"
                    f"（target_type=persona，空大纲，可在看板补充）。"
                )
                return
            else:
                candidate = dismiss_candidate(paths, candidate_id)
                if candidate is None:
                    R.print_error(f"未找到待处理候选：{candidate_id}")
                    return
                R.print_success("已忽略该候选。")
                return

        store = PersonaCandidateStore(paths)
        pending = store.list_candidates(status="pending")
        if not pending:
            R.print_info("暂无待处理的候选人设，可用 /capability persona_candidates scan 触发一次扫描。")
            return
        for c in pending:
            R.print_info(f"[{c.candidate_id}] {c.title}\n  {c.rationale}")
        return

    if sub == "adopt-goal":
        if len(args) < 3:
            R.print_error("用法：/capability adopt-goal <track_id> <topic_id>")
            return
        track_id, topic_id = args[1], args[2]
        store = CapabilityTrackStore(paths)
        track = store.get(track_id)
        if track is None:
            R.print_error(f"未找到 Track：{track_id}")
            return
        topic = next((t for t in track.outline if t.topic_id == topic_id), None)
        if topic is None:
            R.print_error(f"Track {track_id} 下未找到子主题：{topic_id}")
            return

        from mini_agent.evolution.capability_learning import adopt_topic_as_goal

        try:
            goal = adopt_topic_as_goal(paths, track, topic, track_store=store)
        except RuntimeError as exc:
            R.print_error(str(exc))
            return
        R.print_info(f"已创建目标 [{goal.id}] {goal.title}，并关联到子主题 {topic_id}。")
        return

    R.print_error(
        f"未知子命令：{sub}。可用：list | create | cycle | questions | answer | "
        f"suggestions | persona | persona_candidates | adopt-goal"
    )
