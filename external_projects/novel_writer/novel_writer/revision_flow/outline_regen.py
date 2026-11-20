"""R4 大纲重生成：根据 head 版本已写出的全部正文，反推「书实际写成了什么样」的大纲。

流程：
  1. candidate(...)：读取 head 的当前蓝图与全部章节的事实卡（缺卡则拒绝）。
     确定性部分由 Python 产出：章号、章名（保留蓝图中的章名，没有则取正文标题）、实际字数（作为 word_budget）、
     出场人物（事实卡人物经角色档案归一）。
     LLM 只判断需要判断的部分：每章 function / chapter_type / pov / tension_level，以及全书的幕划分
     与主线、支线概要（分批调用，每批 OUTLINE_BATCH 章，最后一次调用处理幕划分）。
     结果是「候选」，写入 _revision/outline/ol_<id>/，不进入版本。
  2. diff：候选与当前蓝图逐章比较（字段变化、未写出的章节、结构变化）。
  3. adopt(...)：作者确认（可整份，或按章勾选；可就地改某章的 function）。派生「仅大纲」新版本：
     从 head 复制，只写入 大纲规划/蓝图（经 blueprint_store 严格读回比对），正文一字不动；
     在 修订记录/蓝图变更日志.md 追加一条记录；定稿并设为 head。可用 discard 撤销。

约束：
  * 候选基于某个版本；采纳时 head 必须仍是它，否则要求重新生成（避免覆盖别人的改动）；
  * 只读版本、只写 _revision/ 与新派生版本；不改 章节简述/（大纲先行书的简述是写作前约束，不是事后记录）；
  * 章节大纲.md 由规划步骤的 LLM 生成，本期不重写（会丢失原有内容），changelog 中注明它可能与蓝图不一致。
"""
from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import json
import math
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from novel_writer import blueprint_store as BS
from novel_writer.revision_flow import review_cards as RC
from novel_writer.revision_flow import review_llm as RL
from novel_writer.revision_flow import versions as V
from novel_writer.revision_flow.common import FlowError, load_json, save_json, sub_dir, version_dir
from novel_writer.textio import read_text_lenient

