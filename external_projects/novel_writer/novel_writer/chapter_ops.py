"""novel_writer/chapter_ops.py — 章节循环的 python 步骤（方案 4.3 W1–W4 / W9 / W11，7.2、7.3）。

全部确定性工作（不调用 LLM），除了 W11 的修改循环：它会按需调用 chapter-writer 与 chapter-review
（每一轮都是新的独立 agent，审稿与写作上下文隔离），最多 3 轮；仍有 🔴 则挂起给作者决定，
**不推进到下一章**（方案 6.3 第 3 条）。
"""

from __future__ import annotations

import datetime as _dt
import glob as _glob
import re
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional

from novel_writer import repair as RP
from novel_writer import validators as V
from novel_writer.textio import read_text_utf8
from novel_writer import steps as S
from novel_writer.fsutil import atomic_write_json, atomic_write_text, read_json
from novel_writer.workspace import Workspace, count_words

MAX_REVISE_ROUNDS = 3
MAX_GARBLE_REGENS = 3        # 乱码守卫：每轮最多自动重新生成的次数（用户要求：三次仍失败即停下）
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


class DataFileError(ChapterOpError, RP.RepairableDataError):
    """数据文件无法读取：既是章节操作错误，也可交给 LLM 修复（见 repair.py）。"""

    def __init__(self, message: str, *, validator: str, files: list, problems: list,
                 scope: Optional[str] = None) -> None:
        # ChapterOpError 继承自 RuntimeError，MRO 里 RuntimeError 会先于 RepairableDataError 生效，
        # 所以这里显式指定构造逻辑，避免关键字参数被基类拒绝
        RP.RepairableDataError.__init__(self, message, validator=validator, files=files,
                                        problems=problems, scope=scope)


class ChapterOpFatal(ChapterOpError):
    """确定性、重试也不会好转的错误（如数据文件损坏且自愈失败）：worker 不再自动重跑，直接停下报原因。"""
    no_retry = True


# ── 通用工具 ───────────────────────────────────────────────────────────────


def _read(path: Path) -> Optional[str]:
    try:
        return read_text_utf8(path)
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


def _chapter_candidates(ws: Workspace, n: int) -> List[Path]:
    """正文目录下所有章号为 n 的文件（兼容两位与三位章号），不含 `_drafts/` 子目录。"""
    out: List[Path] = []
    for width in (2, 3):
        for m in sorted(_glob.glob(str(ws.root / "正文" / f"第{n:0{width}d}章*.md"))):
            p = Path(m)
            if p not in out:
                out.append(p)
    return out


def chapter_file(ws: Workspace, n: int) -> Optional[Path]:
    """第 n 章的正文文件（兼容两位与三位章号）。没有则返回 None。

    同一章号出现多个文件（重新生成时标题变了会产生「第26章 甲.md」「第26章 乙.md」）时，
    不能再取字典序第一个——那往往是旧的乱码文件。规则：优先蓝图标题对应的规范文件名，
    否则取最近修改的那个。
    """
    cands = _chapter_candidates(ws, n)
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    title = str(chapter_entry(ws, n).get("title") or "").strip()
    if title:
        from novel_writer.workspace import chapter_filename

        try:
            canonical = {chapter_filename(n, title, None), chapter_filename(n, title, 100)}
        except Exception:  # noqa: BLE001 - 标题含非法字符等情况：退回按修改时间选
            canonical = set()
        for p in cands:
            if p.name in canonical:
                return p
    return max(cands, key=lambda p: p.stat().st_mtime)


def chapter_entry(ws: Workspace, n: int) -> Dict[str, Any]:
    bp = _blueprint_strict(ws) or {}
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
        if not chapter_entry(ws, n):
            raise ChapterOpFatal(
                f"第 {n} 章没有蓝图条目，无法确定字数配额（蓝图只细化了部分章节）。自动重跑不会好转："
                "请确认设置里 plan_act_auto 为开启后重新展开计划（进入该幕前会自动运行 plan_act 补全章节），"
                "或手动补全蓝图中该章的条目与 word_budget")
        raise ChapterOpError(f"第 {n} 章蓝图条目缺少 word_budget，且 word_count.json 未登记")
    carry = 0
    if n > 1:
        prev = chapter_file(ws, n - 1)
        if prev is not None:
            prev_quota = quota_of(ws, n - 1)
            prev_actual = count_words(read_text_utf8(prev))
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
    items = _tracker_items(_ledger_or_raise(ws))
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
    from novel_writer import patrol_findings as PF
    if PF.feedback_settings(settings)["inject_style"]:
        style_items = PF.active_for_chapter(ws, n, PF.SCOPE_STYLE)
        phrases = PF.avoid_phrases_for_chapter(ws, n)
        if style_items:
            lines.append("- 近期套路（巡检指出，请避免重复；不是禁用词，换一种写法即可）：")
            for f in style_items[:PF.MAX_INJECT_ITEMS]:
                lines.append(f"  - {f['directive']}（常见写法：{'、'.join(f.get('avoid_phrases') or [])}）")
            lines.append(f"  - 搜索词：{'、'.join(phrases)}")
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
    characters = _characters_or_raise(ws)
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
        ("角色状态（出场角色摘要）", _character_brief(characters, present) if characters else None,
         CONTEXT_SECTION_CAP["characters"]),
        ("世界设定（摘要）", json.dumps(world, ensure_ascii=False, indent=1) if world else None,
         CONTEXT_SECTION_CAP["world"]),
        ("作者意图", intake, CONTEXT_SECTION_CAP["intake"]),
    ]
    from novel_writer import patrol_findings as PF
    if PF.feedback_settings(ws.settings())["inject_forward"]:
        directives = PF.render_directives(PF.active_for_chapter(ws, n, PF.SCOPE_FORWARD))
        if directives:
            sections.insert(3, ("巡检整改指令（作者已采纳，本章请落实）", directives, PF.MAX_INJECT_CHARS + 200))
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


