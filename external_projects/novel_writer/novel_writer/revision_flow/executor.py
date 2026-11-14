"""P2 执行器：按章序滚动改写，每章独立校验，失败隔离，超限暂停可续跑。

约定：
  * 每次运行（新建）从基础版本派生一个新版本 vNNN，所有改写只写入它；
  * 章节按章号升序处理；每章的提示里带上「前一章在目标版本中的最新文本」路径，
    让后面的章节看到前面已修正的状态（滚动）；
  * 调用框架执行器与 llm_convert 相同的签名：invoker(skill=, prompt=, export_dir=, step_id=)，
    技能固定为 revision-editor，要求把改写后的章节写到 export_dir 下同名文件；
  * 单章校验失败（空产出、字数偏离、仍含旧设定原文）会带着错误反馈重试，超过次数即该章失败；
    stop_on_failure=True 时整次运行停止，后续章节保持原文，不做推进。
  * 运行记录保存在 _revision/runs/<run_id>.json，状态：running / paused_limit / done / failed。
"""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from novel_writer.fsutil import atomic_write_text
from novel_writer.revision_flow import changes as CH
from novel_writer.revision_flow import config as CFG
from novel_writer.revision_flow import versions as V
from novel_writer.revision_flow.common import FlowError, load_json, revision_root, save_json, sub_dir, version_dir
from novel_writer.textio import read_text_lenient
from novel_writer.workspace import Workspace, count_words

SKILL = "revision-editor"
LIMIT_MARK = "__LIMIT__"
Invoker = Callable[..., Any]


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _runs_dir(ws):
    return sub_dir(ws, "runs")


def load_run(ws, rid: str) -> Dict[str, Any]:
    data = load_json(_runs_dir(ws) / f"{rid}.json", default=None)
    if not isinstance(data, dict):
        raise FlowError(f"运行记录不存在：{rid}")
    return data


def _save_run(ws, run: Dict[str, Any]) -> None:
    run["updated_at"] = _now()
    save_json(_runs_dir(ws) / f"{run['id']}.json", run)


def list_runs(ws) -> List[Dict[str, Any]]:
    if not (revision_root(ws) / "runs").is_dir():
        return []
    out = []
    for p in sorted(_runs_dir(ws).glob("run_*.json")):
        d = load_json(p, default=None)
        if isinstance(d, dict):
            out.append(d)
    return out


def _new_run_id(ws) -> str:
    base = "run_" + re.sub(r"[^0-9]", "", _now())[:14]
    rid, k = base, 1
    while (_runs_dir(ws) / f"{rid}.json").exists():
        k += 1
        rid = f"{base}_{k}"
    return rid


# ─────────────────────────────── 提示词 ───────────────────────────────

def _mode_text(change: Dict[str, Any], chapter_no: int) -> str:
    if change["kind"] == "chapter":
        return "【作者的章节修改意见】\n" + change["instruction"]
    lines = [f"【设定变更：{change['title']}】"]
    if change["before"]:
        lines.append(f"- 旧设定：{change['before']}")
    if change["after"]:
        lines.append(f"- 新设定：{change['after']}")
    if change["reason"]:
        lines.append(f"- 变更理由：{change['reason']}")
    if change["mode"] == "retcon":
        lines.append("- 时间语义：追溯式（全书一直如此）。本章中凡涉及旧设定的地方都要改为新设定，"
                     "并且不要留下「原本/以前」之类暗示旧设定的痕迹。")
    else:
        lines.append(f"- 时间语义：从第{change['effective_from']}章起生效。本章（第{chapter_no}章）"
                     "是变化之后的章节，按新设定改写；前文不做任何改动。")
    return "\n".join(lines)


