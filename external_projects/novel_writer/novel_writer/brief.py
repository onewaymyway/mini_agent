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
import shutil
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
MAX_ATTEMPTS = 3


def is_outline_first(settings: dict) -> bool:
    return (settings or {}).get("writing_mode") == MODE_OUTLINE_FIRST


# ─────────────────────────────── 步骤定义 ───────────────────────────────

def brief_chapter_rel(n: int) -> str:
    return f"{BRIEF_DIR_REL}/第{n:03d}章.json"


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


def deviations_path(ws: Workspace, n: int) -> Path:
    return brief_dir(ws) / "偏离" / f"第{n:03d}章.json"


def load_deviations(ws: Workspace, n: int) -> List[Dict[str, Any]]:
    data = read_json(deviations_path(ws, n), default=[])
    return data if isinstance(data, list) else []


def load_brief(ws: Workspace, n: int) -> Optional[Dict[str, Any]]:
    data = read_json(brief_json_path(ws, n), default=None)
    return data if isinstance(data, dict) else None


def render_md(b: Dict[str, Any], deviations: Optional[List[Dict[str, Any]]] = None) -> str:
    k = b["key"]
    devs = b.get("deviations", []) if deviations is None else deviations
    lines = [f"# 第{b['chapter']}章 {b['title']}", "", "## 叙述简述", "", b["narrative"].strip(), "",
             "## 关键内容（写作不得偏离）", "", "**必须发生的事件**"]
    lines += [f"- {e}" for e in k["must_events"]]
    lines += ["", "**人物状态变化**"] + [f"- {s['who']}：{s['change']}" for s in k["must_states"]]
    lines += ["", "**伏笔动作**"] + ([f"- {f}" for f in k["must_foreshadow"]] or ["- 无"])
    lines += ["", f"**章末钩子**：{k['must_hook']}", "", "## 偏离记录", ""]
    lines += [f"- {d['where']}：{d['original']} → {d['change']}（原因：{d['reason']}）" for d in devs]
    if not devs:
        lines.append("（暂无）")
    return "\n".join(lines) + "\n"


def save_brief(ws: Workspace, b: Dict[str, Any]) -> None:
    n = b["chapter"]
    atomic_write_json(brief_json_path(ws, n), b)
    # 偏离记录单独存放：写作后追加偏离不会改动简述 JSON，因而不会让确认失效或让写作步骤过期
    atomic_write_text(brief_md_path(ws, n), render_md(b, load_deviations(ws, n)))


# ─────────────────────────────── 校验（确定性） ───────────────────────────────

def known_names(ws: Workspace) -> List[str]:
    d = ws.path("characters_dir")
    return sorted({p.stem for p in d.glob("*.md")}) if d.is_dir() else []


def known_foreshadow(ws: Workspace) -> List[str]:
    d = ws.path("plot_items_dir")
    return sorted({p.stem for p in d.glob("F*.md")}) if d.is_dir() else []


def length_note(narrative: str) -> Optional[str]:
    """字数提示（不影响校验）：超出建议范围时返回一句说明，否则 None。"""
    words = count_words(str(narrative or ""))
    if words > NARRATIVE_MAX:
        return f"叙述约 {words} 字，超出建议的 {NARRATIVE_MIN}–{NARRATIVE_MAX} 字（仅供参考，已接受）"
    if words < NARRATIVE_MIN:
        return f"叙述约 {words} 字，低于建议的 {NARRATIVE_MIN}–{NARRATIVE_MAX} 字（仅供参考，已接受）"
    return None


