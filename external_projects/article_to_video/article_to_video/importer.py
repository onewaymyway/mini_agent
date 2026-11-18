"""importer.py — 导入 Markdown 文章（P0）：解析、署名解析、登记到项目目录。不调用任何模型。"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, Optional

from .project_store import (
    SCHEMA_VERSION,
    append_progress,
    normalize_branding,
    normalize_settings,
    now_iso,
    slugify,
    unique_project_dir,
    write_json_atomic,
    write_text_atomic,
)
from .source.author_meta import resolve_author
from .source.md_parse import parse_markdown

CHARS_PER_SECOND = 4.2  # 与 novel_to_video 的语速估算一致：约 4.2 个汉字/秒
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


class ArticleImportError(ValueError):
    """导入失败（内容为空、编码不对等）。界面直接展示其文字。"""


def summarize(parsed: Dict[str, Any], text: str) -> Dict[str, Any]:
    blocks = parsed["blocks"]
    kinds: Dict[str, int] = {}
    shapes: Dict[str, int] = {}
    for b in blocks:
        kinds[b["kind"]] = kinds.get(b["kind"], 0) + 1
        if b["kind"] == "code":
            shapes[b["shape"]] = shapes.get(b["shape"], 0) + 1
    cn = len(_CJK_RE.findall(text))
    return {
        "chars": len(text),
        "chinese_chars": cn,
        "est_full_read_minutes": round(cn / CHARS_PER_SECOND / 60, 1),
        "sections": len(parsed["sections"]),
        "blocks_total": len(blocks),
        "blocks_by_kind": kinds,
        "code_shapes": shapes,
        "code_loops": sum(1 for b in blocks if b["kind"] == "code" and b.get("loop")),
        "formulas": kinds.get("math", 0),
        "tables": kinds.get("table", 0),
        "quotes": kinds.get("quote", 0),
    }


def import_article(
    projects_root: Path,
    *,
    source_text: str,
    source_name: str = "article.md",
    title: str = "",
    author: str = "",
    branding: Optional[Dict[str, Any]] = None,
    settings: Optional[Dict[str, Any]] = None,
    now=None,
) -> Dict[str, Any]:
    text = source_text.lstrip("\ufeff")
    if not text.strip():
        raise ArticleImportError("文章内容为空")

    parsed = parse_markdown(text)
    h1_title = next((s["title"] for s in parsed["sections"] if s.get("title")), "")
    final_title = title.strip() or h1_title or Path(source_name).stem or "未命名文章"

    author_name, author_source = resolve_author(author, parsed["front_matter"], parsed["body_head_lines"])

    branding_raw = dict(branding or {})
    branding_raw["author"] = author_name
    if not branding_raw.get("source_title"):
        branding_raw["source_title"] = final_title
    branding_n, warn_b = normalize_branding(branding_raw)
    settings_n, warn_s = normalize_settings(settings)
    warnings = warn_b + warn_s
    if not author_name:
        warnings.append("未填写作者名：片头与片尾不署名，可在「📚 项目」里补填")

    summary = summarize(parsed, text)
    sha = hashlib.sha256(source_text.encode("utf-8")).hexdigest()

    project_dir = unique_project_dir(Path(projects_root), slugify(final_title))
    write_text_atomic(project_dir / "source" / "article.md", text)
    write_json_atomic(project_dir / "source" / "blocks.json", {
        "front_matter": parsed["front_matter"],
        "sections": parsed["sections"],
        "blocks": parsed["blocks"],
    })
    write_json_atomic(project_dir / "source" / "meta.json", {
        "source_name": source_name,
        "sha256": sha,
        "author": {"name": author_name, "source": author_source},
        "summary": summary,
    })
    project = {
        "schema": SCHEMA_VERSION,
        "id": project_dir.name,
        "title": final_title,
        "stage": "P0_imported",
        "created_at": now_iso(now),
        "source": {"name": source_name, "sha256": sha, "chars": len(text)},
        "author_source": author_source,
        "branding": branding_n,
        "settings": settings_n,
        "summary": summary,
    }
    write_json_atomic(project_dir / "project.json", project)
    append_progress(project_dir, f"导入文章：{final_title}（作者来源：{author_source}）", now)

    return {
        "project_id": project_dir.name,
        "project_dir": str(project_dir),
        "title": final_title,
        "author": author_name,
        "author_source": author_source,
        "warnings": warnings,
        "summary": summary,
    }
