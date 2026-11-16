"""一句话起稿 S2：对构思稿的反馈修订（整稿 / 分部分），生成新版本。

修订方式（v2，按部分独立修订）：
  * 每个被修订的部分单独调用一次模型，输出只包含这一部分（外加可选的 `_affects` 提示）。
    提示词只带其它部分的摘要，不再整份发送构思稿，因此内容变长也不会撑大提示词。
  * 某个部分缺失、格式不符或校验不过，只重试这一部分（每部分最多 MAX_ATTEMPTS 次），错误与上一次输出片段一并反馈。
  * 已通过的部分保存为检查点（_studio/seed_draft/parts/）；整次修订失败时检查点保留，再次修订时复用，不重做。
  * 修改故事走向的幕结构时，剧情的幕标题必须跟着改：若剧情不在范围内，自动加入修订范围（并在日志中说明）。
  * 范围之外的部分只作为 `_affects` 提示返回，不会自动修改。
  * 没有产生任何实际改动 → 报错，不写版本。全部通过后才写新版本（kind = revise），旧版本保留。
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Callable, Dict, List, Optional

from novel_writer.brainstorm import seed_draft as SD
from novel_writer.brainstorm import seed_gen as SG
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace
from novel_writer.fsutil import atomic_write_json, read_json

MAX_ATTEMPTS = 3          # 每个部分的最多尝试次数
FEEDBACK_MAX = 3000
PARTS_DIR = "parts"
SYSTEM_PROMPT = ("你是小说构思助手。根据作者的反馈修改构思稿的一个部分。"
                 "只输出一个 JSON 对象，不要调用任何工具，不要写文件，不要输出 JSON 之外的文字。")

SECTION_HINT = {
    "premise": "字符串（一两句话）。",
    "storyline": ('对象：{"logline": 一句话主线, "structure": {"type", "total_chapters", "acts": [{"title", "chapters", "goal"}]}, '
                  '"threads": [{"name", "role": main/sub/character/mystery, "opening", "development", "turn", "ending", "meets": []}]}。'
                  "结构与篇幅只在反馈要求时改动；改动幕结构时，幕的章节范围必须连续覆盖 1–total_chapters。"),
    "world": '对象：{"rules": [{"text", "cost"}], "locations": [{"text"}], "factions": [{"text"}], "timeline": [字符串], "taboos": [字符串]}。',
    "cast": '角色列表：[{"id": 沿用原 id, "name", "role", "want", "need", "flaw", "lie", "arc"}]。新增角色可不写 id。',
    "plot": ('对象：{"acts": [{"title", "chapters", "beats": [字符串]}], "twists": [字符串], "endings": [字符串]}。'
             "幕标题与顺序必须与「故事走向」的结构幕完全一致（见概要）。"),
    "open_questions": "列表：[{\"id\", \"text\"}] 或字符串列表。",
}


def _labels(sections: List[str]) -> str:
    return "、".join(SD.SECTION_LABELS[s] for s in sections)


def check_scope(sections: Optional[List[str]]) -> List[str]:
    """返回规范化的范围（空 = 整稿 = 全部六部分）。"""
    if not sections:
        return list(SD.SECTIONS)
    bad = [s for s in sections if s not in SD.SECTIONS]
    if bad:
        raise BrainstormError("未知部分：" + "、".join(bad))
    return [s for s in SD.SECTIONS if s in set(sections)]


def _cut(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n] + "…"


def digest(draft: Dict[str, Any], exclude: str) -> str:
    """其它部分的摘要（不含细节），供修订某一部分时作为背景。上限约 1200 字。"""
    lines: List[str] = []
    if exclude != "premise":
        lines.append(f"核心命题：{_cut(draft.get('premise', ''), 200)}")
    if exclude != "storyline":
        story = draft.get("storyline") or {}
        st = story.get("structure") or {}
        lines.append(f"一句话主线：{_cut(story.get('logline', ''), 200)}")
        lines.append(f"结构：{st.get('type', '')}" + (f"，共 {st.get('total_chapters')} 章" if st.get("total_chapters") else ""))
        lines.append("结构幕：" + "、".join(a.get("title", "") for a in st.get("acts") or []))
        lines.append("线索：" + "、".join(f"{t.get('name')}({SD.ROLE_LABELS.get(t.get('role'), '')})" for t in story.get("threads") or []))
    if exclude != "cast":
        cast = draft.get("cast") or {}
        names = [str((c or {}).get("name") or cid) for cid, c in cast.items()] if isinstance(cast, dict) else []
        lines.append("角色：" + "、".join(names))
    if exclude != "plot":
        lines.append("剧情幕：" + "、".join(a.get("title", "") for a in (draft.get("plot") or {}).get("acts") or []))
    return _cut("\n".join(lines), 1200)


def section_prompt(seed: Dict[str, str], old: Dict[str, Any], sec: str, feedback: str,
                   errors: Optional[List[str]] = None, prev_raw: str = "") -> str:
    label = SD.SECTION_LABELS[sec]
    cur = old[sec]
    if sec == "cast":
        cur = [{"id": cid, **card} for cid, card in (old.get("cast") or {}).items()]
    parts = [f"【作者最初的想法】\n{seed.get('idea', '')}"]
    if seed.get("constraints"):
        parts.append(f"【作者的额外要求（始终遵守）】\n{seed['constraints']}")
    parts.append(f"【其它部分概要（只作参考，不要输出）】\n{digest(old, sec)}")
    parts.append(f"【当前的「{label}」（JSON）】\n" + json.dumps(cur, ensure_ascii=False, indent=2))
    parts.append(f"【作者的反馈】\n{feedback.strip()}")
    parts.append(f"【修改要求】只修改与反馈有关的内容，其余保持原样。只输出一个 JSON 对象，形如 "
                 f'{{"{sec}": 修改后的完整「{label}」, "_affects": [需要跟着调整的其它部分键名，可为空]}}。'
                 f"「{label}」的结构：{SECTION_HINT[sec]}")
    if errors:
        msg = "【上一次输出未通过，请修正后重新输出】\n" + "\n".join(f"- {e}" for e in errors)
        if prev_raw:
            msg += "\n上一次输出的开头（供参考）：\n" + _cut(prev_raw, 400)
        parts.append(msg)
    return "\n\n".join(parts) + "\n"


def _parts_dir(ws: BrainstormWorkspace):
    return SD.draft_dir(ws) / PARTS_DIR


def _part_key(base_ver: int, sec: str, feedback: str, old_sec: Any) -> str:
    raw = json.dumps({"b": base_ver, "s": sec, "f": feedback, "o": old_sec}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _revise_section(ws, base_ver, old, seed, sec, feedback, generator, log) -> Dict[str, Any]:
    """修订单个部分：返回 {section, affects, attempts}。优先复用检查点。"""
    key = _part_key(base_ver, sec, feedback, old[sec])
    cp = _parts_dir(ws) / f"{key}.json"
    cached = read_json(cp, default=None)
    if isinstance(cached, dict) and "section" in cached:
        SG._emit(log, "info", f"「{SD.SECTION_LABELS[sec]}」沿用已通过的修订结果（检查点），不再调用模型")
        return {**cached, "key": key, "reused": True}
    errors: List[str] = []
    prev_raw = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        prompt = section_prompt(seed, old, sec, feedback, errors, prev_raw)
        SG._emit(log, "info", f"「{SD.SECTION_LABELS[sec]}」第 {attempt}/{MAX_ATTEMPTS} 次：提示词 {len(prompt)} 字，开始调用模型")
        raw = generator(prompt)
        prev_raw = str(raw)
        try:
            out = SG.parse_output(raw)
            if sec not in out:
                raise BrainstormError(f"输出缺少「{SD.SECTION_LABELS[sec]}」")
            candidate = copy.deepcopy(old)
            candidate[sec] = out[sec]
            SD.validate_draft(candidate, consistency=False)   # 只校验这一部分；幕结构与剧情的一致性在全部部分完成后再查
        except BrainstormError as exc:
            rel = SG._save_raw(ws, raw)
            SG._emit(log, "error", f"「{SD.SECTION_LABELS[sec]}」未通过：{exc}（原始输出已存档 {rel}）")
            errors = [str(exc)]
            continue
        raw_aff = out.get("_affects")
        affects = [s for s in (raw_aff if isinstance(raw_aff, list) else []) if s in SD.SECTIONS and s != sec]
        result = {"section": out[sec], "affects": list(dict.fromkeys(affects)), "attempts": attempt}
        atomic_write_json(cp, result)
        return {**result, "key": key, "reused": False}
    raise BrainstormError(f"修订「{SD.SECTION_LABELS[sec]}」失败：{MAX_ATTEMPTS} 次都未通过（{errors[0]}）；"
                          "没有写入任何版本；已通过的部分已保存为检查点，下次修订会复用")


def _run_body(ws: BrainstormWorkspace, feedback: str, sections: Optional[List[str]] = None, *,
              base_ver: Optional[int] = None, generator: Optional[SG.Generator] = None,
              log: Optional[Callable[[str, str], None]] = None) -> Dict[str, Any]:
    feedback = (feedback or "").strip()
    if not feedback:
        raise BrainstormError("请写下你的反馈")
    if len(feedback) > FEEDBACK_MAX:
        raise BrainstormError(f"反馈太长（{len(feedback)} 字，上限 {FEEDBACK_MAX}）")
    whole = not sections
    scope = check_scope(sections)
    base = SD.load(ws, base_ver)
    old, seed = base["draft"], base["seed"]
    if generator is None:
        from novel_writer.brainstorm.spark import helper_generator
        generator = lambda p: helper_generator(p, system=SYSTEM_PROMPT, timeout=600)  # noqa: E731
    SG._emit(log, "info", f"基于 v{base['ver']:03d}，范围：{'整稿' if whole else _labels(scope)}")

    queue = list(scope)
    done: Dict[str, Dict[str, Any]] = {}
    while queue:
        sec = queue.pop(0)
        if sec in done:
            continue
        res = _revise_section(ws, base["ver"], old, seed, sec, feedback, generator, log)
        done[sec] = res
        if sec == "storyline" and "plot" not in done and "plot" not in queue and not whole:
            new_titles = [a.get("title") for a in (res["section"].get("structure") or {}).get("acts") or []
                          if isinstance(a, dict)]
            old_titles = [a.get("title") for a in (old["storyline"].get("structure") or {}).get("acts") or []]
            if new_titles and new_titles != old_titles:
                queue.append("plot")
                SG._emit(log, "warning", "故事走向的幕结构有变化，剧情的幕标题需要跟着调整，已自动加入修订范围")

    merged = copy.deepcopy(old)
    for sec, res in done.items():
        merged[sec] = res["section"]
    final = SD.validate_draft(merged)
    changed = SD.changed_sections(old, final)
    if not changed:
        raise BrainstormError("这次修改没有产生任何实际改动（各部分都原样返回）")
    affects: List[str] = []
    for res in done.values():
        affects += res["affects"]
    touched = list(done.keys())
    affects = [s for s in dict.fromkeys(affects) if s not in touched]

    reason = f"反馈（{'整稿' if whole else _labels(touched)}）：{feedback[:60]}"
    ver = SD.save_version(ws, final, kind="revise", reason=reason, seed=seed)
    for res in done.values():                     # 成功写入后清掉本次检查点
        (_parts_dir(ws) / f"{res['key']}.json").unlink(missing_ok=True)
    SG._emit(log, "info", f"已保存为 v{ver:03d}，改动的部分：{_labels(changed)}（草稿层，尚未写入卡片）")
    return {"ver": ver, "base": base["ver"], "changed": changed, "discarded": [],
            "affects": affects, "attempts": sum(r["attempts"] for r in done.values() if not r["reused"]) or 1,
            "section_attempts": {s: r["attempts"] for s, r in done.items()},
            "reused": [s for s, r in done.items() if r["reused"]],
            "repaired": False}


def run(ws: BrainstormWorkspace, feedback: str, sections: Optional[List[str]] = None, *,
        base_ver: Optional[int] = None, generator: Optional[SG.Generator] = None,
        log: Optional[Callable[[str, str], None]] = None, _rid: Optional[str] = None) -> Dict[str, Any]:
    """修订（同步）。每次调用都记入输入记录（seed_history），结束时写入结果或失败原因。"""
    from novel_writer.brainstorm import seed_history as H
    payload = {"feedback": feedback or "", "sections": list(sections or []), "base_ver": base_ver}
    rid = _rid or H.submitted(ws, "revise", payload)
    try:
        res = _run_body(ws, feedback, sections, base_ver=base_ver, generator=generator, log=log)
    except BaseException as exc:
        H.outcome(ws, rid, "failed", error=str(exc))
        raise
    H.outcome(ws, rid, "done", ver=res["ver"])
    return res


def start_run(ws: BrainstormWorkspace, feedback: str, sections: Optional[List[str]] = None, *,
              base_ver: Optional[int] = None, generator: Optional[SG.Generator] = None) -> str:
    """输入先同步校验（反馈、范围、基础版本存在），再后台运行。每次提交（包括未通过校验的）都记入输入记录。"""
    from novel_writer.brainstorm import seed_history as H
    ws.meta()
    rid = H.submitted(ws, "revise", {"feedback": feedback or "", "sections": list(sections or []), "base_ver": base_ver})
    try:
        if not (feedback or "").strip():
            raise BrainstormError("请写下你的反馈")
        check_scope(sections)
        SD.load(ws, base_ver)
    except BrainstormError as exc:
        H.outcome(ws, rid, "rejected", error=str(exc))
        raise
    try:
        return SG.start_task(ws, lambda w, log: run(w, feedback, sections, base_ver=base_ver, generator=generator,
                                                    log=log, _rid=rid), prefix="r")
    except BrainstormError as exc:
        H.outcome(ws, rid, "rejected", error=str(exc))
        raise


def diff(ws: BrainstormWorkspace, a: int, b: int) -> Dict[str, Any]:
    """两版之间按部分的差异：{changed: [...], diffs: {部分: unified diff}}。"""
    da, db = SD.load(ws, a)["draft"], SD.load(ws, b)["draft"]
    changed = SD.changed_sections(da, db)
    return {"a": a, "b": b, "changed": changed, "diffs": {s: SD.section_diff(da, db, s) for s in changed}}
