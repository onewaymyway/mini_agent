"""stock_watch/candidate_pool.py — 候选池账本：合并/去重/评分/淘汰。

候选池是本项目"功能 1"的核心状态，存成 `data/candidate_pool.json`
（列表，每条一个标的），本模块只做纯逻辑（合并多个数据源结果、按热度
打分排序、按 `max_size` 淘汰），不直接依赖网络，方便离线单元测试。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from stock_watch.data_sources import HotStockItem


@dataclass
class CandidateEntry:
    code: str
    name: str
    type: str = "stock"          # "stock" | "etf"
    score: float = 0.0
    sources: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    first_seen: str = ""
    last_seen: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CandidateEntry":
        return cls(
            code=data.get("code", ""),
            name=data.get("name", ""),
            type=data.get("type", "stock"),
            score=float(data.get("score", 0.0)),
            sources=list(data.get("sources", [])),
            reasons=list(data.get("reasons", [])),
            first_seen=data.get("first_seen", ""),
            last_seen=data.get("last_seen", ""),
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_pool(path: Path) -> Dict[str, CandidateEntry]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # 与账本损坏容错的既有约定一致：损坏时退化为空池，而不是炸掉整次运行
        return {}
    return {item["code"]: CandidateEntry.from_dict(item) for item in raw}


def save_pool(path: Path, pool: Dict[str, CandidateEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(pool.values(), key=lambda e: e.score, reverse=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps([e.to_dict() for e in ordered], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def save_pool_snapshot(
    snapshots_dir: Path, pool: Dict[str, CandidateEntry], *, on: Optional[str] = None
) -> Path:
    """把候选池当前状态额外归档一份到 `snapshots_dir/<date>.json`。

    与 `save_pool()` 覆盖式保存的当前快照（`data/candidate_pool.json`）
    不同——这里是"追加一份带日期的历史存档"，供
    `entrypoints/reconcile_outcomes.py` 回溯"N 天前打了高分的标的后续
    表现如何"用。同一天多次运行（比如手动重跑）直接覆盖当天的存档文件，
    不追加多份，因为回溯只关心"当天收盘时的候选池状态"这个粒度，不需要
    日内多次快照。

    对应 `next_doc/stock_watch_continuous_improvement_plan.md` 阶段 3——
    该文档指出这是 `candidate_pool.py` 此前实现的一个缺口：`save_pool`
    只保留当下快照，没有历史，导致结果回溯无源可查。
    """
    date_str = on or datetime.now(timezone.utc).strftime("%Y%m%d")
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    out_path = snapshots_dir / f"{date_str}.json"
    ordered = sorted(pool.values(), key=lambda e: e.score, reverse=True)
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps([e.to_dict() for e in ordered], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(out_path)
    return out_path


def load_pool_snapshot(snapshots_dir: Path, date_str: str) -> Dict[str, CandidateEntry]:
    """读取某一天的候选池归档快照；文件不存在或损坏时返回空字典
    （与 `load_pool()` 同样的容错约定——回溯任务里某一天恰好没有存档，
    不应该让整次回溯作业崩掉，跳过那一天即可）。
    """
    path = snapshots_dir / f"{date_str}.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {item["code"]: CandidateEntry.from_dict(item) for item in raw}


def list_snapshot_dates(snapshots_dir: Path) -> List[str]:
    """列出已归档的快照日期（`YYYYMMDD`），按时间升序。"""
    if not snapshots_dir.exists():
        return []
    dates = [p.stem for p in snapshots_dir.glob("*.json") if p.stem.isdigit()]
    return sorted(dates)


def merge_hot_items(
    pool: Dict[str, CandidateEntry],
    items: List[HotStockItem],
    *,
    entry_type: str = "stock",
) -> Dict[str, CandidateEntry]:
    """把一批抓取结果合并进候选池：已存在则加分+更新来源，不存在则新建。"""
    now = _now_iso()
    for item in items:
        if not item.code:
            continue
        entry = pool.get(item.code)
        if entry is None:
            entry = CandidateEntry(
                code=item.code, name=item.name, type=entry_type,
                first_seen=now,
            )
            pool[item.code] = entry
        entry.name = entry.name or item.name
        entry.score += max(item.heat_score, 0.1)
        if item.source not in entry.sources:
            entry.sources.append(item.source)
        if item.reason and item.reason not in entry.reasons:
            entry.reasons.append(item.reason)
        entry.last_seen = now
    return pool


def apply_decay(
    pool: Dict[str, CandidateEntry], *, decay_days: int, decay_rate: float = 0.5
) -> Dict[str, CandidateEntry]:
    """超过 `decay_days` 天未被任何数据源再次提及的标的，分数打折。"""
    now = datetime.now(timezone.utc)
    for entry in pool.values():
        if not entry.last_seen:
            continue
        try:
            last = datetime.fromisoformat(entry.last_seen)
        except ValueError:
            continue
        if (now - last).days > decay_days:
            entry.score *= decay_rate
    return pool


def enforce_max_size(pool: Dict[str, CandidateEntry], max_size: int) -> Dict[str, CandidateEntry]:
    if len(pool) <= max_size:
        return pool
    ranked = sorted(pool.values(), key=lambda e: e.score, reverse=True)
    keep = {e.code for e in ranked[:max_size]}
    return {code: entry for code, entry in pool.items() if code in keep}


def ensure_seeds(
    pool: Dict[str, CandidateEntry], seeds
) -> Dict[str, CandidateEntry]:
    """种子标的始终保留在候选池里，不受淘汰影响（先合并，淘汰时另行豁免）。"""
    now = _now_iso()
    for seed in seeds:
        entry = pool.get(seed.code)
        if entry is None:
            pool[seed.code] = CandidateEntry(
                code=seed.code, name=seed.name, type=seed.type,
                score=1.0, sources=["seed"], first_seen=now, last_seen=now,
            )
    return pool
