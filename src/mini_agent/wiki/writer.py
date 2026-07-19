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
import re
import tempfile
from datetime import date, datetime, timezone
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
    extra_frontmatter: Optional[dict] = None,
) -> str:
    """把结构化字段渲染为完整的 md 文本（frontmatter + 正文）。

    extra_frontmatter: 附加的非核心 frontmatter 字段（比如迁移脚本写入的
    legacy_entity_id，用于追溯旧 entity_index.py 的原始 entity_id）。
    parser.py 不会校验这些字段，只是原样保留在 raw_frontmatter 里。
    """
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
    if extra_frontmatter:
        for k, v in extra_frontmatter.items():
            if k not in fm:
                fm[k] = v

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
    extra_frontmatter: Optional[dict] = None,
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
        extra_frontmatter=extra_frontmatter,
    )
    _atomic_write_text(target_path, text)
    return target_path


def increment_grounded_hit_count(paths: AgentPaths, page: WikiPage) -> Path:
    """把 `grounded_hit_count` frontmatter 字段 +1（wiki 提取层与组织层
    改进计划 O1 §4.2.2：被 LLM 精排判定为"回答主要依据"的页面视为一次
    隐式信度验证）。

    只更新这一个字段，不改动 `updated`（这不是一次内容编辑，不应刷新
    "最近修改时间"语义），也不追加任何正文内容。回写属于非关键路径，
    调用方应当把异常静默吞掉（context_builder.py 调用点已经这样处理），
    不能因为回写失败影响本轮检索结果返回。
    """
    _core_keys = {
        "id", "type", "tags", "status", "confidence", "created", "updated",
        "links", "source_entries",
    }
    extra = {k: v for k, v in page.raw_frontmatter.items() if k not in _core_keys}
    current = int(extra.get("grounded_hit_count") or 0)
    extra["grounded_hit_count"] = current + 1

    text = render_page(
        page_id=page.id,
        page_type=page.type,
        body=page.body,
        tags=page.tags,
        status=page.status,
        confidence=page.confidence,
        created=page.created,
        updated=page.updated,
        links=page.strong_links(),
        source_entries=page.source_entries,
        extra_frontmatter=extra,
    )
    _atomic_write_text(page.path, text)
    return page.path


def set_status(paths: AgentPaths, page: WikiPage, *, status: str, note: str = "") -> Path:
    """更新既有页面的 status 字段（如 active -> superseded），可选追加一条说明
    到"历史沿革"。用于旧 EntityStore.mark_superseded 的镜像场景。"""
    body = page.body
    if note:
        body = body.rstrip("\n") + f"\n\n## 历史沿革\n\n{note.strip()}\n"
    _core_keys = {
        "id", "type", "tags", "status", "confidence", "created", "updated",
        "links", "source_entries",
    }
    extra = {k: v for k, v in page.raw_frontmatter.items() if k not in _core_keys}
    text = render_page(
        page_id=page.id,
        page_type=page.type,
        body=body,
        tags=page.tags,
        status=status,
        confidence=page.confidence,
        created=page.created,
        updated=date.today().isoformat(),
        links=page.strong_links(),
        source_entries=page.source_entries,
        extra_frontmatter=extra,
    )
    _atomic_write_text(page.path, text)
    return page.path


def update_lifecycle_fields(
    paths: AgentPaths,
    page: WikiPage,
    *,
    knowledge_state: Optional[str] = None,
    validated_by_append: str = "",
    note: str = "",
) -> Path:
    """更新知识生命周期 frontmatter 字段（wiki 提取层与组织层改进计划 O4）：
    `knowledge_state`（fresh | stale | superseded）、`last_validated_at`、
    `validated_by`。

    字段名用 `knowledge_state` 而非原计划 §7.2.1 里设想的复用 `confidence`
    字段——`confidence` 在 parser.py 里已经是一个 0-1 的数值型置信度分数
    （见 render_page 的 confidence 参数），语义与"新鲜度状态机"完全不同，
    复用会导致同名字段两种类型冲突，因此改用独立字段名，属于对原计划的
    必要调整（详见 O4 实施记录 §2）。

    只更新状态相关字段，不刷新 `updated`（与 `increment_grounded_hit_count`
    一致：状态标记本身不是一次内容编辑），除非 note 非空——此时会在正文追加
    一段"历史沿革"，才随之刷新 updated。
    """
    _core_keys = {
        "id", "type", "tags", "status", "confidence", "created", "updated",
        "links", "source_entries",
    }
    extra = {k: v for k, v in page.raw_frontmatter.items() if k not in _core_keys}
    if knowledge_state is not None:
        extra["knowledge_state"] = knowledge_state
    extra["last_validated_at"] = datetime.now(timezone.utc).isoformat()
    if validated_by_append:
        validated_by = list(extra.get("validated_by") or [])
        if validated_by_append not in validated_by:
            validated_by.append(validated_by_append)
        extra["validated_by"] = validated_by

    body = page.body
    updated = page.updated
    if note:
        body = body.rstrip("\n") + f"\n\n## 历史沿革\n\n{note.strip()}\n"
        updated = date.today().isoformat()

    text = render_page(
        page_id=page.id,
        page_type=page.type,
        body=body,
        tags=page.tags,
        status=page.status,
        confidence=page.confidence,
        created=page.created,
        updated=updated,
        links=page.strong_links(),
        source_entries=page.source_entries,
        extra_frontmatter=extra,
    )
    _atomic_write_text(page.path, text)
    return page.path


