"""R3 采纳与增量复评：把评估问题变成修改变更，并跟踪问题是否真的消失。

  * options(finding)：每条问题可选的处理方式。矛盾类给出「以 A 为准改 B」「以 B 为准改 A」两个选项，
    不预选；其它类别给一个「按问题修改」选项。「不处理」由作者用状态「已驳回」表达，不在这里生成；
  * adopt(...) / adopt_batch(...)：采纳问题，生成 chapter 变更（带 evidence 与 origin），之后走现有的
    预览 → 确认 → 执行。问题状态变为 adopted。证据未核实的问题不进入批量采纳；矛盾类必须由作者选定选项；
  * sync_applied(...)：变更执行完成（applied）后，把对应问题的状态更新为 applied；
  * increment(...)：增量复评。基于 head 重新生成事实卡（未改的章节命中缓存）、确定性检查与 LLM 复核，
    并与改动前的版本对照：原问题已不在 → resolved；仍在 → still_open；本次新出现的问题单独标记。
    LLM 仲裁结论只沿用未改动章节的部分，改动涉及的候选重新仲裁。

只写 `_revision/`，不改任何版本内容；采纳不会直接改正文，正文只在执行变更时改动。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from novel_writer.revision_flow import changes as CH
from novel_writer.revision_flow import review_cards as RC
from novel_writer.revision_flow import review_checks as RK
from novel_writer.revision_flow import review_llm as RL
from novel_writer.revision_flow import versions as V
from novel_writer.revision_flow.common import FlowError, load_json, save_json

ADOPTABLE = ("open", "still_open")       # 可以采纳的状态
ACTIVE_CHANGE = ("draft", "confirmed")   # 已关联的变更仍在流程中，不能重复采纳


def _find(pool: Dict[str, Any], fid: str) -> Dict[str, Any]:
    for f in pool["findings"]:
        if f["id"] == fid:
            return f
    raise FlowError(f"问题不存在：{fid}")


def _quote_of(f: Dict[str, Any], chapter: int) -> str:
    for e in f["evidence"]:
        if e["chapter"] == chapter and e.get("quote"):
            return e["quote"]
    return ""


def _distinct_chapters(f: Dict[str, Any]) -> List[int]:
    out: List[int] = []
    for e in f["evidence"]:
        if e["chapter"] not in out:
            out.append(e["chapter"])
    for c in f["chapters"]:
        if c not in out:
            out.append(c)
    return out


def options(f: Dict[str, Any]) -> List[Dict[str, Any]]:
    """可选处理方式（不落盘，每次按问题内容生成）。每项：{label, target_chapters, instruction}。"""
    if f["category"] == "outline_deviation":
        return _outline_options(f)
    if f["category"] == "contradiction":
        chs = _distinct_chapters(f)
        if len(chs) < 2:
            return []
        a, b = chs[0], chs[1]
        return [_contradiction_option(f, keep, fix) for keep, fix in ((a, b), (b, a))]
    targets = _distinct_chapters(f)
    lines = [f"针对问题「{f['title']}」修改，问题说明：{f['description']}"]
    for c in targets:
        q = _quote_of(f, c)
        if q:
            lines.append(f"第{c}章相关原文：「{q}」")
    lines.append("要求：只修改与该问题有关的内容，保持其它情节与文风不变。")
    return [{"label": "按问题修改" + "、".join(f"第{c}章" for c in targets),
             "target_chapters": targets, "instruction": "\n".join(lines)}]


def _outline_options(f: Dict[str, Any]) -> List[Dict[str, Any]]:
    """大纲偏离（R5）：只提供「以大纲为准改正文」。规则比对的偏离只是提示，不能直接改正文；
    多写章节与少写章节也没有可改的正文，分别交给大纲重生成或正文续写处理。"""
    if f.get("origin_kind") != "llm" or f["check"] in ("outline_extra_chapter", "outline_missing_chapter"):
        return []
    targets = _distinct_chapters(f)
    if not targets:
        return []
    lines = [f"以大纲为准修改正文（大纲是权威）。偏离：{f['title']}。{f['description']}"]
    for c in targets:
        q = _quote_of(f, c)
        if q:
            lines.append(f"第{c}章需要改动的原文：「{q}」")
    lines.append("要求：只修改正文中与大纲偏离有关的部分，使其符合大纲要求；其它情节与文风保持不变。")
    return [{"label": "以大纲为准修改" + "、".join(f"第{c}章" for c in targets),
             "target_chapters": targets, "instruction": "\n".join(lines)}]


def _contradiction_option(f: Dict[str, Any], keep: int, fix: int) -> Dict[str, Any]:
    lines = [f"以第{keep}章的描述为准，修改第{fix}章中与之矛盾的内容。问题：{f['title']}。{f['description']}"]
    kq, fq = _quote_of(f, keep), _quote_of(f, fix)
    if kq:
        lines.append(f"第{keep}章（为准）原文：「{kq}」")
    if fq:
        lines.append(f"第{fix}章（需要改写）原文：「{fq}」")
    lines.append("要求：只改与上述矛盾有关的句子，其它内容与文风保持不变；不要在正文里解释设定的变化。")
    return {"label": f"以第{keep}章为准，改第{fix}章", "target_chapters": [fix], "instruction": "\n".join(lines)}


def _change_for(f: Dict[str, Any], review_id: str, version: str, *, option_index: Optional[int],
                instruction: Optional[str], target_chapters: Optional[List[int]]) -> Dict[str, Any]:
    opts = options(f)
    if f["category"] == "contradiction" and option_index is None:
        raise FlowError("矛盾类问题需要先选定以哪一方为准")
    if opts:
        if option_index is None:
            option_index = 0
        if not 0 <= option_index < len(opts):
            raise FlowError("选项序号超出范围")
        base = opts[option_index]
    else:
        raise FlowError("这条问题没有可生成的修改选项（涉及章节不足）")
    chapters = target_chapters or base["target_chapters"]
    text = (instruction or "").strip() or base["instruction"]
    return {
        "kind": "chapter", "title": f"[评估] {f['title']}", "reason": f["description"],
        "instruction": text, "chapters": chapters,
        # 只带入核实过的引文：未核实的引文不得作为改写依据
        "evidence": [{"chapter": e["chapter"], "quote": e["quote"]} for e in f["evidence"]
                     if e.get("quote") and e.get("verified", True)],
        "origin": {"finding_id": f["id"], "review_id": review_id, "option": base["label"]},
    }


def _check_adoptable(ws, f: Dict[str, Any], *, allow_unverified: bool) -> None:
    if f["status"] not in ADOPTABLE:
        raise FlowError(f"问题状态为「{f['status']}」，不能采纳")
    if f["source"] in ("llm", "outline") and not f.get("evidence_ok", True) and not allow_unverified:
        raise FlowError("证据未核实（引文不在原文中），请先核对原文，或明确允许采纳")
    if f.get("adjudication", {}).get("verdict") == "false_positive":
        raise FlowError("模型仲裁为误报，如确需修改请先驳回仲裁或改为其它处理")
    if f.get("change_id"):
        try:
            cur = CH.load_change(ws, f["change_id"])["status"]
        except FlowError:
            cur = None
        if cur in ACTIVE_CHANGE:
            raise FlowError(f"该问题已关联变更 {f['change_id']}（{cur}），请直接在「变更与预览」中处理")


def _adopt_one(ws, vid: str, pool: Dict[str, Any], f: Dict[str, Any], *, option_index, instruction,
               target_chapters, allow_unverified: bool) -> Dict[str, Any]:
    _check_adoptable(ws, f, allow_unverified=allow_unverified)
    raw = _change_for(f, pool["review_id"], vid, option_index=option_index, instruction=instruction,
                      target_chapters=target_chapters)
    change = CH.save_change(ws, raw)
    f["status"] = "adopted"
    f["change_id"] = change["id"]
    return change


def adopt(ws, version: Optional[str], fid: str, *, option_index: Optional[int] = None,
          instruction: Optional[str] = None, target_chapters: Optional[List[int]] = None,
          allow_unverified: bool = False) -> Dict[str, Any]:
    """采纳一条问题：生成一条 chapter 变更（draft），问题状态变为 adopted。返回变更。"""
    vid = RC.resolve_version(ws, version)
    pool = RK.load_findings(ws, vid)
    f = _find(pool, fid)
    change = _adopt_one(ws, vid, pool, f, option_index=option_index, instruction=instruction,
                        target_chapters=target_chapters, allow_unverified=allow_unverified)
    _write_pool(ws, vid, pool)
    return change


def adopt_batch(ws, version: Optional[str], fids: List[str], choices: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """批量采纳。跳过：证据未核实、已驳回、矛盾类但未选定方向（choices 中没有该问题）。逐条返回原因。"""
    vid = RC.resolve_version(ws, version)
    pool = RK.load_findings(ws, vid)
    choices = choices or {}
    created, skipped = [], []
    for fid in fids:
        f = _find(pool, fid)
        if f["category"] == "contradiction" and fid not in choices:
            skipped.append({"id": fid, "reason": "矛盾类需要先选定以哪一方为准"})
            continue
        try:
            change = _adopt_one(ws, vid, pool, f, option_index=choices.get(fid), instruction=None,
                                target_chapters=None, allow_unverified=False)
        except FlowError as exc:
            skipped.append({"id": fid, "reason": str(exc)})
            continue
        created.append(change["id"])
    _write_pool(ws, vid, pool)
    return {"created": created, "skipped": skipped}


def sync_applied(ws, version: Optional[str] = None) -> List[str]:
    """把已执行（change.status == applied）的采纳问题标为 applied。返回被更新的问题编号。"""
    updated: List[str] = []
    for vid in [v["id"] for v in V.list_versions(ws)] if version is None else [version]:
        if not (RK.review_dir(ws, vid) / "findings.json").exists():
            continue
        pool = RK.load_findings(ws, vid)
        changed = False
        for f in pool["findings"]:
            if f["status"] == "adopted" and f.get("change_id"):
                try:
                    ch = CH.load_change(ws, f["change_id"])
                except FlowError:
                    continue
                if ch["status"] == "applied":
                    f["status"], f["applied_version"] = "applied", ch.get("applied_version")
                    updated.append(f["id"])
                    changed = True
        if changed:
            _write_pool(ws, vid, pool)
    return updated


def _write_pool(ws, vid: str, pool: Dict[str, Any]) -> None:
    save_json(RK.findings_path(ws, vid), pool)
    (RK.review_dir(ws, vid) / "report.md").write_text(RK.render_report(pool), encoding="utf-8")


# ─────────────────────────────── 增量复评 ───────────────────────────────

def _carry_state(ws, parent: str, vid: str) -> None:
    """把父版本的 LLM 缓存带到新版本：仲裁结论只沿用涉及章节未改动的候选；窗口与全书键含章节 sha，天然安全。"""
    if (RK.review_dir(ws, vid) / "llm_state.json").exists():
        return
    old_state = RL._load_state(ws, parent)
    if not any(old_state[k] for k in ("adjudications", "windows", "whole")):
        return
    old_cards, _ = RC.load_cards(ws, parent)
    new_cards, _ = RC.load_cards(ws, vid)
    changed = {n for n in set(old_cards) | set(new_cards)
               if old_cards.get(n, {}).get("source_sha256") != new_cards.get(n, {}).get("source_sha256")}
    old_pool = RK.load_findings(ws, parent)
    touched = {f["id"] for f in old_pool["findings"] if set(f["chapters"]) & changed}
    old_state["adjudications"] = {k: v for k, v in old_state["adjudications"].items() if k not in touched}
    save_json(RL._state_path(ws, vid), old_state)


def _reconcile(ws, parent: str, vid: str) -> Dict[str, Any]:
    """对照父版本的问题与新版本的问题：已应用过的问题消失 → resolved，仍在 → still_open，其余新出现的单独标记。"""
    old = RK.load_findings(ws, parent)
    new = RK.load_findings(ws, vid)
    new_by_id = {f["id"]: f for f in new["findings"]}
    old_by_id = {f["id"]: f for f in old["findings"]}
    summary = {"resolved": [], "still_open": [], "rejected_carried": [], "new": []}
    for f in old["findings"]:
        if f["status"] != "applied" or f.get("applied_version") != vid:
            continue
        if f["id"] in new_by_id:
            f["status"] = "still_open"
            new_by_id[f["id"]]["status"] = "still_open"
            new_by_id[f["id"]]["notes"] = f.get("notes", "")
            summary["still_open"].append(f["id"])
        else:
            f["status"] = "resolved"
            summary["resolved"].append(f["id"])
    for fid, f in new_by_id.items():
        prev = old_by_id.get(fid)
        if prev and prev["status"] == "rejected" and f["status"] == "open":
            f["status"], f["notes"] = "rejected", prev.get("notes", "")
            summary["rejected_carried"].append(fid)
        if prev is None:
            f["new_since"] = parent
            summary["new"].append(fid)
    _write_pool(ws, parent, old)
    _write_pool(ws, vid, new)
    return summary


def increment(ws, invoker: Callable[..., Any], progress=None) -> Dict[str, Any]:
    """增量复评（基于 head）。需要 head 有父版本。返回对照结果与各阶段摘要。"""
    say = progress or (lambda _t: None)
    vid = V.load_index(ws)["head"]
    if not vid:
        raise FlowError("没有 head 版本")
    entry = V.get_entry(ws, vid)
    parent = entry.get("parent")
    if not parent:
        raise FlowError("head 没有父版本，无法对照改动前后的问题")
    if not (RK.review_dir(ws, parent) / "findings.json").exists():
        raise FlowError(f"父版本 {parent} 没有评估记录，请先在改动前对它运行一次评估")
    say(f"增量复评：head {vid}，对照父版本 {parent}")
    sync_applied(ws, parent)                                   # 父版本上已应用的问题先标为 applied
    cards_res = RC.generate_cards(ws, vid, invoker, progress=say)
    _carry_state(ws, parent, vid)
    RK.run_checks(ws, vid)
    review = RL.run_review(ws, vid, invoker, progress=say)
    summary = _reconcile(ws, parent, vid)
    say(f"增量复评完成：已解决 {len(summary['resolved'])}，仍存在 {len(summary['still_open'])}，"
        f"新出现 {len(summary['new'])}")
    return {"version": vid, "parent": parent, "cards": {k: cards_res[k] for k in ("generated", "cached", "failed")},
            "review_calls": review["calls"], "review_paused": review["paused"], **summary}
