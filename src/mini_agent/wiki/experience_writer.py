"""
wiki/experience_writer.py — 把正面经验写入 wiki/experiences/*.md

对应《wiki 式知识库改进计划》P2：`_templates/experience.md` 此前存在但
从未被任何模块写入——现有链路只在负面事件（纠正/eval_failure）发生时
写 entry_type="lesson"，没有对称的正面经验沉淀路径。

与 wiki/decision_writer.py / wiki/world_writer.py 不同，经验类内容本身
样本量小，不要求攒够阈值或走 pending 队列节流——直接落盘，后续巩固循环
里的 wiki 判重（wiki/dedup.py）负责合并近似经验，不需要额外的节流治理。
"""

from __future__ import annotations

from typing import Optional

from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.parser import WikiLink
from mini_agent.wiki.writer import write_page

SOURCE_KIND = "experience_success"


def _slugify(text: str, fallback: str = "experience") -> str:
    import re
    ascii_tokens = re.findall(r"[a-zA-Z0-9]+", text.lower())
    slug = "-".join(ascii_tokens)[:50].strip("-")
    if not slug:
        import hashlib
        slug = f"{fallback}-{hashlib.sha1(text.encode('utf-8')).hexdigest()[:8]}"
    return slug


def write_experience(
    paths: AgentPaths,
    *,
    trigger: str,
    approach: str,
    outcome: str,
    reusable: bool = True,
    related_entities: Optional[list[str]] = None,
    source_kind: str = SOURCE_KIND,
    confidence: float = 0.6,
):
    """写一篇正面经验页面：触发场景 + 采取的方法 + 效果。

    related_entities 里的名字会尝试解析为已存在的 entity 页面 id
    （启发式 slug 匹配），用 relation="demonstrates" 建立关联，供 P3
    的 topic 聚类使用。任何异常都不应向上抛出——调用方（outcome_tracker
    等）通常挂在巩固循环/session-end 这类非关键路径上。
    """
    page_id = _slugify(trigger)
    body = (
        f"## 触发场景\n\n{trigger}\n\n"
        f"## 采取的方法\n\n{approach}\n\n"
        f"## 效果\n\n{outcome}\n\n"
        f"## 是否可复用\n\n{'是' if reusable else '否（场景较特殊，谨慎复用）'}\n"
    )
    links = [
        WikiLink(target=_slugify(name), relation="demonstrates", source="frontmatter")
        for name in (related_entities or [])
    ]
    try:
        return write_page(
            paths,
            page_id=page_id,
            page_type="experience",
            body=body,
            tags=["reusable"] if reusable else [],
            status="active",
            confidence=confidence,
            links=links,
            extra_frontmatter={"source_kind": source_kind},
            overwrite=True,
        )
    except Exception:
        return None


__all__ = ["write_experience", "SOURCE_KIND"]
