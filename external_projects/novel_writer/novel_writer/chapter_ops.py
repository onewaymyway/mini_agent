"""novel_writer/chapter_ops.py — 章节循环的 python 步骤（方案 4.3 W1–W4 / W9 / W11，7.2、7.3）。

全部确定性工作（不调用 LLM），除了 W11 的修改循环：它会按需调用 chapter-writer 与 chapter-review
（每一轮都是新的独立 agent，审稿与写作上下文隔离），最多 3 轮；仍有 🔴 则挂起给作者决定，
**不推进到下一章**（方案 6.3 第 3 条）。
"""

from __future__ import annotations

import datetime as _dt
import glob as _glob
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional

from novel_writer import steps as S
from novel_writer.fsutil import atomic_write_json, read_json
from novel_writer.workspace import Workspace, count_words

MAX_REVISE_ROUNDS = 3
HARD_UNDER_RATIO = 0.5       # 字数低于配额 50% = 硬问题
HARD_OVER_RATIO = 1.5        # 高于配额 150% = 硬问题
WARN_DEVIATION = 0.10        # 偏离配额 ±10% = 提示
CARRY_THRESHOLD = 0.15       # 上一章偏差超过 15% 才补偿
CARRY_CAP_RATIO = 0.20       # 补偿上限：基准的 20%
DUP_MIN_CHARS = 30           # 段落重复检测的最短段落
LONG_PARAGRAPH = 600         # 超长段落提示
CONTEXT_SECTION_CAP = {      # 上下文包每段的字符上限（合计约 1.2 万字符）
    "blueprint": 2000, "summary": 800, "cards": 1200, "characters": 1500,
    "world": 1500, "intake": 1000, "tasks": 1500,
}
_RED = "🔴"


class ChapterOpError(RuntimeError):
    pass


# ── 通用工具 ───────────────────────────────────────────────────────────────


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _clip(text: Optional[str], cap: int) -> str:
    if not text:
        return "（无）"
    text = text.strip()
    return text if len(text) <= cap else text[:cap] + "\n…（已截断）"


