"""novel_to_video/source_import.py — 只读导入 novel_writer 导出目录。

输入：`小说_YYYYMMDD_HHMMSS/` 导出目录（正文/、设定/、章节总结/、README.md）。
输出：ImportResult，包含章节清单、全书角色库、地点库、问题清单与目录指纹。
本模块**不写入任何文件**，落盘由 project_store 负责。

问题分级：
  error   阻断导入（缺正文目录、章节号重复、角色 id 重复等）
  warning 可导入，但用户需要知悉（章节缺号、角色缺性别、导出清洗提示等）
  info    仅记录
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

LEVEL_ERROR = "error"
LEVEL_WARNING = "warning"
LEVEL_INFO = "info"

CHAPTER_FILE_RE = re.compile(r"^第(\d+)章\s*(.*)\.md$")
SUMMARY_FILE_RE = re.compile(r"第(\d+)章")
HEADING_CHAPTER_RE = re.compile(r"^#\s*第\s*(\d+)\s*章\s*(.*)$")
TIMESTAMP_SUFFIX_RE = re.compile(r"_\d{8}_\d{6}$")
FRONT_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s?(.*)$")
TABLE_ID_RE = re.compile(r"^\|\s*(char_\d+)\s*\|", re.M)
GENDER_RE = re.compile(r"性别[：:]\s*(男|女)")
FIRST_APPEAR_CH_RE = re.compile(r"第\s*(\d+)\s*章")
GENDER_MAP = {"男": "male", "女": "female"}

MIN_CHAPTER_CHARS = 300          # 非空白字数低于此值提示章节可能残缺
PERSONA_SECTION_MAX = 1200       # 人设卡“外在呈现”段落摘入上限


@dataclass
class ImportIssue:
    level: str
    code: str
    message: str
    path: Optional[str] = None


@dataclass
class ImportResult:
    source_dir: str
    source_title: str
    fingerprint: str
    chapters: List[Dict[str, Any]] = field(default_factory=list)
    characters: List[Dict[str, Any]] = field(default_factory=list)
    locations: List[Dict[str, Any]] = field(default_factory=list)
    issues: List[ImportIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.level == LEVEL_ERROR for i in self.issues)

    @property
    def stats(self) -> Dict[str, int]:
        return {
            "chapters": len(self.chapters),
            "characters": len(self.characters),
            "locations": len(self.locations),
            "chapter_chars": sum(c["char_count"] for c in self.chapters),
            "errors": sum(1 for i in self.issues if i.level == LEVEL_ERROR),
            "warnings": sum(1 for i in self.issues if i.level == LEVEL_WARNING),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "source_dir": self.source_dir,
            "source_title": self.source_title,
            "fingerprint": self.fingerprint,
            "stats": self.stats,
            "issues": [asdict(i) for i in self.issues],
            "chapters": self.chapters,
            "characters": self.characters,
            "locations": self.locations,
        }


# ---------------------------------------------------------------- 基础工具

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _nonspace_len(text: str) -> int:
    return len(re.sub(r"\s", "", text))


def _heading(text: str) -> Optional[str]:
    for line in text.splitlines():
        if line.startswith("# "):
            return line
    return None


def _parse_value(raw: str) -> Any:
    raw = raw.strip()
    if raw == "":
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw.strip('"')


def parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """解析 novel_writer 角色卡的 `key: value` 头部（value 多为 JSON）。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, text
    meta: Dict[str, Any] = {}
    for line in lines[1:end]:
        m = FRONT_LINE_RE.match(line)
        if m:
            meta[m.group(1)] = _parse_value(m.group(2))
    return meta, "\n".join(lines[end + 1:])


def _section(text: str, title: str, limit: int = PERSONA_SECTION_MAX) -> str:
    """取 Markdown 中 `## title` 到下一个 `## ` 之间的正文。"""
    lines = text.splitlines()
    out: List[str] = []
    grab = False
    for line in lines:
        if line.startswith("## "):
            if grab:
                break
            grab = title in line
            continue
        if grab:
            out.append(line)
    return "\n".join(out).strip()[:limit]