def machine_findings(ws: Workspace, n: int, settings: Dict[str, Any], path: Optional[Path] = None) -> Dict[str, Any]:
    hard: List[str] = []
    warn: List[str] = []
    path = path or chapter_file(ws, n)
    if path is None:
        return {"chapter": n, "words": 0, "quota": 0, "hard": ["正文文件缺失"], "warn": [], "checked_at": _now()}
    text = read_text_utf8(path)
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
    from novel_writer import patrol_findings as PF
    if PF.feedback_settings(settings)["inject_style"]:
        for phrase in PF.avoid_phrases_for_chapter(ws, n):
            hits = text.count(phrase)
            if hits:
                warn.append(f"出现巡检指出的套路写法「{phrase}」×{hits}（仅提示，不阻断）")
    previous = set()
    for k in range(max(1, n - 3), n):
        other = chapter_file(ws, k)
        if other is not None:
            previous.update(_paragraphs(read_text_utf8(other)))
    for para in _paragraphs(text):
        if para in previous and len(para) >= DUP_MIN_CHARS:
            hard.append(f"段落与前几章重复：「{para[:20]}…」")
            break
    for para in _paragraphs(text):
        if len(para) > LONG_PARAGRAPH:
            warn.append(f"超长段落（{len(para)} 字），考虑拆分")
            break
    # 写作备注格式（过渡阶段：只作提示，不阻断，用于观察违规率）
    from novel_writer import writer_notes as WN
    for issue in WN.audit_notes(text):
        warn.append(f"写作备注格式不统一：{issue}")
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


# ── 乱码守卫（写作后、机器校验 / 择优前）────────────────────────────────────

GARBLE_OPTIONS = ["再重新生成（再试三次）", "我手动修改后重新核查", "接受当前内容（风险自负）"]


def _is_garbled_file(path: Path) -> bool:
    """读不出来（编码错误等）也按乱码处理。"""
    from novel_writer.garble import is_garbled

    try:
        return is_garbled(read_text_utf8(path)).garbled
    except Exception:  # noqa: BLE001
        return True


def _stash_chapter_strays(ws: Workspace, n: int, keep: Path) -> List[str]:
    """把同章号的其它正文文件移到 `正文/_drafts/`（改名保留，不删除），避免出现多个「第N章」。"""
    import os

    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    drafts = ws.root / "正文" / "_drafts"
    moved: List[str] = []
    for p in _chapter_candidates(ws, n):
        if p == keep:
            continue
        drafts.mkdir(parents=True, exist_ok=True)
        dest = drafts / f"{p.stem}_重复残留_{stamp}{p.suffix}"
        k = 1
        while dest.exists():
            k += 1
            dest = drafts / f"{p.stem}_重复残留_{stamp}_{k}{p.suffix}"
        os.replace(p, dest)
        moved.append(f"{p.name} → _drafts/{dest.name}")
    return moved


def _reconcile_regen_result(ws: Workspace, n: int, target: Path, started: float) -> None:
    """重新生成后收敛：只保留 `target` 一个第 n 章文件。

    重新生成的智能体可能换了标题，写出「第n章 别的标题.md」而没动原文件。这时：
      * 原文件仍是乱码、而本次新写的文件正常 → 用新内容覆盖原文件（文件名保持规范）；
      * 其余同章号文件一律移入 `正文/_drafts/`，保证目录里同一章只有一个文件。
    """
    import os

    fresh = [p for p in _chapter_candidates(ws, n) if p != target and p.stat().st_mtime >= started - 1]
    if fresh and target.is_file() and _is_garbled_file(target):
        for best in sorted(fresh, key=lambda p: p.stat().st_mtime, reverse=True):
            if not _is_garbled_file(best):
                os.replace(best, target)
                _log(ws, n, f"乱码守卫：重新生成的内容写成了「{best.name}」，已用它覆盖「{target.name}」")
                break
    for moved in _stash_chapter_strays(ws, n, keep=target):
        _log(ws, n, f"乱码守卫：同章号多余文件已移出正文目录：{moved}")


def _pin_regen_output(ws: Workspace, regen: S.StepDef, target: Path, label: str) -> S.StepDef:
    """让重新生成的步骤必须覆盖写入 `target`（不得换标题/换文件名）。"""
    rel_path = target.relative_to(ws.root).as_posix()
    note = (f"\n【重新生成】上一版 `{rel_path}` 疑似乱码。请重写{label}全文，并**覆盖写入该文件**："
            f"文件名与标题保持不变，不要新建其它文件名（例如换了标题的「第N章 xxx.md」）。")
    return replace(regen, outputs=(rel_path,), instructions=(regen.instructions or "") + note)