def _int(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def chapter_file(ws: Workspace, n: int) -> Optional[Path]:
    """第 n 章的正文文件（兼容两位与三位章号）。没有则返回 None。"""
    for width in (2, 3):
        matches = sorted(_glob.glob(str(ws.root / "正文" / f"第{n:0{width}d}章*.md")))
        if matches:
            return Path(matches[0])
    return None


def chapter_entry(ws: Workspace, n: int) -> Dict[str, Any]:
    bp = read_json(ws.path("blueprint"), default=None) or {}
    for ch in bp.get("chapters") or []:
        if isinstance(ch, dict) and ch.get("id") == n:
            return ch
    return {}


def _names(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _clean_name(raw: str) -> str:
    import re

    return re.sub(r"[（(].*$", "", raw).strip()


def _base_quota(ws: Workspace, n: int) -> int:
    word_count = read_json(ws.path("word_count"), default=None) or {}
    budget = (word_count.get("per_chapter_budget") or {}).get(str(n))
    if _int(budget, 0) > 0:
        return _int(budget)
    return max(_int(chapter_entry(ws, n).get("word_budget"), 0), 0)


def quota_of(ws: Workspace, n: int) -> int:
    data = read_json(ws.path("quota_file", n=n), default=None) or {}
    if _int(data.get("quota"), 0) > 0:
        return _int(data["quota"])
    return _base_quota(ws, n)


# ── W1 字数配额 ─────────────────────────────────────────────────────────────


def h_word_quota(ws: Workspace, step: S.StepDef, settings: Dict[str, Any], invoker=None) -> Dict[str, Any]:
    n = step.chapter
    base = _base_quota(ws, n)
    if base <= 0:
        raise ChapterOpError("没有字数配额（蓝图缺少 word_budget，且 word_count.json 未登记）")
    carry = 0
    if n > 1:
        prev = chapter_file(ws, n - 1)
        if prev is not None:
            prev_quota = quota_of(ws, n - 1)
            prev_actual = count_words(prev.read_text(encoding="utf-8"))
            short = prev_quota - prev_actual
            if prev_quota and abs(short) / prev_quota > CARRY_THRESHOLD:
                cap = int(base * CARRY_CAP_RATIO)
                carry = max(-cap, min(cap, short))
    quota = base + carry
    atomic_write_json(ws.path("quota_file", n=n), {"chapter": n, "base": base, "carry": carry, "quota": quota})
    return {"summary": f"配额 {quota} 字（基准 {base}，偏差补偿 {carry:+d}）"}


# ── W2 章前情节清单 ──────────────────────────────────────────────────────────


def _tracker_items(tracker: Any) -> List[Dict[str, Any]]:
    if not isinstance(tracker, dict):
        return []
    for key in ("foreshadowing", "threads", "items"):
        value = tracker.get(key)
        if isinstance(value, list):
            return [v for v in value if isinstance(v, dict)]
    return []


def h_plot_tasks(ws: Workspace, step: S.StepDef, settings: Dict[str, Any], invoker=None) -> Dict[str, Any]:
    n = step.chapter
    items = _tracker_items(read_json(ws.path("plot_tracker"), default=None))
    plant, resolve = [], []
    for it in items:
        brief = {"id": it.get("id"), "name": it.get("name") or it.get("title") or "",
                 "note": it.get("description") or ""}
        if _int(it.get("plant_chapter")) == n:
            plant.append(brief)
        if _int(it.get("resolve_chapter", it.get("payoff_chapter"))) == n:
            resolve.append(brief)
    atomic_write_json(ws.path("chapter_tasks", n=n), {"chapter": n, "plant": plant, "resolve": resolve})
    return {"summary": f"待埋设 {len(plant)} · 待兑现 {len(resolve)}"}


# ── W4 风格提示 ─────────────────────────────────────────────────────────────


def h_style_hint(ws: Workspace, step: S.StepDef, settings: Dict[str, Any], invoker=None) -> Dict[str, Any]:
    n = step.chapter
    profile = read_json(ws.path("style_profile"), default=None)
    profile = profile if isinstance(profile, dict) else {}
    banned = sorted({str(w) for w in (settings.get("banned_words") or []) + (profile.get("banned_words") or []) if w})
    tone = profile.get("tone") or "（未建立风格指纹，按蓝图基调写作）"
    lines = [f"# 风格提示（第{n}章）", "", f"- 基调：{tone}"]
    if banned:
        lines.append("- 禁用词（正文中不得出现）：" + "、".join(banned))
    frequent = profile.get("high_frequency_words") or []
    if frequent:
        lines.append("- 高频词（避免过度使用）：" + "、".join(str(w) for w in frequent[:20]))
    path = ws.path("style_hint", n=n)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"summary": f"禁用词 {len(banned)} 个"}


# ── W3 上下文包（确定性组装，7.3）────────────────────────────────────────────


def _previous_summaries(ws: Workspace, n: int, count: int = 3) -> str:
    parts = []
    for k in range(max(1, n - count), n):
        text = _read(ws.path("chapter_summary", nn=k))
        if text:
            parts.append(f"### 第{k}章概述\n{_clip(text, CONTEXT_SECTION_CAP['summary'])}")
    return "\n\n".join(parts) or "（无：本章是前几章或概述尚未归档）"


def _cards_for(ws: Workspace, names: List[str]) -> str:
    parts = []
    for name in names[:4]:
        text = _read(ws.path("character_card", name=name))
        if text:
            parts.append(f"### {name}\n{_clip(text, CONTEXT_SECTION_CAP['cards'])}")
    return "\n\n".join(parts) or "（无人设卡）"


def build_context(ws: Workspace, n: int) -> str:
    entry = chapter_entry(ws, n)
    present = [_clean_name(x) for x in _names(entry.get("characters_present"))]
    tasks = read_json(ws.path("chapter_tasks", n=n), default=None) or {}
    characters = read_json(ws.path("characters"), default=None)
    world = read_json(ws.path("world_bible"), default=None)
    intake = _read(ws.path("intake_note"))
    blueprint_part = {k: entry.get(k) for k in ("title", "function", "chapter_type", "pov", "tension_level",
                                                 "key_scenes", "characters_present", "word_budget") if k in entry}
    import json

    sections = [
        ("本章蓝图要求", json.dumps(blueprint_part, ensure_ascii=False, indent=2) if blueprint_part else "（蓝图中无本章条目）",
         CONTEXT_SECTION_CAP["blueprint"]),
        ("字数配额", f"{quota_of(ws, n)} 字", 200),
        ("本章情节任务", json.dumps({"待埋设": tasks.get("plant") or [], "待兑现": tasks.get("resolve") or []},
                                    ensure_ascii=False, indent=2), CONTEXT_SECTION_CAP["tasks"]),
        ("出场角色人设", _cards_for(ws, present), CONTEXT_SECTION_CAP["cards"] * 4),
        ("最近章节概述", _previous_summaries(ws, n), CONTEXT_SECTION_CAP["summary"] * 3),
        ("角色状态（档案摘要）", json.dumps(characters, ensure_ascii=False, indent=1) if characters else None,
         CONTEXT_SECTION_CAP["characters"]),
        ("世界设定（摘要）", json.dumps(world, ensure_ascii=False, indent=1) if world else None,
         CONTEXT_SECTION_CAP["world"]),
        ("作者意图", intake, CONTEXT_SECTION_CAP["intake"]),
    ]
    out = [f"# 第{n}章 上下文包", "", f"> 确定性组装；各段有字符上限。风格提示见 {ws.path('style_hint', n=n).relative_to(ws.root).as_posix()}", ""]
    for title, body, cap in sections:
        out.append(f"## {title}")
        out.append(_clip(body, cap))
        out.append("")
    return "\n".join(out)


def h_context_pack(ws: Workspace, step: S.StepDef, settings: Dict[str, Any], invoker=None) -> Dict[str, Any]:
    n = step.chapter
    text = build_context(ws, n)
    path = ws.path("context_pack", n=n)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return {"summary": f"上下文包 {len(text)} 字符"}


# ── W9 机器校验（零 LLM 成本，7.2）──────────────────────────────────────────


def machine_findings(ws: Workspace, n: int, settings: Dict[str, Any]) -> Dict[str, Any]:
    hard: List[str] = []
    warn: List[str] = []
    path = chapter_file(ws, n)
    if path is None:
        return {"chapter": n, "words": 0, "quota": 0, "hard": ["正文文件缺失"], "warn": [], "checked_at": _now()}
    text = path.read_text(encoding="utf-8")
    words = count_words(text)
    quota = quota_of(ws, n)
    if words == 0:
        hard.append("正文为空")
    elif quota:
        ratio = words / quota
        if ratio < HARD_UNDER_RATIO or ratio > HARD_OVER_RATIO:
            hard.append(f"字数 {words}，配额 {quota}，偏离超过一半")
        elif abs(ratio - 1) > WARN_DEVIATION:
            warn.append(f"字数 {words}，配额 {quota}，偏离 {ratio - 1:+.0%}")
    style = read_json(ws.path("style_profile"), default=None) or {}
    banned = {str(w) for w in (settings.get("banned_words") or []) + (style.get("banned_words") or []) if w}
    for word in sorted(banned):
        if word in text:
            hard.append(f"出现禁用词「{word}」")
    previous = set()
    for k in range(max(1, n - 3), n):
        other = chapter_file(ws, k)
        if other is not None:
            previous.update(_paragraphs(other.read_text(encoding="utf-8")))
    for para in _paragraphs(text):
        if para in previous and len(para) >= DUP_MIN_CHARS:
            hard.append(f"段落与前几章重复：「{para[:20]}…」")
            break
    for para in _paragraphs(text):
        if len(para) > LONG_PARAGRAPH:
            warn.append(f"超长段落（{len(para)} 字），考虑拆分")
            break
    return {"chapter": n, "words": words, "quota": quota, "hard": hard, "warn": warn, "checked_at": _now()}


def _paragraphs(text: str) -> List[str]:
    return [p.strip() for p in text.split("\n") if p.strip() and not p.lstrip().startswith("#")]


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def write_machine_report(ws: Workspace, n: int, settings: Dict[str, Any]) -> Dict[str, Any]:
    report = machine_findings(ws, n, settings)
    atomic_write_json(ws.path("machine_report", nn=n), report)
    return report


def h_machine_check(ws: Workspace, step: S.StepDef, settings: Dict[str, Any], invoker=None) -> Dict[str, Any]:
    report = write_machine_report(ws, step.chapter, settings)
    return {"summary": f"机器硬问题 {len(report['hard'])} · 提示 {len(report['warn'])}"}


# ── W11 修改循环（7.4）────────────────────────────────────────────────────────


def count_red(text: Optional[str]) -> int:
    return len(red_items(text))


def red_items(text: Optional[str]) -> List[str]:
    """审稿报告里以 🔴 开头的问题条目（忽略列表符号与表格竖线）。"""
    items = []
    for line in (text or "").splitlines():
        stripped = line.strip().lstrip("-*|>").strip()
        if stripped.startswith(_RED):
            items.append(stripped)
    return items


def _log(ws: Workspace, n: int, line: str) -> None:
    path = ws.path("revision_log", nn=n)
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    with path.open("a", encoding="utf-8") as f:
        f.write(f"- {stamp} {line}\n")


def _review_step(n: int) -> S.StepDef:
    for step in S.chapter_steps(n, "", total=n, prev_last="x"):
        if step.id.endswith(".review"):
            return step
    raise ChapterOpError("找不到审稿步骤定义")


def h_revise_loop(ws: Workspace, step: S.StepDef, settings: Dict[str, Any], invoker=None) -> Dict[str, Any]:
    from novel_writer.runner import NeedsUser, _answers_file, run_skill_step

    n = step.chapter
    if invoker is None:
        raise ChapterOpError("修改循环需要执行器（invoker）")
    answers_path = _answers_file(ws, step.id)
    answers = (read_json(answers_path, default=None) or {}).get("answers") or {}
    decision = str(answers.get("decision") or "")
    if answers_path.exists():
        answers_path.unlink()
    review_path = ws.path("review_report", nn=n)
    review = _review_step(n)

    if decision.startswith("接受"):
        _log(ws, n, "作者接受风险，流程继续（未清零项见审稿报告）")
        return {"summary": "作者接受风险，继续推进"}
    if decision.startswith("重新"):
        run_skill_step(ws, review, invoker=invoker, settings=settings, run_no=1)
        _log(ws, n, "作者手动修改后重新核查")

    red = red_items(_read(review_path))
    hard = write_machine_report(ws, n, settings)["hard"]
    if not red and not hard:
        return {"summary": "无 🔴 硬问题，无需修改"}

    for k in range(1, MAX_REVISE_ROUNDS + 1):
        items = red + [f"[机器] {h}" for h in hard]
        writer = S.StepDef(
            id=f"ch{n:02d}.revise.r{k}", title=f"第{n}章：修改第{k}轮", stage=step.stage,
            category=S.CAT_CHAPTER_REVIEW, executor=S.EXECUTOR_SKILL, skill="chapter-writer", chapter=n,
            instructions=(
                f"这是第 {n} 章的修改（第 {k}/{MAX_REVISE_ROUNDS} 轮）。请只针对下列问题做**最小修改**："
                "只改动指出问题的句段，不改动其它段落，不改变情节走向与出场设定，字数保持在配额附近。\n"
                "问题清单：\n" + "\n".join(f"- {i}" for i in items) +
                "\n完成后在回执 summary 中逐条说明改了什么。"
            ),
            inputs=(ws.path("review_report", nn=n).relative_to(ws.root).as_posix(),
                    ws.path("machine_report", nn=n).relative_to(ws.root).as_posix()),
            outputs=(S.glob_of("chapter_text", nn=n),),
        )
        run_skill_step(ws, writer, invoker=invoker, settings=settings, run_no=k)
        run_skill_step(ws, replace(review, id=f"ch{n:02d}.review.r{k}"), invoker=invoker, settings=settings, run_no=k)
        red = red_items(_read(review_path))
        hard = write_machine_report(ws, n, settings)["hard"]
        _log(ws, n, f"第 {k} 轮修改后：🔴 {len(red)} 条，机器硬问题 {len(hard)} 条")
        if not red and not hard:
            return {"summary": f"第 {k} 轮修改后清零"}

    remaining = red + [f"[机器] {h}" for h in hard]
    atomic_write_json(ws.studio / "inbox" / f"ch{n:02d}_escalation.json", {
        "chapter": n, "rounds": MAX_REVISE_ROUNDS, "remaining": remaining, "raised_at": _now(),
    })
    _log(ws, n, f"修改 {MAX_REVISE_ROUNDS} 轮后仍有 {len(remaining)} 条硬问题，升级人工（不推进下一章）")
    raise NeedsUser(
        f"第 {n} 章修改 {MAX_REVISE_ROUNDS} 轮后仍有硬问题，已暂停，下一章不会开始。",
        [{
            "id": "decision",
            "question": "剩余问题：\n" + "\n".join(remaining[:8]) + "\n请选择：",
            "options": ["重新核查（我已手动修改）", "接受风险继续"],
        }],
    )


CHAPTER_HANDLERS = {
    "word_quota": h_word_quota,
    "plot_tasks": h_plot_tasks,
    "context_pack": h_context_pack,
    "style_hint": h_style_hint,
    "machine_check": h_machine_check,
    "revise_loop": h_revise_loop,
}
