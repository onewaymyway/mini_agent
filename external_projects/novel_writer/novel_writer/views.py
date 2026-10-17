"""novel_writer/views.py — 规划 / 设定库 / 线索 页面的数据视图与保存（M2）。

全部是纯函数（输入 Workspace，输出可 JSON 化的 dict），由 tool_api 包一层返回 {ok, data|error}。
保存类操作只改动一个文件，并调用 state.mark_stale 让下游产物进入 stale，由用户决定何时重跑。
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from typing import Any, Dict, List, Optional

from novel_writer import pipeline as P
from novel_writer import steps as S
from novel_writer import state as st
from novel_writer.fsutil import atomic_write_json, atomic_write_text, read_json
from novel_writer.workspace import Workspace, WorkspaceError, count_words, sanitize_name

CHAPTER_EDIT_FIELDS = ("title", "function", "word_budget", "tension_level", "characters_present")
_SPLIT_RE = re.compile(r"[、,，;；]")


class ViewError(WorkspaceError):
    pass


def _read_text(path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _names_to_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p.strip() for p in _SPLIT_RE.split(str(value or "")) if p.strip()]


# ── 规划 ───────────────────────────────────────────────────────────────────


def planning_view(ws: Workspace) -> Dict[str, Any]:
    bp = read_json(ws.path("blueprint"), default=None)
    docs = {
        "theme": _read_text(ws.path("theme_design")),
        "pacing": _read_text(ws.path("pacing_design")),
        "opening": _read_text(ws.path("opening_design")),
        "readable": _read_text(ws.path("blueprint_readable")),
    }
    if not isinstance(bp, dict):
        return {"exists": False, "docs": docs, "main_characters": []}
    chapters = []
    for ch in bp.get("chapters") or []:
        if not isinstance(ch, dict) or not isinstance(ch.get("id"), int):
            continue
        chapters.append({
            "id": ch["id"],
            "title": str(ch.get("title") or ""),
            "act": ch.get("act"),
            "chapter_type": ch.get("chapter_type") or "",
            "function": str(ch.get("function") or ""),
            "word_budget": ch.get("word_budget"),
            "tension_level": ch.get("tension_level"),
            "characters_present": "、".join(_names_to_list(ch.get("characters_present"))),
        })
    structure = bp.get("structure") or {}
    return {
        "exists": True,
        "meta": bp.get("meta") or {},
        "total_chapters": structure.get("total_chapters"),
        "acts": structure.get("acts") or [],
        "chapters": chapters,
        "main_characters": P.main_characters(bp),
        "docs": docs,
    }


def save_blueprint_chapters(ws: Workspace, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """保存章节表的编辑。只允许修改已有章节（不增删行），改动记入蓝图变更日志。"""
    bp = read_json(ws.path("blueprint"), default=None)
    if not isinstance(bp, dict):
        raise ViewError("还没有蓝图，无法保存章节表")
    if not isinstance(rows, list) or not rows:
        raise ViewError("章节表为空")
    chapters = [c for c in bp.get("chapters") or [] if isinstance(c, dict)]
    by_id = {c.get("id"): c for c in chapters}
    row_ids = [r.get("id") for r in rows]
    if sorted(row_ids, key=str) != sorted(by_id, key=str) or len(set(row_ids)) != len(row_ids):
        raise ViewError("M2 只支持修改已有章节，不能增加或删除章节（请重新生成蓝图）")

    before_main = P.main_characters(bp)
    changed: List[int] = []
    for row in rows:
        ch = by_id[row["id"]]
        new_values = {
            "title": str(row.get("title") or "").strip(),
            "function": str(row.get("function") or "").strip(),
            "characters_present": _names_to_list(row.get("characters_present")),
        }
        if not new_values["title"]:
            raise ViewError(f"第 {row['id']} 章标题不能为空")
        try:
            new_values["word_budget"] = int(row.get("word_budget")) if row.get("word_budget") not in (None, "") else ch.get("word_budget")
            new_values["tension_level"] = int(row.get("tension_level")) if row.get("tension_level") not in (None, "") else ch.get("tension_level")
        except (TypeError, ValueError):
            raise ViewError(f"第 {row['id']} 章的字数或张力必须是整数") from None
        if _differs(ch, new_values):
            ch.update(new_values)
            changed.append(row["id"])
    if not changed:
        return {"changed_chapters": [], "stale": [], "main_characters_changed": False}

    atomic_write_json(ws.path("blueprint"), bp)
    _append_changelog(ws, f"章节表修改：第 {', '.join(str(i) for i in changed)} 章")
    stale = st.mark_stale(ws, P.build_plan(ws))
    after_main = P.main_characters(bp)
    return {
        "changed_chapters": changed,
        "stale": stale,
        "main_characters_changed": before_main != after_main,
        "main_characters": after_main,
    }


def _differs(old: Dict[str, Any], new: Dict[str, Any]) -> bool:
    """逐字段比较，缺失的文本字段与空串视为相同（避免把未改动的章节误判为已改）。"""
    for key, value in new.items():
        before = old.get(key)
        if key == "characters_present":
            before = _names_to_list(before)
        elif key in ("title", "function"):
            before = str(before or "").strip()
        if before != value:
            return True
    return False


def _append_changelog(ws: Workspace, line: str) -> None:
    path = ws.path("blueprint_changelog")
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    with path.open("a", encoding="utf-8") as f:
        f.write(f"- {stamp} {line}\n")


# ── 设定库 ─────────────────────────────────────────────────────────────────


def codex_view(ws: Workspace) -> Dict[str, Any]:
    bp = read_json(ws.path("blueprint"), default=None)
    roles = P.main_characters(bp)
    cards = []
    for name in roles:
        path = ws.path("character_card", name=sanitize_name(name, what="角色名"))
        cards.append({"name": name, "path": str(path.relative_to(ws.root)), "text": _read_text(path)})
    extra_dir = ws.path("character_card", name="x").parent
    extra = []
    if extra_dir.is_dir():
        for p in sorted(extra_dir.glob("*.md")):
            if p.stem not in roles:
                extra.append({"name": p.stem, "path": str(p.relative_to(ws.root)), "text": _read_text(p)})
    return {
        "main_characters": roles,
        "cards": cards,
        "other_cards": extra,
        "characters": read_json(ws.path("characters"), default=None),
        "world_bible": read_json(ws.path("world_bible"), default=None),
    }


def save_character_card(ws: Workspace, name: str, text: str) -> Dict[str, Any]:
    safe = sanitize_name(name, what="角色名")
    body = str(text or "").strip()
    if not body:
        raise ViewError("人设卡内容不能为空")
    atomic_write_text(ws.path("character_card", name=safe), body + "\n")
    stale = st.mark_stale(ws, P.build_plan(ws))
    return {"name": safe, "stale": stale}


# ── 线索 ───────────────────────────────────────────────────────────────────


def threads_view(ws: Workspace) -> Dict[str, Any]:
    tracker = read_json(ws.path("plot_tracker"), default=None)
    return {
        "plot_tracker": tracker,
        "foreshadowing_md": _read_text(ws.path("foreshadowing_list")),
        "tension_md": _read_text(ws.path("tension_curve")),
    }


# ── M3：写作台 / 审校 / 阅读 ────────────────────────────────────────────────

from novel_writer import chapter_ops as C  # noqa: E402

_CHAPTER_STEP_SUFFIXES = ("word_quota", "plot_tasks", "context_pack", "style_hint", "write_chapter",
                          "machine_check", "review", "revise_loop", "archive", "sync", "sync_plot", "reader_sim")


def _chapter_numbers(ws: Workspace) -> List[int]:
    nums = [s.chapter for s in P.build_plan(ws) if s.chapter]
    return sorted(set(nums))


def chapters_overview(ws: Workspace) -> Dict[str, Any]:
    plan_ids = {s.id for s in P.build_plan(ws)}
    statuses = {sid: e.get("status", st.ST_PENDING) for sid, e in st.load_state(ws)["steps"].items()}
    titles = {c["id"]: c["title"] for c in planning_view(ws).get("chapters", [])}
    rows = []
    for n in _chapter_numbers(ws):
        steps = [f"ch{n:02d}.{suffix}" for suffix in _CHAPTER_STEP_SUFFIXES]
        present = [sid for sid in steps if sid in plan_ids]
        done = sum(1 for sid in present if statuses.get(sid) in st.FINISHED_STATUSES)
        path = C.chapter_file(ws, n)
        text = path.read_text(encoding="utf-8") if path else ""
        review = _read_text(ws.path("review_report", nn=n)) or ""
        machine = read_json(ws.path("machine_report", nn=n), default=None) or {}
        current = next((sid for sid in present if statuses.get(sid, st.ST_PENDING) not in st.FINISHED_STATUSES), None)
        rows.append({
            "chapter": n,
            "title": titles.get(n) or (path.stem if path else ""),
            "file": path.name if path else None,
            "words": count_words(text),
            "quota": C.quota_of(ws, n),
            "red": C.count_red(review),
            "machine_hard": len(machine.get("hard") or []),
            "progress": f"{done}/{len(present)}",
            "current": current.split(".", 1)[1] if current else "已完成",
            "needs_attention": any(statuses.get(sid) in (st.ST_WAITING_USER, st.ST_FAILED, st.ST_BLOCKED) for sid in present),
        })
    return {"chapters": rows}


def chapter_detail(ws: Workspace, n: int) -> Dict[str, Any]:
    if n not in _chapter_numbers(ws):
        raise ViewError(f"第 {n} 章不在流水线中（章节数以蓝图为准）")
    path = C.chapter_file(ws, n)
    plan_steps = {s.id: s for s in P.build_plan(ws)}
    state_steps = st.load_state(ws)["steps"]
    steps = []
    for suffix in _CHAPTER_STEP_SUFFIXES:
        sid = f"ch{n:02d}.{suffix}"
        if sid in plan_steps:
            entry = state_steps.get(sid, {})
            steps.append({"id": sid, "title": plan_steps[sid].title, "status": entry.get("status", st.ST_PENDING),
                          "error": entry.get("error"), "summary": entry.get("summary"),
                          "needs_user": entry.get("needs_user")})
    return {
        "chapter": n,
        "entry": C.chapter_entry(ws, n),
        "text": path.read_text(encoding="utf-8") if path else "",
        "file": path.name if path else None,
        "words": count_words(path.read_text(encoding="utf-8")) if path else 0,
        "quota": C.quota_of(ws, n),
        "context": _read_text(ws.path("context_pack", n=n)),
        "tasks": read_json(ws.path("chapter_tasks", n=n), default=None),
        "machine": read_json(ws.path("machine_report", nn=n), default=None),
        "review": _read_text(ws.path("review_report", nn=n)),
        "summary": _read_text(ws.path("chapter_summary", nn=n)),
        "revision_log": _read_text(ws.path("revision_log", nn=n)),
        "steps": steps,
    }


def save_chapter_text(ws: Workspace, n: int, text: str) -> Dict[str, Any]:
    """作者手动保存正文。下游产物（审稿、归档、同步）会因输入变化而标为 stale。"""
    body = str(text or "")
    if not body.strip():
        raise ViewError("正文不能为空")
    path = C.chapter_file(ws, n)
    if path is None:
        title = str(chapter_entry_title(ws, n) or f"第{n}章")
        path = ws.chapter_path(n, title, total_chapters=len(_chapter_numbers(ws)))
    atomic_write_text(path, body.rstrip() + "\n")
    _append_log(ws.path("revision_log", nn=n), f"作者手动保存正文（{count_words(body)} 字）")
    stale = st.mark_stale(ws, P.build_plan(ws))
    return {"file": path.name, "words": count_words(body), "stale": stale}


def chapter_entry_title(ws: Workspace, n: int) -> Optional[str]:
    for c in planning_view(ws).get("chapters", []):
        if c["id"] == n:
            return c["title"]
    return None


def _append_log(path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    with path.open("a", encoding="utf-8") as f:
        f.write(f"- {stamp} {line}\n")


def export_book(ws: Workspace) -> Dict[str, Any]:
    parts, words, count = [], 0, 0
    title = read_json(ws.path("workspace_config"), default={}).get("novel_title") or "小说"
    for n in _chapter_numbers(ws):
        path = C.chapter_file(ws, n)
        if path is None:
            continue
        text = path.read_text(encoding="utf-8").strip()
        parts.append(text)
        words += count_words(text)
        count += 1
    if not parts:
        raise ViewError("还没有任何正文可以导出")
    out = ws.path("export_dir") / f"{sanitize_name(title, what='书名')}_全书合稿.md"
    atomic_write_text(out, "\n\n".join(parts) + "\n")
    return {"file": str(out.relative_to(ws.root)), "chapters": count, "words": words}


# ── M4：设置矩阵 / 度量 / 灵感 / 修订请求 / 步骤详情 ──────────────────────────

from novel_writer import gates as _gates  # noqa: E402
from novel_writer import ondemand as _ondemand  # noqa: E402
from novel_writer import settings as _settings_mod  # noqa: E402

GATE_CHOICES = ["", "manual", "review", "auto", "skip"]


def matrix_view(ws: Workspace) -> Dict[str, Any]:
    settings = ws.settings()
    rows = []
    for step in P.build_plan(ws):
        decision = _gates.resolve_gate(step, settings)
        preset = _gates.GEAR_GATES.get(step.category, {}).get(settings["gear"]) if step.category != S.CAT_CHECKPOINT else "review"
        rows.append({
            "id": step.id, "title": step.title, "stage": step.stage, "category": step.category,
            "preset": preset or "—", "override": (settings.get("step_overrides") or {}).get(step.id, ""),
            "effective": decision.gate, "source": decision.source,
        })
    return {
        "gear": settings["gear"],
        "stage_gears": settings.get("stage_gear_overrides") or {},
        "stages": sorted({r["stage"] for r in rows}),
        "rows": rows,
        "checkpoints": settings.get("checkpoints") or {},
        "budget": settings.get("budget") or {},
        "banned_words": settings.get("banned_words") or [],
        "gear_table": _gates.gear_table_rows(),
    }


def save_matrix(ws: Workspace, step_overrides: Dict[str, str], stage_gears: Dict[str, str],
                extra: Dict[str, Any]) -> Dict[str, Any]:
    """保存矩阵：单步覆盖（空值 = 恢复预设）、阶段挡位、检查点豁免、预算与禁用词。"""
    cur = ws.settings()
    overrides = {k: v for k, v in (step_overrides or {}).items() if v}
    stages = {k: v for k, v in (stage_gears or {}).items() if v}
    merged = {**cur, "step_overrides": overrides, "stage_gear_overrides": stages}
    for key, value in (extra or {}).items():
        if key in ("checkpoints", "budget") and isinstance(value, dict):
            merged[key] = {**(cur.get(key) or {}), **value}
        elif key == "banned_words":
            merged[key] = list(value)
        elif key in ("gear", "quality"):
            merged[key] = value
    try:
        saved = ws.save_settings(merged)
    except _settings_mod.SettingsError as exc:
        raise ViewError(str(exc)) from exc
    return {"step_overrides": saved["step_overrides"], "stage_gear_overrides": saved["stage_gear_overrides"]}


def metrics_view(ws: Workspace) -> Dict[str, Any]:
    chapters = chapters_overview(ws)["chapters"]
    rounds = {}
    for n in _chapter_numbers(ws):
        log = _read_text(ws.path("revision_log", nn=n)) or ""
        rounds[n] = sum(1 for line in log.splitlines() if "轮修改后" in line)
    for row in chapters:
        row["revise_rounds"] = rounds.get(row["chapter"], 0)
    records = []
    path = ws.path("studio_usage")
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
    step_records = [r for r in records if "step" in r]
    by_skill: Dict[str, Dict[str, Any]] = {}
    for r in step_records:
        key = r.get("skill") or r.get("executor") or "python"
        agg = by_skill.setdefault(key, {"skill": key, "runs": 0, "failed": 0, "seconds": 0.0})
        agg["runs"] += 1
        agg["seconds"] = round(agg["seconds"] + float(r.get("duration_sec") or 0), 1)
        if r.get("status") in (st.ST_FAILED, st.ST_BLOCKED):
            agg["failed"] += 1
    log_tail = ""
    log = ws.path("studio_log")
    if log.is_file():
        log_tail = "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-40:])
    return {
        "chapters": chapters,
        "usage_by_skill": sorted(by_skill.values(), key=lambda a: -a["seconds"]),
        "recent": step_records[-30:][::-1],
        "total_runs": len(step_records),
        "total_failed": sum(a["failed"] for a in by_skill.values()),
        "log_tail": log_tail,
    }


def step_detail(ws: Workspace, step_id: str) -> Dict[str, Any]:
    base = ws.step_dir(step_id, 1).parent
    runs = sorted((p for p in base.glob("run_*") if p.is_dir()), key=lambda p: int(p.name.split("_")[1])) if base.is_dir() else []
    if not runs:
        return {"step": step_id, "runs": 0, "prompt": None, "receipt": None}
    latest = runs[-1]
    prompts = sorted(latest.glob("prompt_*.md"))
    return {
        "step": step_id,
        "runs": len(runs),
        "run_dir": str(latest.relative_to(ws.root)),
        "prompt": prompts[-1].read_text(encoding="utf-8") if prompts else None,
        "receipt": read_json(latest / "receipt.json", default=None),
    }


def ideas_view(ws: Workspace) -> Dict[str, Any]:
    statuses = {sid: e for sid, e in st.load_state(ws)["steps"].items()}
    rows = []
    for req in _ondemand.list_requests(ws, kind="idea"):
        entry = statuses.get(f"idea.{req['id']}", {})
        result = req.get("result") or {}
        rows.append({"id": req["id"], "text": req.get("text", ""), "created_at": req.get("created_at"),
                     "state": entry.get("status", st.ST_PENDING), "decision": result.get("decision"),
                     "target_chapter": result.get("target_chapter"), "summary": result.get("summary", "")})
    return {"ideas": rows}


def add_idea(ws: Workspace, text: str) -> Dict[str, Any]:
    body = str(text or "").strip()
    if not body:
        raise ViewError("灵感内容不能为空")
    req = _ondemand.create_request(ws, "idea", text=body)
    return {"id": req["id"], "step": f"idea.{req['id']}"}


def request_revision(ws: Workspace, chapter: int, level: str, instruction: str) -> Dict[str, Any]:
    if level not in _ondemand.REVISION_LEVELS:
        raise ViewError(f"未知修订级别 {level!r}")
    if chapter not in _chapter_numbers(ws):
        raise ViewError(f"第 {chapter} 章不在流水线中")
    body = str(instruction or "").strip()
    if not body:
        raise ViewError("请写明修订要求")
    req = _ondemand.create_request(ws, "revision", chapter=int(chapter), level=level, instruction=body)
    return {"id": req["id"], "step": f"rev.{req['id']}", "level": level}