def validate_brief(b: Any, *, chapter: int, title: str, names: List[str], foreshadow: List[str]) -> List[str]:
    """返回错误列表（空表示通过）。"""
    errs: List[str] = []
    if not isinstance(b, dict):
        return [f"第{chapter}章：条目不是对象"]
    if b.get("chapter") != chapter:
        errs.append(f"第{chapter}章：chapter 应为 {chapter}，当前 {b.get('chapter')!r}")
    if not str(b.get("title") or "").strip():
        errs.append(f"第{chapter}章：缺少 title")
    # 字数只作参考（见 length_note），不作为校验条件：超出或不足都可以通过
    if not str(b.get("narrative") or "").strip():
        errs.append(f"第{chapter}章：叙述简述为空")
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
        # 伏笔账本是编号的唯一来源（文件名即编号，如 fs_001 或 F1）；按账本原样比对，不再强求 F+数字
        for f in fs:
            if not isinstance(f, str) or not f.strip():
                errs.append(f"第{chapter}章：must_foreshadow 中有空的编号")
            elif not foreshadow:
                errs.append(f"第{chapter}章：伏笔账本为空，must_foreshadow 应为空列表（不能自造编号「{f}」）")
            elif f not in foreshadow:
                errs.append(f"第{chapter}章：伏笔编号「{f}」不在伏笔账本中，只能从这些编号中原样选取：{'、'.join(foreshadow[:40])}")
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
        f"【建议】每章叙述简述约 {NARRATIVE_MIN}–{NARRATIVE_MAX} 字（仅供参考，超出也可以，不影响通过）；优先把关键内容写具体；"
        "must_foreshadow 只能从【可用伏笔编号】中原样选取（如 fs_001），不得改写或自造编号。"
        "叙述简述是场景段落（不要列表），写清场景顺序、人物行动和对话要点；关键内容（必须事件、人物状态变化、伏笔动作、章末钩子）要具体。",
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
        f"本次只需写出下列章节（其余章节已通过校验，不要输出）：{'、'.join(str(c['id']) for c in blueprint_entries)}。只写 JSON，不要代码围栏。"
    )
    if errors:
        parts.append("【上一次输出未通过校验，请修正后重新写出全部章节】\n" + "\n".join(f"- {e}" for e in errors[:30]))
    return "\n\n".join(parts) + "\n"


# ─────────────────────────────── 处理器 ───────────────────────────────

def _invoke_info(res: Any) -> Dict[str, Any]:
    """执行器返回值（InvokeResult）→ 诊断信息。旧式执行器返回 None 时按成功处理。"""
    if res is None:
        return {"ok": True, "error": "", "timed_out": False, "output_path": ""}
    return {"ok": bool(getattr(res, "ok", True)), "error": str(getattr(res, "error", "") or ""),
            "timed_out": bool(getattr(res, "timed_out", False)), "output_path": str(getattr(res, "output_path", "") or "")}


def _tail(path: str, n: int = 400) -> str:
    if not path:
        return ""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-n:].strip()


def _extract_chapters_json(text: str) -> Optional[str]:
    """从执行器的输出文本中找出第一个含 chapters 键的 JSON 对象（容忍前后说明文字与代码围栏）。"""
    dec = json.JSONDecoder()
    for m in list(re.finditer(r"\{", text or ""))[:200]:
        try:
            obj, _ = dec.raw_decode(text[m.start():])
        except ValueError:
            continue
        if isinstance(obj, dict) and "chapters" in obj:
            return json.dumps(obj, ensure_ascii=False)
    return None


def _salvage(out_dir: Path, out_file: Path, output_path: str) -> Optional[str]:
    """写文件失败时的补救：目录里别的 JSON、或执行器完整输出中的 JSON 若结构合法，即采用。"""
    for cand in sorted(out_dir.glob("*.json")):
        if cand == out_file:
            continue
        text = _extract_chapters_json(cand.read_text(encoding="utf-8", errors="replace"))
        if text:
            return text
    if output_path and Path(output_path).is_file():
        return _extract_chapters_json(Path(output_path).read_text(encoding="utf-8", errors="replace"))
    return None


def _missing_file_reason(info: Dict[str, Any], out_dir: Path, out_file: Path) -> str:
    """没有写出文件时，说明具体发生了什么（不是只报「没有写出」）。"""
    files = sorted(p.name for p in out_dir.iterdir() if p.name != "invoke.json") if out_dir.is_dir() else []
    parts = [f"没有写出简述文件 {out_file.name}（目录内容：{'、'.join(files) if files else '空'}）"]
    if not info["ok"]:
        parts.append(f"执行器返回失败：{info['error'] or '未给出原因'}")
    else:
        parts.append("执行器已结束，但回复中没有可用的简述 JSON，也没有调用写文件")
        tail = _tail(info["output_path"], 300)
        if tail:
            parts.append(f"执行器输出末尾：「{tail}」")
    return "；".join(parts)


