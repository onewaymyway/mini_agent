"""
wiki/migration.py — entity_index.py → entities/*.md

两个用途：

1. `migrate_entity_store()`：一次性导出脚本（重构计划阶段二第一条），把
   `EntityStore`（entity_index.json）里现有的全部实体逐个转换成
   `entities/*.md`。字段映射：
       summary            -> "当前状态" section（滚动覆盖的单一字符串，
                              迁移后成为可累加 section 的初始内容）
       superseded_notes   -> "历史沿革" section（旧结论不再是"最近 5 条"
                              的截断列表，而是可无限追加的正文）
       related_entry_ids  -> source_entries（保持可追溯）
       category           -> tags 里追加一条 "category:<code>"（分类树在
                              过渡期仍是权威来源，wiki 侧先如实记录，不重新
                              判断）
       entity_type        -> tags 里追加一条 entity_type 本身
       aliases            -> 写进"概述" section 正文，不建模成独立字段
                              （wiki 页面本身的 id/文件名即可作为规范名）

2. `mirror_entity()`：单个实体的增量镜像，供 `library_index.py` 的双写
   路径（`on_new_entry`）和 `consolidate()` 复用，逻辑和 1 的单条版本
   一致，但目标页面已存在时走"追加历史沿革"而不是整篇重新生成，避免
   覆盖人工在 wiki 页面里手改过的内容。

entity_id -> page_id 的映射通过 `wiki/_migration_map.json`（不放在
`_index/` 下，因为它不是"可随时删除重建"的派生索引，而是迁移与双写路径
依赖的持久状态——删掉它会导致同一实体被重复创建新页面）维护。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from mini_agent.perception.entity_index import Entity, EntityStore
from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.parser import WikiPage, parse_page
from mini_agent.wiki.writer import _atomic_write_text, append_section, set_status, write_page

_MAP_FILENAME = "_migration_map.json"
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    bare = name.replace(".py", "")
    slug = _SLUG_RE.sub("-", bare.lower()).strip("-")
    return slug or "entity"


def _epoch_to_date(ts: float) -> str:
    if not ts:
        return date.today().isoformat()
    try:
        return datetime.fromtimestamp(ts).date().isoformat()
    except (OSError, OverflowError, ValueError):
        return date.today().isoformat()


def _map_path(paths: AgentPaths) -> Path:
    return paths.wiki_dir / _MAP_FILENAME


def load_entity_map(paths: AgentPaths) -> dict[str, str]:
    """读取 entity_id -> page_id 映射，文件不存在时返回空 dict。"""
    p = _map_path(paths)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_entity_map(paths: AgentPaths, mapping: dict[str, str]) -> None:
    _atomic_write_text(_map_path(paths), json.dumps(mapping, ensure_ascii=False, indent=2))


def _resolve_page_id(entity: Entity, mapping: dict[str, str], existing_ids: set[str]) -> str:
    """已迁移过的实体复用旧 page_id；新实体在候选 slug 冲突时追加 entity_id
    后缀区分（同名不同实体的场景，比如两个项目里都有一个叫 `config` 的模块，
    在合并成同一个 EntityStore 前理论上不该发生，但保守处理）。"""
    if entity.entity_id in mapping:
        return mapping[entity.entity_id]
    slug = _slugify(entity.name)
    page_id = slug
    if page_id in existing_ids:
        page_id = f"{slug}-{entity.entity_id[:6]}"
    return page_id


def _entity_body(entity: Entity) -> str:
    aliases_line = f"（别名：{', '.join(entity.aliases)}）" if entity.aliases else ""
    lines = [
        "## 概述",
        "",
        f"{entity.name}{aliases_line}，类型：{entity.entity_type}。",
        "",
        "## 当前状态",
        "",
        entity.summary or "（暂无摘要）",
    ]
    if entity.superseded_notes:
        lines += ["", "## 历史沿革", ""]
        lines += [f"- {note}" for note in entity.superseded_notes]
    return "\n".join(lines) + "\n"


def _entity_tags(entity: Entity) -> list[str]:
    tags = [entity.entity_type]
    if entity.category:
        tags.append(f"category:{entity.category}")
    return tags


def _entity_status(entity: Entity) -> str:
    # entity_index.py 的 status 取值 active|deprecated|superseded 与
    # wiki STATUS_VALUES 完全重合，直接透传即可。
    return entity.status if entity.status in ("active", "deprecated", "superseded") else "active"


@dataclass
class MigrationReport:
    migrated: int = 0
    skipped: int = 0
    page_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def migrate_entity_store(
    entity_index_path: Path, paths: AgentPaths, *, overwrite: bool = False
) -> MigrationReport:
    """一次性导出：EntityStore(entity_index_path) 的全部实体 -> entities/*.md。

    overwrite=False（默认）时跳过已经迁移过的实体（entity_id 已在映射表
    里），可安全重复运行——增量迁移新增的实体，不重复处理旧的。
    """
    paths.ensure_wiki_dirs()
    store = EntityStore(entity_index_path)
    mapping = load_entity_map(paths)
    existing_ids = {p.stem for p in paths.wiki_entities_dir.glob("*.md")}
    report = MigrationReport()

    for entity in store.all_entities():
        if not overwrite and entity.entity_id in mapping:
            report.skipped += 1
            continue
        try:
            page_id = _resolve_page_id(entity, mapping, existing_ids)
            write_page(
                paths,
                page_id=page_id,
                page_type="entity",
                body=_entity_body(entity),
                tags=_entity_tags(entity),
                status=_entity_status(entity),
                created=_epoch_to_date(entity.first_seen),
                updated=_epoch_to_date(entity.last_summary_update or entity.first_seen),
                source_entries=entity.related_entry_ids,
                extra_frontmatter={"legacy_entity_id": entity.entity_id},
                overwrite=True,
            )
            mapping[entity.entity_id] = page_id
            existing_ids.add(page_id)
            report.migrated += 1
            report.page_ids.append(page_id)
        except Exception as exc:  # noqa: BLE001 - 单个实体迁移失败不阻断整批
            report.errors.append(f"{entity.entity_id} ({entity.name}): {exc}")

    save_entity_map(paths, mapping)
    return report


def mirror_entity(entity: Entity, paths: AgentPaths, *, note: Optional[str] = None) -> Optional[Path]:
    """把单个实体的当前状态镜像进 wiki（library_index 双写路径 / consolidate 复用）。

    - 实体第一次出现（映射表里没有）：新建页面，内容取当前 summary 快照。
    - 已存在：追加一条"历史沿革"记录（note 显式给出时用 note，否则用当前
      summary 作为快照），不整篇重写，保留人工可能做过的手改。

    任何异常（比如 pyyaml 未安装、磁盘写入失败）都会向上抛出——调用方
    （library_index.py）负责决定要不要把镜像失败当成"可忽略的最佳努力"。
    """
    paths.ensure_wiki_dirs()
    mapping = load_entity_map(paths)
    existing_ids = {p.stem for p in paths.wiki_entities_dir.glob("*.md")}

    if entity.entity_id not in mapping:
        page_id = _resolve_page_id(entity, mapping, existing_ids)
        path = write_page(
            paths,
            page_id=page_id,
            page_type="entity",
            body=_entity_body(entity),
            tags=_entity_tags(entity),
            status=_entity_status(entity),
            source_entries=entity.related_entry_ids,
            extra_frontmatter={"legacy_entity_id": entity.entity_id},
            overwrite=False,
        )
        mapping[entity.entity_id] = page_id
        save_entity_map(paths, mapping)
        return path

    page_id = mapping[entity.entity_id]
    page_path = paths.wiki_entities_dir / f"{page_id}.md"
    if not page_path.exists():
        # 映射表存在但文件被人工删了：当作首次出现重新创建，避免镜像流程
        # 因为文件缺失而永久失败。
        del mapping[entity.entity_id]
        save_entity_map(paths, mapping)
        return mirror_entity(entity, paths, note=note)

    page = parse_page(page_path)
    content = note or entity.summary or "（无新增摘要内容）"
    target_status = _entity_status(entity)
    if target_status != page.status:
        return set_status(paths, page, status=target_status, note=content)
    return append_section(paths, page, heading="历史沿革", content=content)