def build_prompt(*, chapter_no: int, title: str, source: Path, prev_path: Optional[Path], out_dir: Path,
                 applicable: List[Dict[str, Any]], errors: List[str]) -> str:
    parts = [
        f"【任务】对第{chapter_no}章《{title}》做定向修改，只改与下列变更有关的部分；与变更无关的情节、"
        "人物、文风保持原样；章节标题与文件格式不变。",
        f"【原文路径】{source}（请用文件读取工具完整阅读）",
    ]
    if prev_path is not None:
        parts.append(f"【前一章最新文本】{prev_path}（只用于保持前后衔接，不要修改它）")
    parts.append("【变更内容】\n" + "\n\n".join(_mode_text(c, chapter_no) for c in applicable))
    parts.append(f"【写回位置】把改写后的完整章节写入目录 {out_dir}，文件名与原文件名完全相同。"
                 "只写这一个文件，不要修改其它路径。")
    parts.append("【长度】改写后的字数应接近原文，不要大幅增删。")
    if errors:
        parts.append("【上一次产出未通过校验，问题如下，请修正后重新写出全文】\n"
                     + "\n".join(f"- {e}" for e in errors[:20]))
    return "\n\n".join(parts) + "\n"


# ─────────────────────────────── 校验 ───────────────────────────────