def _bullet_value(text: str, key: str) -> Optional[str]:
    m = re.search(rf"^\s*-\s*\*\*{re.escape(key)}\*\*[：:]\s*(.+)$", text, re.M)
    return m.group(1).strip() if m else None


def _age_range(age: Any) -> Optional[str]:
    if not isinstance(age, (int, float)) or isinstance(age, bool):
        return None
    if age < 13:
        return "child"
    if age < 18:
        return "teen"
    if age < 35:
        return "youth"
    if age < 60:
        return "middle_aged"
    return "elderly"


def _fingerprint(root: Path) -> str:
    """对导出目录内全部 .md/.txt 的相对路径与大小做 sha1，前 12 位。"""
    h = hashlib.sha1()
    files = sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix in (".md", ".txt")
    )
    for p in files:
        h.update(f"{_rel(root, p)}:{p.stat().st_size}\n".encode("utf-8"))
    return "sha1:" + h.hexdigest()[:12]


# ---------------------------------------------------------------- 章节

def _load_chapters(root: Path, issues: List[ImportIssue]) -> List[Dict[str, Any]]:
    chap_dir = root / "正文"
    if not chap_dir.is_dir():
        issues.append(ImportIssue(LEVEL_ERROR, "MISSING_CHAPTERS",
                                  "缺少 正文/ 目录，不是有效的 novel_writer 导出目录", "正文"))
        return []

    summaries: Dict[int, Path] = {}
    sum_dir = root / "章节总结"
    if sum_dir.is_dir():
        for p in sorted(sum_dir.glob("*.md")):
            m = SUMMARY_FILE_RE.search(p.name)
            if m:
                summaries[int(m.group(1))] = p

    chapters: List[Dict[str, Any]] = []
    seen: Dict[int, str] = {}
    for p in sorted(chap_dir.glob("*.md")):
        rel = _rel(root, p)
        m = CHAPTER_FILE_RE.match(p.name)
        if not m:
            issues.append(ImportIssue(LEVEL_WARNING, "UNRECOGNIZED_CHAPTER_FILE",
                                      f"无法识别章节文件名，已跳过：{p.name}", rel))
            continue
        num = int(m.group(1))
        if num in seen:
            issues.append(ImportIssue(LEVEL_ERROR, "DUPLICATE_CHAPTER",
                                      f"章节号 {num} 重复：{seen[num]} 与 {rel}", rel))
            continue
        seen[num] = rel

        text = _read(p)
        title = m.group(2).strip()
        head = _heading(text)
        hm = HEADING_CHAPTER_RE.match(head) if head else None
        if hm:
            if int(hm.group(1)) != num:
                issues.append(ImportIssue(LEVEL_WARNING, "CHAPTER_HEADING_MISMATCH",
                                          f"文件名章号 {num} 与正文标题章号 {hm.group(1)} 不一致", rel))
            if hm.group(2).strip():
                title = hm.group(2).strip()

        chars = _nonspace_len(text)
        if chars < MIN_CHAPTER_CHARS:
            issues.append(ImportIssue(LEVEL_WARNING, "SHORT_CHAPTER",
                                      f"第 {num} 章只有 {chars} 字，可能不完整", rel))
        sp = summaries.get(num)
        chapters.append({
            "no": num,
            "title": title,
            "source_file": rel,
            "summary_file": _rel(root, sp) if sp else None,
            "char_count": chars,
        })

    chapters.sort(key=lambda c: c["no"])
    if chapters:
        nums = {c["no"] for c in chapters}
        gaps = [n for n in range(1, max(nums) + 1) if n not in nums]
        if gaps:
            issues.append(ImportIssue(LEVEL_WARNING, "CHAPTER_GAP",
                                      f"章节号不连续，缺失：{', '.join(map(str, gaps))}"))
        no_sum = [c["no"] for c in chapters if c["summary_file"] is None]
        if no_sum:
            issues.append(ImportIssue(LEVEL_INFO, "NO_SUMMARY",
                                      f"{len(no_sum)} 章没有章节总结（不影响导入）"))
    else:
        issues.append(ImportIssue(LEVEL_ERROR, "NO_CHAPTERS", "正文/ 中没有可识别的章节文件", "正文"))
    return chapters