def replace_body(paths: AgentPaths, page: WikiPage, *, body: str) -> Path:
    """整体替换页面正文，frontmatter 保持不变（除 `updated` 刷新为今天）。

    用于 O4 §7.2.3 的 fact 锚点粒度状态标记——锚点标记是正文内的一条 HTML
    注释（`<!-- fact_id: ...; knowledge_state: ... -->`），需要整体重写正文
    而不是走 frontmatter 更新路径。
    """
    _core_keys = {
        "id", "type", "tags", "status", "confidence", "created", "updated",
        "links", "source_entries",
    }
    extra = {k: v for k, v in page.raw_frontmatter.items() if k not in _core_keys}
    text = render_page(
        page_id=page.id,
        page_type=page.type,
        body=body,
        tags=page.tags,
        status=page.status,
        confidence=page.confidence,
        created=page.created,
        updated=date.today().isoformat(),
        links=page.strong_links(),
        source_entries=page.source_entries,
        extra_frontmatter=extra,
    )
    _atomic_write_text(page.path, text)
    return page.path


def _last_section_content(body: str, heading: str) -> Optional[str]:
    """提取正文里最后一个 `## <heading>` 段落的内容（到下一个 `## ` 或文末）。

    找不到该 heading 时返回 `None`。用于 `append_section()` 的重复内容检测
    ——修复的 bug：`wiki/migration.py::mirror_entity()` 每次有新记忆链接到
    某实体就会调用一次 `_mirror_entities_to_wiki()`，摘要没变时追加的
    "历史沿革"内容跟上一次一字不差，同一个高频实体（比如 goal 模式的
    `goal` 概念实体）短时间内被反复镜像，正文里会堆出几十段完全相同的
    "## 历史沿革"，页面失去可读性。
    """
    pattern = re.compile(
        r"^## " + re.escape(heading) + r"[ \t]*\n\n(.*?)(?=\n## |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    matches = pattern.findall(body)
    if not matches:
        return None
    return matches[-1].strip()


def append_section(
    paths: AgentPaths,
    page: WikiPage,
    *,
    heading: str,
    content: str,
    extra_links: Optional[list[WikiLink]] = None,
    extra_frontmatter_updates: Optional[dict] = None,
    dedupe: bool = True,
) -> Path:
    """在既有页面正文末尾追加一个 section（用于"历史沿革"类追加更新）。

    直接操作已解析的 WikiPage.body，重新渲染整份文件后原子写回，保留原有
    frontmatter 字段（updated 会刷新为今天）。

    `dedupe`（默认开启）：追加前跟同名 heading 下最后一段已有内容做精确
    比较，完全相同则视为无意义的重复调用，直接跳过（不写文件、不刷新
    `updated`，`page.path` 原样返回）——修复"同一实体反复被镜像、正文里
    堆出几十段一字不差的历史沿革"的问题（见 `_last_section_content()` 的
    说明）。只在追加内容真的和上一次不同、或该 heading 首次出现时才会
    真正写入。传 `dedupe=False` 可以恢复旧行为（比如确实需要允许连续两条
    内容相同的记录时）。

    `extra_links`（wiki 提取层与组织层改进计划 O3）：追加时顺带补充的
    frontmatter 强链接（比如 topic 页面吸收新成员时补充 `absorbs` 关系），
    与既有 `page.strong_links()` 按 target 去重合并（新链接优先覆盖同
    target 的旧记录），不传时行为与改动前完全一致。

    `extra_frontmatter_updates`：追加时顺带更新/新增的非核心 frontmatter
    字段（比如 topic 再巩固计数、`needs_review` 标记），与既有
    `raw_frontmatter` 合并（新值覆盖同名旧值），不传时行为不变。
    """
    if dedupe:
        last = _last_section_content(page.body, heading)
        if last is not None and last == content.strip():
            return page.path

    new_body = page.body.rstrip("\n") + f"\n\n## {heading}\n\n{content.strip()}\n"
    _core_keys = {
        "id", "type", "tags", "status", "confidence", "created", "updated",
        "links", "source_entries",
    }
    extra = {k: v for k, v in page.raw_frontmatter.items() if k not in _core_keys}
    if extra_frontmatter_updates:
        extra.update(extra_frontmatter_updates)

    links = list(page.strong_links())
    if extra_links:
        by_target = {l.target: l for l in links}
        for l in extra_links:
            by_target[l.target] = l
        links = list(by_target.values())

    text = render_page(
        page_id=page.id,
        page_type=page.type,
        body=new_body,
        tags=page.tags,
        status=page.status,
        confidence=page.confidence,
        created=page.created,
        updated=date.today().isoformat(),
        links=links,
        source_entries=page.source_entries,
        extra_frontmatter=extra,
    )
    _atomic_write_text(page.path, text)
    return page.path
