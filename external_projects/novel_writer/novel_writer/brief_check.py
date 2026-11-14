"""大纲先行 O3：写作接入后的关键内容核对与偏离记录（步骤 ch{nn}.brief_check）。

流程（每章）：
  1. 把本章简述的关键内容拆成带编号的条目（e1…、s1…、f1…、h1）；
  2. LLM 逐条判断正文是否实现，并列出正文相对叙述简述的非关键偏离（只读，不改正文）；
  3. 全部关键条目实现 → 偏离记录追加到 偏离/第NNN章.json（以及 .md），步骤通过；
  4. 有关键条目未实现 → 带着缺失条目修正本章（revision-editor 式的定向改写，正文其它部分不动），最多两次；
     仍未实现 → 步骤挂起为 waiting_user，把缺失条目交给作者决定。

偏离规则：关键内容不允许偏离（由第 3/4 步强制）；非关键偏离允许，但必须有记录（原因缺失时记为「未说明」）。
"""
from __future__ import annotations

import datetime as _dt
import json
from typing import Any, Dict, List, Optional

from novel_writer import brief as BR
from novel_writer import steps as S
from novel_writer.fsutil import atomic_write_json, atomic_write_text
from novel_writer.workspace import Workspace

MAX_FIXES = 2
MIN_RATIO, MAX_RATIO = 0.6, 1.5   # 修正后的字数相对原文的允许范围（只做明显失控的检查）


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def key_items(b: Dict[str, Any]) -> List[Dict[str, str]]:
    k = b["key"]
    items: List[Dict[str, str]] = []
    for i, e in enumerate(k.get("must_events", []), 1):
        items.append({"id": f"e{i}", "kind": "must_events", "text": e})
    for i, s in enumerate(k.get("must_states", []), 1):
        items.append({"id": f"s{i}", "kind": "must_states", "text": f"{s['who']}：{s['change']}"})
    for i, f in enumerate(k.get("must_foreshadow", []), 1):
        items.append({"id": f"f{i}", "kind": "must_foreshadow", "text": f"伏笔 {f} 的动作"})
    items.append({"id": "h1", "kind": "must_hook", "text": f"章末钩子：{k.get('must_hook', '')}"})
    return items


def _chapter_path(ws: Workspace, n: int):
    for ch in Workspace(ws.root).list_chapters():
        if ch.number == n:
            return ch.path
    raise RuntimeError(f"第{n}章正文文件不存在")


def _judge_prompt(n: int, b: Dict[str, Any], items: List[Dict[str, str]], chapter: Any, out_file, errors) -> str:
    lines = "\n".join(f"- {it['id']}（{it['kind']}）：{it['text']}" for it in items)
    parts = [
        f"【任务】核对第{n}章正文是否实现了下列关键内容，并找出正文相对叙述简述的非关键偏离。只做判断，不要修改正文。",
        f"【正文路径】{chapter}（请完整阅读）",
        "【叙述简述】\n" + b["narrative"].strip(),
        "【关键内容条目（必须逐条给出判断）】\n" + lines,
        "【输出】把一个 JSON 对象写入文件：" + str(out_file) + "\n"
        "格式：{\"items\": [{\"id\": \"e1\", \"implemented\": true 或 false, \"evidence\": \"正文中的依据（原文摘录或说明）\"}], "
        "\"deviations\": [{\"where\": \"位置（场景/段落）\", \"original\": \"简述中的写法\", \"change\": \"正文实际写法\", "
        "\"reason\": \"偏离的原因（没有就写空字符串）\"}]}",
        "关键条目必须全部给出 implemented；偏离只列非关键内容，没有则为空列表。只写 JSON，不要代码围栏。",
    ]
    if errors:
        parts.append("【上一次输出未通过校验】\n" + "\n".join(f"- {e}" for e in errors))
    return "\n\n".join(parts) + "\n"


