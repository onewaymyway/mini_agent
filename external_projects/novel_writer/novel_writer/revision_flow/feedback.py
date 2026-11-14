"""P3 章节反馈分诊：作者对某一章给出意见，LLM 判断它是章内修改还是设定变更。

流程：
  1. triage(...)：调用执行器（技能 revision-editor）读取该章与意见，写出 triage.json；
     结果严格校验（kind、字段、章号），不合格最多重试一次；保存为 pending。
  2. accept(...)：作者确认分诊结果。章内修改 → 生成 chapter 变更（只作用于该章）；
     设定变更 → 生成 setting 变更，时间语义由作者选择（分诊结果不替作者决定 mode）。
  3. 之后的预览、确认、执行与 P1/P2 完全相同，局部修改同样产生新版本。
分诊不直接改动任何文件。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from novel_writer.revision_flow import changes as CH
from novel_writer.revision_flow import versions as V
from novel_writer.revision_flow.common import FlowError, load_json, save_json, sub_dir, version_dir
from novel_writer.workspace import Workspace

SKILL = "revision-editor"
MAX_ATTEMPTS = 2
Invoker = Callable[..., Any]


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _triage_dir(ws):
    return sub_dir(ws, "triage")


def _build_prompt(*, chapter_no: int, chapter_path: Path, feedback: str, selection: str,
                  out_file: Path, errors: List[str]) -> str:
    sel = f"\n【作者选中的段落】\n{selection}\n" if selection else ""
    parts = [
        f"【任务】阅读第{chapter_no}章，判断作者的意见属于哪一类，只做判断，不要修改章节。",
        f"【章节路径】{chapter_path}",
        f"【作者意见】\n{feedback}\n{sel}",
        "【分类规则】\n"
        "- chapter：只影响本章的措辞、段落、节奏或本章内的情节逻辑，不涉及其它章节或设定。\n"
        "- setting：涉及人物设定、世界规则、时间线事实、伏笔等，会影响到其它章节。",
        "【输出】把一个 JSON 对象写入文件："
        f"{out_file}\n"
        "格式：{\"kind\": \"chapter\"|\"setting\", \"reason\": \"判断理由\", "
        "\"instruction\": \"（chapter 时）修改意见，尽量具体\", "
        "\"change\": {\"title\": \"\", \"before\": \"\", \"after\": \"\", \"reason\": \"\", "
        "\"terms\": [\"定位词\"], \"after_terms\": [\"新设定的关键词\"]}（setting 时必填，否则为 null）}\n"
        "只写 JSON，不要代码围栏，不要多余文字。",
    ]
    if errors:
        parts.append("【上一次输出未通过校验】\n" + "\n".join(f"- {e}" for e in errors))
    return "\n\n".join(parts) + "\n"


def _parse(raw: str, chapter_no: int) -> Dict[str, Any]:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise FlowError(f"不是合法 JSON：{exc}") from None
    if not isinstance(data, dict):
        raise FlowError("顶层必须是 JSON 对象")
    kind = data.get("kind")
    if kind not in ("chapter", "setting"):
        raise FlowError("kind 必须是 chapter 或 setting")
    reason = str(data.get("reason") or "").strip()
    if not reason:
        raise FlowError("缺少 reason")
    if kind == "chapter":
        if not str(data.get("instruction") or "").strip():
            raise FlowError("chapter 类需要 instruction")
        return {"kind": "chapter", "reason": reason, "instruction": str(data["instruction"]).strip(), "change": None}
    change = data.get("change")
    if not isinstance(change, dict):
        raise FlowError("setting 类需要 change 对象")
    if not change.get("terms"):
        raise FlowError("setting 类的 change.terms 不能为空")
    return {"kind": "setting", "reason": reason, "instruction": "", "change": change}


def triage(ws, base_version: str, chapter_no: int, feedback: str, invoker: Invoker,
           selection: str = "") -> Dict[str, Any]:
    """对某章的意见做分诊，保存为 pending 记录并返回。"""
    if not str(feedback or "").strip():
        raise FlowError("请写下对本章的意见")
    V.get_entry(ws, base_version)
    tws = Workspace(version_dir(ws, base_version))
    chapter = next((c for c in tws.list_chapters() if c.number == chapter_no), None)
    if chapter is None:
        raise FlowError(f"基础版本中找不到第{chapter_no}章")
    tid = "tri_" + hashlib.sha1(f"{_now()}{chapter_no}{feedback}".encode("utf-8")).hexdigest()[:8]
    work = _triage_dir(ws) / tid
    errors: List[str] = []
    result: Optional[Dict[str, Any]] = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        out_dir = work / f"attempt{attempt:02d}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / "triage.json"
        prompt = _build_prompt(chapter_no=chapter_no, chapter_path=chapter.path, feedback=feedback,
                               selection=selection, out_file=out_file, errors=errors)
        try:
            invoker(skill=SKILL, prompt=prompt, export_dir=out_dir, step_id=f"triage_{tid}_{attempt}")
        except Exception as exc:
            errors = [f"执行器异常：{exc}"]
            continue
        if not out_file.is_file():
            errors = ["没有写出 triage.json"]
            continue
        try:
            result = _parse(out_file.read_text(encoding="utf-8"), chapter_no)
            break
        except (FlowError, UnicodeDecodeError) as exc:
            errors = [str(exc)]
            result = None
    record = {
        "id": tid, "created_at": _now(), "base_version": base_version, "chapter": chapter_no,
        "feedback": feedback, "selection": selection, "status": "pending" if result else "failed",
        "result": result, "errors": [] if result else errors,
    }
    save_json(_triage_dir(ws) / f"{tid}.json", record)
    return record


def load_triage(ws, tid: str) -> Dict[str, Any]:
    data = load_json(_triage_dir(ws) / f"{tid}.json", default=None)
    if not isinstance(data, dict):
        raise FlowError(f"分诊记录不存在：{tid}")
    return data


def accept(ws, tid: str, *, mode: Optional[str] = None, effective_from: Optional[int] = None) -> Dict[str, Any]:
    """作者确认分诊。返回新建的变更（draft 状态）。设定类必须由作者给出 mode。"""
    rec = load_triage(ws, tid)
    if rec["status"] != "pending":
        raise FlowError(f"分诊记录状态为 {rec['status']}，不能采纳")
    res = rec["result"]
    if res["kind"] == "chapter":
        raw = {"kind": "chapter", "title": f"第{rec['chapter']}章修改", "reason": res["reason"],
               "instruction": res["instruction"], "chapters": [rec["chapter"]]}
    else:
        if mode not in CH.MODES:
            raise FlowError("设定变更需要作者选择 mode（retcon 或 from_chapter）")
        change = dict(res["change"])
        raw = {"kind": "setting", "title": change.get("title") or "设定变更",
               "before": change.get("before", ""), "after": change.get("after", ""),
               "reason": change.get("reason") or res["reason"], "terms": change.get("terms", []),
               "after_terms": change.get("after_terms", []), "mode": mode, "effective_from": effective_from}
    created = CH.save_change(ws, raw)
    rec["status"] = "accepted"
    rec["change_id"] = created["id"]
    save_json(_triage_dir(ws) / f"{tid}.json", rec)
    return created


def reject(ws, tid: str) -> Dict[str, Any]:
    rec = load_triage(ws, tid)
    if rec["status"] == "pending":
        rec["status"] = "rejected"
        save_json(_triage_dir(ws) / f"{tid}.json", rec)
    return rec
