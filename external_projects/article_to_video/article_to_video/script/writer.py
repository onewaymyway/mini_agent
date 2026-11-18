"""script/writer.py — S3 分章口播稿：写稿、规范化、校验重试、落盘、编辑保存、确认（C1）。

每章一个文件 parts/<章ID>/script.json；输入未变时复用（断点续跑）。
校验错误（V2/V3）与节奏问题各自带问题清单重试，最多 MAX_ATTEMPTS 次；
仍只剩节奏问题时接受并给出警告（设计文档：超限留给人工）；仍有硬错误则抛 PlanError，不写结果。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import cancel, joblog
from ..planning.errors import PlanError
from ..planning.jsonio import parse_json_object
from ..planning.runner import load_spine
from ..project_store import append_progress, load_project, now_iso, read_json, write_json_atomic
from .budget import chars_for_minutes
from .lexicon import apply_lexicon, load_lexicon
from .validate import canonicalize_quotes, validate_script

MAX_ATTEMPTS = 3
MAX_PART_CHARS = 12000
PROMPT_VERSION = "s3-v1"
PACE_VALUES = ("normal", "slow", "fast")

SCRIPT_SYSTEM = (
    "你是讲解视频的口播稿作者。只输出一个 JSON 对象，不要任何解释、前言或 Markdown 围栏。"
    "口语化：一句一个意思，单句不超过 30 字；不得添加原文没有的事实、数字或英文词；"
    "金句必须逐字复制原文；公式用文字口述。"
)

SCRIPT_SCHEMA = """{
  "beats": [
    {"role": "hook|claim|example|turn|summary",
     "source_refs": ["本章的小节ID"],
     "sentences": [{"text": "一句口播", "quote": false, "emphasis": ["句中要强调的词"]}],
     "pace": "normal|slow|fast",
     "pause_after_ms": 300}
  ]
}"""


def _retry_hint(issues: List[str]) -> str:
    return "\n\n【上次输出的问题，必须修正后重新输出完整 JSON】\n" + "\n".join(f"- {x}" for x in issues) + "\n"


def _part_source(project_dir: Path, section_ids: List[str]) -> str:
    lines = (project_dir / "source" / "article.md").read_text(encoding="utf-8").splitlines()
    doc = read_json(project_dir / "source" / "blocks.json")
    wanted = set(section_ids)
    chunks: List[str] = []
    for b in doc["blocks"]:
        if b["section"] in wanted:
            chunks.append("\n".join(lines[b["line_start"] - 1:b["line_end"]]))
    return "\n".join(chunks)


def _part_index(spine: Dict[str, Any], part_id: str) -> int:
    for i, p in enumerate(spine["parts"]):
        if p["id"] == part_id:
            return i
    raise PlanError(f"大纲中没有章 {part_id}")


def _script_path(project_dir: Path, part_id: str) -> Path:
    return project_dir / "parts" / part_id / "script.json"


def _load_script(project_dir: Path, part_id: str) -> Optional[Dict[str, Any]]:
    p = _script_path(project_dir, part_id)
    return read_json(p) if p.is_file() else None


def build_prompt(part: Dict[str, Any], source: str, *, persona: str, budget_chars: int,
                 glossary: List[Dict[str, str]], prev_tail: str, issues: List[str]) -> str:
    gl = "\n".join(f"- {g['term']} → {g.get('zh', '')}" for g in glossary if g.get("term")) or "（无）"
    body = (
        "【口播稿任务】\n"
        f"PART_ID={part['id']}\n"
        f"SECTION_IDS={json.dumps(part['section_ids'], ensure_ascii=False)}\n"
        f"BUDGET_CHARS={budget_chars}\n"
        f"PERSONA={persona}\n"
        f"章标题：{part['title']}　本章角色：{part.get('role', '')}　一句话：{part.get('one_line', '')}\n"
        f"上一章结尾（衔接用，不要重复）：{prev_tail or '（本章是第一章）'}\n\n"
        "术语统一译法：\n" + gl + "\n\n"
        f"请把本章原文改写为口播稿，输出如下结构的 JSON（字数接近 BUDGET_CHARS，允许 ±15%）：\n{SCRIPT_SCHEMA}\n\n"
        "要求：\n"
        "1. 每个 beat 的 source_refs 只能使用 SECTION_IDS 中的小节。\n"
        "2. 数字、年份、英文词必须来自本章原文，不得新增。\n"
        "3. 标记 quote=true 的句子必须是原文逐字片段，用于金句与引文。\n"
        "4. 不要 Markdown 符号，不要公式符号。\n"
        "5. 开场第一个 beat 用 hook（抛问题或反差）；结尾用 summary 回扣。\n\n"
        "【本章原文】\n" + source
    )
    return body + (_retry_hint(issues) if issues else "")


def normalize_script(obj: Dict[str, Any], part: Dict[str, Any], *, persona: str,
                     budget_chars: int, budget_minutes: float, lexicon: Dict[str, str]) -> Dict[str, Any]:
    beats: List[Dict[str, Any]] = []
    for i, b in enumerate(obj.get("beats") or []):
        if not isinstance(b, dict):
            continue
        sentences = []
        for s in b.get("sentences") or []:
            if isinstance(s, str):
                s = {"text": s}
            if not isinstance(s, dict):
                continue
            text = str(s.get("text", "")).strip()
            emphasis = [str(e) for e in s.get("emphasis") or [] if str(e) and str(e) in text]
            sentences.append({
                "text": text,
                "tts": apply_lexicon(text, lexicon),
                "emphasis": emphasis,
                "quote": bool(s.get("quote", False)),
            })
        pace = str(b.get("pace") or "normal")
        beats.append({
            "id": f"{part['id']}.b{i + 1:02d}",
            "role": str(b.get("role") or "claim"),
            "source_refs": [str(x) for x in b.get("source_refs") or []],
            "sentences": sentences,
            "pace": pace if pace in PACE_VALUES else "normal",
            "pause_after_ms": max(0, min(2000, int(b.get("pause_after_ms") or 300))),
        })
    return {
        "schema": 1,
        "part": part["id"],
        "title": part["title"],
        "persona": persona,
        "budget_minutes": budget_minutes,
        "budget_chars": budget_chars,
        "beats": beats,
        "confirmed": False,
        "engine": "llm",
    }


def _update_index(project_dir: Path, part_id: str, script: Dict[str, Any], v: Dict[str, Any]) -> None:
    idx_path = project_dir / "parts" / "index.json"
    index = read_json(idx_path) if idx_path.is_file() else {}
    index[part_id] = {
        "title": script["title"],
        "confirmed": bool(script.get("confirmed")),
        "sentences": sum(len(b["sentences"]) for b in script["beats"]),
        "chars": v["total_chars"],
        "budget_chars": script["budget_chars"],
        "warnings": len(v["warnings"]) + (1 if v.get("pacing") else 0),
    }
    write_json_atomic(idx_path, index)


def write_part(project_dir: Path, part_id: str, llm, *, force: bool = False,
               now: Optional[datetime] = None) -> Dict[str, Any]:
    project_dir = Path(project_dir)
    spine = load_spine(project_dir)
    if spine is None:
        raise PlanError("还没有大纲，请先在「🗺️ 大纲」生成并确认")
    if not spine.get("confirmed"):
        raise PlanError("请先确认大纲（C0），再写口播稿")
    part = spine["parts"][_part_index(spine, part_id)]
    from ..app_settings import load_effective_project  # 延迟导入
    settings_eff = load_effective_project(project_dir)["settings"]
    persona = settings_eff.get("persona", "host")
    mode = settings_eff.get("mode", "condensed")
    budget_minutes = float(part["budget_minutes"])
    budget_chars = chars_for_minutes(budget_minutes)
    glossary = spine.get("glossary") or []
    lexicon = load_lexicon(project_dir)

    source = _part_source(project_dir, part["section_ids"])
    if len(source) > MAX_PART_CHARS:
        raise PlanError(f"{part_id} 原文 {len(source)} 字，超过单次写稿上限 {MAX_PART_CHARS} 字；"
                        "请在大纲页把本章拆开")

    prev_idx = _part_index(spine, part_id) - 1
    prev_tail = ""
    if prev_idx >= 0:
        prev = _load_script(project_dir, spine["parts"][prev_idx]["id"])
        if prev and prev["beats"] and prev["beats"][-1]["sentences"]:
            prev_tail = prev["beats"][-1]["sentences"][-1]["text"]

    sha = hashlib.sha256("|".join([
        PROMPT_VERSION, json.dumps(part, ensure_ascii=False, sort_keys=True), source, persona,
        str(budget_chars), json.dumps(glossary, ensure_ascii=False, sort_keys=True),
        json.dumps(lexicon, ensure_ascii=False, sort_keys=True), prev_tail, mode,
    ]).encode("utf-8")).hexdigest()
    existing = _load_script(project_dir, part_id)
    if existing and existing.get("input_sha") == sha and not force:
        joblog.bound_log(f"{part_id}：输入未变，复用已有口播稿")
        return existing

    issues: List[str] = []
    raw = ""
    script: Dict[str, Any] = {}
    verdict: Dict[str, Any] = {}
    accepted = False
    for attempt in range(MAX_ATTEMPTS):
        cancel.check()
        prompt = build_prompt(part, source, persona=persona, budget_chars=budget_chars,
                              glossary=glossary, prev_tail=prev_tail, issues=issues)
        joblog.bound_log(f"{part_id}：调用模型写稿（第 {attempt + 1} 次，预算 {budget_chars} 字）")
        raw = llm.ask(prompt, system=SCRIPT_SYSTEM)
        cancel.check()
        verdict = {}  # 每次尝试都重新判断，避免沿用上一次的结果
        try:
            obj = parse_json_object(raw)
        except ValueError as exc:
            issues = [f"JSON 解析失败：{exc}"]
            joblog.bound_log(f"{part_id}：输出不是有效 JSON，将重试", level="warn")
            continue
        script = normalize_script(obj, part, persona=persona, budget_chars=budget_chars,
                                  budget_minutes=budget_minutes, lexicon=lexicon)
        canonicalize_quotes(script, source)                 # R9：金句对齐到原文的规范写法
        verdict = validate_script(script, source, part["section_ids"], check_pacing=(mode == "condensed"))
        if verdict["errors"]:
            issues = verdict["errors"][:12]
            joblog.bound_log(f"{part_id}：稿件未通过校验（{issues[0]}），将重试", level="warn")
            continue
        if verdict["pacing"] and attempt < MAX_ATTEMPTS - 1:
            issues = [verdict["pacing"]]
            joblog.bound_log(f"{part_id}：节奏不达标，将重试", level="warn")
            continue
        accepted = True  # 无硬错误；节奏问题若仍在，接受并记为警告
        joblog.bound_log(f"{part_id}：稿件通过校验（{verdict['total_chars']} 字）", level="ok")
        break

    if not accepted:
        if raw:
            raw_path = _script_path(project_dir, part_id).parent / "raw_last.txt"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(raw, encoding="utf-8")
        detail = "；".join((verdict.get("errors") or issues)[:6])
        raise PlanError(f"{part_id} 口播稿 {MAX_ATTEMPTS} 次输出仍不合格：{detail}", raw=raw)

    script["input_sha"] = sha
    script["warnings"] = verdict["warnings"] + ([verdict["pacing"]] if verdict.get("pacing") else [])
    script["total_chars"] = verdict["total_chars"]
    write_json_atomic(_script_path(project_dir, part_id), script)
    _update_index(project_dir, part_id, script, verdict)
    append_progress(project_dir, f"写口播稿 {part_id}（{verdict['total_chars']} 字，预算 {budget_chars}）", now)
    return script


def write_all(project_dir: Path, llm, *, force: bool = False, now: Optional[datetime] = None) -> List[str]:
    """按章顺序写稿；已有且未变的章复用。返回写完的章 ID。"""
    spine = load_spine(Path(project_dir))
    if spine is None:
        raise PlanError("还没有大纲")
    done = []
    for p in spine["parts"]:
        cancel.check()
        write_part(project_dir, p["id"], llm, force=force, now=now)
        done.append(p["id"])
    project = load_project(Path(project_dir))
    project["stage"] = "P2_scripted"
    write_json_atomic(Path(project_dir) / "project.json", project)
    return done


def load_script(project_dir: Path, part_id: str) -> Optional[Dict[str, Any]]:
    return _load_script(Path(project_dir), part_id)


def save_script(project_dir: Path, part_id: str, script: Dict[str, Any],
                now: Optional[datetime] = None) -> Dict[str, Any]:
    """保存人工编辑。校验失败不写入；保存后本章变为未确认。"""
    project_dir = Path(project_dir)
    spine = load_spine(project_dir)
    if spine is None:
        raise PlanError("还没有大纲")
    part = spine["parts"][_part_index(spine, part_id)]
    project = load_project(project_dir)
    lexicon = load_lexicon(project_dir)
    budget_minutes = float(part["budget_minutes"])
    script = normalize_script(script, part, persona=project.get("settings", {}).get("persona", "host"),
                              budget_chars=chars_for_minutes(budget_minutes),
                              budget_minutes=budget_minutes, lexicon=lexicon)
    source = _part_source(project_dir, part["section_ids"])
    canonicalize_quotes(script, source)                     # R9：人工编辑的金句同样对齐原文
    verdict = validate_script(script, source, part["section_ids"],
                              check_pacing=project.get("settings", {}).get("mode") == "condensed")
    if verdict["errors"]:
        raise PlanError("口播稿不合格：" + "；".join(verdict["errors"][:8]))
    old = _load_script(project_dir, part_id)
    script["input_sha"] = old.get("input_sha", "") if old else ""
    script["warnings"] = verdict["warnings"] + ([verdict["pacing"]] if verdict.get("pacing") else [])
    script["total_chars"] = verdict["total_chars"]
    write_json_atomic(_script_path(project_dir, part_id), script)
    _update_index(project_dir, part_id, script, verdict)
    append_progress(project_dir, f"保存口播稿编辑 {part_id}（未确认）", now)
    return script


def confirm_script(project_dir: Path, part_id: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """C1：本章口播通过校验后确认。"""
    project_dir = Path(project_dir)
    script = _load_script(project_dir, part_id)
    if script is None:
        raise PlanError(f"{part_id} 还没有口播稿")
    spine = load_spine(project_dir)
    part = spine["parts"][_part_index(spine, part_id)]
    verdict = validate_script(script, _part_source(project_dir, part["section_ids"]),
                              part["section_ids"], check_pacing=False)
    if verdict["errors"]:
        raise PlanError("口播稿不合格，不能确认：" + "；".join(verdict["errors"][:6]))
    script["confirmed"] = True
    script["confirmed_at"] = now_iso(now)
    write_json_atomic(_script_path(project_dir, part_id), script)
    _update_index(project_dir, part_id, script, verdict)
    append_progress(project_dir, f"确认口播稿（C1）{part_id}", now)
    return script