def _garble_guard(ws: Workspace, step: S.StepDef, settings: Dict[str, Any], invoker, *,
                  path: Optional[Path], regen: S.StepDef, label: str, pin_chapter: bool = False) -> Dict[str, Any]:
    """对一个文件做乱码检测：命中则用 regen 步骤重新生成，最多 MAX_GARBLE_REGENS 次；仍乱码则挂起给作者。

    决策（来自上一次挂起的作者回答）：
      * 无 / 「再重新生成」：自动重新生成，最多三次；
      * 「我手动修改后重新核查」：不再生成，只重新检测；
      * 「接受」：记录风险后继续推进。
    """
    from novel_writer.garble import is_garbled
    from novel_writer.runner import NeedsUser, StepFailed, StepTimeout, _answers_file, run_skill_step

    n = step.chapter
    if invoker is None:
        raise ChapterOpError("乱码守卫需要执行器（invoker）")
    answers_path = _answers_file(ws, step.id)
    answers = (read_json(answers_path, default=None) or {}).get("answers") or {}
    decision = str(answers.get("decision") or "")
    if answers_path.exists():
        answers_path.unlink()

    if path is None or not path.is_file():
        raise ChapterOpError(f"第 {n} 章{label}文件缺失，无法做乱码检测")

    def check() -> Any:
        return is_garbled(read_text_utf8(path))

    if pin_chapter:
        # 正文：重新生成必须覆盖当前这个文件；同时先收敛历史遗留的同章号重复文件
        regen = _pin_regen_output(ws, regen, path, label)
        if _is_garbled_file(path):
            # 历史遗留的重复文件里若有正常的（比如之前换标题重生成出的好稿），先拿来顶替，省一次生成
            import os

            others = [p for p in _chapter_candidates(ws, n) if p != path]
            for cand in sorted(others, key=lambda p: p.stat().st_mtime, reverse=True):
                if not _is_garbled_file(cand):
                    os.replace(cand, path)
                    _log(ws, n, f"乱码守卫：同章号文件「{cand.name}」内容正常，已用它覆盖乱码的「{path.name}」")
                    break
        for moved in _stash_chapter_strays(ws, n, keep=path):
            _log(ws, n, f"乱码守卫：同章号多余文件已移出正文目录：{moved}")

    report = check()
    if not report.garbled:
        return {"summary": f"乱码守卫：{label}未发现乱码"}

    if decision.startswith("接受"):
        _log(ws, n, f"乱码守卫：作者接受{label}风险，继续推进（{'；'.join(report.reasons)}）")
        return {"summary": f"作者接受{label}乱码风险，继续推进"}

    if decision.startswith("我手动修改"):
        _log(ws, n, f"乱码守卫：作者手动修改{label}后重新检测")
    else:
        for k in range(1, MAX_GARBLE_REGENS + 1):
            _log(ws, n, f"乱码守卫：{label}检测到乱码（{'；'.join(report.reasons)}），自动重新生成第 {k}/{MAX_GARBLE_REGENS} 次")
            started = time.time()
            try:
                run_skill_step(ws, regen, invoker=invoker, settings=settings, run_no=k + 1)
            except StepTimeout:
                raise   # 步骤总时限已到：不再继续重生成，交给 worker 终止并重跑本步骤
            except StepFailed as exc:
                _log(ws, n, f"乱码守卫：{label}重新生成第 {k} 次失败：{exc}")
                if pin_chapter:
                    _reconcile_regen_result(ws, n, path, started)
                    report = check()
                    if not report.garbled:
                        _log(ws, n, f"乱码守卫：{label}第 {k} 次重新生成虽报失败，但文件已正常，通过检测")
                        return {"summary": f"乱码守卫：{label}重新生成 {k} 次后通过"}
                continue
            if pin_chapter:
                _reconcile_regen_result(ws, n, path, started)
            report = check()
            if not report.garbled:
                _log(ws, n, f"乱码守卫：{label}第 {k} 次重新生成后通过检测")
                return {"summary": f"乱码守卫：{label}重新生成 {k} 次后通过"}

    if not report.garbled:
        return {"summary": f"乱码守卫：{label}手动修改后通过检测"}

    _log(ws, n, f"乱码守卫：{label}仍为乱码，挂起等待作者决定（{'；'.join(report.reasons)}）")
    raise NeedsUser(
        f"第 {n} 章{label}疑似乱码，已自动重新生成 {MAX_GARBLE_REGENS} 次仍未通过，已暂停，下一章不会开始。",
        [{
            "id": "decision",
            "question": f"{label}检测结果：" + "；".join(report.reasons) + "\n请选择：",
            "options": GARBLE_OPTIONS,
        }],
    )


def h_garble_guard(ws: Workspace, step: S.StepDef, settings: Dict[str, Any], invoker=None) -> Dict[str, Any]:
    """正文 A 的乱码守卫。"""
    n = step.chapter
    return _garble_guard(ws, step, settings, invoker, path=chapter_file(ws, n),
                         regen=_chapter_step(n, "write_chapter"), label="正文", pin_chapter=True)


