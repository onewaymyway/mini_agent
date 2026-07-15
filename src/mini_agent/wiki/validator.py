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

    report.issues.extend(_check_supersession_pairs(pages))

    return report


def _check_supersession_pairs(pages: Iterable["WikiPage"]) -> list[ValidationIssue]:
    """校验 supersedes / superseded_by 关系对的一致性（决策/取舍知识提炼计划阶段一）。

    - superseded_by 指向的页面必须存在，且该页面须有反向 supersedes 指回本页。
    - supersedes 指向的旧页面若没有反向 superseded_by 指回本页，给 warning
      （旧页面可能还没来得及补写，不阻断整体重建）。
    - status=overturned 的 decision 页面若完全没有 superseded_by 出边，给
      warning：推翻了却没有指向替代方案，沿革链条不完整。
    """
    issues: list[ValidationIssue] = []
    by_id = {p.id: p for p in pages}

    for p in by_id.values():
        supersedes_targets = {l.target for l in p.strong_links() if l.relation == "supersedes"}
        superseded_by_targets = {l.target for l in p.strong_links() if l.relation == "superseded_by"}

        for target in superseded_by_targets:
            new_page = by_id.get(target)
            if new_page is None:
                issues.append(ValidationIssue(
                    severity="error", kind="dead_link", page_id=p.id,
                    detail=f"superseded_by 指向不存在的页面: {p.id} -> {target}",
                ))
                continue
            back = {l.target for l in new_page.strong_links() if l.relation == "supersedes"}
            if p.id not in back:
                issues.append(ValidationIssue(
                    severity="warning", kind="inconsistent_supersession", page_id=p.id,
                    detail=f"{p.id} 声明 superseded_by -> {target}，但 {target} 没有反向的 supersedes -> {p.id}",
                ))

        for target in supersedes_targets:
            old_page = by_id.get(target)
            if old_page is None:
                continue  # dead_link 已由通用检查覆盖
            back = {l.target for l in old_page.strong_links() if l.relation == "superseded_by"}
            if p.id not in back:
                issues.append(ValidationIssue(
                    severity="warning", kind="inconsistent_supersession", page_id=p.id,
                    detail=f"{p.id} 声明 supersedes -> {target}，但 {target} 没有反向的 superseded_by -> {p.id}",
                ))

        if p.type == "decision" and p.status == "overturned" and not superseded_by_targets:
            issues.append(ValidationIssue(
                severity="warning", kind="incomplete_supersession_chain", page_id=p.id,
                detail=f"{p.id} status=overturned 但没有 superseded_by 指向替代方案",
            ))

    return issues
