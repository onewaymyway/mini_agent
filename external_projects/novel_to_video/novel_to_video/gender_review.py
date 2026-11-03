"""gender_review.py — 性别建议的落盘与人工确认。

  * suggest_all：对全部角色生成建议，写入 global/gender_suggestions.json（只读材料，不改角色）；
  * confirm：用户选择 male / female / unknown，才写入角色的 gender，gender_source = user；
  * 门禁：gender_source 只有 user 或 persona 才算“已确认”；由 LLM 或规则写入的性别不放行。

确认记录保留在 gender_suggestions.json 的 decisions 中，包括时间与用户选择的依据，便于追溯。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import gender_evidence as ge
from .project_store import append_progress, read_json, write_json_atomic
from .source_import import import_source
from .name_normalize import decode_name
from . import joblog

SUGGEST_FILE = "global/gender_suggestions.json"
# user：人工确认；persona：人设卡明确写明；auto：补建角色由 C-5 自动推断（证据或 LLM 二选一），可随时人工覆盖
CONFIRMED_SOURCES = ("user", "persona", "auto")
CHOICES = ("male", "female", "unknown")


def _path(project_dir: Path) -> Path:
    return project_dir / SUGGEST_FILE


def _load_texts(project_dir: Path) -> Dict[str, Any]:
    """从工作目录读取正文与人设卡（转义导出时为项目内规范化副本）。"""
    from .project_store import working_source_dir
    src = working_source_dir(project_dir)
    chapters = []
    chap_dir = src / "正文"
    for f in sorted(chap_dir.glob("*.md")) if chap_dir.is_dir() else []:
        chapters.append((decode_name(f.name)[:6], f.read_text(encoding="utf-8")))
    persona = []
    pdir = src / "设定" / "人物" / "人设卡"
    for f in sorted(pdir.glob("*.md")) if pdir.is_dir() else []:
        persona.append((decode_name(f.name), f.read_text(encoding="utf-8")))
    return {"chapters": chapters, "persona": persona}


def suggest_all(project_dir: Path) -> Dict[str, Any]:
    chars = read_json(project_dir / "global" / "characters.json")["characters"]
    joblog.log(project_dir, "gender", f"性别线索检索开始：{len(chars)} 个角色")
    texts = _load_texts(project_dir)
    joblog.log(project_dir, "gender", f"已读取正文 {len(texts['chapters'])} 章、人设卡 {len(texts['persona'])} 份")
    out = {}
    for c in chars:
        sug = ge.suggest(c, texts["chapters"], texts["persona"])
        out[c["id"]] = sug.to_dict()
        joblog.log(project_dir, "gender",
                   f"{c['names'][0]}：建议 {sug.suggested}，证据 {sug.evidence_total} 条（{sug.basis}）")
    prev = read_json(_path(project_dir)) if _path(project_dir).is_file() else {}
    data = {"generated_at": datetime.now().isoformat(timespec="seconds"),
            "suggestions": out,
            "decisions": prev.get("decisions", {})}
    write_json_atomic(_path(project_dir), data)
    return data


def load(project_dir: Path) -> Dict[str, Any]:
    p = _path(project_dir)
    return read_json(p) if p.is_file() else {"suggestions": {}, "decisions": {}}


def confirm(project_dir: Path, char_id: str, gender: str, *, note: str = "") -> Dict[str, Any]:
    if gender not in CHOICES:
        raise ValueError(f"性别只能是 {', '.join(CHOICES)}")
    chars_doc = read_json(project_dir / "global" / "characters.json")
    ent = next((c for c in chars_doc["characters"] if c["id"] == char_id), None)
    if ent is None:
        raise KeyError(f"角色不存在：{char_id}")
    sug = load(project_dir).get("suggestions", {}).get(char_id, {})
    if gender == "unknown":
        ent["gender"] = None
        ent["gender_source"] = None
    else:
        ent["gender"] = gender
        ent["gender_source"] = "user"
    write_json_atomic(project_dir / "global" / "characters.json", chars_doc)

    data = load(project_dir)
    data.setdefault("decisions", {})[char_id] = {
        "choice": gender, "suggested": sug.get("suggested"),
        "agreed": gender == sug.get("suggested"), "note": note.strip(),
        "at": datetime.now().isoformat(timespec="seconds"),
    }
    write_json_atomic(_path(project_dir), data)
    append_progress(project_dir, f"性别确认：{ent['names'][0]}（{char_id}）→ {gender}"
                                 f"{'，与建议不同' if gender != sug.get('suggested') else ''}")
    return data["decisions"][char_id]


def pending(project_dir: Path) -> List[str]:
    """尚未由用户确认性别的角色 id（不含非人类实体，它们也需确认，但单独标注）。"""
    chars = read_json(project_dir / "global" / "characters.json")["characters"]
    return [c["id"] for c in chars if c.get("gender_source") not in CONFIRMED_SOURCES]
