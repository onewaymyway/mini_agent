"""
cli/commands/capability_cmd.py — /capability 命令处理（人设能力自主学习，见
next_doc/persona_capability_learning_design.md）。

补齐设计文档 §4 里发现的那一层缺失：`cron_scheduler.py` 的 `sys:` 内置任务
是"生成一段 task_template 文本交给 Agent 带着工具执行"的模式（参照
`sys:growth_advisor_daily` 引用 `/growth scan`），不是直接调用 Python 函数。
本模块提供的 `/capability cycle` 就是 `sys:capability_learning_cycle`
引用的中间层命令（该 cron job 已注册，默认 enabled=False，见
cron_scheduler.py SYSTEM_JOBS 里对应条目的说明）。

子命令：
  /capability                    — 展示所有 Track 概况（标题/状态/覆盖率）
  /capability list                — 同上（显式别名，风格对齐 /growth list）
  /capability create <title> | <persona_desc>
                                   — 创建一个 knowledge 型 Track（大纲用内置
                                     规则式模板起草，LLM 辅助起草留到 P2）
  /capability cycle                — 手动触发一轮学习循环（等价于
                                     sys:capability_learning_cycle 的内容，
                                     不依赖那条 cron job 是否已注册/enabled）。
                                     是否使用真实检索由
                                     capability_learning.retriever_enabled
                                     配置项控制（默认 False）：关闭时安全
                                     跳过需要检索的子主题并记台账；打开时
                                     用 make_web_search_retriever 接
                                     web_search，检索结果写入前仍会经过
                                     §13.3-g 合规过滤（make_wiki_writer 里
                                     已经接好，不受这个开关影响）
  /capability questions [track_id] — 列出 pending 状态的待回答问题
  /capability questions --sweep-expired
                                   — 清理超过 TTL 未回答的问题，标记为
                                     expired（§3.3），供
                                     sys:capability_question_sweep 引用
  /capability answer <question_id> <answer text>
                                   — 提交一条问题的回答（下一轮 cycle 会消费）
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
        rest = " ".join(args[1:]).strip()
        if not rest:
            R.print_error("用法：/capability create <title> | <persona_desc>")
            return
        if "|" in rest:
            title, _, persona_desc = rest.partition("|")
        else:
            title, persona_desc = rest, rest
        title = title.strip()
        persona_desc = persona_desc.strip() or title
        if not title:
            R.print_error("用法：/capability create <title> | <persona_desc>")
            return
        store = CapabilityTrackStore(paths)
        track = store.create(title=title, persona_desc=persona_desc)
        R.print_success(
            f"已创建 Track [{track.track_id}] {track.title}，"
            f"大纲 {len(track.outline)} 个子主题，wiki_tag={track.wiki_tag}"
        )
        return

    if sub == "cycle":
        # 是否使用真实检索由 CapabilityLearningConfig.retriever_enabled
        # 控制（默认 False，opt-in）。关闭时保持 P1 原有安全默认行为：
        # retriever=None，需要检索的子主题记 skipped 台账，不产生网络
        # 请求；打开时用 make_web_search_retriever(cfg) 接真实
        # web_search，检索结果在写入前仍会经过 make_wiki_writer 里已经
        # 接好的 §13.3-g 合规过滤，不会绕过。
        cfg = getattr(agent, "cfg", None)
        retriever = None
        retriever_enabled = bool(getattr(getattr(cfg, "capability_learning", None), "retriever_enabled", False))
        if retriever_enabled and cfg is not None:
            from mini_agent.evolution.capability_learning import make_web_search_retriever
            retriever = make_web_search_retriever(cfg)
        result = run_capability_learning_cycle(
            paths, retriever=retriever, wiki_writer=make_wiki_writer(paths),
        )
        skip_note = (
            "（真实检索已开启，检索失败/无结果的子主题仍会被跳过并记台账）"
            if retriever_enabled
            else "（未开启真实检索，需要检索的子主题会被跳过并记台账；"
                 "在 agent_config.json 里设置 capability_learning.retriever_enabled=true 可开启）"
        )
        R.print_info(
            "本轮学习循环完成："
            f"处理 Track {result['tracks_processed']} 个，"
            f"检索并写入 {result['topics_researched']} 个子主题，"
            f"生成问题 {result['questions_raised']} 条，"
            f"消费已回答问题 {result['questions_consumed']} 条，"
            f"跳过 {result['topics_skipped']} 个子主题"
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

    R.print_error(
        f"未知子命令：{sub}。可用：list | create | cycle | questions | answer"
    )
