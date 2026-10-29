"""novel_writer/final_export.py — 导出「最终稿」。

输出到 `导出/最终稿/<书名>_<时间戳>/`（每次新建目录），内容包括：
- 设定/      主题、节奏、开篇、结局、章节大纲、人物、世界观、线索、文风（md 可读版）
- 章节总结/  每章的总结（原样复制，只去掉误入的文件名行）
- 正文/      每章一个文件，只保留标题与正文，去掉写作备注、实际字数等元信息
- 全书合稿.txt

正文清洗规则（章节文件的元信息格式不统一，见下）：
1. 开头误入的文件名行（如「第44章_候选B.md」）删除。
2. 若存在「正文」标记行（如 `## 正文`），标记之前的内容视为元信息头部，全部丢弃；章节标题从头部取。
3. 结尾的写作备注：从最早出现的备注关键字行（写作备注 / 实际字数 / chapter_type / 完成情节任务 …）开始，
   到文件末尾全部丢弃，但只有当其后不存在正文样式的长段落时才切（防止误删正文）。
4. 切除后仍残留备注样式的行，记为警告，不会静默通过。
清洗后正文为空，或切除行为异常时，该章记入 errors 并跳过，不会把备注写进最终稿。
"""

from __future__ import annotations

import datetime as _dt
import glob as _glob
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from novel_writer import chapter_ops as C
from novel_writer import exporter as E
from novel_writer.fsutil import atomic_write_text, read_json
from novel_writer.textio import read_text_utf8
from novel_writer.workspace import Workspace, count_words, sanitize_name

FINAL_DIR_NAME = "最终稿"

# ── 清洗 ──────────────────────────────────────────────────────────────────

NOTE_KEYS = (
    "写作备注", "实际字数", "chapter_type", "完成情节任务", "完成的情节任务",
    "本章场景覆盖", "本章埋设的伏笔", "埋设伏笔", "埋设的伏笔", "本章兑现的伏笔", "兑现伏笔",
    "本章出场角色变化", "出场角色变化", "本章出场角色", "角色状态变化", "角色状态",
    "章末钩子", "实际钩子文本",
)
_NOTE_START = re.compile("^(" + "|".join(re.escape(k) for k in NOTE_KEYS) + ")")
_STRAY_NAME = re.compile(r"^第\d+章[_\s].*\.md$")
_HEADING = re.compile(r"^#{1,6}\s*(第[^章\n]{0,12}章.*)$")
_TITLE_PREFIX = re.compile(r"^第[^章]{0,12}章\s*")
_FILE_TITLE = re.compile(r"^第\d+章[_\s]*(.*)\.md$")
_SEPARATORS = {"---", "***", "___"}


def _norm(line: str) -> str:
    """去掉 markdown 与括号装饰后的纯文本，用于识别备注关键字。"""
    s = re.sub(r"[*【】]", "", line.strip())
    s = re.sub(r"^[#>\s]+", "", s)
    return s.strip()


def _is_note_start(line: str) -> bool:
    return bool(_NOTE_START.match(_norm(line)))


def _is_prose(line: str) -> bool:
    """正文样式的长段落：非空、非分隔线、非备注关键字、非缩进/列表项，且较长。"""
    s = line.strip()
    if not s or s in _SEPARATORS or _is_note_start(line):
        return False
    if line[:1].isspace() or s.startswith(("-", "•", "✓", "✗", "×", "✔")):
        return False
    return len(_norm(line)) > 60


def _tail_cut(lines: List[str]) -> int:
    """返回结尾备注块的起始行下标（没有则为 len(lines)）。"""
    for i, line in enumerate(lines):
        if not _is_note_start(line):
            continue
        if any(_is_prose(rest) for rest in lines[i:]):
            continue
        return i
    return len(lines)


def _strip_blank_and_separators(lines: List[str]) -> List[str]:
    start, end = 0, len(lines)
    while start < end and (not lines[start].strip() or lines[start].strip() in _SEPARATORS):
        start += 1
    while end > start and (not lines[end - 1].strip() or lines[end - 1].strip() in _SEPARATORS):
        end -= 1
    return lines[start:end]


