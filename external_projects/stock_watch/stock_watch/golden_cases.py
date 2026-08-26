"""stock_watch/golden_cases.py — 黄金案例回归护栏（功能扩展）。

对应 `next_doc/stock_watch_continuous_improvement_plan.md` 阶段 5：
`reconcile_outcomes` 跑出的"预测 vs 结果"里，误判幅度大的典型案例，
定期人工挑选固化成"黄金案例"——给定某个历史时点的行情快照（一批
`HotStockItem` + 种子配置），跑当前候选池评分/淘汰逻辑，断言结果不能
比历史已知的"应该入选/不应该入选"结论差太多。

**这只是"回归护栏"，不是"自动判断更好"**——是否更好仍然是人工判断
（见第5节），黄金案例测试通过只代表"没有引入已知的历史型错误"这个
更弱的保证，不代表评分逻辑已经足够好。

本模块只做纯逻辑（复现 `entrypoints/run_hotlist_scan.py` 里
"ensure_seeds → merge_hot_items → apply_decay → enforce_max_size"
这条流水线 + 对照期望结论打分），不直接触网，案例数据来自固定 JSON
fixture（`tests/golden_cases/cases.json`），方便离线单测，也方便
review session / 人工今后往里面追加新案例而不用碰 Python 代码。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from stock_watch.candidate_pool import (
    CandidateEntry,
    apply_decay,
    enforce_max_size,
    ensure_seeds,
    merge_hot_items,
)
from stock_watch.config import SeedStock
from stock_watch.data_sources import HotStockItem

DEFAULT_CASES_PATH = Path(__file__).resolve().parent.parent / "tests" / "golden_cases" / "cases.json"


@dataclass
class GoldenCase:
    id: str
    description: str
    # 触发这条黄金案例的证据来源，通常是 outcome_ledger.jsonl 里的一条
    # 记录或 improvement_backlog.jsonl 里的一个 backlog item id，纯文本
    # 备注，不做强校验（不同人固化案例时手上的证据形态不一定统一）。
    evidence_ref: str
    hot_items: List[HotStockItem]
    seeds: List[SeedStock] = field(default_factory=list)
    decay_days: int = 5
    decay_rate: float = 0.5
    max_size: int = 50
    # 历史结论：跑完流水线之后，这些代码必须在最终候选池里
    # （"应该选中"），这些代码必须不在（"不应该选中"）。
    expected_included: List[str] = field(default_factory=list)
    expected_excluded: List[str] = field(default_factory=list)
    # 可选：对入选标的分数的下限要求（code -> 最低分），用来护栏
    # "虽然还在池子里，但分数被改得低到几乎等于没入选"这类退化情况。
    min_score: Dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "GoldenCase":
        return cls(
            id=data["id"],
            description=data.get("description", ""),
            evidence_ref=data.get("evidence_ref", ""),
            hot_items=[
                HotStockItem(
                    code=i["code"], name=i.get("name", ""),
                    source=i.get("source", "unknown"),
                    heat_score=float(i.get("heat_score", 0.0)),
                    reason=i.get("reason", ""),
                )
                for i in data.get("hot_items", [])
            ],
            seeds=[
                SeedStock(
                    code=s["code"], name=s.get("name", ""),
                    market=s.get("market", "sh"), type=s.get("type", "stock"),
                )
                for s in data.get("seeds", [])
            ],
            decay_days=int(data.get("decay_days", 5)),
            decay_rate=float(data.get("decay_rate", 0.5)),
            max_size=int(data.get("max_size", 50)),
            expected_included=list(data.get("expected_included", [])),
            expected_excluded=list(data.get("expected_excluded", [])),
            min_score={k: float(v) for k, v in data.get("min_score", {}).items()},
        )


@dataclass
class GoldenCaseResult:
    case_id: str
    passed: bool
    missing_included: List[str]   # 期望入选但实际没入选
    unexpected_included: List[str]  # 期望不入选但实际入选了
    score_violations: List[str]   # 分数低于 min_score 要求的标的（附说明）
    final_pool: Dict[str, CandidateEntry]


def load_golden_cases(path: Optional[Path] = None) -> List[GoldenCase]:
    """从 JSON fixture 读取黄金案例列表；文件不存在时返回空列表（与仓库
    其它账本一致的容错约定——新项目/尚未固化案例时不应该让测试炸掉，
    只是护栏暂时是空的）。"""
    p = path or DEFAULT_CASES_PATH
    if not p.exists():
        return []
    raw: List[Dict[str, Any]] = json.loads(p.read_text(encoding="utf-8"))
    return [GoldenCase.from_dict(item) for item in raw]


def run_pipeline(case: GoldenCase) -> Dict[str, CandidateEntry]:
    """复现 `run_hotlist_scan.main()` 的核心流水线顺序：
    ensure_seeds → merge_hot_items → apply_decay → enforce_max_size。
    与真实 entrypoint 的唯一区别是数据来自固定 fixture 而不是网络抓取。
    """
    pool: Dict[str, CandidateEntry] = {}
    pool = ensure_seeds(pool, case.seeds)
    pool = merge_hot_items(pool, case.hot_items)
    pool = apply_decay(pool, decay_days=case.decay_days, decay_rate=case.decay_rate)
    pool = enforce_max_size(pool, case.max_size)
    return pool


def evaluate(case: GoldenCase) -> GoldenCaseResult:
    pool = run_pipeline(case)
    missing_included = [c for c in case.expected_included if c not in pool]
    unexpected_included = [c for c in case.expected_excluded if c in pool]
    score_violations = [
        f"{code}: {pool[code].score:.2f} < {min_required}"
        for code, min_required in case.min_score.items()
        if code in pool and pool[code].score < min_required
    ]
    passed = not (missing_included or unexpected_included or score_violations)
    return GoldenCaseResult(
        case_id=case.id, passed=passed,
        missing_included=missing_included,
        unexpected_included=unexpected_included,
        score_violations=score_violations,
        final_pool=pool,
    )
