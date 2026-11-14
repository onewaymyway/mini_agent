"""P1 变更模型、受影响章节计算、预览与确认（确定性，不调用 LLM）。

变更（Change）两类：
  * setting：设定变更。mode=retcon（全书追溯）或 from_chapter（从第 N 章起生效）。
    受影响章节 = 正文中含 terms 任一词的章节（from_chapter 只取第 N 章及之后）。
  * chapter：章节反馈。作用于显式列出的章节（chapters），instruction 为作者的修改意见。

预览（Preview）把一组变更展开为「章节 → 命中词 → 适用的变更」清单，并给出警告与调用估计。
执行只接受已确认的预览，且预览所依据的基础版本必须仍然存在、未被改动。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from typing import Any, Dict, List, Optional

from novel_writer.revision_flow import config as CFG
from novel_writer.revision_flow import versions as V
from pathlib import Path

from novel_writer.revision_flow.common import FlowError, load_json, revision_root, save_json, sub_dir, version_dir
from novel_writer.textio import read_text_lenient
from novel_writer.workspace import Workspace

MODES = ("retcon", "from_chapter")
REVIEW_ATTEMPTS = 2
KINDS = ("setting", "chapter")
TARGET_KINDS = ("character", "world_rule", "plot_thread", "timeline_fact", "chapter_outline", "other")


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _str_list(value: Any, field: str) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(x, str) and x.strip() for x in value):
        raise FlowError(f"{field} 必须是非空字符串列表")
    return [x.strip() for x in value]


def normalize(raw: Dict[str, Any]) -> Dict[str, Any]:
    """校验并规范化一条变更（不落盘）。"""
    if not isinstance(raw, dict):
        raise FlowError("变更必须是 JSON 对象")
    kind = raw.get("kind")
    if kind not in KINDS:
        raise FlowError(f"kind 必须是 {KINDS} 之一")
    title = str(raw.get("title") or "").strip()
    if not title:
        raise FlowError("请填写变更标题")
    reason = str(raw.get("reason") or "").strip()
    out: Dict[str, Any] = {
        "kind": kind, "title": title, "reason": reason,
        "before": str(raw.get("before") or "").strip(),
        "after": str(raw.get("after") or "").strip(),
        "instruction": str(raw.get("instruction") or "").strip(),
        "terms": _str_list(raw.get("terms"), "terms"),
        "after_terms": _str_list(raw.get("after_terms"), "after_terms"),
        "target": None,
    }
    target = raw.get("target")
    if target:
        if not isinstance(target, dict) or target.get("kind") not in TARGET_KINDS or not str(target.get("id") or "").strip():
            raise FlowError("target 需要 {kind, id}，kind 取值见 TARGET_KINDS")
        out["target"] = {"kind": target["kind"], "id": str(target["id"]).strip()}

    if kind == "setting":
        if not out["terms"]:
            raise FlowError("设定变更需要至少一个定位词（terms），用于找到涉及的章节")
        if not (out["before"] or out["after"]):
            raise FlowError("设定变更需要填写 before 或 after")
        mode = raw.get("mode")
        if mode not in MODES:
            raise FlowError(f"mode 必须是 {MODES} 之一（作者需显式选择时间语义）")
        out["mode"] = mode
        if mode == "from_chapter":
            n = raw.get("effective_from")
            if isinstance(n, bool) or not isinstance(n, int) or n < 1:
                raise FlowError("from_chapter 模式需要 effective_from（章号，≥1）")
            out["effective_from"] = n
        else:
            out["effective_from"] = None
        out["chapters"] = None
    else:
        chapters = raw.get("chapters")
        if not isinstance(chapters, list) or not chapters or not all(isinstance(c, int) and not isinstance(c, bool) and c >= 1 for c in chapters):
            raise FlowError("章节反馈需要 chapters（章号列表）")
        if not out["instruction"]:
            raise FlowError("章节反馈需要 instruction（修改意见）")
        out["mode"] = None
        out["effective_from"] = None
        out["chapters"] = sorted(set(chapters))
    return out


def _change_dir(ws):
    return sub_dir(ws, "changes")


def save_change(ws, raw: Dict[str, Any], change_id: Optional[str] = None) -> Dict[str, Any]:
    data = normalize(raw)
    cid = change_id or ("chg_" + hashlib.sha1(f"{_now()}{data['title']}".encode("utf-8")).hexdigest()[:8])
    data.update({"id": cid, "created_at": _now(), "status": "draft", "applied_version": None})
    save_json(_change_dir(ws) / f"{cid}.json", data)
    return data


def load_change(ws, cid: str) -> Dict[str, Any]:
    data = load_json(_change_dir(ws) / f"{cid}.json", default=None)
    if not isinstance(data, dict):
        raise FlowError(f"变更不存在：{cid}")
    return data


def list_changes(ws) -> List[Dict[str, Any]]:
    out = []
    folder = revision_root(ws) / "changes"
    if not folder.is_dir():
        return out
    for p in sorted(folder.glob("chg_*.json")):
        d = load_json(p, default=None)
        if isinstance(d, dict):
            out.append(d)
    return out


def set_change_status(ws, cid: str, status: str, **extra: Any) -> Dict[str, Any]:
    data = load_change(ws, cid)
    data["status"] = status
    data.update(extra)
    save_json(_change_dir(ws) / f"{cid}.json", data)
    return data


# ─────────────────────────────── 受影响章节 ───────────────────────────────

def _chapter_texts(version_root) -> List[Dict[str, Any]]:
    out = []
    for ch in Workspace(version_root).list_chapters():
        text, _ = read_text_lenient(ch.path)
        out.append({"number": ch.number, "title": ch.title, "path": ch.path, "text": text})
    return out


def affected_for_change(change: Dict[str, Any], chapters: List[Dict[str, Any]]) -> Dict[str, Any]:
    """返回 {"chapters": {n: {"title", "hits"}}, "warnings": [...]}。纯函数，便于测试。"""
    hits_by_chapter: Dict[int, Dict[str, Any]] = {}
    warnings: List[str] = []
    if change["kind"] == "chapter":
        wanted = set(change["chapters"] or [])
        for ch in chapters:
            if ch["number"] in wanted:
                hits_by_chapter[ch["number"]] = {"title": ch["title"], "hits": []}
        missing = wanted - set(hits_by_chapter)
        if missing:
            warnings.append(f"章节不存在：{sorted(missing)}")
        return {"chapters": hits_by_chapter, "warnings": warnings}

    start = change.get("effective_from") if change["mode"] == "from_chapter" else None
    for ch in chapters:
        if start is not None and ch["number"] < start:
            if any(t in ch["text"] for t in change["after_terms"]):
                warnings.append(f"第{ch['number']}章（早于第{start}章）已出现新设定相关词，可能与“从第{start}章起”的语义冲突")
            continue
        hits = [t for t in change["terms"] if t in ch["text"]]
        if hits:
            hits_by_chapter[ch["number"]] = {"title": ch["title"], "hits": hits}
    if not hits_by_chapter:
        warnings.append("没有找到涉及该设定的章节，请检查定位词（terms）")
    return {"chapters": hits_by_chapter, "warnings": warnings}


# ─────────────────────────────── 预览与确认 ───────────────────────────────

_REVIEW_NAME = "review.json"


def _review_prompt(*, chapter_no: int, chapter_path: Path, changes: List[Dict[str, Any]], out_file: Path,
                   errors: List[str]) -> str:
    lines = []
    for c in changes:
        if c["kind"] == "setting":
            lines.append(f"- 设定变更「{c['title']}」：旧设定「{c['before']}」→ 新设定「{c['after']}」"
                         f"（定位词：{'、'.join(c['terms'])}）")
        else:
            lines.append(f"- 章节修改意见：{c['instruction']}")
    parts = [
        f"【任务】阅读第{chapter_no}章，判断它是否真的涉及下列变更。注意：有的章节会用指代而不直接出现定位词"
        "（如“他父亲”“那件信物”），这种情况也算涉及；只出现同字但指不同事物的，不算涉及。",
        f"【章节路径】{chapter_path}（请完整阅读）",
        "【变更】\n" + "\n".join(lines),
        f"【输出】把一个 JSON 对象写入文件：{out_file}\n"
        "格式：{\"affected\": true 或 false, \"reason\": \"一句话理由\"}\n只写 JSON，不要代码围栏，不要多余文字。",
    ]
    if errors:
        parts.append("【上一次输出未通过校验】\n" + "\n".join(f"- {e}" for e in errors))
    return "\n\n".join(parts) + "\n"


def _parse_review(raw: str) -> Dict[str, Any]:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise FlowError(f"不是合法 JSON：{exc}") from None
    if not isinstance(data, dict) or not isinstance(data.get("affected"), bool):
        raise FlowError("affected 必须是布尔值")
    return {"affected": data["affected"], "reason": str(data.get("reason") or "").strip()}


def semantic_review(ws, rows: List[Dict[str, Any]], changes: Dict[str, Dict[str, Any]], version_root: Path,
                    invoker, pid: str) -> Dict[str, int]:
    """对每个候选章节做一次 LLM 判定。结果写入 row["review"]；判定失败的章节保持未判定（不排除）。
    返回 {"calls": 调用次数, "failed": 失败章数}。"""
    files = {ch.number: ch.path for ch in Workspace(version_root).list_chapters()}
    calls = failed = 0
    for row in rows:
        path = files.get(row["number"])
        if path is None:
            row["review"] = None
            failed += 1
            continue
        group = [changes[cid] for cid in row["changes"]]
        errors: List[str] = []
        verdict = None
        for attempt in range(1, REVIEW_ATTEMPTS + 1):
            out_dir = sub_dir(ws, "review") / pid / f"ch{row['number']:03d}" / f"attempt{attempt:02d}"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file = out_dir / _REVIEW_NAME
            prompt = _review_prompt(chapter_no=row["number"], chapter_path=path, changes=group,
                                    out_file=out_file, errors=errors)
            calls += 1
            try:
                invoker(skill="revision-editor", prompt=prompt, export_dir=out_dir,
                        step_id=f"review_{pid}_{row['number']}_{attempt}")
            except Exception as exc:
                errors = [f"执行器异常：{exc}"]
                continue
            if not out_file.is_file():
                errors = ["没有写出 review.json"]
                continue
            try:
                verdict = _parse_review(out_file.read_text(encoding="utf-8"))
                break
            except (FlowError, UnicodeDecodeError) as exc:
                errors = [str(exc)]
        if verdict is None:
            row["review"] = None
            failed += 1
        else:
            row["review"] = verdict
            row["excluded"] = not verdict["affected"]
    return {"calls": calls, "failed": failed}


def build_preview(ws, base_version: str, change_ids: List[str], invoker=None) -> Dict[str, Any]:
    """为一组变更生成预览，并保存到 _revision/preview/<id>.json。返回预览内容。
    传入 invoker 时做 LLM 语义判定：判定为不涉及的章节默认排除（作者可用 set_excluded 加回）。"""
    if not change_ids:
        raise FlowError("请至少选择一条变更")
    V.get_entry(ws, base_version)
    problems = V.verify(ws, base_version)
    if problems:
        raise FlowError(f"基础版本 {base_version} 校验失败，不能基于它预览：{problems[:3]}")
    changes = [load_change(ws, cid) for cid in change_ids]
    for c in changes:
        if c["status"] not in ("draft", "confirmed"):
            raise FlowError(f"变更 {c['id']} 状态为 {c['status']}，不能再次预览")

    chapters = _chapter_texts(version_dir(ws, base_version))
    per_chapter: Dict[int, Dict[str, Any]] = {}
    warnings: List[str] = []
    for c in changes:
        result = affected_for_change(c, chapters)
        warnings.extend(f"[{c['title']}] {w}" for w in result["warnings"])
        for n, info in result["chapters"].items():
            slot = per_chapter.setdefault(n, {"number": n, "title": info["title"], "changes": [], "hits": []})
            slot["changes"].append(c["id"])
            slot["hits"].extend(h for h in info["hits"] if h not in slot["hits"])
    rows = [per_chapter[n] for n in sorted(per_chapter)]
    for row in rows:
        row["excluded"] = False
        row["review"] = None

    body = {"base_version": base_version, "change_ids": sorted(change_ids),
            "chapters": [{k: r[k] for k in ("number", "title", "changes", "hits")} for r in rows]}
    pid = "prv_" + hashlib.sha1(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:10]
    review_info = {"calls": 0, "failed": 0, "done": False}
    if invoker is not None and rows:
        by_id = {c["id"]: c for c in changes}
        info = semantic_review(ws, rows, by_id, version_dir(ws, base_version), invoker, pid)
        review_info = {**info, "done": True}
        for row in rows:
            if row["review"] is None:
                warnings.append(f"第{row['number']}章语义判定失败，已保留在预览中，请人工确认")
    settings = CFG.load(ws)
    active = [r for r in rows if not r["excluded"]]
    preview = {
        "id": pid, "created_at": _now(), "confirmed": False,
        "base_version": base_version, "change_ids": sorted(change_ids),
        "chapters": rows, "warnings": warnings, "review": review_info,
        "estimated_llm_calls": len(active) + review_info["calls"],
        "runs_needed": max(1, -(-len(active) // settings["max_chapters_per_run"])),
    }
    save_json(sub_dir(ws, "preview") / f"{pid}.json", preview)
    return preview


def set_excluded(ws, pid: str, number: int, excluded: bool) -> Dict[str, Any]:
    """作者在确认前调整某章是否参与修改。确认后的预览不可修改。"""
    preview = load_preview(ws, pid)
    if preview["confirmed"]:
        raise FlowError("预览已确认，不能再调整章节范围")
    rows = [r for r in preview["chapters"] if r["number"] == number]
    if not rows:
        raise FlowError(f"预览中没有第{number}章")
    rows[0]["excluded"] = bool(excluded)
    if not any(not r["excluded"] for r in preview["chapters"]):
        raise FlowError("至少需要保留一章")
    active = [r for r in preview["chapters"] if not r["excluded"]]
    preview["estimated_llm_calls"] = len(active) + preview["review"]["calls"]
    preview["runs_needed"] = max(1, -(-len(active) // CFG.load(ws)["max_chapters_per_run"]))
    save_json(sub_dir(ws, "preview") / f"{pid}.json", preview)
    return preview


def load_preview(ws, pid: str) -> Dict[str, Any]:
    data = load_json(sub_dir(ws, "preview") / f"{pid}.json", default=None)
    if not isinstance(data, dict):
        raise FlowError(f"预览不存在：{pid}")
    return data


def confirm_preview(ws, pid: str) -> Dict[str, Any]:
    """作者确认预览：只有确认后的预览才能执行。变更状态转为 confirmed。"""
    preview = load_preview(ws, pid)
    if preview["confirmed"]:
        return preview
    if V.get_entry(ws, preview["base_version"])["id"] != preview["base_version"]:
        raise FlowError("基础版本已不存在")
    for cid in preview["change_ids"]:
        set_change_status(ws, cid, "confirmed", preview_id=pid)
    preview["confirmed"] = True
    preview["confirmed_at"] = _now()
    save_json(sub_dir(ws, "preview") / f"{pid}.json", preview)
    return preview