def clean_chapter(raw: str, *, number: int, fallback_title: str = "") -> Tuple[str, List[str]]:
    """清洗一章的原始文本。返回 (最终稿文本, 警告列表)。"""
    lines = raw.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and _STRAY_NAME.match(lines[0].strip()):
        lines.pop(0)

    marker = next((i for i, l in enumerate(lines) if _norm(l) == "正文"), None)
    if marker is not None:
        head, body = lines[:marker], lines[marker + 1:]
    else:
        head, body = [], lines

    title_full: Optional[str] = None
    for source in (head, body):
        for i, l in enumerate(source):
            m = _HEADING.match(l.strip())
            if m:
                title_full = m.group(1).strip()
                source.pop(i)   # 标题统一由下面重新写出
                break
        if title_full:
            break

    body = body[:_tail_cut(body)]
    body = _strip_blank_and_separators(body)
    warnings: List[str] = []
    if not any(l.strip() for l in body):
        raise ValueError(f"第{number}章清洗后正文为空")
    for i, l in enumerate(body):
        if _is_note_start(l) and not _is_prose(l):
            warnings.append(f"第{number}章第{i + 1}行仍含备注样式内容：{l.strip()[:20]}")
            break

    if title_full:
        title = _TITLE_PREFIX.sub("", title_full).strip() or fallback_title
    else:
        title = fallback_title
    heading = f"# 第{number}章 {title}".rstrip() if title else f"# 第{number}章"
    text = heading + "\n\n" + "\n".join(body).strip() + "\n"
    return text, warnings


# ── 设定 md 化 ──────────────────────────────────────────────────────────────

def _scalar(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "是" if v else "否"
    return str(v).replace("\n", " ")


def _render_value(value: Any, depth: int, out: List[str]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, (dict, list)) and v:
                out.append("")
                out.append(f"{'#' * min(depth + 2, 6)} {k}")
                _render_value(v, depth + 1, out)
            else:
                out.append(f"- **{k}**：{_scalar(v) if not isinstance(v, (dict, list)) else '（空）'}")
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                sub: List[str] = []
                _render_value(item, depth + 1, sub)
                if sub:
                    out.append("- " + sub[0].lstrip("- ").strip())
                    out.extend("  " + s if s else "" for s in sub[1:])
            else:
                out.append(f"- {_scalar(item)}")
    else:
        out.append(_scalar(value))


def json_to_md(data: Any, title: str) -> str:
    out: List[str] = [f"# {title}", ""]
    _render_value(data, 1, out)
    return "\n".join(out).rstrip() + "\n"


def _read_md(path: Path) -> str:
    return read_text_utf8(path).strip() + "\n"


# ── 导出主流程 ──────────────────────────────────────────────────────────────

# (目标相对路径, [(来源 key), ...] 的 md 文件合并；json 来源用 ("json", key, 标题))
_SETTING_FILES: List[Tuple[str, List[Any]]] = [
    ("设定/主题与基调.md", ["theme_design", "pacing_design", "opening_design"]),
    ("设定/结局设计.md", ["ending_design"]),
    ("设定/章节大纲.md", ["blueprint_readable"]),
    ("设定/人物/角色索引.md", ["characters"]),
    ("设定/世界观/世界观.md", [("json", "world_bible", "世界观")]),
    ("设定/线索/伏笔注册表.md", ["foreshadowing_list"]),
    ("设定/线索/故事线状态.md", ["thread_status"]),
    ("设定/线索/张力曲线.md", ["tension_curve"]),
    ("设定/文风/风格档案.md", [("json", "style_profile", "风格档案")]),
]
# 目录型来源：(目标目录, 来源目录 key 或 callable)
_SETTING_DIRS: List[Tuple[str, Any]] = [
    ("设定/人物/角色", "characters_dir"),
    ("设定/人物/人设卡", "character_card"),
    ("设定/世界观/地点详情", "location_detail"),
    ("设定/线索/伏笔", "plot_items_dir"),
]


def _source_dir(ws: Workspace, key: str) -> Path:
    if key == "character_card":
        return ws.path("character_card", name="x").parent
    if key == "location_detail":
        return ws.path("location_detail", name="x").parent
    return ws.path(key)


def _chapter_numbers(ws: Workspace) -> List[int]:
    nums = set()
    for p in _glob.glob(str(ws.root / "正文" / "第*章*.md")):
        m = re.match(r"^第(\d+)章", Path(p).name)
        if m:
            nums.add(int(m.group(1)))
    return sorted(nums)


def _fallback_title(ws: Workspace, n: int) -> str:
    entry_title = str(C.chapter_entry(ws, n).get("title") or "").strip()
    if entry_title:
        return entry_title
    f = C.chapter_file(ws, n)
    if f:
        m = _FILE_TITLE.match(f.name)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return ""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, text)


