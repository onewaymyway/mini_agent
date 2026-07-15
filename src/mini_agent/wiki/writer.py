"""
wiki/writer.py — 新建/更新 wiki 页面

复用 perception/global_knowledge.py 等模块里已经验证过的原子写模式
（tmp + os.fsync + os.replace），避免并发/中断写出半截 md 文件。

本模块只负责"把结构化数据渲染成 md 文本并落盘"，不负责决定内容——
调用方（library_index.py 双写路径、迁移脚本、巩固循环）负责组装
WikiPage 后传进来。
"""

from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Optional

from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.parser import PAGE_TYPES, STATUS_VALUES, PageParseError, WikiLink, WikiPage

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, path)


def render_page(
    *,
    page_id: str,
    page_type: str,
    body: str,
    tags: Optional[list[str]] = None,
    status: str = "active",
    confidence: Optional[float] = None,
    created: Optional[str] = None,
    updated: Optional[str] = None,
    links: Optional[list[WikiLink]] = None,
    source_entries: Optional[list[str]] = None,
) -> str:
    """把结构化字段渲染为完整的 md 文本（frontmatter + 正文）。"""
    if yaml is None:
        raise PageParseError("渲染 wiki 页面需要 pyyaml，请先安装：pip install pyyaml")
    if page_type not in PAGE_TYPES:
        raise ValueError(f"未知 page_type: {page_type!r}，可选: {PAGE_TYPES}")
    if status not in STATUS_VALUES:
        raise ValueError(f"未知 status: {status!r}，可选: {STATUS_VALUES}")

    today = date.today().isoformat()
    fm: dict = {
        "id": page_id,
        "type": page_type,
        "tags": tags or [],
        "status": status,
        "created": created or today,
        "updated": updated or today,
    }
    if confidence is not None:
        fm["confidence"] = confidence
    if links:
        # 只序列化 frontmatter 强关系，正文弱引用（[[..]]）留在正文内，
        # 由解析器在读取时重新提取，不重复存储。
        strong = [l for l in links if l.source == "frontmatter"]
        if strong:
            fm["links"] = [
                {"target": l.target, "relation": l.relation, **({"note": l.note} if l.note else {})}
                for l in strong
            ]
    if source_entries:
        fm["source_entries"] = source_entries

    frontmatter_text = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip()
    body_text = body if body.startswith("\n") else "\n" + body
    return f"---\n{frontmatter_text}\n---\n{body_text}"


def write_page(
    paths: AgentPaths,
    *,
    page_id: str,
    page_type: str,
    body: str,
    tags: Optional[list[str]] = None,
    status: str = "active",
    confidence: Optional[float] = None,
    created: Optional[str] = None,
    updated: Optional[str] = None,
    links: Optional[list[WikiLink]] = None,
    source_entries: Optional[list[str]] = None,
    overwrite: bool = True,
) -> Path:
    """渲染并原子写入一个页面，返回写入路径。

    文件名固定为 `<page_id>.md`，落在 paths.wiki_type_dir(page_type) 下。
    overwrite=False 且目标文件已存在时抛 FileExistsError，避免误覆盖人工
    手改过的页面。
    """
    target_dir = paths.wiki_type_dir(page_type)
    target_path = target_dir / f"{page_id}.md"
    if not overwrite and target_path.exists():
        raise FileExistsError(f"页面已存在，未开启 overwrite: {target_path}")

    text = render_page(
        page_id=page_id,
        page_type=page_type,
        body=body,
        tags=tags,
        status=status,
        confidence=confidence,
        created=created,
        updated=updated,
        links=links,
        source_entries=source_entries,
    )
    _atomic_write_text(target_path, text)
    return target_path


def append_section(paths: AgentPaths, page: WikiPage, *, heading: str, content: str) -> Path:
    """在既有页面正文末尾追加一个 section（用于"历史沿革"类追加更新）。

    直接操作已解析的 WikiPage.body，重新渲染整份文件后原子写回，保留原有
    frontmatter 字段（updated 会刷新为今天）。
    """
    new_body = page.body.rstrip("\n") + f"\n\n## {heading}\n\n{content.strip()}\n"
    text = render_page(
        page_id=page.id,
        page_type=page.type,
        body=new_body,
        tags=page.tags,
        status=page.status,
        confidence=page.confidence,
        created=page.created,
        updated=date.today().isoformat(),
        links=page.strong_links(),
        source_entries=page.source_entries,
    )
    _atomic_write_text(page.path, text)
    return page.path