def _judge(ws: Workspace, n: int, b, items, chapter, invoker, step_id: str) -> Dict[str, Any]:
    out_dir = ws.root / "_studio" / "brief_checks" / f"ch{n:03d}" / _now().replace(":", "")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "check.json"
    errors: List[str] = []
    for attempt in range(1, 3):
        prompt = _judge_prompt(n, b, items, chapter, out_file, errors)
        invoker(skill="novel-planner", prompt=prompt, export_dir=out_dir, step_id=f"{step_id}#judge{attempt}")
        if not out_file.is_file():
            errors = ["没有写出核对结果文件"]
            continue
        try:
            data = json.loads(out_file.read_text(encoding="utf-8"))
        except ValueError as exc:
            errors = [f"不是合法 JSON：{exc}"]
            continue
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            errors = ["顶层需要 items 列表"]
            continue
        by_id = {}
        for it in data["items"]:
            if isinstance(it, dict) and isinstance(it.get("id"), str):
                by_id.setdefault(it["id"], it)
        verdict = {}
        for it in items:
            got = by_id.get(it["id"])
            if got is None or not isinstance(got.get("implemented"), bool):
                verdict[it["id"]] = {"implemented": False, "evidence": "核对结果未给出该条目的判断"}
            else:
                verdict[it["id"]] = {"implemented": got["implemented"], "evidence": str(got.get("evidence") or "")}
        devs = []
        for d in data.get("deviations") or []:
            if isinstance(d, dict) and str(d.get("where") or "").strip():
                devs.append({"where": str(d.get("where")), "original": str(d.get("original") or ""),
                             "change": str(d.get("change") or ""),
                             "reason": str(d.get("reason") or "").strip() or "未说明"})
        return {"items": verdict, "deviations": devs}
    raise RuntimeError("关键内容核对失败：" + "；".join(errors))


def _rewrite(ws: Workspace, n: int, chapter, missing: List[Dict[str, str]], invoker, step_id: str) -> None:
    original = chapter.read_text(encoding="utf-8")
    out_dir = ws.root / "_studio" / "brief_fixes" / f"ch{n:03d}" / _now().replace(":", "")
    out_dir.mkdir(parents=True, exist_ok=True)
    brief_path = ws.root / BR.brief_chapter_rel(n)
    prompt = (
        f"【任务】第{n}章正文没有实现已确认简述中的下列关键内容。请定向修正正文，把它们具体写进正文；"
        "其它段落保持原样，不要删减无关情节，章节标题不变。\n"
        f"【简述文件】{brief_path}（关键内容以它为准）\n"
        f"【正文路径】{chapter}（请先完整阅读）\n"
        "【未实现的关键内容】\n" + "\n".join(f"- {m['id']}（{m['kind']}）：{m['text']}" for m in missing) + "\n"
        f"【输出】把修正后的完整章节写入目录 {out_dir}，文件名与原文件名完全相同，只写这一个文件。"
    )
    invoker(skill="revision-editor", prompt=prompt, export_dir=out_dir, step_id=f"{step_id}#fix")
    out_file = out_dir / chapter.name
    if not out_file.is_file():
        raise RuntimeError("修正步骤没有写出章节文件")
    revised = out_file.read_text(encoding="utf-8")
    base = max(1, BR.count_words(original))
    ratio = BR.count_words(revised) / base
    if not revised.strip() or not (MIN_RATIO <= ratio <= MAX_RATIO):
        raise RuntimeError(f"修正结果字数异常（为原文的 {ratio:.2f} 倍），未写入")
    atomic_write_text(chapter, revised)


def _append_deviations(ws: Workspace, n: int, devs: List[Dict[str, str]], check_id: str) -> int:
    path = BR.deviations_path(ws, n)
    current = BR.load_deviations(ws, n)
    stamp = _now()
    for d in devs:
        current.append({**d, "at": stamp, "check": check_id})
    if devs:
        atomic_write_json(path, current)
        b = BR.load_brief(ws, n)
        if b:
            atomic_write_text(BR.brief_md_path(ws, n), BR.render_md(b, current))
    return len(devs)


def h_brief_check(ws: Workspace, step: S.StepDef, settings: Dict[str, Any], invoker=None) -> Dict[str, Any]:
    from novel_writer.runner import NeedsUser  # 惰性导入：避免与 runner 的循环依赖
    if invoker is None:
        raise RuntimeError("未配置执行器（invoker），无法核对关键内容")
    n = step.chapter
    b = BR.load_brief(ws, n)
    if b is None:
        raise RuntimeError(f"第{n}章没有已确认的简述，无法核对")
    items = key_items(b)
    chapter = _chapter_path(ws, n)
    fixes = 0
    while True:
        verdict = _judge(ws, n, b, items, chapter, invoker, step.id)
        missing = [it for it in items if not verdict["items"][it["id"]]["implemented"]]
        if not missing:
            count = _append_deviations(ws, n, verdict["deviations"], step.id)
            return {"summary": f"第{n}章关键内容全部实现；非关键偏离 {count} 处已记入偏离记录",
                    "deviations": count, "fixes": fixes}
        if fixes >= MAX_FIXES:
            raise NeedsUser(
                f"第{n}章：{len(missing)} 条关键内容经 {MAX_FIXES} 次修正仍未实现，请作者决定（修改简述或放宽该章）",
                [{"id": m["id"], "kind": m["kind"], "text": m["text"],
                  "evidence": verdict["items"][m["id"]]["evidence"]} for m in missing])
        _rewrite(ws, n, chapter, missing, invoker, step.id)
        fixes += 1