# ---------------------------------------------------------------- 角色

def _describe_character(identity: Dict[str, Any], persona_text: str) -> str:
    parts: List[str] = []
    if identity.get("one_line_essence"):
        parts.append(f"定位：{identity['one_line_essence']}")
    if identity.get("appearance"):
        parts.append(f"外貌：{identity['appearance']}")
    if identity.get("signature_detail"):
        parts.append(f"标志：{identity['signature_detail']}")
    text = "；".join(parts)
    persona_look = _section(persona_text, "外在呈现") if persona_text else ""
    if persona_look:
        text = (text + "\n" if text else "") + f"外在呈现（人设卡）：\n{persona_look}"
    return text


def _load_characters(root: Path, issues: List[ImportIssue]) -> List[Dict[str, Any]]:
    char_dir = root / "设定" / "人物" / "角色"
    persona_dir = root / "设定" / "人物" / "人设卡"
    if not char_dir.is_dir():
        issues.append(ImportIssue(LEVEL_ERROR, "MISSING_CHARACTERS",
                                  "缺少 设定/人物/角色/ 目录", "设定/人物/角色"))
        return []

    files = sorted(char_dir.glob("char_*.md"))
    index_path = root / "设定" / "人物" / "角色索引.md"
    if index_path.is_file():
        indexed = set(TABLE_ID_RE.findall(_read(index_path)))
        actual = {p.stem for p in files}
        if indexed != actual:
            only_idx = sorted(indexed - actual)
            only_files = sorted(actual - indexed)
            issues.append(ImportIssue(
                LEVEL_WARNING, "INDEX_MISMATCH",
                f"角色索引与角色文件不一致：仅索引有 {only_idx or '无'}；仅文件有 {only_files or '无'}",
                "设定/人物/角色索引.md"))

    chars: List[Dict[str, Any]] = []
    seen: set = set()
    no_gender: List[str] = []
    for p in files:
        rel = _rel(root, p)
        meta, body = parse_frontmatter(_read(p))
        cid = str(meta.get("id") or p.stem)
        if cid in seen:
            issues.append(ImportIssue(LEVEL_ERROR, "DUPLICATE_CHARACTER", f"角色 id 重复：{cid}", rel))
            continue
        seen.add(cid)

        name = str(meta.get("name") or "").strip()
        if not name:
            issues.append(ImportIssue(LEVEL_WARNING, "MISSING_CHARACTER_NAME",
                                      f"角色 {cid} 缺少 name 字段", rel))
            name = p.stem

        identity = meta.get("identity") if isinstance(meta.get("identity"), dict) else {}
        psychology = meta.get("psychology") if isinstance(meta.get("psychology"), dict) else {}

        persona_path = persona_dir / f"{name}.md"
        persona_text = _read(persona_path) if persona_path.is_file() else ""
        gm = GENDER_RE.search(persona_text) or GENDER_RE.search(body)
        gender = GENDER_MAP[gm.group(1)] if gm else None
        if gender is None:
            no_gender.append(name)

        chars.append({
            "id": cid,
            "names": [name],
            "role": meta.get("role"),
            "age": identity.get("age"),
            "age_range": _age_range(identity.get("age")),
            "gender": gender,
            "description_zh": _describe_character(identity, persona_text),
            "speech_pattern": psychology.get("speech_pattern"),
            "first_appear": meta.get("introduced_chapter"),
            "relations": [],
            "visual_anchor_en": None,
            "voice_profile": None,
            "asset_path": None,
            "appearance_variants": [],
            "persona_card": _rel(root, persona_path) if persona_text else None,
            "character_file": rel,
            "source": "novel_writer",
        })

    if no_gender:
        preview = "、".join(no_gender[:6]) + ("…" if len(no_gender) > 6 else "")
        issues.append(ImportIssue(LEVEL_WARNING, "MISSING_GENDER",
                                  f"{len(no_gender)} 个角色的导出数据中没有性别字段（{preview}），需在 G-2 补全"))
    chars.sort(key=lambda c: c["id"])
    return chars


