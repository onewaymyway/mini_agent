"""大纲先行模式（writing_mode = outline_first）。

O0：步骤结构与接入点（计划、闸门、模式开关）。
O1：章节简述的生成与确定性校验（本文件 + brief_draft 处理器）。

数据：每章一个规范 JSON `大纲规划/章节简述/第NNN章.json`（唯一事实来源），并渲染为同名 `.md` 供阅读。
字段：
  chapter, title, act
  narrative      叙述段落，300–600 个非空白字符（场景顺序、人物行动、对话要点）
  key:
    must_events     [str]  必须发生的事件（非空）
    must_states     [{who, change}]  人物状态变化（非空；who 必须是已知角色）
    must_foreshadow [str]  伏笔编号 F\\d+（可为空；非空时必须存在于伏笔账本）
    must_hook       str    章末钩子（非空）
  deviations       [{where, original, change, reason}]  O3 写作偏离记录，O1 为空

生成：每幕一个步骤 `brief_draft:<幕号>`，由 LLM 产出该幕全部章节的简述，写到执行器的输出目录，
再经确定性校验后落盘；校验失败的幕带着错误重试一次，仍失败则步骤失败（不写半成品）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from novel_writer import steps as S
from novel_writer.fsutil import atomic_write_json, atomic_write_text, read_json
from novel_writer.workspace import Workspace, count_words

BRIEF_DRAFT_ID = "brief_draft"        # 步骤 id 前缀：实际为 brief_draft:<幕号>
BRIEF_REVIEW_ID = "brief_review"
MODE_OUTLINE_FIRST = "outline_first"
BRIEF_DIR_REL = "大纲规划/章节简述"
NARRATIVE_MIN, NARRATIVE_MAX = 300, 600
FORESHADOW_RE = re.compile(r"^F\d+$")
MAX_ATTEMPTS = 2


def is_outline_first(settings: dict) -> bool:
    return (settings or {}).get("writing_mode") == MODE_OUTLINE_FIRST


# ─────────────────────────────── 步骤定义 ───────────────────────────────

def brief_draft_step(act: int, start: int, end: int, deps: Tuple[str, ...]) -> S.StepDef:
    return S.StepDef(
        id=f"{BRIEF_DRAFT_ID}:{act}", title=f"第{act}幕：生成章节简述（第{start}–{end}章）",
        stage="p2", category=S.CAT_BUILD, executor=S.EXECUTOR_PYTHON, handler=BRIEF_DRAFT_ID,
        inputs=(S.rel("blueprint"),), outputs=(), deps=deps, chapter=0,
        extra={"act": act, "start": start, "end": end},
    )


def brief_review_step(deps: Tuple[str, ...]) -> S.StepDef:
    return S.StepDef(
        id=BRIEF_REVIEW_ID, title="章节简述确认（等待作者确认，O2 实现中）", stage="p2", category=S.CAT_CHECKPOINT,
        executor=S.EXECUTOR_PYTHON, handler="checkpoint",
        inputs=(f"{BRIEF_DIR_REL}/第*章.json",), outputs=(), deps=deps,   # 简述任何改动都会让确认失效
    )


# ─────────────────────────────── 存储 ───────────────────────────────

def brief_dir(ws: Workspace) -> Path:
    return ws.root / BRIEF_DIR_REL


def brief_json_path(ws: Workspace, n: int) -> Path:
    return brief_dir(ws) / f"第{n:03d}章.json"


def brief_md_path(ws: Workspace, n: int) -> Path:
    return brief_dir(ws) / f"第{n:03d}章.md"


def load_brief(ws: Workspace, n: int) -> Optional[Dict[str, Any]]:
    data = read_json(brief_json_path(ws, n), default=None)
    return data if isinstance(data, dict) else None


def render_md(b: Dict[str, Any]) -> str:
    k = b["key"]
    lines = [f"# 第{b['chapter']}章 {b['title']}", "", "## 叙述简述", "", b["narrative"].strip(), "",
             "## 关键内容（写作不得偏离）", "", "**必须发生的事件**"]
    lines += [f"- {e}" for e in k["must_events"]]
    lines += ["", "**人物状态变化**"] + [f"- {s['who']}：{s['change']}" for s in k["must_states"]]
    lines += ["", "**伏笔动作**"] + ([f"- {f}" for f in k["must_foreshadow"]] or ["- 无"])
    lines += ["", f"**章末钩子**：{k['must_hook']}", "", "## 偏离记录", ""]
    lines += [f"- {d['where']}：{d['original']} → {d['change']}（原因：{d['reason']}）" for d in b.get("deviations", [])]
    if not b.get("deviations"):
        lines.append("（暂无）")
    return "\n".join(lines) + "\n"


def save_brief(ws: Workspace, b: Dict[str, Any]) -> None:
    n = b["chapter"]
    atomic_write_json(brief_json_path(ws, n), b)
    atomic_write_text(brief_md_path(ws, n), render_md(b))


# ─────────────────────────────── 校验（确定性） ───────────────────────────────

def known_names(ws: Workspace) -> List[str]:
    d = ws.path("characters_dir")
    return sorted({p.stem for p in d.glob("*.md")}) if d.is_dir() else []


def known_foreshadow(ws: Workspace) -> List[str]:
    d = ws.path("plot_items_dir")
    return sorted({p.stem for p in d.glob("F*.md")}) if d.is_dir() else []


def validate_brief(b: Any, *, chapter: int, title: str, names: List[str], foreshadow: List[str]) -> List[str]:
    """返回错误列表（空表示通过）。"""
    errs: List[str] = []
    if not isinstance(b, dict):
        return [f"第{chapter}章：条目不是对象"]
    if b.get("chapter") != chapter:
        errs.append(f"第{chapter}章：chapter 应为 {chapter}，当前 {b.get('chapter')!r}")
    if not str(b.get("title") or "").strip():
        errs.append(f"第{chapter}章：缺少 title")
    narrative = str(b.get("narrative") or "")
    words = count_words(narrative)
    if not (NARRATIVE_MIN <= words <= NARRATIVE_MAX):
        errs.append(f"第{chapter}章：叙述简述 {words} 字，应在 {NARRATIVE_MIN}–{NARRATIVE_MAX} 字之间")
    key = b.get("key")
    if not isinstance(key, dict):
        return errs + [f"第{chapter}章：缺少 key 对象"]
    events = key.get("must_events")
    if not isinstance(events, list) or not events or not all(isinstance(x, str) and x.strip() for x in events):
        errs.append(f"第{chapter}章：must_events 必须是非空字符串列表")
    states = key.get("must_states")
    if not isinstance(states, list) or not states:
        errs.append(f"第{chapter}章：must_states 必须是非空列表")
    else:
        for s in states:
            who = s.get("who") if isinstance(s, dict) else None
            if not isinstance(s, dict) or not str(s.get("change") or "").strip() or not str(who or "").strip():
                errs.append(f"第{chapter}章：must_states 每项需要 who 与 change")
            elif names and who not in names:
                errs.append(f"第{chapter}章：must_states 中的人物「{who}」不在人物档案中")
    fs = key.get("must_foreshadow")
    if not isinstance(fs, list):
        errs.append(f"第{chapter}章：must_foreshadow 必须是列表（可为空）")
    else:
        for f in fs:
            if not isinstance(f, str) or not FORESHADOW_RE.match(f):
                errs.append(f"第{chapter}章：伏笔编号「{f}」格式应为 F+数字")
            elif foreshadow and f not in foreshadow:
                errs.append(f"第{chapter}章：伏笔「{f}」不在伏笔账本中")
    if not str(key.get("must_hook") or "").strip():
        errs.append(f"第{chapter}章：must_hook 不能为空")
    if b.get("deviations") not in (None, []):
        errs.append(f"第{chapter}章：新生成的简述不应包含偏离记录")
    return errs


def parse_act_output(raw: str, numbers: List[int], titles: Dict[int, str], names: List[str],
                     foreshadow: List[str], act: int) -> Tuple[List[Dict[str, Any]], List[str]]:
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as exc:
        return [], [f"不是合法 JSON：{exc}"]
    items = data.get("chapters") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return [], ["顶层必须是 {\"chapters\": [...]}"]
    by_no = {}
    for it in items:
        if isinstance(it, dict) and isinstance(it.get("chapter"), int):
            by_no.setdefault(it["chapter"], it)
    errs: List[str] = []
    missing = [n for n in numbers if n not in by_no]
    extra = sorted(set(by_no) - set(numbers))
    if missing:
        errs.append(f"缺少章节：{missing}")
    if extra:
        errs.append(f"不属于第{act}幕的章节：{extra}")
    out: List[Dict[str, Any]] = []
    for n in numbers:
        if n not in by_no:
            continue
        item = by_no[n]
        item.setdefault("title", titles.get(n, ""))
        item["act"] = act
        item.setdefault("deviations", [])
        item_errs = validate_brief(item, chapter=n, title=titles.get(n, ""), names=names, foreshadow=foreshadow)
        errs.extend(item_errs)
        out.append(item)
    return (out if not errs else []), errs


# ─────────────────────────────── 提示词 ───────────────────────────────

def _prompt(ws: Workspace, act: int, start: int, end: int, blueprint_entries: List[Dict[str, Any]],
            prev_tail: Optional[Dict[str, Any]], names: List[str], foreshadow: List[str],
            out_file: Path, errors: List[str]) -> str:
    parts = [
        f"【任务】为第{act}幕（第{start}–{end}章）的每一章写「章节简述」。"
        "每章的叙述简述是 300–600 字的场景段落（不要列表），写清场景顺序、人物行动和对话要点，"
        "并给出关键内容（必须事件、人物状态变化、伏笔动作、章末钩子）。关键内容之后会被强制核对，必须具体。",
        "【蓝图中本幕的章节条目】\n" + json.dumps(blueprint_entries, ensure_ascii=False, indent=2),
        f"【可用人物（must_states 的 who 只能从中选）】{'、'.join(names) if names else '（人物档案为空，请使用蓝图中出现的人物名）'}",
        f"【可用伏笔编号】{'、'.join(foreshadow) if foreshadow else '（伏笔账本为空，must_foreshadow 留空列表）'}",
    ]
    if prev_tail:
        parts.append("【前一章简述的结尾（保持衔接）】\n"
                     f"第{prev_tail['chapter']}章章末钩子：{prev_tail['key']['must_hook']}")
    parts.append(
        "【输出】把一个 JSON 对象写入文件：" + str(out_file) + "\n"
        "格式：{\"chapters\": [{\"chapter\": 章号整数, \"title\": \"章名\", \"narrative\": \"...\", "
        "\"key\": {\"must_events\": [\"...\"], \"must_states\": [{\"who\": \"人物名\", \"change\": \"...\"}], "
        "\"must_foreshadow\": [\"F1\"], \"must_hook\": \"...\"}}]}\n"
        f"必须覆盖第{start}–{end}章的每一章，且只包含这些章。只写 JSON，不要代码围栏。"
    )
    if errors:
        parts.append("【上一次输出未通过校验，请修正后重新写出全部章节】\n" + "\n".join(f"- {e}" for e in errors[:30]))
    return "\n\n".join(parts) + "\n"


# ─────────────────────────────── 处理器 ───────────────────────────────

def generate_act(ws: Workspace, act: int, start: int, end: int, invoker, *, feedback: str = "",
                 step_id: str = "brief") -> Dict[str, Any]:
    """生成第 act 幕（start–end 章）的全部简述；通过校验后才落盘。feedback 为作者对本幕的修改意见。"""
    if invoker is None:
        raise RuntimeError("未配置执行器（invoker），无法生成章节简述")
    from novel_writer import blueprint_store as BS
    blueprint = BS.load_for_ws(ws, strict=True) or {}
    chapters = [c for c in blueprint.get("chapters") or [] if isinstance(c, dict) and isinstance(c.get("id"), int)
                and start <= c["id"] <= end]
    numbers = list(range(start, end + 1))
    titles = {c["id"]: str(c.get("title") or "") for c in chapters}
    missing_entries = [n for n in numbers if n not in {c["id"] for c in chapters}]
    if missing_entries:
        raise RuntimeError(f"第{act}幕蓝图缺少章节条目 {missing_entries}，请先完成 plan_act / blueprint_extend")
    names, foreshadow = known_names(ws), known_foreshadow(ws)
    prev = None
    for n in range(start - 1, 0, -1):
        prev = load_brief(ws, n)
        if prev:
            break
    last_error: List[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        out_dir = ws.root / "_studio" / "brief_runs" / f"act{act:02d}" / f"attempt{attempt:02d}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"brief_act_{act:02d}.json"
        prompt = _prompt(ws, act, start, end, chapters, prev, names, foreshadow, out_file, last_error)
        if feedback.strip():
            prompt += "\n【作者对本幕的修改意见（必须落实，关键内容仍须具体）】\n" + feedback.strip() + "\n"
        invoker(skill="novel-planner", prompt=prompt, export_dir=out_dir, step_id=f"{step_id}#{attempt}")
        if not out_file.is_file():
            last_error = ["没有写出简述 JSON 文件"]
            continue
        items, errs = parse_act_output(out_file.read_text(encoding="utf-8"), numbers, titles, names, foreshadow, act)
        if not errs:
            for item in items:
                save_brief(ws, item)
            _write_index(ws)
            return {"summary": f"第{act}幕：已生成第{start}–{end}章简述（{len(items)} 章）", "chapters": numbers}
        last_error = errs
    raise RuntimeError(f"第{act}幕简述未通过校验：" + "；".join(last_error[:8]))


def h_brief_draft(ws: Workspace, step: S.StepDef, settings: Dict[str, Any], invoker=None) -> Dict[str, Any]:
    return generate_act(ws, step.extra["act"], step.extra["start"], step.extra["end"], invoker,
                        feedback=str(step.extra.get("feedback") or ""), step_id=step.id)


def _write_index(ws: Workspace) -> None:
    rows = []
    for p in sorted(brief_dir(ws).glob("第*章.json")):
        b = read_json(p, default={}) or {}
        rows.append(f"- 第{b.get('chapter')}章 {b.get('title', '')}（第{b.get('act')}幕）")
    atomic_text = "# 章节简述索引\n\n" + ("\n".join(rows) if rows else "（尚无简述）") + "\n"
    atomic_write_text(brief_dir(ws) / "_index.md", atomic_text)


HANDLERS = {BRIEF_DRAFT_ID: h_brief_draft}