def _fix_instruction(info: Dict[str, Any], out_file: Path) -> str:
    if not info["ok"]:
        return (f"上一次执行器报错（{info['error'] or '未给出原因'}）。本次请先确保输出文件可写："
                f"用写文件工具把完整 JSON 保存到绝对路径 {out_file}（目录已存在），不要只在回复中输出。")
    return (f"上一次执行器结束时没有保存文件。本次必须用写文件工具把完整 JSON 保存到绝对路径 {out_file}，"
            "不要只在回复中输出 JSON，也不要改用其它文件名。")


def _accepted_dir(ws: Workspace, act: int) -> Path:
    return ws.root / "_studio" / "brief_runs" / f"act{act:02d}" / "accepted"


def _load_accepted(ws: Workspace, act: int, numbers: List[int]) -> Dict[int, Dict[str, Any]]:
    """已通过校验的章节（检查点）。重跑时只重做没有通过的章节。"""
    out: Dict[int, Dict[str, Any]] = {}
    for n in numbers:
        b = read_json(_accepted_dir(ws, act) / f"ch{n:03d}.json", default=None)
        if isinstance(b, dict):
            out[n] = b
    return out


def _split_output(raw: str, numbers: List[int], titles: Dict[int, str], names: List[str],
                  foreshadow: List[str], act: int) -> Tuple[Dict[int, Dict[str, Any]], Dict[int, List[str]], List[str]]:
    """逐章校验：通过的章节单独保留，未通过的章节只带自己的问题。返回 (通过的章, 每章的问题, 其它问题)。"""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as exc:
        return {}, {}, [f"不是合法 JSON：{exc}"]
    items = data.get("chapters") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return {}, {}, ["缺少 chapters 列表"]
    good: Dict[int, Dict[str, Any]] = {}
    bad: Dict[int, List[str]] = {}
    extra: List[str] = []
    for it in items:
        n = it.get("chapter") if isinstance(it, dict) else None
        if not isinstance(n, int) or n not in numbers:
            extra.append(f"多余的章节 {n!r}（不在本次需要的范围内，已忽略）")
            continue
        it.setdefault("title", titles.get(n, ""))          # 与 parse_act_output 的规范化保持一致
        it["act"] = act
        it.setdefault("deviations", [])
        errs = validate_brief(it, chapter=n, title=titles.get(n, ""), names=names, foreshadow=foreshadow)
        if errs:
            bad.setdefault(n, []).extend(errs)
        elif n in good:
            bad.setdefault(n, []).append(f"第{n}章：重复写出了该章")
        else:
            good[n] = it
    for n in numbers:
        if n not in good and n not in bad:
            bad[n] = [f"第{n}章：没有写出该章"]
    return good, bad, extra


def _final_hint(kind: str, info: Dict[str, Any], out_dir: Path, accepted_dir: Path) -> str:
    """最终失败的处理建议。按最后一次失败的类型给出（不再一律说「执行器没有保存文件」）。"""
    if kind == "missing":
        return (f"执行器没有保存简述文件。处理：1）检查 {out_dir} 下的 invoke.json、reply.txt；"
                "2）确认 novel-planner 技能的工具权限包含写文件；"
                "3）若回复里已有完整 JSON，把它保存为 " + str(out_dir / "brief_act_*.json") + " 后重新运行此步骤。")
    if kind == "invoke":
        return ("执行器调用失败。处理：1）确认 mini_agent 可用；2）确认 novel-planner 技能存在且允许写文件；"
                "3）根据上面的错误信息修复后重新运行此步骤。")
    return (f"未通过的是内容校验（字数、伏笔编号、人物、缺章）。上面列出了每章的具体问题。"
            f"已通过的章节保存在 {accepted_dir}，重跑只会重做未通过的章节。"
            "处理：若长度反复超标，可在本幕的修改意见中写明「每章叙述控制在 450 字左右」后重跑。")


