"""stock_watch/outcomes.py — 结果回溯（功能扩展：候选池"预测 vs 结果"）。

对应 `next_doc/stock_watch_continuous_improvement_plan.md` 第 3.2 节：
评价"选股/打分逻辑本身好不好"的唯一站得住脚的依据，是把某天候选池里
打了分的标的，和它后续的实际涨跌对照起来看。这是 stock_watch 的业务
逻辑（不是框架能力），产出写进 `data/outcome_ledger.jsonl`。

本模块只做纯逻辑（给定候选池快照 + 一批已经查好的涨跌幅，拼出结果
记录、挑出"值得关注"的案例），不直接触网——网络请求（
`data_sources.fetch_price_change_pct`）由调用方
（`entrypoints/reconcile_outcomes.py`）负责，这样本模块可以用固定
mock 数据离线单测，呼应 `PROJECT.md`/`analysis.py` 已经确立的
"抓取与结构化逻辑分层"的写法。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from stock_watch.candidate_pool import CandidateEntry


@dataclass
class OutcomeRecord:
    code: str
    name: str
    snapshot_date: str          # 候选池快照日期，YYYYMMDD
    evaluated_at: str           # 本次回溯发生的时间，ISO-8601
    score_at_snapshot: float
    change_pct: Optional[float]  # None 表示查不到（停牌/退市/接口失败）
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "OutcomeRecord":
        return cls(
            code=data.get("code", ""),
            name=data.get("name", ""),
            snapshot_date=data.get("snapshot_date", ""),
            evaluated_at=data.get("evaluated_at", ""),
            score_at_snapshot=float(data.get("score_at_snapshot", 0.0)),
            change_pct=data.get("change_pct"),
            error=data.get("error"),
        )

    @property
    def ok(self) -> bool:
        return self.change_pct is not None


def build_outcome_records(
    snapshot: Dict[str, CandidateEntry],
    change_pcts: Dict[str, float],
    errors: Dict[str, str],
    *,
    snapshot_date: str,
) -> List[OutcomeRecord]:
    """把一份候选池快照 + 一批已经查好的涨跌幅/错误信息，拼成结果记录。

    `change_pcts`/`errors` 通常互斥（一个标的要么查到了涨跌幅，要么
    记了失败原因），但不强制校验——两边都没有时 `change_pct=None` 且
    `error=None`，代表"调用方没查这个标的"，属于调用方自己的责任。
    """
    now = datetime.now(timezone.utc).isoformat()
    records = []
    for code, entry in snapshot.items():
        records.append(
            OutcomeRecord(
                code=code, name=entry.name, snapshot_date=snapshot_date,
                evaluated_at=now, score_at_snapshot=entry.score,
                change_pct=change_pcts.get(code), error=errors.get(code),
            )
        )
    return records


def append_outcomes(path: Path, records: List[OutcomeRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")


def read_outcomes(path: Path, *, limit: Optional[int] = None) -> List[OutcomeRecord]:
    if not path.exists():
        return []
    records: List[OutcomeRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(OutcomeRecord.from_dict(json.loads(line)))
        except (json.JSONDecodeError, TypeError):
            continue
    if limit is not None:
        records = records[-limit:]
    return records


def notable_outcomes(
    records: List[OutcomeRecord], *, threshold_pct: float
) -> List[OutcomeRecord]:
    """挑出涨跌幅绝对值超过阈值的案例——无论是"打了高分确实大涨"（说明
    评分逻辑起作用）还是"打了高分结果大跌"（说明评分逻辑可能有问题），
    这类案例都值得被 review session 看到，写进改进积压账本（见
    `entrypoints/reconcile_outcomes.py`）。
    """
    return [r for r in records if r.ok and abs(r.change_pct) >= threshold_pct]


def summarize_by_score_bucket(records: List[OutcomeRecord]) -> Dict[str, dict]:
    """按打分区间（<10 / 10-50 / >=50）汇总平均涨跌幅——粗粒度地回答
    "分数高的标的是不是确实后续表现更好"这个问题，供报告展示。只统计
    `ok=True`（查到了涨跌幅）的记录，跳过查不到的。
    """
    buckets = {"<10": [], "10-50": [], ">=50": []}
    for r in records:
        if not r.ok:
            continue
        if r.score_at_snapshot < 10:
            buckets["<10"].append(r.change_pct)
        elif r.score_at_snapshot < 50:
            buckets["10-50"].append(r.change_pct)
        else:
            buckets[">=50"].append(r.change_pct)
    return {
        bucket: {
            "count": len(changes),
            "avg_change_pct": (sum(changes) / len(changes)) if changes else None,
        }
        for bucket, changes in buckets.items()
    }