def _ledger_or_raise(ws: Workspace) -> Dict[str, Any]:
    """读取伏笔账本；损坏或未迁移时给出可操作的错误（不静默当作空账本）。"""
    from novel_writer import plot_delta as PD
    try:
        return V._load_ledger(ws)
    except PD.PlotDeltaError as exc:
        raise ChapterOpFatal("伏笔账本读取失败（自动重跑不会好转，已停止重试）：" + "；".join(exc.problems)
                             + "。可对「伏笔」目录运行 plot_store.heal_store / repair_free_markdown 修复，"
                               "或备份后删除该目录并重跑 plot_import")


def h_plot_import(ws: Workspace, step: S.StepDef, settings: Dict[str, Any], invoker=None) -> Dict[str, Any]:
    """把一次性注册结果（plot_register.json）转为小文件账本。
    安全规则：账本已存在则跳过，绝不覆盖（覆盖会丢失已有的推进/兑现记录）。
    要重新注册，请先手动删除伏笔目录再重跑。"""
    from novel_writer import plot_store as PS
    from novel_writer import plot_delta as PD
    base = ws.path("plot_items_dir")
    if PS.is_store(base):
        problems = PS.store_problems(base)
        if not problems:
            return {"summary": "伏笔账本已存在，跳过导入（不覆盖已有的推进记录）"}
        # 账本「存在」但内容损坏（如 register_plot 的 agent 手写了不合规文件）：不能静默跳过
        if not settings.get("ledger_auto_repair", True):
            raise ChapterOpFatal("伏笔账本已存在但格式损坏：" + "；".join(problems[:5])
                                 + "。请备份并删除「伏笔」目录后重跑，或开启 ledger_auto_repair")
        actions, left = PS.heal_store(base, ws.path("plot_register"))
        if left:
            raise ChapterOpFatal("伏笔账本损坏且自动修复未能完成：" + "；".join(left[:5]))
        return {"summary": "伏笔账本已存在但损坏，已自动修复：" + "；".join(actions[:3])}
    if ws.path("plot_legacy").is_file():
        raise ChapterOpError("项目仍使用旧版伏笔账本（JSON）。请先运行伏笔迁移，再继续流水线")
    data = read_json(ws.path("plot_register"), default=None)
    if not isinstance(data, dict):
        raise ChapterOpError("伏笔注册结果缺失或已损坏（不是合法的 JSON 对象）。"
                             "可重跑 register_plot，或在伏笔迁移中以修复模式处理")
    data.setdefault(PD.DEFAULT_LIST_KEY, [])
    try:
        PS.write_store(ws.path("plot_items_dir"), data)
    except PD.PlotDeltaError as exc:
        raise ChapterOpError("伏笔导入失败：" + "；".join(exc.problems))
    n = len(data.get(PD.list_key(data), []))
    return {"summary": f"已导入 {n} 条伏笔到小文件账本"}

# ── 人物档案（第四阶段：每个角色一个文件，由 LLM 直接更新）────────────────────────

def _characters_or_raise(ws: Workspace) -> Optional[Dict[str, Any]]:
    from novel_writer import character_store as CS
    try:
        return CS.load_or_none(ws.path("characters_dir"), ws.path("characters"), ws.path("characters_legacy"))
    except CS.CharacterStoreError as exc:
        base = ws.path("characters_dir")
        named = sorted({m for prob in exc.problems for m in re.findall(r"([\w\-]+\.md)", prob)})
        if named:
            names = named
        else:   # 报错里没点名文件：范围内的全部角色文件都允许修改
            names = sorted(f.name for f in base.glob("*.md")) if base.is_dir() else []
        files = [str((base / n).relative_to(ws.root)) for n in names]
        raise DataFileError("人物档案读取失败：" + "；".join(exc.problems), validator="characters",
                            files=files, problems=exc.problems,
                            scope=str(base.relative_to(ws.root))) from exc


def _characters_soft(ws: Workspace) -> Optional[Dict[str, Any]]:
    from novel_writer import character_store as CS
    try:
        return CS.load_or_none(ws.path("characters_dir"), ws.path("characters"), ws.path("characters_legacy"))
    except CS.CharacterStoreError:
        return None


def _character_brief(chars: Dict[str, Any], present: List[str]) -> str:
    from novel_writer import character_store as CS
    return CS.brief_for(chars, present)


def h_characters_refresh(ws: Workspace, step: S.StepDef, settings: Dict[str, Any], invoker=None) -> Dict[str, Any]:
    """人物档案刷新（确定性）：校验全部角色文件，重生成正文与索引；内容无变化不写盘。"""
    from novel_writer import character_store as CS
    if not CS.is_store(ws.path("characters")) and ws.path("characters_legacy").is_file():
        raise ChapterOpError("项目仍使用旧版人物档案（characters.json）。请先运行人物档案迁移，再继续流水线")
    fixed = CS.repair_dir(ws.path("characters_dir"))
    if fixed:
        from novel_writer import state as _st
        _st.log_line(ws, f"🔧 {step.id}：已确定性修复 {len(fixed)} 个角色文件的格式：{'、'.join(fixed[:8])}")
    try:
        chars, changed = CS.refresh(ws.path("characters_dir"), ws.path("characters"))
    except CS.CharacterStoreError as exc:
        raise ChapterOpError("人物档案校验失败：" + "；".join(exc.problems))
    return {"summary": f"人物档案已刷新（{len(chars)} 个角色）" + ("" if changed else "，无实质变化，未写盘")}


# ── 蓝图（第五阶段：每章一个文件；plan_blueprint 产出草稿，blueprint_split 拆分）──────