def check_rewrite(original: str, revised: str, applicable: List[Dict[str, Any]], settings: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    if not revised.strip():
        return ["改写结果为空"]
    base = max(1, count_words(original))
    ratio = count_words(revised) / base
    if not (settings["length_ratio_min"] <= ratio <= settings["length_ratio_max"]):
        errs.append(f"字数偏离：改写后为原文的 {ratio:.2f} 倍，允许范围 "
                    f"{settings['length_ratio_min']}–{settings['length_ratio_max']}")
    for c in applicable:
        old = c.get("before") or ""
        if c["kind"] == "setting" and old and len(old) <= 60 and old in original and old in revised:
            errs.append(f"改写后仍含旧设定原文：「{old}」")
    return errs


def _find_output(out_dir: Path, name: str) -> Optional[Path]:
    exact = out_dir / name
    if exact.is_file():
        return exact
    candidates = [p for p in out_dir.rglob("*.md") if p.is_file()] if out_dir.exists() else []
    return candidates[0] if len(candidates) == 1 else None


# ─────────────────────────────── 运行 ───────────────────────────────

def start(ws, preview_id: str, invoker: Invoker) -> Dict[str, Any]:
    """从已确认的预览新建运行：派生目标版本并开始改写。"""
    preview = CH.load_preview(ws, preview_id)
    if not preview["confirmed"]:
        raise FlowError("预览尚未确认，不能执行")
    if any(r["preview_id"] == preview_id and r["status"] in ("running", "paused_limit") for r in list_runs(ws)):
        raise FlowError("该预览已有未完成的运行，请续跑或丢弃")
    target = V.derive(ws, preview["base_version"], note=f"修改：{preview_id}")
    active = [c["number"] for c in preview["chapters"] if not c.get("excluded")]
    run = {
        "id": _new_run_id(ws), "preview_id": preview_id, "base_version": preview["base_version"],
        "target_version": target, "status": "running", "queue": active,
        "done": [], "failed": None, "llm_calls": 0, "log": [], "created_at": _now(),
    }
    _save_run(ws, run)
    return _execute(ws, run, invoker)


def resume(ws, run_id: str, invoker: Invoker) -> Dict[str, Any]:
    run = load_run(ws, run_id)
    if run["status"] != "paused_limit":
        raise FlowError(f"只有因上限暂停的运行可以续跑（当前状态：{run['status']}）")
    run["status"] = "running"
    _save_run(ws, run)
    return _execute(ws, run, invoker)


def _execute(ws, run: Dict[str, Any], invoker: Invoker) -> Dict[str, Any]:
    settings = CFG.load(ws)
    preview = CH.load_preview(ws, run["preview_id"])
    by_id = {c["id"]: c for c in CH.list_changes(ws)}
    applicable_by_chapter = {row["number"]: [by_id[c] for c in row["changes"]] for row in preview["chapters"]}
    target_root = version_dir(ws, run["target_version"])
    tws = Workspace(target_root)
    files = {ch.number: ch for ch in tws.list_chapters()}
    session_done = 0

    for n in run["queue"]:
        if n in run["done"]:
            continue
        if session_done >= settings["max_chapters_per_run"] or run["llm_calls"] >= settings["max_llm_calls_per_run"]:
            run["status"] = "paused_limit"
            run["log"].append(f"{_now()} 达到上限，暂停；续跑时从第{n}章继续")
            _save_run(ws, run)
            return run
        ok, errors = _rewrite_chapter(ws, run, n, files, applicable_by_chapter.get(n, []), settings, invoker)
        session_done += 1
        if errors == [LIMIT_MARK]:
            run["status"] = "paused_limit"
            run["log"].append(f"{_now()} 第{n}章未完成：调用次数达到上限，暂停；续跑时从本章重新开始")
            _save_run(ws, run)
            return run
        if ok:
            run["done"].append(n)
        else:
            run["failed"] = {"chapter": n, "errors": errors}
            run["log"].append(f"{_now()} 第{n}章失败：{'; '.join(errors)[:300]}")
            if settings["stop_on_failure"]:
                run["status"] = "failed"
                _save_run(ws, run)
                return run
        _save_run(ws, run)

    return _finish(ws, run, preview)


def _rewrite_chapter(ws, run, n, files, applicable, settings, invoker) -> Tuple[bool, List[str]]:
    if n not in files:
        return False, [f"目标版本中找不到第{n}章"]
    ch = files[n]
    original, _ = read_text_lenient(ch.path)
    prev = files.get(n - 1)
    prev_path = prev.path if prev else None
    errors: List[str] = []
    for attempt in range(1, settings["max_attempts_per_chapter"] + 1):
        if run["llm_calls"] >= settings["max_llm_calls_per_run"]:
            return False, [LIMIT_MARK]
        out_dir = sub_dir(ws, "runs") / run["id"] / f"ch{n:03d}" / f"attempt{attempt:02d}" / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        prompt = build_prompt(chapter_no=n, title=ch.title, source=ch.path, prev_path=prev_path,
                              out_dir=out_dir, applicable=applicable, errors=errors)
        run["llm_calls"] += 1
        try:
            invoker(skill=SKILL, prompt=prompt, export_dir=out_dir, step_id=f"rf_{run['id']}_{n}_{attempt}")
        except Exception as exc:  # 框架异常计为一次失败尝试
            errors = [f"执行器异常：{exc}"]
            run["log"].append(f"{_now()} 第{n}章第{attempt}次尝试异常：{exc}")
            continue
        out_file = _find_output(out_dir, ch.path.name)
        if out_file is None:
            errors = ["没有在输出目录写出章节文件"]
            continue
        revised, _ = read_text_lenient(out_file)
        errors = check_rewrite(original, revised, applicable, settings)
        if not errors:
            atomic_write_text(ch.path, revised)
            run["log"].append(f"{_now()} 第{n}章改写完成（第{attempt}次尝试）")
            return True, []
        run["log"].append(f"{_now()} 第{n}章第{attempt}次尝试未通过：{'; '.join(errors)[:200]}")
    return False, errors


def _finish(ws, run, preview) -> Dict[str, Any]:
    if len(run["done"]) != len(run["queue"]):
        run["status"] = "failed"
        _save_run(ws, run)
        return run
    target = run["target_version"]
    note_lines = [f"# 修改记录 {run['id']}", "", f"- 基础版本：{run['base_version']}",
                  f"- 变更：{', '.join(preview['change_ids'])}", f"- 改写章节：{', '.join(str(n) for n in run['done'])}"]
    note_path = version_dir(ws, target) / "修订记录" / f"{run['id']}.md"
    atomic_write_text(note_path, "\n".join(note_lines) + "\n")
    V.finalize(ws, target, list(preview["change_ids"]))
    V.set_head(ws, target)
    for cid in preview["change_ids"]:
        CH.set_change_status(ws, cid, "applied", applied_version=target)
    run["status"] = "done"
    run["finished_at"] = _now()
    from novel_writer.revision_flow import derived
    kinds = [CH.load_change(ws, cid)["kind"] for cid in preview["change_ids"]]
    run["derived_marks"] = derived.stale_items(run["done"], kinds)
    _save_run(ws, run)
    return run
