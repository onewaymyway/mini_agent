"""
cli/commands/capability_cmd.py — /capability 命令处理（人设能力自主学习，见
next_doc/persona_capability_learning_design.md）。

补齐设计文档 §4 里发现的那一层缺失：`cron_scheduler.py` 的 `sys:` 内置任务
是"生成一段 task_template 文本交给 Agent 带着工具执行"的模式（参照
`sys:growth_advisor_daily` 引用 `/growth scan`），不是直接调用 Python 函数。
本模块提供的 `/capability cycle` 就是 `sys:capability_learning_cycle` 将来
引用的那个中间层命令——cron 任务表本身仍未注册（见文档「实施状态」，
这一步需要功能评审通过后再开），但命令处理器先落地，接线时只需要在
cron_scheduler.py 的 SYSTEM_JOBS 里新增一条 task_template 引用本命令，
不需要再回头改这里。

子命令：
  /capability                    — 展示所有 Track 概况（标题/状态/覆盖率）
  /capability list                — 同上（显式别名，风格对齐 /growth list）
  /capability create <title> | <persona_desc>
                                   — 创建一个 knowledge 型 Track（大纲用内置
                                     规则式模板起草，LLM 辅助起草留到 P2）
  /capability cycle                — 手动触发一轮学习循环（等价于
                                     sys:capability_learning_cycle 的内容，
                                     不依赖那条 cron job 是否已注册/enabled）。
                                     P1 阶段仍不传入 retriever（真实检索需要
                                     网络 + 13.3-g 合规过滤，留到接线阶段单独
                                     评审），wiki_writer 使用已有测试覆盖的
                                     make_wiki_writer；因此本命令目前只会
                                     推进"不需要检索"的部分（消费已回答问题、
                                     记录 skipped 台账），不会产生真实网络
                                     请求或未经合规过滤的 wiki 写入
  /capability questions [track_id] — 列出 pending 状态的待回答问题
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
        # P1 阶段刻意不传 retriever：真实检索需要网络请求，且金融等专业
        # 建议类领域必须先有 13.3-g 合规过滤才能落 wiki，这一步留到接线
        # 阶段单独评审（见设计文档「实施状态」）。wiki_writer 传入
        # make_wiki_writer 是安全的——它不产生网络请求，只在 retriever
        # 已经返回结果时才会被调用，而 P1 这里 retriever 恒为 None，
        # 所以实际不会被触发，传入只是让函数签名/未来接线路径保持一致。
        result = run_capability_learning_cycle(paths, wiki_writer=make_wiki_writer(paths))
        R.print_info(
            "本轮学习循环完成："
            f"处理 Track {result['tracks_processed']} 个，"
            f"检索并写入 {result['topics_researched']} 个子主题，"
            f"生成问题 {result['questions_raised']} 条，"
            f"消费已回答问题 {result['questions_consumed']} 条，"
            f"跳过 {result['topics_skipped']} 个子主题"
            "（P1 阶段未接入真实检索，需要检索的子主题会被跳过并记台账）"
        )
        return

    if sub == "questions":
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