def _blueprint_strict(ws: Workspace) -> Optional[Dict[str, Any]]:
    from novel_writer import blueprint_store as BS
    try:
        return BS.load_for_ws(ws, strict=True)
    except BS.BlueprintStoreError as exc:
        raise ChapterOpError("蓝图读取失败：" + "；".join(exc.problems))


def h_blueprint_split(ws: Workspace, step: S.StepDef, settings: Dict[str, Any], invoker=None) -> Dict[str, Any]:
    """蓝图拆分（确定性）：把 plan_blueprint 的草稿拆成 _meta.md + 每章文件 + 索引；内容无变化不写盘。"""
    from novel_writer import blueprint_store as BS
    draft = read_json(ws.path("blueprint_draft"), default=None)
    if not isinstance(draft, dict):
        raise ChapterOpError("蓝图草稿缺失或不是 JSON 对象，请重新运行 plan_blueprint")
    try:
        changed = BS.save_for_ws(ws, draft)
    except BS.BlueprintStoreError as exc:
        raise ChapterOpError("蓝图拆分失败：" + "；".join(exc.problems))
    n = len(draft.get("chapters") or [])
    return {"summary": f"蓝图已拆分为 {n} 章文件" + ("" if changed else "，无实质变化，未写盘")}


def _read_plot_delta_or_raise(ws: Workspace, n: int) -> Dict[str, Any]:
    """读取本章伏笔增量。用与产出校验相同的读取方式，并区分「不存在 / 无法读取 / 格式不对」，给出具体原因。"""
    from novel_writer import validators as V
    path = ws.path("plot_delta", nn=n)
    if not path.is_file():
        raise ChapterOpError(f"第 {n} 章伏笔增量文件不存在：{path.name}。请重新运行本章的伏笔同步步骤（sync_plot / sync）")
    try:
        data, err, _fenced = V._read_json_loose(path)
    except OSError as exc:
        raise ChapterOpError(f"第 {n} 章伏笔增量无法读取（{exc}）。文件可能被其它程序占用，请关闭后重试")
    if err is not None:
        raise ChapterOpError(f"第 {n} 章伏笔增量不是合法 JSON（{err}）：{path.name}")
    if not isinstance(data, dict):
        raise ChapterOpError(f"第 {n} 章伏笔增量顶层必须是 JSON 对象：{path.name}")
    return data


def h_plot_merge(ws: Workspace, step: S.StepDef, settings: Dict[str, Any], invoker=None) -> Dict[str, Any]:
    """把本章伏笔增量合并进账本（M9）。确定性合并，内容无实质变化则不写盘。"""
    from novel_writer import plot_store as PS
    from novel_writer import plot_delta as PD
    n = step.chapter
    if not PS.is_store(ws.path("plot_items_dir")) and ws.path("plot_legacy").is_file():
        raise ChapterOpError("项目仍使用旧版伏笔账本（JSON）。请先运行伏笔迁移，再继续流水线")
    tracker = _ledger_or_raise(ws)
    delta = _read_plot_delta_or_raise(ws, n)
    try:
        merged = PD.merge_result(tracker, delta, n)
        changed = PS.write_store(ws.path("plot_items_dir"), merged)
    except PD.PlotDeltaError as exc:
        raise ChapterOpError("伏笔合并失败：" + "；".join(exc.problems))
    return {"summary": f"第 {n} 章伏笔已合并" + ("" if changed else "（无实质变化，未写盘）")}

def h_garble_guard_alt(ws: Workspace, step: S.StepDef, settings: Dict[str, Any], invoker=None) -> Dict[str, Any]:
    """候选稿 B 的乱码守卫：B 乱码时只重新生成 B，不动正文 A。"""
    n = step.chapter
    regen = S.alt_write_step(n, prev_last=S.chapter_id(n, "write_chapter"))
    return _garble_guard(ws, step, settings, invoker, path=ws.path("candidate_alt", nn=n),
                         regen=regen, label="候选稿B")


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


def _chapter_step(n: int, suffix: str) -> S.StepDef:
    for step in S.chapter_steps(n, "", total=n, prev_last="x"):
        if step.id == f"ch{n:02d}.{suffix}":
            return step
    raise ChapterOpError(f"找不到步骤 ch{n:02d}.{suffix}")


def _refresh_check_steps(ws: Workspace, n: int, *, clean: bool) -> None:
    """修改循环改写了正文，但循环内每轮已重新核查与机器校验，结果是新的。

    这两个步骤的输入指纹需要同步到当前正文，否则会被脏标记误判为「上游变化、需要重跑」。
    核查通过则标为通过；未通过则只更新指纹，状态交给升级流程。
    """
    from novel_writer import state as st

    for suffix in ("machine_check", "review"):
        step = _chapter_step(n, suffix)
        fields = {"input_hash": st.input_hash(ws, step), "input_digests": st.input_digests(ws, step)}
        if clean:
            fields.update(status=st.ST_PASSED, error=None, needs_user=None, note=None)
        st.update_step(ws, step.id, **fields)


