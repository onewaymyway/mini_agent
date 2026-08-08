"""
wiki/quarantine.py — 解析失败页面的检测与记录（问题数据隔离区）

背景：`wiki/parser.py::parse_page()` 遇到结构性错误（frontmatter 缺字段、
links 格式不对等）会抛 `PageParseError`；`wiki/stats.py::compute_stats()` /
`wiki/indexer.py::build_index()` 目前的处理方式是"捕获异常、log_exception、
跳过这一页、继续处理其它页面"——这保证了一个页面写坏不会让全库统计/索引
不可用，但问题数据本身会一直留在磁盘上，每次扫描都重新触发同一条异常日志，
没有人会去主动翻日志把它修好，属于"发现了问题，但发现即止"。

本模块把"发现"和"记录"分开做成一件事本身：
    - `record_issue()`：解析失败时调用，把 (页面路径, 错误类型, 错误信息)
      写成一条持久化记录，而不只是打一条日志。多次检测到同一个页面的
      同一类问题时合并计数，不重复建记录。
    - `scan_and_record()`：对整个 wiki/ 目录做一次全量扫描，发现新的解析
      失败页面、记录到隔离区；同时对已经在隔离区里、但现在能正常解析的
      页面做"自愈确认"（可能是人工手动修好的，或者被
      `wiki/quarantine_repair.py` 修好后又重新验证了一遍）——避免隔离区
      里堆积已经不再是问题的历史记录。

存储：整表 JSON（`AgentPaths.wiki_quarantine_path`），`page_path ->
QuarantineRecord`。选择整表重写而不是追加日志，是因为这里天然需要"同一个
页面的记录去重/更新状态"的语义（参考 `evolution/wiki_utility_audit.py` 的
`usage_stats.json` 同类做法），预期问题页面数量远小于总页面数，整表重写
的开销可以忽略。

真正的"修复"逻辑（怎么把 links 里的裸字符串改成 {target: ...} 这种具体
修法）放在 `wiki/quarantine_repair.py`，跟"发现/记录"职责分开——这样以后
要新增修复策略，不需要碰这个模块。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from mini_agent.storage.paths import AgentPaths
from mini_agent.utils.atomic_write import atomic_write_json
from mini_agent.wiki.indexer import discover_pages
from mini_agent.wiki.parser import parse_page

STATUS_PENDING = "pending"           # 待修复
STATUS_REPAIRED = "repaired"         # 已修复（保留记录供追溯，不再参与修复循环）
STATUS_NEEDS_HUMAN = "needs_human"   # 自动修复尝试次数已耗尽，或没有匹配的修复策略

# 单个页面自动修复的最大尝试次数——超过后转 needs_human，避免对一份
# 自动修复策略无法处理的坏数据每个 cron 周期都重复尝试、刷屏日志。
DEFAULT_MAX_REPAIR_ATTEMPTS = 5


@dataclass
class QuarantineRecord:
    page_path: str                      # 绝对路径字符串（检测和修复运行在
                                         # 同一台机器/同一个 paths 上下文，
                                         # 直接存绝对路径最简单可靠）
    error_type: str                     # 异常类名，如 "PageParseError"
    error_message: str
    status: str = STATUS_PENDING
    first_seen_at: float = 0.0
    last_seen_at: float = 0.0
    detect_count: int = 1               # 累计检测到同一问题的次数
    repair_attempts: int = 0
    last_attempt_at: Optional[float] = None
    last_attempt_error: Optional[str] = None
    repaired_at: Optional[float] = None
    repaired_by: Optional[str] = None   # 命中的修复策略名（供追溯）

    def to_dict(self) -> dict:
        return {
            "page_path": self.page_path,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "status": self.status,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
            "detect_count": self.detect_count,
            "repair_attempts": self.repair_attempts,
            "last_attempt_at": self.last_attempt_at,
            "last_attempt_error": self.last_attempt_error,
            "repaired_at": self.repaired_at,
            "repaired_by": self.repaired_by,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "QuarantineRecord":
        return cls(
            page_path=str(d.get("page_path", "")),
            error_type=str(d.get("error_type", "")),
            error_message=str(d.get("error_message", "")),
            status=str(d.get("status", STATUS_PENDING)),
            first_seen_at=float(d.get("first_seen_at") or 0.0),
            last_seen_at=float(d.get("last_seen_at") or 0.0),
            detect_count=int(d.get("detect_count") or 1),
            repair_attempts=int(d.get("repair_attempts") or 0),
            last_attempt_at=d.get("last_attempt_at"),
            last_attempt_error=d.get("last_attempt_error"),
            repaired_at=d.get("repaired_at"),
            repaired_by=d.get("repaired_by"),
        )


@dataclass
class ScanReport:
    scanned: int = 0
    newly_quarantined: int = 0
    still_pending: int = 0
    auto_resolved: int = 0   # 曾经在隔离区、现在能正常解析了，自动摘除
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "newly_quarantined": self.newly_quarantined,
            "still_pending": self.still_pending,
            "auto_resolved": self.auto_resolved,
            "errors": self.errors,
        }


def load_quarantine(paths: AgentPaths) -> dict[str, QuarantineRecord]:
    """加载隔离区全表，key 是页面绝对路径字符串。文件不存在/损坏都返回
    空 dict（消费方不应因为隔离区本身读取失败就连带崩溃——这是一份辅助
    诊断数据，不是核心索引）。"""
    p = paths.wiki_quarantine_path
    if not p.exists():
        return {}
    try:
        import json

        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, QuarantineRecord] = {}
    for key, raw in (data.get("pages") or {}).items():
        try:
            out[key] = QuarantineRecord.from_dict(raw)
        except Exception:
            continue
    return out


def _save_quarantine(paths: AgentPaths, records: dict[str, QuarantineRecord]) -> None:
    paths.ensure_wiki_dirs()
    atomic_write_json(
        paths.wiki_quarantine_path,
        {"updated_at": time.time(), "pages": {k: v.to_dict() for k, v in records.items()}},
    )


def save_quarantine(paths: AgentPaths, records: dict[str, QuarantineRecord]) -> None:
    """公开的整表落盘入口，供 `quarantine_repair.py` 更新修复状态时使用
    （避免跨模块引用私有的 `_save_quarantine`）。"""
    _save_quarantine(paths, records)


def record_issue(paths: AgentPaths, page_path: Path, exc: Exception) -> QuarantineRecord:
    """记一条解析失败问题。同一个页面路径已有记录时合并（更新
    last_seen_at/detect_count/错误信息），不重复建记录；若该页面此前是
    `repaired` 状态、现在又解析失败了（比如被重新编辑坏了），重新置为
    `pending` 并清零修复尝试计数——这是一份"新"问题，不该沿用旧的尝试
    次数上限。
    """
    records = load_quarantine(paths)
    key = str(page_path)
    now = time.time()
    error_type = type(exc).__name__
    error_message = str(exc)[:2000]

    existing = records.get(key)
    if existing is None:
        rec = QuarantineRecord(
            page_path=key,
            error_type=error_type,
            error_message=error_message,
            first_seen_at=now,
            last_seen_at=now,
        )
    else:
        rec = existing
        rec.last_seen_at = now
        rec.detect_count += 1
        rec.error_type = error_type
        rec.error_message = error_message
        if rec.status == STATUS_REPAIRED:
            rec.status = STATUS_PENDING
            rec.repair_attempts = 0
            rec.last_attempt_at = None
            rec.last_attempt_error = None
            rec.repaired_at = None
            rec.repaired_by = None

    records[key] = rec
    _save_quarantine(paths, records)
    return rec


def resolve_if_present(paths: AgentPaths, page_path: Path) -> bool:
    """页面现在能正常解析了（无论是人工修好还是自动修复成功），把隔离区
    里对应记录标记为 `repaired`。不存在对应记录时是安全的空操作，返回
    `False`。"""
    records = load_quarantine(paths)
    key = str(page_path)
    rec = records.get(key)
    if rec is None or rec.status == STATUS_REPAIRED:
        return False
    rec.status = STATUS_REPAIRED
    rec.repaired_at = time.time()
    records[key] = rec
    _save_quarantine(paths, records)
    return True


def scan_and_record(paths: AgentPaths) -> ScanReport:
    """对 wiki/ 全量扫描一遍，发现的新解析失败页面记入隔离区；同时对
    隔离区里已有记录、但现在解析正常的页面做自愈确认并摘除。

    独立于 `compute_stats()` / `build_index()` 之外单独提供一个全量扫描
    入口，是因为后两者不是每次都会被调用（`build_index` 只在用户手动
    `/wiki reindex` 或索引缺失时触发），而"发现问题"这个机制需要有一个
    稳定的、周期性运行的触发点——这个触发点就是
    `sys:wiki_quarantine_repair` cron job（见 `quarantine_repair.py`），
    它每次运行都会先调用本函数做一次全量探测，再对 pending 记录尝试
    修复。
    """
    report = ScanReport()
    records = load_quarantine(paths)
    seen_this_scan: set[str] = set()

    for md_path in discover_pages(paths):
        report.scanned += 1
        key = str(md_path)
        seen_this_scan.add(key)
        try:
            parse_page(md_path)
        except Exception as exc:
            existing = records.get(key)
            was_new = existing is None or existing.status == STATUS_REPAIRED
            record_issue(paths, md_path, exc)
            if was_new:
                report.newly_quarantined += 1
            else:
                report.still_pending += 1
            continue
        # 解析成功：如果隔离区里还挂着这个页面的 pending/needs_human
        # 记录，说明它已经被修好了（人工或自动），做自愈确认。
        if key in records and records[key].status != STATUS_REPAIRED:
            try:
                resolve_if_present(paths, md_path)
                report.auto_resolved += 1
            except Exception as exc:  # noqa: BLE001 - 自愈确认失败不影响主扫描结果
                report.errors.append(f"resolve_if_present failed for {key}: {exc}")

    return report


__all__ = [
    "STATUS_PENDING",
    "STATUS_REPAIRED",
    "STATUS_NEEDS_HUMAN",
    "DEFAULT_MAX_REPAIR_ATTEMPTS",
    "QuarantineRecord",
    "ScanReport",
    "load_quarantine",
    "save_quarantine",
    "record_issue",
    "resolve_if_present",
    "scan_and_record",
]
