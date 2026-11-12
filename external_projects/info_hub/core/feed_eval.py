"""推荐信息流的离线回放评估（P2，见 next_doc/info_hub_recommendation_feed_design.md 第 7.1 节）。

做法：取 shown.jsonl 里每一批次“推荐”展示的结果，以之后一段时间内的反馈与打开作为标签：
- 正例：该条目之后被标记“感兴趣”或被打开；
- 负例：之后被标记“不感兴趣”（且没有正例）；
- 无标签：忽略（既不算对，也不算错）。

对每一批次比较两种顺序，都只在这一批次展示过的条目之内排：
- 推荐顺序：展示时的位置（feed_v1 的实际排序）；
- 基线顺序：按首次出现时间倒序（“抓取结果”页的默认 plain 视图）。

指标（前 K 条，默认 10）：正例率、👎 率、NDCG@K（二元相关性）。

局限（必须看到结果时一并考虑）：
- 位置偏差：排在前面的条目更容易被看到和打开，正例标签本身偏向推荐顺序靠前的条目。
- 只覆盖“展示过”的条目，没有展示的条目没有标签。
- 标签稀疏时结果波动大，样本不足时只显示数字，并明确提示“仅供参考”。
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from core.feed import _parse_time, shown_path
from core.feedback import read_feedback
from core.store import read_jsonl

MIN_LABELED = 20          # 有标签的展示条目少于该数，不给结论
LABEL_WINDOW_DAYS = 7     # 展示后多久内的反馈算作该展示的标签


def _labels(data_dir: Path, since: datetime) -> dict[str, list[tuple[datetime, int]]]:
    """每个条目的标签事件：(时间, +1 正例 / -1 负例)。只保留 since 之后的事件。"""
    from core.evaluation import exposure_path
    out: dict[str, list[tuple[datetime, int]]] = {}

    def add(key: str, when: Optional[datetime], value: int) -> None:
        if key and when is not None and when >= since:
            out.setdefault(key, []).append((when, value))

    for e in read_feedback(data_dir):
        value = {"interested": 1, "not_interested": -1}.get(e.get("action"))
        if value is not None:
            add(e.get("item_key", ""), _parse_time(e.get("time")), value)
    for row in read_jsonl(exposure_path(data_dir)):
        if row.get("kind") == "open":
            add(row.get("item_key", ""), _parse_time(row.get("time")), 1)
    return out


def _label_for(events: list[tuple[datetime, int]], batch_time: datetime, window_days: float) -> Optional[int]:
    """批次之后、窗口之内的标签：有正例即正例；否则有负例为负例；都没有为 None。"""
    end = batch_time + timedelta(days=window_days)
    window = [v for when, v in events if batch_time <= when <= end]
    if any(v > 0 for v in window):
        return 1
    if any(v < 0 for v in window):
        return 0
    return None


def _metrics(order: list[dict[str, Any]], labels: dict[str, Optional[int]], k: int) -> dict[str, float]:
    top = order[:k]
    if not top:
        return {"precision": 0.0, "negative_rate": 0.0, "ndcg": 0.0}
    rel = [1 if labels.get(x["item_key"]) == 1 else 0 for x in top]
    neg = sum(1 for x in top if labels.get(x["item_key"]) == 0)
    dcg = sum(r / math.log2(i + 2) for i, r in enumerate(rel))
    total_pos = sum(1 for x in order if labels.get(x["item_key"]) == 1)
    idcg = sum(1 / math.log2(i + 2) for i in range(min(k, total_pos)))
    return {
        "precision": round(sum(rel) / len(top), 4),
        "negative_rate": round(neg / len(top), 4),
        "ndcg": round(dcg / idcg, 4) if idcg else 0.0,
    }


def replay(data_dir: Path, *, now: Optional[datetime] = None, days: float = 30, top_k: int = 10,
           min_labeled: int = MIN_LABELED, label_window_days: float = LABEL_WINDOW_DAYS) -> dict[str, Any]:
    now = now or datetime.now().astimezone()
    since = now - timedelta(days=days)
    labels_by_key = _labels(data_dir, since)

    batches = []
    for row in read_jsonl(shown_path(data_dir)):
        if row.get("kind") != "feed_impression":
            continue
        when = _parse_time(row.get("time"))
        if when is None or when < since:
            continue
        items = [x for x in (row.get("items") or []) if x.get("item_key")]
        if len(items) < 2:
            continue
        labels: dict[str, Optional[int]] = {
            x["item_key"]: _label_for(labels_by_key.get(x["item_key"], []), when, label_window_days) for x in items}
        if all(v is None for v in labels.values()):
            continue
        batches.append((items, labels))

    labeled_total = sum(sum(1 for v in labels.values() if v is not None) for _, labels in batches)
    agg_new = {"precision": [], "negative_rate": [], "ndcg": []}
    agg_base = {"precision": [], "negative_rate": [], "ndcg": []}
    for items, labels in batches:
        new_order = sorted(items, key=lambda x: x.get("position", 0))
        base_order = sorted(items, key=lambda x: str(x.get("first_seen") or ""), reverse=True)
        k = min(top_k, len(items))
        for agg, order in ((agg_new, new_order), (agg_base, base_order)):
            m = _metrics(order, labels, k)
            for name in agg:
                agg[name].append(m[name])

    def mean(xs: list[float]) -> Optional[float]:
        return round(sum(xs) / len(xs), 4) if xs else None

    sufficient = labeled_total >= min_labeled and bool(batches)
    return {
        "window_days": days,
        "top_k": top_k,
        "batches": len(batches),
        "labeled_items": labeled_total,
        "sufficient": sufficient,
        "message": ("样本充足，但仍有位置偏差，结果仅供参考。" if sufficient
                    else f"样本不足（有标签的展示条目 {labeled_total} 条，需要至少 {min_labeled} 条），结果仅供参考。"),
        "recommended": {name: mean(v) for name, v in agg_new.items()},
        "baseline": {name: mean(v) for name, v in agg_base.items()},
    }
