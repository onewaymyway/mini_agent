"""
wiki/promotion.py — wiki 转正为主索引的评估标准（wiki 式知识库改进计划 P4）

P4 原文定义了三条"转正"（把 `library_index_enabled` 的默认检索路径从
`shelf_search` 切到 `wiki_search`）的量化标准，但明确"暂不执行，先定指标"：

  1. 连续 2 周，`/wiki stats` 显示 world_model + decision + experience
     三类来源合计占比 >= 50%（即不再是"错题本"）。
  2. `wiki_shelf_search`（三段式）与旧 `shelf_search`（分类树两步）做 A/B，
     wiki 侧 grounded 命中率不低于旧方案。
  3. `validator.py` 全量校验无 error 级别问题（死链/id 冲突）持续 1 周。

本模块不做任何"切换"动作本身（那是决策，不是可以自动化的判断），只负责
把这三条标准从"文字描述"变成"可持续观测、可随时查询的量化指标"：

  - `record_daily_snapshot()`：每天最多记一条快照（source_kind 占比 +
    校验错误数），追加进 `paths.wiki_promotion_log_path`。
  - `record_search_comparison()`：每次同时跑了 wiki_search 和 shelf_search
    做人工/自动 A/B 对比时，记一条对比结果，追加进
    `paths.wiki_search_ab_log_path`。
  - `evaluate_promotion_readiness()`：读取上述两份日志，逐条判断三项标准
    是否满足，供 `/wiki promotion` 命令展示。

与项目里其它"观测记录"模块（比如 evolution/outcome_tracker.py 的
traces.jsonl）风格一致：append-only jsonl、原子写、单条记录读写失败不中断
调用方。
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from mini_agent.storage.paths import AgentPaths
from mini_agent.utils.atomic_write import atomic_append_jsonl
from mini_agent.wiki.indexer import discover_pages
from mini_agent.wiki.parser import parse_page
from mini_agent.wiki.stats import WikiStats, compute_stats
from mini_agent.wiki.validator import ValidationReport, validate_pages

# P4 §6 三条标准里被计入"不再是错题本"的 source_kind 集合。
_PROMOTION_SOURCE_KINDS = frozenset(
    {"world_model", "decision", "experience_success", "experience_session_reflection"}
)

_RATIO_THRESHOLD = 0.5          # 目标来源占比阈值
_RATIO_STREAK_DAYS = 14         # 连续 2 周
_VALIDATION_STREAK_DAYS = 7     # 连续 1 周
_AB_MIN_SAMPLES = 20            # A/B 对比样本量低于此值时不下结论，避免小样本噪声误判


def _today_str(today: Optional[date] = None) -> str:
    return (today or date.today()).isoformat()


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


def _target_ratio(stats: WikiStats) -> float:
    if stats.total_pages <= 0:
        return 0.0
    hit = sum(v for k, v in stats.by_source_kind.items() if k in _PROMOTION_SOURCE_KINDS)
    return hit / stats.total_pages


def record_daily_snapshot(
    paths: AgentPaths,
    *,
    validation: Optional[ValidationReport] = None,
    today: Optional[date] = None,
) -> Optional[dict]:
    """记一条当日快照，同一天只记一次（幂等，避免一天内被多次巩固循环触发时刷屏）。

    `validation` 由调用方传入时复用（比如 consolidate() 步骤 6 已经跑过一次
    `build_index()`，其 `IndexResult.validation` 可以直接传进来，不需要本
    模块重新扫描一遍全量页面）；不传则本函数自己跑一次 `validate_pages`。

    返回本次实际写入的记录；当天已有记录时跳过写入并返回 None。
    """
    day = _today_str(today)
    log_path = paths.wiki_promotion_log_path
    existing = _read_jsonl(log_path)
    if any(r.get("date") == day for r in existing):
        return None

    stats = compute_stats(paths)

    if validation is None:
        pages = []
        for md_path in discover_pages(paths):
            try:
                pages.append(parse_page(md_path))
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.wiki.promotion.record_daily_snapshot')
                continue
        validation = validate_pages(pages)

    record = {
        "date": day,
        "total_pages": stats.total_pages,
        "target_ratio": round(_target_ratio(stats), 4),
        "validation_errors": len(validation.errors),
    }
    try:
        atomic_append_jsonl(log_path, record)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.wiki.promotion.record_daily_snapshot')
        return None
    return record


def record_search_comparison(
    paths: AgentPaths,
    *,
    wiki_grounded: bool,
    shelf_grounded: bool,
    query: str = "",
    today: Optional[date] = None,
) -> None:
    """记一条 wiki_search vs shelf_search 的 A/B 命中对比。

    "grounded" 由调用方判定"这次检索是否真正给出了有依据的结果"——wiki 侧
    通常取 `WikiSearchResult.grounded_page_ids` 是否非空，shelf 侧可以是
    `shelf_search()` 返回列表是否非空，或者接入
    `record_retrieval_feedback()` 的 useful 判断，本模块不替调用方做这个
    判断，只负责记录结果。失败静默降级（观测记录不应该影响检索主流程）。
    """
    record = {
        "date": _today_str(today),
        "query": query[:200],
        "wiki_grounded": bool(wiki_grounded),
        "shelf_grounded": bool(shelf_grounded),
    }
    try:
        atomic_append_jsonl(paths.wiki_search_ab_log_path, record)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.wiki.promotion.record_search_comparison')
        pass


@dataclass
class PromotionReadiness:
    ratio_ok: bool = False
    ratio_days_observed: int = 0
    ratio_days_required: int = _RATIO_STREAK_DAYS
    current_ratio: float = 0.0

    validation_ok: bool = False
    validation_days_observed: int = 0
    validation_days_required: int = _VALIDATION_STREAK_DAYS
    latest_validation_errors: Optional[int] = None

    ab_ok: Optional[bool] = None  # None = 样本不足，尚无法下结论
    ab_sample_size: int = 0
    wiki_hit_rate: Optional[float] = None
    shelf_hit_rate: Optional[float] = None

    @property
    def overall_ready(self) -> bool:
        return self.ratio_ok and self.validation_ok and bool(self.ab_ok)

    def to_dict(self) -> dict:
        return {
            "overall_ready": self.overall_ready,
            "ratio": {
                "ok": self.ratio_ok,
                "days_observed": self.ratio_days_observed,
                "days_required": self.ratio_days_required,
                "current_ratio": self.current_ratio,
                "threshold": _RATIO_THRESHOLD,
            },
            "validation": {
                "ok": self.validation_ok,
                "days_observed": self.validation_days_observed,
                "days_required": self.validation_days_required,
                "latest_errors": self.latest_validation_errors,
            },
            "search_ab": {
                "ok": self.ab_ok,
                "sample_size": self.ab_sample_size,
                "wiki_hit_rate": self.wiki_hit_rate,
                "shelf_hit_rate": self.shelf_hit_rate,
            },
        }


def _consecutive_days_meeting(
    records: list[dict],
    *,
    date_key: str,
    predicate,
    required_days: int,
    latest_day: Optional[date] = None,
) -> tuple[bool, int]:
    """判断"以最新一条记录的日期为终点，往前数 required_days 个自然日"

    是否每天都有记录且都满足 predicate。返回 (是否达标, 实际连续满足的天数
    ——从最新日期往前数，直到第一次缺记录或不满足为止)。

    只看"自然日连续"，不要求记录本身连续追加（比如中间跳过的周末/未运行
    的日子会被视为"缺记录"从而中断连续计数，这是有意为之——转正标准要求
    的是"持续观测都达标"，观测缺口本身就说明还不够稳定）。
    """
    if not records:
        return False, 0
    by_date: dict[str, dict] = {}
    for r in records:
        d = r.get(date_key)
        if d:
            by_date[d] = r  # 同一天多条时后写的覆盖前面的（记录本身应该是幂等的）

    if not by_date:
        return False, 0

    end_day = latest_day or max(
        (datetime.strptime(d, "%Y-%m-%d").date() for d in by_date), default=None
    )
    if end_day is None:
        return False, 0

    streak = 0
    cursor = end_day
    while True:
        key = cursor.isoformat()
        rec = by_date.get(key)
        if rec is None or not predicate(rec):
            break
        streak += 1
        cursor = cursor - timedelta(days=1)

    return streak >= required_days, streak


def evaluate_promotion_readiness(
    paths: AgentPaths,
    *,
    ratio_threshold: float = _RATIO_THRESHOLD,
    ratio_streak_days: int = _RATIO_STREAK_DAYS,
    validation_streak_days: int = _VALIDATION_STREAK_DAYS,
    ab_min_samples: int = _AB_MIN_SAMPLES,
) -> PromotionReadiness:
    """汇总 promotion_log.jsonl + search_ab_log.jsonl，逐条判断 P4 三项标准。

    只读，不做任何写入，可以随时调用（`/wiki promotion` 命令的数据来源）。
    """
    daily_records = _read_jsonl(paths.wiki_promotion_log_path)
    ab_records = _read_jsonl(paths.wiki_search_ab_log_path)

    result = PromotionReadiness(
        ratio_days_required=ratio_streak_days,
        validation_days_required=validation_streak_days,
    )

    if daily_records:
        latest = max(daily_records, key=lambda r: r.get("date", ""))
        result.current_ratio = float(latest.get("target_ratio") or 0.0)
        result.latest_validation_errors = latest.get("validation_errors")

        ratio_ok, ratio_days = _consecutive_days_meeting(
            daily_records,
            date_key="date",
            predicate=lambda r: float(r.get("target_ratio") or 0.0) >= ratio_threshold,
            required_days=ratio_streak_days,
        )
        result.ratio_ok = ratio_ok
        result.ratio_days_observed = ratio_days

        validation_ok, validation_days = _consecutive_days_meeting(
            daily_records,
            date_key="date",
            predicate=lambda r: int(r.get("validation_errors") or 0) == 0,
            required_days=validation_streak_days,
        )
        result.validation_ok = validation_ok
        result.validation_days_observed = validation_days

    if ab_records:
        result.ab_sample_size = len(ab_records)
        wiki_hits = sum(1 for r in ab_records if r.get("wiki_grounded"))
        shelf_hits = sum(1 for r in ab_records if r.get("shelf_grounded"))
        result.wiki_hit_rate = round(wiki_hits / len(ab_records), 4)
        result.shelf_hit_rate = round(shelf_hits / len(ab_records), 4)
        if len(ab_records) >= ab_min_samples:
            result.ab_ok = result.wiki_hit_rate >= result.shelf_hit_rate

    return result


__all__ = [
    "PromotionReadiness",
    "record_daily_snapshot",
    "record_search_comparison",
    "evaluate_promotion_readiness",
]
