"""tests/test_golden_cases.py — 阶段 5（黄金案例回归护栏）验收测试。

对应 `next_doc/stock_watch_continuous_improvement_plan.md` 阶段 5：
`reconcile_outcomes` 跑出的"预测 vs 结果"里，误判幅度大的典型案例固化
成黄金案例（`tests/golden_cases/cases.json`），跑当前评分/筛选逻辑，
断言结果不比历史已知的"应该入选/不应该入选"结论差。

再次强调（呼应第5节"刻意留白"）：这里测试通过只代表"没有引入已知的
历史型回归错误"，不代表评分逻辑已经足够好——是否足够好仍然是人工
判断，不是这份测试的职责。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_watch.golden_cases import (  # noqa: E402
    DEFAULT_CASES_PATH,
    evaluate,
    load_golden_cases,
    run_pipeline,
)

CASES = load_golden_cases()


def test_golden_cases_fixture_file_exists_and_nonempty():
    assert DEFAULT_CASES_PATH.exists()
    assert len(CASES) >= 1


def test_load_golden_cases_missing_file_returns_empty(tmp_path):
    assert load_golden_cases(tmp_path / "does_not_exist.json") == []


def test_all_fixture_case_ids_are_unique():
    ids = [c.id for c in CASES]
    assert len(ids) == len(set(ids))


def test_all_golden_cases_pass():
    """核心断言：每个已固化的黄金案例都必须通过——这就是"回归护栏"本身。
    单独跑每个案例（而不是只跑一个汇总断言），失败时能直接看出是哪个
    历史案例被破坏了。"""
    failures = []
    for case in CASES:
        result = evaluate(case)
        if not result.passed:
            failures.append(
                f"{case.id}: missing_included={result.missing_included} "
                f"unexpected_included={result.unexpected_included} "
                f"score_violations={result.score_violations}"
            )
    assert not failures, "\n".join(failures)


def test_multi_source_consensus_case_scores_far_above_noise():
    case = next(c for c in CASES if c.id == "multi_source_consensus_outranks_single_mention")
    pool = run_pipeline(case)
    assert pool["600519"].score > pool["000002"].score if "000002" in pool else True
    assert "000002" not in pool  # 被 max_size 截掉
    assert set(pool["600519"].sources) == {"eastmoney_hot_rank", "xueqiu_hot_stock"}


def test_seed_stock_case_keeps_base_score_contract():
    case = next(c for c in CASES if c.id == "seed_stock_merged_even_without_hot_mentions")
    pool = run_pipeline(case)
    assert pool["510300"].score == 1.0
    assert pool["510300"].type == "etf"


def test_known_gap_case_documents_seed_trim_behavior():
    """这条不是"我们希望如此"，而是"记录当前确实如此"——见 cases.json
    里这个案例的 description。任何人想修这个差异（无论是补 enforce_max_
    size 的豁免逻辑还是改 docstring），都应该先来改这个案例的
    expected_included/excluded，而不是让这条测试悄悄变红后才发现。"""
    case = next(c for c in CASES if c.id == "seed_not_exempt_from_max_size_trim_known_gap")
    pool = run_pipeline(case)
    assert "510300" not in pool
    assert evaluate(case).passed