def revise_cycle(ws: Workspace, n: int, *, stage: str, invoker, settings: Dict[str, Any],
                 id_prefix: str, feedback: str = "", max_rounds: int = MAX_REVISE_ROUNDS) -> Dict[str, Any]:
    """修改 → 核查 循环（通用）。

    每一轮：chapter-writer 按「作者反馈（若有）+ 当前 🔴 与机器硬问题」做最小修改，
    然后重新跑完章核查与机器校验。清零即停；否则进入下一轮，最多 `max_rounds` 轮。
    作者反馈只在第一轮作为输入，之后的轮次只针对剩余问题。

    返回 {"clean": bool, "rounds": int, "remaining": [str]}。不负责升级人工，由调用方决定。
    """
    from dataclasses import replace

    from novel_writer.runner import run_skill_step

    review = _review_step(n)
    review_path = ws.path("review_report", nn=n)
    red = red_items(_read(review_path))
    hard = write_machine_report(ws, n, settings)["hard"]
    feedback = (feedback or "").strip()
    if not red and not hard and not feedback:
        return {"clean": True, "rounds": 0, "remaining": []}

    for k in range(1, max_rounds + 1):
        requests = ([f"[作者反馈，若与审稿意见冲突以此为准] {feedback}"] if feedback else [])
        requests += red + [f"[机器] {h}" for h in hard]
        writer = S.StepDef(
            id=f"{id_prefix}.revise.r{k}", title=f"第{n}章：修改第{k}轮", stage=stage,
            category=S.CAT_CHAPTER_REVIEW, executor=S.EXECUTOR_SKILL, skill="chapter-writer", chapter=n,
            instructions=(
                f"这是第 {n} 章的修改（第 {k}/{max_rounds} 轮）。请只针对下列问题做**最小修改**："
                "只改动指出问题的句段，不改动其它段落，不改变情节走向与出场设定，字数保持在配额附近。\n"
                "问题与要求：\n" + "\n".join(f"- {i}" for i in requests) +
                "\n完成后在回执 summary 中逐条说明改了什么。"
            ),
            inputs=(ws.path("review_report", nn=n).relative_to(ws.root).as_posix(),
                    ws.path("machine_report", nn=n).relative_to(ws.root).as_posix()),
            outputs=(S.glob_of("chapter_text", nn=n),),
        )
        run_skill_step(ws, writer, invoker=invoker, settings=settings, run_no=k)
        run_skill_step(ws, replace(review, id=f"{id_prefix}.review.r{k}"),
                       invoker=invoker, settings=settings, run_no=k)
        red = red_items(_read(review_path))
        hard = write_machine_report(ws, n, settings)["hard"]
        _log(ws, n, f"第 {k} 轮修改后：🔴 {len(red)} 条，机器硬问题 {len(hard)} 条")
        feedback = ""
        if not red and not hard:
            _refresh_check_steps(ws, n, clean=True)
            return {"clean": True, "rounds": k, "remaining": []}

    _refresh_check_steps(ws, n, clean=False)
    return {"clean": False, "rounds": max_rounds,
            "remaining": red + [f"[机器] {h}" for h in hard]}


