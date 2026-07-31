"""archive/gc.py — 统一的"热文件 → 月度只读归档"迁移器（§4）。

背景：`pending_hits.jsonl`（`consumed:true` 后永久留在文件里）、
`alerts.jsonl`（`acknowledged:true` 同理）、
`goal_relevance_candidates.jsonl`（`judged:true` 同理）、
`notification/reports.jsonl`（`acknowledged:true` 同理）都没有清理/归档
机制——要么无限增长，要么超限直接截断丢弃旧数据，无法支持"过去一个月
外部世界发生了什么"这类回顾式查询。

设计取舍（见 next_doc/external_input_reliability_observability_archive_plan.md
§4.2/§4.5）：
  - 归档文件按自然月分片、只追加、视为只读；不做自动删除/过期。
  - 已处理记录要在热文件里至少保留 `retention_hours` 小时才归档，避免
    "刚点完已读，看板还没刷新就从热文件消失"的观感突兀。
  - 单个 target 归档失败不影响其它 target。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from mini_agent.external_input.filelock import ExclusiveFileLock

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths


@dataclass
class ArchiveTarget:
    hot_path_attr: str        # AgentPaths 上的属性名，如 "external_input_alerts"
    archive_subdir: str       # 归档子目录名，如 "external_input"
    settled_field: str        # 判断"已处理，可以归档"的字段名，如 "acknowledged"
    id_field: str             # 记录的唯一 id 字段，用于日志/去重核对
    time_field: str = "created_at"  # 归档分片依据的时间字段（不同队列命名不同，
                                     # 比如 pending_hits.jsonl 用 "matched_at"）
    retention_hours: int = 24 # 已处理记录在热文件里至少保留这么久才归档


ARCHIVE_TARGETS: list[ArchiveTarget] = [
    ArchiveTarget("external_input_alerts", "external_input", "acknowledged", "alert_id", "created_at"),
    ArchiveTarget("external_input_pending_hits", "external_input", "consumed", "id", "matched_at"),
    ArchiveTarget("external_input_goal_relevance_candidates", "external_input", "judged", "event_id", "created_at"),
    ArchiveTarget("notification_reports", "notification", "acknowledged", "report_id", "created_at"),
]


@dataclass
class ArchiveGcSummary:
    target: str = ""
    ok: bool = True
    error: Optional[str] = None
    total_records: int = 0
    archived_count: int = 0
    kept_count: int = 0


def _load_jsonl(p) -> list[dict]:
    if not p.exists():
        return []
    records: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except Exception:
            continue
    return records


def _year_month(ts: float) -> str:
    return time.strftime("%Y-%m", time.localtime(ts))


def run_archive_gc_once(paths: "AgentPaths", target: ArchiveTarget) -> ArchiveGcSummary:
    """对单个 target 执行一次归档流程：

    1. 独占锁读取热文件全部记录。
    2. 拆成"迁出"（settled_field=true 且时间字段早于
       now - retention_hours）与"保留"两批。
    3. 迁出的记录按其时间字段所在的自然月，append 到
       `.agent/archive/<archive_subdir>/<file_stem>-YYYY-MM.jsonl`
       （只追加，视为只读）。
    4. 热文件整体重写为"剩余记录"，原子替换（写临时文件 + 覆盖）。

    单个 target 归档失败不抛出，返回 `ok=False` + `error`，供调用方跳过
    继续处理下一个 target。
    """
    summary = ArchiveGcSummary(target=target.hot_path_attr)
    try:
        hot_path = getattr(paths, target.hot_path_attr)
    except AttributeError as exc:
        summary.ok = False
        summary.error = f"unknown hot_path_attr: {exc}"
        return summary

    file_stem = hot_path.stem  # 例如 "alerts"（去掉 .jsonl）

    try:
        with ExclusiveFileLock(hot_path):
            records = _load_jsonl(hot_path)
            summary.total_records = len(records)
            if not records:
                return summary

            now = time.time()
            cutoff = now - target.retention_hours * 3600

            to_archive: list[dict] = []
            to_keep: list[dict] = []
            for rec in records:
                settled = bool(rec.get(target.settled_field))
                ts = rec.get(target.time_field)
                if settled and isinstance(ts, (int, float)) and ts < cutoff:
                    to_archive.append(rec)
                else:
                    to_keep.append(rec)

            if not to_archive:
                summary.kept_count = len(to_keep)
                return summary

            # 按自然月分片写入归档文件（追加，视为只读）。
            by_month: dict[str, list[dict]] = {}
            for rec in to_archive:
                ts = rec.get(target.time_field) or now
                ym = _year_month(float(ts))
                by_month.setdefault(ym, []).append(rec)

            for ym, recs in by_month.items():
                archive_path = paths.archive_file(target.archive_subdir, file_stem, ym)
                archive_path.parent.mkdir(parents=True, exist_ok=True)
                with open(archive_path, "a", encoding="utf-8") as f:
                    for rec in recs:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

            # 热文件整体重写为剩余记录（原子替换）。
            tmp = hot_path.with_suffix(hot_path.suffix + ".tmp")
            text = "\n".join(json.dumps(r, ensure_ascii=False) for r in to_keep)
            tmp.write_text(text + ("\n" if text else ""), encoding="utf-8")
            import os
            os.replace(tmp, hot_path)

            summary.archived_count = len(to_archive)
            summary.kept_count = len(to_keep)
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where=f"mini_agent.archive.gc.run_archive_gc_once:{target.hot_path_attr}")
        summary.ok = False
        summary.error = str(exc)

    return summary


def run_archive_gc_all(paths: "AgentPaths", targets: Optional[list[ArchiveTarget]] = None) -> list[ArchiveGcSummary]:
    """依次对所有 target 执行归档，单个失败不影响其它 target 继续归档。"""
    targets = targets if targets is not None else ARCHIVE_TARGETS
    results: list[ArchiveGcSummary] = []
    for target in targets:
        results.append(run_archive_gc_once(paths, target))
    return results


# ── 调度：daemon 启动时补注册 sys:archive_gc（默认每天凌晨 3 点，零 LLM 成本）──

JOB_ID = "sys:archive_gc"


def ensure_archive_gc_job(
    paths: "AgentPaths", cron_scheduler, *, schedule: str = "cron:0 3 * * *",
) -> bool:
    """daemon 启动时调用：缺失才补注册 `sys:archive_gc` job 并注册本地回调
    handler（零 LLM 成本）。返回是否为本次新注册。"""
    existing_ids = {j.id for j in cron_scheduler.list_jobs()}
    newly_added = JOB_ID not in existing_ids
    cron_scheduler.ensure_job(
        job_id=JOB_ID,
        name="外部输入/通知系统热文件长期归档",
        schedule=schedule,
        description=(
            "把 alerts.jsonl/pending_hits.jsonl/goal_relevance_candidates.jsonl/"
            "notification/reports.jsonl 中已处理超过 retention_hours 的记录"
            "按自然月迁出到 .agent/archive/，热文件只保留仍需展示/处理的记录。"
        ),
        tags=["archive", "maintenance"],
    )

    def _handler(job, _paths=paths) -> bool:
        results = run_archive_gc_all(_paths)
        return all(r.ok for r in results)

    cron_scheduler.register_local_handler(JOB_ID, _handler)
    return newly_added


# ── 回顾式查询：GET /v1/archive/query 的底层实现 ──────────────────────────

def _iter_month_range(since: str, until: str):
    """since/until 形如 "YYYY-MM"，按自然月粒度递增枚举（含首尾）。"""
    sy, sm = int(since[:4]), int(since[5:7])
    uy, um = int(until[:4]), int(until[5:7])
    y, m = sy, sm
    while (y, m) <= (uy, um):
        yield f"{y:04d}-{m:02d}"
        m += 1
        if m > 12:
            m = 1
            y += 1


def query_archive(
    paths: "AgentPaths",
    *,
    category: str,
    since: str,
    until: str,
    keyword: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """`GET /v1/archive/query` 的底层查询实现。

    `category` 对应 `ArchiveTarget.archive_subdir`（external_input/
    notification）；`since`/`until` 为 "YYYY-MM" 自然月粒度；`keyword` 对
    `title`/`detail` 做简单子串匹配，不引入全文检索引擎。跨命中月份文件
    做拼接、按时间倒序、分页返回。
    """
    all_records: list[dict] = []
    subdir_path = paths.archive_dir / category
    if subdir_path.exists():
        for ym in _iter_month_range(since, until):
            for f in subdir_path.glob(f"*-{ym}.jsonl"):
                all_records.extend(_load_jsonl(f))

    if keyword:
        kw = keyword.lower()

        def _match(rec: dict) -> bool:
            title = str(rec.get("title") or "").lower()
            detail = str(rec.get("detail") or "").lower()
            return kw in title or kw in detail

        all_records = [r for r in all_records if _match(r)]

    def _sort_key(r: dict):
        for f in ("created_at", "matched_at", "occurred_at", "ts"):
            v = r.get(f)
            if isinstance(v, (int, float)):
                return v
        return 0

    all_records.sort(key=_sort_key, reverse=True)
    total = len(all_records)
    page = all_records[offset: offset + limit] if limit else all_records[offset:]
    return {"records": page, "total": total, "has_more": offset + len(page) < total}


__all__ = [
    "ArchiveTarget",
    "ArchiveGcSummary",
    "ARCHIVE_TARGETS",
    "run_archive_gc_once",
    "run_archive_gc_all",
    "ensure_archive_gc_job",
    "query_archive",
    "JOB_ID",
]
