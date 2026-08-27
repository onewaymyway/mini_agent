"""stock_watch/candidate_pool.py — 候选池账本：合并/去重/评分/淘汰。

候选池是本项目"功能 1"的核心状态，存成 `data/candidate_pool.json`
（列表，每条一个标的），本模块只做纯逻辑（合并多个数据源结果、按热度
打分排序、按 `max_size` 淘汰），不直接依赖网络，方便离线单元测试。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("stock_watch.candidate_pool")

from stock_watch.data_sources import HotStockItem

# 候选池状态机（次-stock_watch_pool_state_tracking_and_kanban_plan.md 阶段2）。
# 不用 Enum：与仓库既有的"轻量 dataclass 优先"风格保持一致，且状态值本身
# 就要落盘成字符串，用 Enum 反而多一层转换。
POOL_STATES = (
    "watching",        # 观察池（默认，进池即此状态）
    "focused",         # 重点关注
    "buy_suggested",   # 建议买入
    "holding",         # 已建仓
    "sell_suggested",  # 建议卖出
    "dropped",         # 已淘汰（终态，不参与每日跟踪，但保留历史）
)
DEFAULT_STATE = "watching"
TERMINAL_STATES = ("dropped",)


@dataclass
class StateEvent:
    """候选池标的的一次状态变更记录。"""

    state: str
    entered_at: str                          # ISO8601
    price_at_entry: Optional[float] = None   # 取不到价格时为 None，不阻塞状态变更
    note: str = ""                            # 变更原因：人工填写或"信号自动触发：xxx"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "StateEvent":
        return cls(
            state=data.get("state", DEFAULT_STATE),
            entered_at=data.get("entered_at", ""),
            price_at_entry=(
                float(data["price_at_entry"])
                if data.get("price_at_entry") is not None
                else None
            ),
            note=data.get("note", ""),
        )


@dataclass
class StateReturn:
    """某一段状态区间的收益快照，供 `compute_state_returns()` 输出。"""

    state: str
    entered_at: str
    price_at_entry: Optional[float]
    days_in_state: int
    change_pct: Optional[float]   # price_at_entry 或 current_price 缺失时为 None


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
    state: str = DEFAULT_STATE
    state_history: List[StateEvent] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["state_history"] = [e.to_dict() for e in self.state_history]
        return data

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
            # 兼容阶段2之前写入的旧数据：缺 state/state_history 时给默认值，
            # 不能让老的 candidate_pool.json 读取报错。
            state=data.get("state", DEFAULT_STATE),
            state_history=[
                StateEvent.from_dict(e) for e in data.get("state_history", [])
            ],
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
            # 新标的进池即进入默认状态 watching，立即记一条状态事件；
            # price_at_entry 在这里留空（candidate_pool.py 是纯逻辑模块，
            # 不发起网络请求），由调用方（run_hotlist_scan.py）在拿到
            # 抓取结果、查完价格后统一回填，见 backfill_entry_price()。
            entry.state_history.append(StateEvent(state=DEFAULT_STATE, entered_at=now))
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
    # 已经进入 watching 之外状态（重点关注/建议买入/已建仓/建议卖出）的
    # 标的是用户已经显式做过判断的对象，不应该被单纯的热度衰减自动清出
    # 池子——需要用户显式操作降级到 dropped 才会被下一轮淘汰考虑到。
    protected = {code for code, e in pool.items() if e.state != DEFAULT_STATE}
    candidates = [e for e in pool.values() if e.code not in protected]
    if len(protected) >= max_size:
        if candidates:
            logger.warning(
                "候选池中受保护的非 watching 标的数量(%d)已达到或超过 max_size(%d)，"
                "本轮不淘汰任何 watching 标的",
                len(protected), max_size,
            )
        return {code: entry for code, entry in pool.items() if code in protected}
    keep_extra = max_size - len(protected)
    ranked = sorted(candidates, key=lambda e: e.score, reverse=True)
    keep = protected | {e.code for e in ranked[:keep_extra]}
    return {code: entry for code, entry in pool.items() if code in keep}


def backfill_entry_price(entry: CandidateEntry, price: Optional[float]) -> None:
    """给某标的最初那条（尚未回填价格的）状态事件补上价格。

    `merge_hot_items()` 创建新标的时不发起网络请求，`price_at_entry`
    先留空；调用方（如 `run_hotlist_scan.py`）抓完热点、拿到价格后
    调这个函数补回去。只回填 `price_at_entry is None` 的最早一条事件，
    不影响后续已经记录过价格的状态事件。
    """
    if price is None or not entry.state_history:
        return
    first = entry.state_history[0]
    if first.price_at_entry is None:
        first.price_at_entry = price


def change_state(
    pool: Dict[str, CandidateEntry],
    code: str,
    new_state: str,
    *,
    price_at_entry: Optional[float] = None,
    note: str = "",
) -> CandidateEntry:
    """把某标的的状态切换到 `new_state`，并记一条状态事件。

    - 标的不在池中：抛 `KeyError`，交给调用方（entrypoint）转成合适的
      退出码/提示信息，本函数不吞异常。
    - `new_state` 不在 `POOL_STATES` 里：抛 `ValueError`。
    - 状态未变化（比如已经是 focused 又设一次 focused）：不追加新的
      StateEvent，只更新最近一条事件的 note，避免刷历史噪音。
    - 迁移路径不做强校验（允许从 holding 直接改回 watching）：这是
      个人使用的分析辅助工具，强流程约束的维护成本大于收益，只在
      "非常规迁移"时打一条 info 日志，不阻止操作。
    """
    if new_state not in POOL_STATES:
        raise ValueError(f"未知状态: {new_state!r}，可选值: {POOL_STATES}")
    entry = pool.get(code)
    if entry is None:
        raise KeyError(f"标的 {code} 不在候选池中，无法变更状态")

    now = _now_iso()
    if entry.state == new_state:
        if entry.state_history:
            entry.state_history[-1].note = note or entry.state_history[-1].note
        return entry

    logger.info("标的 %s(%s) 状态变更: %s -> %s", entry.name, code, entry.state, new_state)
    entry.state = new_state
    entry.state_history.append(
        StateEvent(state=new_state, entered_at=now, price_at_entry=price_at_entry, note=note)
    )
    return entry


def compute_state_returns(
    entry: CandidateEntry, current_price: Optional[float]
) -> List[StateReturn]:
    """对 `entry.state_history` 里的每一段区间都算一遍涨跌幅。

    不只是当前状态，而是每一段历史状态都单独算——这样能看到"这只票在
    '重点关注'阶段涨了多少、进入'建议买入'后又涨了多少"这种分段收益。
    某段区间缺 `price_at_entry`（未回填成功）或整体缺 `current_price`
    （本次跟踪任务查价失败）时，`change_pct` 为 `None`，但仍然输出该段
    的 `days_in_state`，不整体跳过。
    """
    now = datetime.now(timezone.utc)
    results: List[StateReturn] = []
    for ev in entry.state_history:
        try:
            entered = datetime.fromisoformat(ev.entered_at)
        except ValueError:
            entered = now
        days = max((now - entered).days, 0)
        change_pct: Optional[float] = None
        if ev.price_at_entry is not None and current_price is not None and ev.price_at_entry != 0:
            change_pct = (current_price - ev.price_at_entry) / ev.price_at_entry * 100.0
        results.append(
            StateReturn(
                state=ev.state,
                entered_at=ev.entered_at,
                price_at_entry=ev.price_at_entry,
                days_in_state=days,
                change_pct=change_pct,
            )
        )
    return results


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