def export_final(ws: Workspace) -> Dict[str, Any]:
    title = read_json(ws.path("workspace_config"), default={}).get("novel_title") or "小说"
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_root = ws.path("export_dir") / FINAL_DIR_NAME / f"{sanitize_name(title, what='书名')}_{stamp}"
    out_root.mkdir(parents=True, exist_ok=True)
    warnings: List[str] = []
    errors: List[str] = []

    # 设定
    settings_written = 0
    for rel, keys in _SETTING_FILES:
        chunks: List[str] = []
        for k in keys:
            if isinstance(k, tuple):
                _, key, label = k
                data = read_json(ws.path(key), default=None)
                if data:
                    chunks.append(json_to_md(data, label))
            else:
                p = ws.path(k)
                if p.exists():
                    chunks.append(_read_md(p))
        if chunks:
            _write(out_root / rel, "\n\n---\n\n".join(chunks))
            settings_written += 1
    for rel_dir, key in _SETTING_DIRS:
        src = _source_dir(ws, key)
        if src.is_dir():
            for f in sorted(src.glob("*.md")):
                _write(out_root / rel_dir / f.name, _read_md(f))
                settings_written += 1

    # 章节总结（原样复制，只去掉误入的文件名行）
    summaries = 0
    numbers = _chapter_numbers(ws)
    for n in numbers:
        p = ws.path("chapter_summary", nn=n)
        if p.exists():
            lines = read_text_utf8(p).splitlines()
            if lines and _STRAY_NAME.match(lines[0].strip()):
                lines = lines[1:]
            _write(out_root / "章节总结" / p.name, "\n".join(lines).strip() + "\n")
            summaries += 1

    # 正文
    parts: List[str] = []
    words = 0
    count = 0
    for n in numbers:
        f = C.chapter_file(ws, n)
        if f is None:
            continue
        try:
            text, warn = clean_chapter(read_text_utf8(f), number=n, fallback_title=_fallback_title(ws, n))
        except ValueError as exc:
            errors.append(str(exc))
            continue
        warnings.extend(warn)
        title_line = text.splitlines()[0][2:]
        safe = sanitize_name(title_line.split(" ", 1)[1], what="章名") if " " in title_line else f"第{n}章"
        _write(out_root / "正文" / f"第{n:03d}章 {safe}.md", text)
        parts.append(text)
        words += count_words(text)
        count += 1

    if not parts:
        raise ValueError("还没有任何可导出的正文")

    txt_path = out_root / f"{sanitize_name(title, what='书名')}_全书合稿.txt"
    atomic_write_text(txt_path, E.to_txt(parts))

    readme = [
        f"# {title} · 最终稿",
        "",
        f"- 导出时间：{_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 章节数：{count}　总字数：{words}",
        f"- 设定文件：{settings_written} 个　章节总结：{summaries} 个",
        "",
        "## 目录",
        "- 设定/   主题、大纲、人物、世界观、线索、文风（md 可读版）",
        "- 章节总结/   每章总结",
        "- 正文/   每章正文（已去除写作备注等元信息）",
        f"- {txt_path.name}   全书合稿（纯文本）",
    ]
    if warnings:
        readme += ["", "## 清洗警告（请人工检查）"] + [f"- {w}" for w in warnings]
    if errors:
        readme += ["", "## 未导出的章节"] + [f"- {e}" for e in errors]
    _write(out_root / "README.md", "\n".join(readme) + "\n")

    return {
        "dir": str(out_root.relative_to(ws.root)),
        "chapters": count,
        "words": words,
        "settings": settings_written,
        "summaries": summaries,
        "txt": str(txt_path.relative_to(ws.root)),
        "warnings": warnings,
        "errors": errors,
    }