def route_review_answers(ws: Workspace, review_step_id: str, answers: Dict[str, Any],
                         questions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """完章核查待答复时，把作者的答复整理成修改意见，交给修改循环执行（修改由 chapter-writer 完成）。

    核查步骤记为通过；修改循环的答复文件写入「按审稿意见再修改一轮 + 修改意见」，
    然后把修改循环重置为待运行，由流水线接着执行：修改 → 重新核查 → 机器校验。
    """
    from novel_writer import state as st
    from novel_writer.runner import _answers_file

    m = re.fullmatch(r"ch(\d+)\.review", review_step_id)
    if not m:
        raise ChapterOpError(f"{review_step_id} 不是完章核查步骤")
    n = int(m.group(1))
    if not questions:
        raise ChapterOpError("这一步没有待答复的问题")
    lines = []
    for q in questions:
        qid = str(q.get("id") or "q")
        text = str(q.get("question") or "").strip()
        ans = str(answers.get(qid) or "").strip()
        if not ans:
            raise ChapterOpError(f"请回答：{text[:30]}")
        lines.append(f"{text}\n  → 作者答复：{ans}")
    feedback = "\n".join(lines)

    loop_id = f"ch{n:02d}.revise_loop"
    atomic_write_json(_answers_file(ws, loop_id), {
        "answers": {"decision": "按审稿意见再修改一轮", "feedback": feedback},
        "answered_at": _now(),
    })
    st.update_step(ws, review_step_id, status=st.ST_PASSED, error=None, needs_user=None,
                   summary="作者答复已转为修改意见")
    st.reset_step(ws, loop_id)
    _log(ws, n, "作者对核查问题的答复已转为修改意见，进入修改核查循环")
    return {"chapter": n, "routed_to": loop_id}


def h_revise_loop(ws: Workspace, step: S.StepDef, settings: Dict[str, Any], invoker=None) -> Dict[str, Any]:
    from novel_writer.runner import NeedsUser, _answers_file, run_skill_step

    n = step.chapter
    if invoker is None:
        raise ChapterOpError("修改循环需要执行器（invoker）")
    answers_path = _answers_file(ws, step.id)
    answers = (read_json(answers_path, default=None) or {}).get("answers") or {}
    decision = str(answers.get("decision") or "")
    feedback = str(answers.get("feedback") or "").strip()
    if answers_path.exists():
        answers_path.unlink()
    review = _review_step(n)

    if decision.startswith("接受"):
        _log(ws, n, "作者接受风险，流程继续（未清零项见审稿报告）")
        return {"summary": "作者接受风险，继续推进"}
    if decision.startswith("重新"):
        run_skill_step(ws, review, invoker=invoker, settings=settings, run_no=1)
        _log(ws, n, "作者手动修改后重新核查")

    result = revise_cycle(ws, n, stage=step.stage, invoker=invoker, settings=settings,
                          id_prefix=f"ch{n:02d}", feedback=feedback)
    if result["clean"]:
        if result["rounds"] == 0:
            return {"summary": "无 🔴 硬问题，无需修改"}
        return {"summary": f"第 {result['rounds']} 轮修改后清零"}

    remaining = result["remaining"]
    atomic_write_json(ws.studio / "inbox" / f"ch{n:02d}_escalation.json", {
        "chapter": n, "rounds": MAX_REVISE_ROUNDS, "remaining": remaining, "raised_at": _now(),
    })
    _log(ws, n, f"修改 {MAX_REVISE_ROUNDS} 轮后仍有 {len(remaining)} 条硬问题，升级人工（不推进下一章）")
    raise NeedsUser(
        f"第 {n} 章修改 {MAX_REVISE_ROUNDS} 轮后仍有硬问题，已暂停，下一章不会开始。",
        [{
            "id": "decision",
            "question": "剩余问题：\n" + "\n".join(remaining[:8]) + "\n请选择：",
            "options": ["按审稿意见再修改一轮", "重新核查（我已手动修改）", "接受风险继续"],
        }, {
            "id": "feedback",
            "question": "修改意见（选「按审稿意见再修改一轮」时生效；可写具体要求，留空则只按剩余问题修改）",
        }],
    )


def h_snapshot(ws: Workspace, step: S.StepDef, settings: Dict[str, Any], invoker=None) -> Dict[str, Any]:
    """每 10 章的全局快照：人物档案、伏笔账本、全书字数（方案 4.2 snapshot）。"""
    n = int(step.extra.get("through_chapter") or 0)
    words = 0
    for k in range(1, n + 1):
        path = chapter_file(ws, k)
        if path is not None:
            words += count_words(read_text_utf8(path))
    # 快照只记录轻量摘要：不再整份复制人物档案与伏笔账本（过去每章复制一次，体积随章数平方增长）。
    # 需要完整状态时，以当前的 characters 与伏笔小文件为准。
    from novel_writer import plot_delta as PD
    ledger = _ledger_or_raise(ws)
    items = ledger.get(PD.list_key(ledger), [])
    by_status: Dict[str, int] = {}
    for it in items:
        st = str(it.get("status") or "planted")
        by_status[st] = by_status.get(st, 0) + 1
    chars = _characters_soft(ws)
    snap = {
        "through_chapter": n,
        "total_words": words,
        "characters_count": len(chars) if isinstance(chars, (dict, list)) else None,
        "foreshadowing": {"total": len(items), "by_status": by_status},
        "created_at": _now(),
    }
    atomic_write_json(ws.path("memory_snapshot", n=n), snap)
    return {"summary": f"快照已保存（截至第 {n} 章，{words} 字）"}


def garble_reasons_of(path: Optional[Path]) -> List[str]:
    """文件的乱码理由；无乱码或文件不存在返回空列表。"""
    from novel_writer.garble import is_garbled
    if path is None or not path.is_file():
        return []
    report = is_garbled(read_text_utf8(path))
    return report.reasons if report.garbled else []


def h_best_pick_prep(ws: Workspace, step: S.StepDef, settings: Dict[str, Any], invoker=None) -> Dict[str, Any]:
    """精品档择优·准备：对正文（A）与候选稿（B）各做一次机器校验，结果供 LLM 择优时参考。"""
    n = step.chapter
    a_path = chapter_file(ws, n)
    b_path = ws.path("candidate_alt", nn=n)
    if a_path is None:
        raise ChapterOpError(f"第 {n} 章正文缺失，无法择优")
    if not b_path.is_file():
        raise ChapterOpError(f"第 {n} 章没有候选稿，无法择优")
    a = machine_findings(ws, n, settings, path=a_path)
    b = machine_findings(ws, n, settings, path=b_path)
    a_garble = garble_reasons_of(a_path)
    b_garble = garble_reasons_of(b_path)
    atomic_write_json(ws.path("best_pick_check", nn=n), {
        "chapter": n, "checked_at": _now(),
        "A": {"hard": a["hard"], "warn": a["warn"], "words": a.get("words"), "quota": a.get("quota"), "garble": a_garble},
        "B": {"hard": b["hard"], "warn": b["warn"], "words": b.get("words"), "quota": b.get("quota"), "garble": b_garble},
    })
    return {"summary": f"机检完成：A 硬问题 {len(a['hard'])} 条，B 硬问题 {len(b['hard'])} 条"}


def h_best_pick_apply(ws: Workspace, step: S.StepDef, settings: Dict[str, Any], invoker=None) -> Dict[str, Any]:
    """精品档择优·应用：按 LLM 的择优报告决定正文。选 B 则以候选稿替换正文；选 A 则保持不变。

    报告格式不对时直接失败（不猜测），以免误改正文。
    """
    n = step.chapter
    a_path = chapter_file(ws, n)
    b_path = ws.path("candidate_alt", nn=n)
    report = read_json(ws.path("best_pick_report", nn=n), default=None)
    winner = V.normalize_pick_winner(report.get("winner")) if isinstance(report, dict) else None
    if winner is None:
        # 正常情况下前一步的内容校验已保证合格；走到这里说明报告被改动或校验被绕过
        detail = "报告缺失" if report is None else f"winner={report.get('winner')!r}" if isinstance(report, dict) else "报告不是 JSON 对象"
        raise ChapterOpError(f"择优报告不合格（{detail}），无法应用择优结果")
    # 乱码稿不参与择优：LLM 选了乱码的一方，改用另一方（两方都乱码则挂起给作者）
    a_bad = bool(garble_reasons_of(a_path))
    b_bad = bool(garble_reasons_of(b_path))
    if winner == "B" and b_bad and not a_bad:
        _log(ws, n, "择优：LLM 选了乱码的候选稿 B，已改用正文 A（B 未通过乱码检测）")
        winner = "A"
    elif winner == "A" and a_bad and not b_bad:
        _log(ws, n, "择优：正文 A 乱码，已改用候选稿 B（B 通过乱码检测）")
        winner = "B"
    elif a_bad and b_bad:
        from novel_writer.runner import NeedsUser
        raise NeedsUser(
            f"第 {n} 章正文与候选稿都疑似乱码，无法择优，已暂停，下一章不会开始。",
            [{"id": "decision", "question": "请选择：", "options": GARBLE_OPTIONS}],
        )
    if winner == "B":
        if not b_path.is_file():
            raise ChapterOpError("择优选择了候选稿 B，但候选稿文件不存在")
        atomic_write_text(a_path, read_text_utf8(b_path))
    reason = str(report.get("reason") or "").strip()[:80]
    _log(ws, n, f"择优（LLM）：采用{winner}稿" + (f"，理由：{reason}" if reason else ""))
    return {"summary": f"择优结果：采用{winner}稿" + (f"（{reason}）" if reason else "")}


def h_patrol_apply(ws: Workspace, step: S.StepDef, settings: Dict[str, Any], invoker=None) -> Dict[str, Any]:
    """巡检整改项分流（确定性）：校验提取结果并入账本；默认自动采纳后续约束与避免套路。"""
    from novel_writer import patrol_findings as PF
    from novel_writer import validators as V
    n = int((step.extra or {}).get("through_chapter") or 0)
    path = ws.path("patrol_findings", nn=n)
    if not path.is_file():
        raise ChapterOpError(f"第 {n} 章后的巡检整改项文件不存在：{path.name}。请先运行 patrol_extract:{n}")
    data, err, _fenced = V._read_json_loose(path)
    if err is not None:
        raise ChapterOpError(f"巡检整改项不是合法 JSON（{err}）：{path.name}")
    probs = PF.problems(data)
    if probs:
        raise ChapterOpError("巡检整改项不合格：" + "；".join(probs[:5]))
    res = PF.apply_extract(ws, n, data, PF.feedback_settings(settings)["auto_adopt"])
    return {"summary": f"整改项 {res['total']} 条，自动采纳 {res['adopted']} 条（其余为蓝图待办/回改建议，进收件箱，不阻塞流水线）"}


def h_act_apply(ws: Workspace, step: S.StepDef, settings: Dict[str, Any], invoker=None) -> Dict[str, Any]:
    """幕末整改项分流（确定性）：校验提取结果并入整改项账本（与巡检共用，来源标为 act:N）。"""
    from novel_writer import patrol_findings as PF
    from novel_writer import validators as V
    act = int((step.extra or {}).get("act") or 0)
    end = int((step.extra or {}).get("through_chapter") or 0)
    path = ws.path("act_findings", act=act)
    if not path.is_file():
        raise ChapterOpError(f"第 {act} 幕整改项文件不存在：{path.name}。请先运行 act_extract:{act}")
    data, err, _fenced = V._read_json_loose(path)
    if err is not None:
        raise ChapterOpError(f"第 {act} 幕整改项不是合法 JSON（{err}）：{path.name}")
    probs = PF.problems(data)
    if probs:
        raise ChapterOpError("幕末整改项不合格：" + "；".join(probs[:5]))
    res = PF.apply_extract(ws, end, data, PF.feedback_settings(settings)["auto_adopt"], source=f"act:{act}")
    return {"summary": f"第 {act} 幕整改项 {res['total']} 条，自动采纳 {res['adopted']} 条（其余进收件箱，不阻塞流水线）"}


from novel_writer import act_planning as AP  # noqa: E402

CHAPTER_HANDLERS = {
    "blueprint_extend": AP.h_blueprint_extend,
    "act_apply": h_act_apply,
    "patrol_apply": h_patrol_apply,
    "snapshot": h_snapshot,
    "word_quota": h_word_quota,
    "plot_tasks": h_plot_tasks,
    "context_pack": h_context_pack,
    "style_hint": h_style_hint,
    "garble_guard": h_garble_guard,
    "garble_guard_alt": h_garble_guard_alt,
    "plot_merge": h_plot_merge,
    "characters_refresh": h_characters_refresh,
    "blueprint_split": h_blueprint_split,
    "plot_import": h_plot_import,
    "machine_check": h_machine_check,
    "revise_loop": h_revise_loop,
    "best_pick_prep": h_best_pick_prep,
    "best_pick_apply": h_best_pick_apply,
}