def generate_act(ws: Workspace, act: int, start: int, end: int, invoker, *, feedback: str = "",
                 step_id: str = "brief") -> Dict[str, Any]:
    """生成第 act 幕（start–end 章）的全部简述。

    * 逐章校验：通过的章节保存为检查点（_studio/brief_runs/actNN/accepted/），后续尝试与重跑只重做未通过的章节。
    * 每次尝试记录执行器结果（invoke.json）与回复（reply.txt）；写文件失败时先尝试补救合法 JSON。
    * 超时不再重试；错误信息与下一次提示词都带上每章的具体问题。
    * 作者给了修改意见时整幕重写（清空检查点）。全部章节通过后才落盘。"""
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
    accepted_dir = _accepted_dir(ws, act)
    if feedback.strip():
        shutil.rmtree(accepted_dir, ignore_errors=True)          # 作者的修改意见：整幕重写
    accepted = _load_accepted(ws, act, numbers)
    errors: List[str] = []
    kind, info, attempts = "content", {"ok": True, "error": "", "timed_out": False, "output_path": ""}, 0
    out_dir = accepted_dir
    for attempt in range(1, MAX_ATTEMPTS + 1):
        remaining = [n for n in numbers if n not in accepted]
        if not remaining:
            break
        attempts = attempt
        out_dir = ws.root / "_studio" / "brief_runs" / f"act{act:02d}" / f"attempt{attempt:02d}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"brief_act_{act:02d}.json"
        todo = [c for c in chapters if c["id"] in remaining]
        prompt = _prompt(ws, act, start, end, todo, prev, names, foreshadow, out_file, errors)
        if feedback.strip():
            prompt += "\n【作者对本幕的修改意见（必须落实，关键内容仍须具体）】\n" + feedback.strip() + "\n"
        res = invoker(skill="novel-planner", prompt=prompt, export_dir=out_dir, step_id=f"{step_id}#{attempt}")
        info = _invoke_info(res)
        atomic_write_json(out_dir / "invoke.json", {"attempt": attempt, "remaining": remaining, **info,
                                                    "out_file": str(out_file)})
        if info["timed_out"]:
            raise RuntimeError(f"第{act}幕简述失败（第 {attempt} 次）：步骤时限已到，执行器被强制终止。"
                               + _final_hint("invoke", info, out_dir, accepted_dir))
        if not out_file.is_file():
            reply = _tail(info["output_path"], 20000)
            if reply:
                atomic_write_text(out_dir / "reply.txt", reply)
            salvaged = _salvage(out_dir, out_file, info["output_path"])
            if salvaged is None:
                errors = [_missing_file_reason(info, out_dir, out_file), _fix_instruction(info, out_file)]
                kind = "invoke" if not info["ok"] else "missing"
                continue
            atomic_write_text(out_file, salvaged)
        good, bad, extra = _split_output(out_file.read_text(encoding="utf-8"), remaining, titles, names, foreshadow, act)
        for n, item in good.items():
            atomic_write_json(accepted_dir / f"ch{n:03d}.json", item)
            accepted[n] = item
        kind = "content"
        errors = [e for n in sorted(bad) for e in bad[n]] + extra
    still = [n for n in numbers if n not in accepted]
    if still:
        head = f"第{act}幕简述未通过校验（已尝试 {attempts} 次，已通过 {len(accepted)}/{len(numbers)} 章，通过的章节不会重做）"
        detail = "；".join(errors[:12]) if errors else "没有可用的结果"
        raise RuntimeError(head + "：" + detail + "。" + _final_hint(kind, info, out_dir, accepted_dir))
    for n in numbers:
        save_brief(ws, accepted[n])
    _write_index(ws)
    shutil.rmtree(accepted_dir, ignore_errors=True)
    notes = {n: note for n in numbers if (note := length_note(accepted[n].get("narrative", ""))) for n in [n]}
    summary = f"第{act}幕：已生成第{start}–{end}章简述（{len(numbers)} 章）"
    if notes:
        summary += "；字数提示：" + "；".join(f"第{n}章 {t}" for n, t in notes.items())
    return {"summary": summary, "chapters": numbers, "attempts": attempts, "length_notes": notes}


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