SCHEMA = 1
OUTLINE_BATCH = 20                 # 每次 LLM 调用处理的章数
MAX_ATTEMPTS = 2
SKILL = "novel-planner"
CHAPTER_FIELDS = ("function", "chapter_type", "pov", "tension_level", "word_budget", "characters_present")
BATCH_MARK = "【任务】反推大纲：逐章"
STRUCT_MARK = "【任务】反推大纲：全书结构"
CHANGELOG = "修订记录/蓝图变更日志.md"
BLUEPRINT_REL = "大纲规划/蓝图"


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _s(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def _chars(text: str) -> int:
    return sum(1 for c in text if not c.isspace())


# ─────────────────────────────── 读取当前蓝图 ───────────────────────────────

def current_blueprint(ws, vid: str) -> Dict[str, Any]:
    base = version_dir(ws, vid) / BLUEPRINT_REL
    try:
        return BS.read_store(base)
    except BS.BlueprintStoreError as exc:
        raise FlowError("当前版本的蓝图无法读取：" + "；".join(exc.problems)) from None


def _state_path(ws, oid: str) -> Path:
    return sub_dir(ws, "outline") / oid / "candidate.json"


def load_candidate(ws, oid: str) -> Dict[str, Any]:
    data = load_json(_state_path(ws, oid), default=None)
    if not isinstance(data, dict):
        raise FlowError(f"候选大纲不存在：{oid}")
    return data


def list_candidates(ws) -> List[Dict[str, Any]]:
    folder = sub_dir(ws, "outline")
    out = []
    for p in sorted(folder.glob("ol_*/candidate.json")):
        d = load_json(p, default=None)
        if isinstance(d, dict):
            out.append({k: d.get(k) for k in ("id", "created_at", "base_version", "status", "adopted_version",
                                              "outline_first", "summary", "unwritten")})
    return out


# ─────────────────────────────── 提示词 ───────────────────────────────

_BATCH_FORMAT = ('{"chapters": [{"chapter": 章号, "function": "本章在全书中的功能（一句话）", '
                 '"chapter_type": "类型（如 铺垫 / 冲突 / 转折 / 高潮 / 过渡 / 收束）", '
                 '"pov": "视角人物或「全知」", "tension_level": 1到10的整数}]}')
_STRUCT_FORMAT = ('{"acts": [{"name": "幕名", "chapter_range": [起章, 止章]}], '
                  '"main_plot": "主线概要（一段话）", "sub_plots": ["支线概要"]}')


def _batch_prompt(rows: List[Dict[str, Any]], out_file: Path, errors: List[str]) -> str:
    lines = []
    for r in rows:
        lines.append(f"第{r['number']}章《{r['title']}》　路径：{r['path']}\n"
                     f"  时间：{r['time_marker'] or '—'}；地点：{r['location_start'] or '—'} → {r['location_end'] or '—'}\n"
                     f"  人物：{'、'.join(r['characters']) or '—'}\n"
                     f"  事件：{'；'.join(r['events'][:6]) or '—'}\n"
                     f"  结尾局面：{r['ending_state'] or '—'}")
    parts = [
        f"{BATCH_MARK}：只根据正文实际写出的内容判断每一章的功能，不要参照原规划、不要补全或美化。",
        "【章节摘要】（来自事实卡；需要核对时请读取对应章节路径）\n" + "\n\n".join(lines),
        "【要求】每个章号都要给出一项，且只给这些章号；function 写该章实际起到的作用；tension_level 为该章的紧张程度。",
        f"【输出】把一个 JSON 对象写入文件：{out_file}\n格式：{_BATCH_FORMAT}\n只写 JSON，不要代码围栏，不要多余文字。",
    ]
    if errors:
        parts.append("【上一次输出未通过校验】\n" + "\n".join(f"- {e}" for e in errors))
    return "\n\n".join(parts) + "\n"


def _struct_prompt(rows: List[Dict[str, Any]], total: int, out_file: Path, errors: List[str]) -> str:
    body = "\n".join(f"第{r['number']}章《{r['title']}》：{r['function']}" for r in rows)
    parts = [
        f"{STRUCT_MARK}：根据各章实际功能，划分全书的幕，并概括主线与支线。",
        f"【全书共 {total} 章】\n【各章实际功能】\n{body}",
        f"【要求】幕必须首尾相接、覆盖第 1 章到第 {total} 章，不重叠；幕名简短。主线与支线只概括正文实际写出的内容。",
        f"【输出】把一个 JSON 对象写入文件：{out_file}\n格式：{_STRUCT_FORMAT}\n只写 JSON，不要代码围栏，不要多余文字。",
    ]
    if errors:
        parts.append("【上一次输出未通过校验】\n" + "\n".join(f"- {e}" for e in errors))
    return "\n\n".join(parts) + "\n"


# ─────────────────────────────── 校验 ───────────────────────────────

def _parse_batch(raw: str, expected: List[int]) -> Dict[int, Dict[str, Any]]:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise FlowError(f"不是合法 JSON：{exc}") from None
    items = data.get("chapters") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise FlowError("chapters 必须是列表")
    out: Dict[int, Dict[str, Any]] = {}
    for it in items:
        if not isinstance(it, dict) or isinstance(it.get("chapter"), bool) or not isinstance(it.get("chapter"), int):
            raise FlowError("每一项需要整数 chapter")
        n = it["chapter"]
        if n not in expected:
            raise FlowError(f"不应包含第 {n} 章")
        if n in out:
            raise FlowError(f"第 {n} 章重复")
        tension = it.get("tension_level")
        if isinstance(tension, bool) or not isinstance(tension, int) or not 1 <= tension <= 10:
            raise FlowError(f"第 {n} 章 tension_level 必须是 1 到 10 的整数")
        fields = {k: _s(it.get(k)) for k in ("function", "chapter_type", "pov")}
        if not fields["function"] or not fields["chapter_type"] or not fields["pov"]:
            raise FlowError(f"第 {n} 章的 function、chapter_type、pov 都不能为空")
        out[n] = {**fields, "tension_level": tension}
    missing = [n for n in expected if n not in out]
    if missing:
        raise FlowError(f"缺少章节：{missing}")
    return out


def _parse_struct(raw: str, total: int) -> Dict[str, Any]:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise FlowError(f"不是合法 JSON：{exc}") from None
    if not isinstance(data, dict) or not isinstance(data.get("acts"), list) or not data["acts"]:
        raise FlowError("acts 必须是非空列表")
    acts, expect = [], 1
    for a in data["acts"]:
        rng = a.get("chapter_range") if isinstance(a, dict) else None
        if not _s(a.get("name") if isinstance(a, dict) else None) or not isinstance(rng, list) or len(rng) != 2:
            raise FlowError("每一幕需要 name 与 chapter_range [起, 止]")
        lo, hi = rng
        if not all(isinstance(x, int) and not isinstance(x, bool) for x in (lo, hi)) or lo != expect or hi < lo:
            raise FlowError(f"幕的章节范围必须从第 {expect} 章开始、首尾相接，实际为 {rng}")
        acts.append({"name": _s(a["name"]), "chapter_range": [lo, hi]})
        expect = hi + 1
    if expect - 1 != total:
        raise FlowError(f"幕必须覆盖到第 {total} 章，实际只覆盖到第 {expect - 1} 章")
    subs = data.get("sub_plots") or []
    if not isinstance(subs, list) or not all(isinstance(x, str) for x in subs):
        raise FlowError("sub_plots 必须是字符串列表")
    return {"acts": acts, "main_plot": _s(data.get("main_plot")), "sub_plots": [x.strip() for x in subs if x.strip()]}


# ─────────────────────────────── 候选生成 ───────────────────────────────

def _book_rows(ws, vid: str) -> List[Dict[str, Any]]:
    """确定性部分：每章的章号、正文标题、实际字数、事实卡摘要。"""
    cards, missing = RC.load_cards(ws, vid)
    if missing:
        raise FlowError(f"有 {len(missing)} 章没有事实卡（第{'、'.join(str(n) for n in missing)}章），"
                        "请先在「🔎 评估」页生成事实卡后再重生成大纲")
    names = RC.known_names(ws, vid)
    rows = []
    for src in RC.list_chapter_sources(ws, vid):
        text, _ = read_text_lenient(src["path"])
        card = cards[src["number"]]
        rows.append({"number": src["number"], "title": src["title"], "path": src["path"],
                     "words": _chars(text),
                     "characters": [RC.canon_name(c["name"], names)[0] for c in card["characters"]],
                     "time_marker": card["time_marker"], "location_start": card["location_start"],
                     "location_end": card["location_end"], "events": card["events"],
                     "ending_state": card["ending_state"]})
    return rows


def _merge_candidate(old_bp: Dict[str, Any], rows: List[Dict[str, Any]], judged: Dict[int, Dict[str, Any]],
                     structure: Dict[str, Any]) -> Dict[str, Any]:
    old_by = {c["id"]: c for c in old_bp["chapters"]}
    chapters = []
    for r in rows:
        n = r["number"]
        old = old_by.get(n, {})
        j = judged[n]
        title = old.get("title") or f"第{n}章 {r['title']}"
        chapters.append({
            "id": n, "title": title, "function": j["function"], "chapter_type": j["chapter_type"],
            "pov": j["pov"], "tension_level": j["tension_level"], "word_budget": max(100, round(r["words"], -2)),
            "characters_present": r["characters"],
        })
    meta = {k: copy.deepcopy(v) for k, v in old_bp.items() if k not in ("chapters", "structure")}
    struct = copy.deepcopy(old_bp.get("structure") or {})
    struct.update({"acts": structure["acts"], "total_chapters": len(rows)})
    struct["actual_summary"] = {"main_plot": structure["main_plot"], "sub_plots": structure["sub_plots"]}
    return {**meta, "structure": struct, "chapters": chapters}


def diff_blueprints(old_bp: Dict[str, Any], new_bp: Dict[str, Any]) -> Dict[str, Any]:
    """候选与当前蓝图的逐章差异（纯函数）。"""
    old_by = {c["id"]: c for c in old_bp["chapters"]}
    new_by = {c["id"]: c for c in new_bp["chapters"]}
    rows: List[Dict[str, Any]] = []
    for n in sorted(set(old_by) | set(new_by)):
        o, nw = old_by.get(n), new_by.get(n)
        if o is None:
            rows.append({"id": n, "kind": "added", "fields": {}})
        elif nw is None:
            rows.append({"id": n, "kind": "unwritten", "fields": {}})
        else:
            fields = {f: {"old": o.get(f), "new": nw.get(f)} for f in CHAPTER_FIELDS if o.get(f) != nw.get(f)}
            if fields:
                rows.append({"id": n, "kind": "changed", "fields": fields})
    structure_changed = (old_bp.get("structure") or {}).get("acts") != (new_bp.get("structure") or {}).get("acts")
    return {"chapters": rows, "structure_changed": structure_changed,
            "counts": {"changed": sum(r["kind"] == "changed" for r in rows),
                       "added": sum(r["kind"] == "added" for r in rows),
                       "unwritten": sum(r["kind"] == "unwritten" for r in rows)}}


def estimate(ws, version: Optional[str] = None) -> Dict[str, Any]:
    vid = RC.resolve_version(ws, version)
    n = len(RC.list_chapter_sources(ws, vid))
    batches = max(1, math.ceil(n / OUTLINE_BATCH))
    settings = ws.settings() or {}
    return {"version": vid, "chapters": n, "batches": batches, "calls_typical": batches + 1,
            "outline_first": settings.get("writing_mode") == "outline_first"}


def generate_candidate(ws, version: Optional[str], invoker, progress=None) -> Dict[str, Any]:
    say = progress or (lambda _t: None)
    vid = RC.resolve_version(ws, version)
    old_bp = current_blueprint(ws, vid)
    rows = _book_rows(ws, vid)
    total = len(rows)
    oid = "ol_" + uuid.uuid4().hex[:8]                       # 同一秒内的多次生成也不能互相覆盖
    work = sub_dir(ws, "outline") / oid
    say(f"大纲重生成：版本 {vid}，共 {total} 章，分 {math.ceil(total / OUTLINE_BATCH)} 批判定")
    budget = RL._Budget(0)                                   # 不设上限（与评估一致，估算仅作提示）
    judged: Dict[int, Dict[str, Any]] = {}
    for i in range(0, total, OUTLINE_BATCH):
        batch = rows[i:i + OUTLINE_BATCH]
        nums = [r["number"] for r in batch]
        say(f"第 {nums[0]}–{nums[-1]} 章：判定功能与张力")
        res, errs = RL._ask(ws, vid, kind="outline_batch", key=f"{oid}_{nums[0]}", skill=SKILL, invoker=invoker,
                            budget=budget, make_prompt=lambda out, e, b=batch: _batch_prompt(b, out, e),
                            parse=lambda raw, nums=nums: _parse_batch(raw, nums), say=say)
        if res is None:
            raise FlowError(f"第 {nums[0]}–{nums[-1]} 章判定失败：{'；'.join(errs)}")
        judged.update(res)
    by_no = {r["number"]: r for r in rows}
    struct_rows = [{"number": n, "title": by_no[n]["title"], "function": judged[n]["function"]} for n in sorted(judged)]
    say("全书结构：划分幕、概括主线与支线")
    structure, errs = RL._ask(ws, vid, kind="outline_struct", key=oid, skill=SKILL, invoker=invoker, budget=budget,
                              make_prompt=lambda out, e: _struct_prompt(struct_rows, total, out, e),
                              parse=lambda raw: _parse_struct(raw, total), say=say)
    if structure is None:
        raise FlowError(f"全书结构判定失败：{'；'.join(errs)}")
    new_bp = _merge_candidate(old_bp, rows, judged, structure)
    BS.split_parts(new_bp)                                   # 候选必须能被蓝图存储接受
    diff = diff_blueprints(old_bp, new_bp)
    book_nums = {r["number"] for r in rows}
    unwritten = [c["id"] for c in old_bp["chapters"] if c["id"] not in book_nums]
    settings = ws.settings() or {}
    cand = {
        "schema": SCHEMA, "id": oid, "created_at": _now(), "base_version": vid, "status": "candidate",
        "adopted_version": None, "outline_first": settings.get("writing_mode") == "outline_first",
        "unwritten": unwritten,
        "summary": {"chapters": total, "changed": diff["counts"]["changed"], "structure_changed": diff["structure_changed"]},
        "candidate": new_bp,
    }
    save_json(_state_path(ws, oid), cand)
    save_json(work / "diff.json", diff)
    say(f"候选大纲完成：{diff['counts']['changed']} 章的字段有变化，结构{'有' if diff['structure_changed'] else '无'}变化；"
        f"未写出的蓝图章节 {len(unwritten)} 个")
    return {"id": oid, "base_version": vid, "summary": cand["summary"], "unwritten": unwritten}


# ─────────────────────────────── 采纳 ───────────────────────────────

def discard_candidate(ws, oid: str) -> Dict[str, Any]:
    cand = load_candidate(ws, oid)
    if cand["status"] != "candidate":
        raise FlowError(f"候选状态为 {cand['status']}，不能放弃")
    cand["status"] = "discarded"
    save_json(_state_path(ws, oid), cand)
    return {"id": oid, "status": "discarded"}


def _canon(bp: Dict[str, Any]) -> str:
    return json.dumps(BS.split_parts(bp), ensure_ascii=False, sort_keys=True, default=str)


def adopt(ws, oid: str, *, chapters: Optional[List[int]] = None, edits: Optional[Dict[int, Dict[str, Any]]] = None,
          take_structure: bool = True, note: str = "") -> Dict[str, Any]:
    """采纳候选：派生「仅大纲」新版本。chapters=None 表示全部；edits 可就地改某章的字段（仅限 CHAPTER_FIELDS）。"""
    cand = load_candidate(ws, oid)
    if cand["status"] != "candidate":
        raise FlowError(f"候选状态为 {cand['status']}，不能采纳")
    head = V.load_index(ws)["head"]
    if cand["base_version"] != head:
        raise FlowError(f"候选基于 {cand['base_version']}，但 head 已是 {head}，请重新生成候选大纲")
    new_bp = cand["candidate"]
    cand_by = {c["id"]: c for c in new_bp["chapters"]}
    chosen = sorted(cand_by) if chapters is None else sorted(set(chapters))
    bad = [n for n in chosen if n not in cand_by]
    if bad:
        raise FlowError(f"候选中没有这些章：{bad}")
    edits = edits or {}
    for n, patch in edits.items():
        if int(n) not in chosen:
            raise FlowError(f"只能修改已勾选的章节（第 {n} 章未勾选）")
        bad_fields = [k for k in patch if k not in CHAPTER_FIELDS]
        if bad_fields:
            raise FlowError(f"不允许修改的字段：{bad_fields}")

    old_bp = current_blueprint(ws, head)
    bp = copy.deepcopy(old_bp)
    by_id = {c["id"]: c for c in bp["chapters"]}
    for n in chosen:
        dst = by_id.get(n) or {"id": n}
        src = cand_by[n]
        for f in CHAPTER_FIELDS:
            if f in src:
                dst[f] = copy.deepcopy(src[f])
        dst.setdefault("title", src["title"])
        for k, v in (edits.get(n) or edits.get(str(n)) or {}).items():
            dst[k] = v
        by_id[n] = dst
    if take_structure:
        bp["structure"] = copy.deepcopy(new_bp["structure"])
    bp["chapters"] = [by_id[k] for k in sorted(by_id)]

    # 严格校验：能拆分，且拆分后读回与内存中的结构一致
    tmp = Path(tempfile.mkdtemp(prefix="ol_rt_"))
    try:
        BS.write_store(tmp / "蓝图", tmp / "蓝图" / BS.INDEX_NAME, bp)
        if _canon(BS.read_store(tmp / "蓝图")) != _canon(bp):
            raise FlowError("回读比对不一致，已中止，head 未改动")
    except BS.BlueprintStoreError as exc:
        raise FlowError("生成的蓝图未通过校验：" + "；".join(exc.problems)) from None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    diff = diff_blueprints(old_bp, bp)
    vid2 = V.derive(ws, head, note=f"大纲重生成 {oid}")
    root = version_dir(ws, vid2)
    base = root / BLUEPRINT_REL
    BS.write_store(base, base / BS.INDEX_NAME, bp)
    back = BS.read_store(base)
    if _canon(back) != _canon(bp):
        raise FlowError("写入后读回不一致")
    _append_changelog(root, oid, head, chosen, diff, note)
    V.finalize(ws, vid2, [oid])
    V.set_head(ws, vid2)
    cand["status"], cand["adopted_version"] = "adopted", vid2
    cand["adopted_chapters"] = chosen
    save_json(_state_path(ws, oid), cand)
    return {"version": vid2, "parent": head, "chapters": chosen, "counts": diff["counts"],
            "structure_changed": diff["structure_changed"], "unwritten": cand.get("unwritten", []),
            "note": "正文未改动；章节大纲.md 与章节简述未自动同步（见变更日志）"}


def _append_changelog(root: Path, oid: str, parent: str, chosen: List[int], diff: Dict[str, Any], note: str) -> None:
    path = root / CHANGELOG
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("# 蓝图变更日志\n\n", encoding="utf-8")
    lines = [f"## {_now()}　大纲重生成 {oid}（基于 {parent}）", ""]
    if note:
        lines += [f"- 作者备注：{note}"]
    lines += [f"- 采纳章节：{'、'.join(str(n) for n in chosen)}（共 {len(chosen)} 章）",
              f"- 字段变化：{diff['counts']['changed']} 章；结构{'有' if diff['structure_changed'] else '无'}变化",
              "- 正文未改动。章节大纲.md 未重写，可能与蓝图不一致；章节简述未同步。", ""]
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