# ---------------------------------------------------------------- 地点

def _load_locations(root: Path, issues: List[ImportIssue]) -> List[Dict[str, Any]]:
    loc_dir = root / "设定" / "世界观" / "地点详情"
    if not loc_dir.is_dir():
        issues.append(ImportIssue(LEVEL_WARNING, "MISSING_LOCATIONS",
                                  "缺少 设定/世界观/地点详情/ 目录，地点库为空", "设定/世界观"))
        return []
    locs: List[Dict[str, Any]] = []
    for i, p in enumerate(sorted(loc_dir.glob("*.md")), start=1):
        rel = _rel(root, p)
        text = _read(p)
        head = _heading(text)
        name = head[2:].strip() if head else p.stem
        first = _bullet_value(text, "首次出现") or ""
        fm = FIRST_APPEAR_CH_RE.search(first)
        body_lines = text.splitlines()
        if head:
            body_lines = body_lines[body_lines.index(head) + 1:]
        locs.append({
            "id": f"loc_{i:02d}",
            "name": name,
            "location_type": _bullet_value(text, "类型"),
            "era_setting": None,
            "first_appear": int(fm.group(1)) if fm else None,
            "description_zh": "\n".join(body_lines).strip(),
            "visual_anchor_en": None,
            "asset_path": None,
            "appearance_variants": [],
            "source_file": rel,
            "source": "novel_writer",
        })
    return locs


# ---------------------------------------------------------------- 导出说明与书名

def _export_notes(root: Path, issues: List[ImportIssue]) -> None:
    readme = root / "README.md"
    if not readme.is_file():
        issues.append(ImportIssue(LEVEL_INFO, "NO_README", "导出目录没有 README.md"))
        return
    in_cleanup = False
    for line in _read(readme).splitlines():
        if line.startswith("## "):
            in_cleanup = "清洗警告" in line
            continue
        if in_cleanup and line.lstrip().startswith("- "):
            issues.append(ImportIssue(LEVEL_WARNING, "EXPORT_CLEANUP_NOTE",
                                      "novel_writer 导出时提示：" + line.lstrip()[2:].strip(),
                                      "README.md"))


def default_title(root: Path) -> str:
    """导出包不含书名，默认取目录名去掉时间戳后缀；导入时用户可改。"""
    return TIMESTAMP_SUFFIX_RE.sub("", root.name) or root.name


# ---------------------------------------------------------------- 入口

def import_source(source_dir: str | Path, title: Optional[str] = None) -> ImportResult:
    root = Path(source_dir).expanduser().resolve()
    issues: List[ImportIssue] = []
    if not root.is_dir():
        issues.append(ImportIssue(LEVEL_ERROR, "SOURCE_NOT_FOUND", f"导出目录不存在：{root}"))
        return ImportResult(source_dir=str(root), source_title=title or root.name,
                            fingerprint="", issues=issues)

    chapters = _load_chapters(root, issues)
    characters = _load_characters(root, issues)
    locations = _load_locations(root, issues)
    _export_notes(root, issues)

    if not (root / "小说_全书合稿.txt").is_file():
        issues.append(ImportIssue(LEVEL_INFO, "NO_FULL_TEXT", "导出目录没有 小说_全书合稿.txt（不影响导入）"))

    return ImportResult(
        source_dir=str(root),
        source_title=title or default_title(root),
        fingerprint=_fingerprint(root),
        chapters=chapters,
        characters=characters,
        locations=locations,
        issues=issues,
    )
