"""
wiki/validator.py — 校验页面 frontmatter 与链接完整性

parser.py 解析阶段已经拦掉了"结构性错误"（缺字段、type/status 不在枚举内、
links 格式不对）——这些错误会在 parse_page 时直接抛 PageParseError。

本模块负责"跨页面才能发现的问题"：
    - 死链：links.target 指向不存在的页面 id
    - id 冲突：多个文件声明了同一个 id
    - 孤儿页面（可选提示，非错误）：没有任何入边/出边的页面

供 indexer.py 在全量重建后调用，也可以单独跑做 CI 检查。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from mini_agent.wiki.graph import GraphIndex
from mini_agent.wiki.parser import WikiPage


@dataclass
class ValidationIssue:
    severity: str  # "error" | "warning"
    kind: str  # "dead_link" | "duplicate_id" | "orphan_page"
    page_id: str
    detail: str


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "errors": [vars(i) for i in self.errors],
            "warnings": [vars(i) for i in self.warnings],
        }


def validate_pages(pages: Iterable[WikiPage]) -> ValidationReport:
    pages = list(pages)
    report = ValidationReport()

    seen_ids: dict[str, Path] = {}
    for p in pages:
        if p.id in seen_ids and seen_ids[p.id] != p.path:
            report.issues.append(
                ValidationIssue(
                    severity="error",
                    kind="duplicate_id",
                    page_id=p.id,
                    detail=f"id 在多个文件中重复: {seen_ids[p.id]} 与 {p.path}",
                )
            )
        else:
            seen_ids[p.id] = p.path

    graph = GraphIndex.build(pages)
    for edge in graph.dead_links():
        report.issues.append(
            ValidationIssue(
                severity="error",
                kind="dead_link",
                page_id=edge.source,
                detail=f"链接目标不存在: {edge.source} -> {edge.target} ({edge.relation})",
            )
        )

    known_ids = {p.id for p in pages}
    for pid in known_ids:
        if not graph.outgoing(pid) and not graph.incoming(pid):
            report.issues.append(
                ValidationIssue(
                    severity="warning",
                    kind="orphan_page",
                    page_id=pid,
                    detail="页面没有任何入边或出边，未与其他页面建立关系",
                )
            )

    return report
