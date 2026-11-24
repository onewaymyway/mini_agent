"""writing/revise.py — 按用户意见修改已成稿的文章（审阅页「按意见修改」）。

与「退回重写」的区别：重写是按意见整篇重新生成；这里保留原文，逐节让模型判断意见是否涉及该节，
只改相关小节，其余小节原样不动。
* 只用于已成稿 / 已审阅的选题；修改后文章回到「待审阅」（已审阅的需重新通过审阅）。
* 修改后的小节与成文同样经过代码校验（证据标记、来源、照抄、禁用说法等）、全文检查与独立评审；
  评审问题只修本次改动的小节，其余小节的既有问题只记录。
* 修改失败不会破坏原文：文章与选题状态保持修改前的样子，原因写入运行日志。
* 修改前的版本保存在 drafts/<tid>/revisions/；本次修改记录写入 checks.json 的 last_revision。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .. import cancel, joblog, settings, store
from ..digest import runner as drunner
from ..digest import summarize as sm
from ..library import front_matter
from ..planning import states
from . import brief as brief_mod
from . import checks, critic, draft, outline
from .runner import (MAX_DRAFT_ATTEMPTS, SCOPE, WriteFailed, _article_row, _doc_issues, _map_critic, _now,
                     _register, _rules, _topic_dir)

HEAD = re.compile(r"(?m)^##\s+(.+?)\s*$")


def split_sections(body: str) -> List[Tuple[str, str]]:
    """把成稿正文拆成 [(小节标题, 正文)]，只含正文小节（不含「延伸阅读」及其后内容）。"""
    ms = list(HEAD.finditer(body))
    out: List[Tuple[str, str]] = []
    for i, m in enumerate(ms):
        if outline.is_refs(m.group(1)):
            break
        end = ms[i + 1].start() if i + 1 < len(ms) else len(body)
        out.append((m.group(1).strip(), body[m.end():end].strip()))
    return out


def prompt(brief: Dict[str, Any], heading: str, current: str, by_id: Dict[str, Dict[str, Any]],
           feedback: str, hints: List[str]) -> str:
    src = "\n".join(f"[{s['id']}] {s['title']}（可信度 {s['credibility']}）\n摘录：{s['excerpt']}" for s in by_id.values())
    terms = "；".join(f"{k}（别名：{'、'.join(v)}）" for k, v in (brief.get("terms") or {}).items()) or "无"
    forb = "、".join(brief["risk"]["forbidden_claims"]) or "无"
    out = "、".join(brief["scope"]["out"]) or "无"
    hint_block = ("上一次输出的问题（必须修正）：\n" + "\n".join(f"- {h}" for h in hints) + "\n") if hints else ""
    return ("【阶段:修改】按编辑意见修改小节「" + heading + "」。只输出 YAML：\n"
            "changed: true 或 false\ntext: |\n  修改后的完整小节正文（仅 changed 为 true 时给出）\n\n"
            "规则：\n"
            "1. 先判断编辑意见是否涉及本小节；与本节无关则 changed: false，不要写 text；\n"
            "2. 涉及本节则落实意见，其余内容尽量保持原文措辞，不要无故改写；\n"
            "3. 每个段落至少包含一个证据标记 [^来源编号]，只能使用下列来源；事实只能来自下列摘录，不得编造；\n"
            "4. 不得整句照抄摘录；正文不要包含标题行；\n"
            f"5. 用语必须统一：{terms}；禁止使用的说法：{forb}；不得写范围外内容：{out}；\n"
            f"6. 语气：{brief['style']['tone']}；读者：{brief['audience'] or '未指定'}；层级：{brief['level']}\n"
            f"文章：{brief['title']}\n编辑意见（必须落实）：{feedback}\n{hint_block}\n"
            f"本小节当前正文：\n{current}\n\n可用来源：\n{src}")


def _is_changed(obj: Dict[str, Any]) -> Optional[bool]:
    """changed 缺失但给了 text，按已修改处理（对模型输出宽容）；都没有则返回 None（格式不对）。"""
    ch = obj.get("changed")
    if isinstance(ch, bool):
        return ch
    return True if isinstance(obj.get("text"), str) else None


def validator(by_id: Dict[str, Dict[str, Any]], rules: Dict[str, Any]):
    allowed = list(by_id)
    excerpts = {k: v["excerpt"] for k, v in by_id.items()}

    def _v(obj: Dict[str, Any]) -> List[str]:
        ch = _is_changed(obj)
        if ch is None:
            return ["changed 应为 true 或 false"]
        if not ch:
            return []
        text = obj.get("text")
        if not isinstance(text, str):
            return ["changed 为 true 时应包含 text 字段（字符串）"]
        return checks.check_section(text, allowed=allowed, excerpts=excerpts, rules=rules)
    return _v


def revise_section(llm, brief, heading, current, by_id, budget, feedback, hints, rules) -> Optional[str]:
    """返回修改后的正文；本节无需修改（或模型原样返回）时返回 None。"""
    obj = sm.ask_validated(llm, prompt(brief, heading, current, by_id, feedback, hints),
                           validator(by_id, rules), budget)
    if not _is_changed(obj):
        return None
    new = str(obj["text"]).strip()
    return None if new == current.strip() else new


def revise(ctx, tid: str, llm, feedback: str, *, max_rounds: Optional[int] = None) -> Dict[str, Any]:
    feedback = (feedback or "").strip()
    if not feedback:
        raise ValueError("请填写修改意见")
    if llm is None:
        raise ValueError("修改需要文本模型，请先确认模型配置可用")
    topic = states.get(ctx, tid)
    if topic["status"] not in (states.DRAFTED, states.REVIEWED) or not topic.get("article_id"):
        raise ValueError(f"只有已成稿或已审阅的选题可以按意见修改（当前为「{topic['status']}」）")
    row = _article_row(ctx, topic["article_id"])
    if row is None:
        raise ValueError(f"找不到文章：{topic['article_id']}")
    _, old_body = front_matter.split(ctx.path(row["path"]).read_text(encoding="utf-8"))
    cur = split_sections(old_body)
    if not cur:
        raise ValueError("无法从文章中识别出正文小节（需要「## 小节标题」格式），请用「退回重写」")

    eff, _ = settings.effective_settings(ctx)
    rounds = int(max_rounds or eff["budget"]["max_repair_rounds"])
    prev, aid, d = topic["status"], topic["article_id"], _topic_dir(ctx, tid)
    run_id = joblog.run_start(ctx.root, SCOPE, f"按意见修改 {tid}")
    states.begin_draft(ctx, tid, f"按意见修改（{feedback[:40]}）")
    try:
        res = _revise_inner(ctx, tid, topic, llm, rounds, feedback, cur, old_body)
    except (WriteFailed, sm.DigestFailed) as exc:
        reasons = exc.reasons if isinstance(exc, WriteFailed) else exc.issues
        states.revert_to(ctx, tid, prev, "按意见修改未通过，保持原文：" + "；".join(reasons[:3]))
        joblog.run_end(ctx.root, SCOPE, run_id, f"按意见修改 {tid}", status="failed", error="；".join(reasons[:3]))
        return {"topic_id": tid, "result": "failed", "reasons": reasons}
    except cancel.Cancelled:
        states.revert_to(ctx, tid, prev, "用户停止，保持原文")
        joblog.run_end(ctx.root, SCOPE, run_id, f"按意见修改 {tid}", status="stopped", error="用户请求停止")
        raise
    except BaseException as exc:
        states.revert_to(ctx, tid, prev, f"按意见修改中断，保持原文（{type(exc).__name__}）")
        joblog.run_end(ctx.root, SCOPE, run_id, f"按意见修改 {tid}", status="failed", error=f"{type(exc).__name__}: {exc}")
        raise

    if not res["changed"]:
        states.revert_to(ctx, tid, prev, "按意见修改：没有小节需要改动，保持原文")
        joblog.log(ctx.root, SCOPE, "按意见修改：模型认为意见不涉及任何小节，文章未改动；可把意见写得更具体，或改用「退回重写」", level="warn")
        joblog.run_end(ctx.root, SCOPE, run_id, f"按意见修改 {tid}", status="ok")
        return {"topic_id": tid, "result": "unchanged"}

    n = len(list((d / "revisions").glob("*.md")) if (d / "revisions").is_dir() else []) + 1
    store.write_text_atomic(d / "revisions" / f"r{n:02d}.md", old_body)
    _register(ctx, tid, topic, {"brief": res["brief"], "body": res["body"]})
    store.write_text_atomic(d / "draft.md", res["body"])
    for i, (h, t) in enumerate(res["parts"], 1):
        store.write_text_atomic(d / "sections" / f"{i:02d}.md", f"## {h}\n\n{t}\n")
    ck = store.read_json(d / "checks.json", default={}) or {}
    ck.update({"article_id": aid, "passed": True, "checked_at": _now(), "critic_notes": res["critic_notes"],
               "length_note": checks.length_note(res["body"], res["brief"]["style"]["length"]),
               "last_revision": {"feedback": feedback, "changed_sections": res["changed_headings"], "at": _now(),
                                 "budget_used": res["budget"].used, "budget_max": rounds}})
    store.write_json_atomic(d / "checks.json", ck)
    counts = drunner.run_digest(ctx, llm, only=[aid], force=True)
    states.end_draft(ctx, tid, states.DRAFTED, f"已按意见修改 {aid}（改动 {len(res['changed_headings'])} 节），待重新审阅")
    joblog.run_end(ctx.root, SCOPE, run_id, f"按意见修改 {tid}", status="ok")
    return {"topic_id": tid, "result": "revised", "article_id": aid, "changed_sections": res["changed_headings"],
            "digest": counts}


def _revise_inner(ctx, tid, topic, llm, rounds, feedback, cur, old_body) -> Dict[str, Any]:
    budget = sm.RepairBudget(rounds)
    brief = brief_mod.build(ctx, tid, [feedback])
    by_id = {s["id"]: s for s in brief["sources"]}
    rules = _rules(ctx, brief, topic.get("article_id"))
    lib_snippets = [f"{a['title']}：{'、'.join(a['concepts'])}" for a in brief["existing_articles"]
                    if a.get("origin") != "generated"]
    headings = [h for h, _ in cur]
    texts = {i: t for i, (_, t) in enumerate(cur)}
    changed: Dict[int, bool] = {}

    for i, (h, t) in enumerate(cur):
        cancel.check()
        new = revise_section(llm, brief, h, t, by_id, budget, feedback, [], rules)
        if new is not None:
            texts[i], changed[i] = new, True
        joblog.log(ctx.root, SCOPE, f"按意见修改：第 {i + 1}/{len(cur)} 节「{h}」" + ("已修改" if new is not None else "无需修改"))
    if not changed:
        return {"changed": False}

    attempts = 0
    while True:
        parts = [(headings[i], texts[i]) for i in range(len(cur))]
        body = draft.assemble(brief, parts, by_id, brief["library_links"])
        doc = _doc_issues(body, brief)
        if doc:
            joblog.log(ctx.root, SCOPE, f"按意见修改：全文检查未通过：{'；'.join(doc[:3])}", level="warn")
            per_sec, pending = {i: list(doc) for i in changed}, doc
        else:
            cr = critic.review(llm, body, brief["sources"], lib_snippets)
            level = settings.effective_settings(ctx)[0]["thresholds"]["critic_level"]
            todo = critic.actionable(cr, level)
            per_sec = {i: h for i, h in _map_critic(todo, headings).items() if i in changed} if todo else {}
            joblog.log(ctx.root, SCOPE, f"按意见修改：独立评审发现 {len(cr)} 个问题，需修复 {len(per_sec)} 节")
            if not per_sec:
                return {"changed": True, "body": body, "brief": brief, "parts": parts, "budget": budget,
                        "critic_notes": cr,
                        "changed_headings": [headings[i] for i in sorted(changed)]}
            pending = [f"{it['section']}：{it['note']}" for it in todo]
        attempts += 1
        if attempts > MAX_DRAFT_ATTEMPTS:
            raise WriteFailed(pending, body)
        if not budget.take():
            raise WriteFailed(pending + ["修复轮数已用尽"], body)
        for i, hints in per_sec.items():
            cancel.check()
            new = revise_section(llm, brief, headings[i], texts[i], by_id, budget, feedback, hints, rules)
            if new is not None:
                texts[i] = new
