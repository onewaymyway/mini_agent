"""tests/test_trend.py — 阶段三十一（"顺势/逆势/改变趋势"判断）单元测试。

对应 `next_doc/world_simulator_universal_simulator_gap_analysis_and_
roadmap_v2_plan.md` 4.23 节的验收标准：`classify_trend()` 能正确识别
"个人线反向影响外部线"（改变趋势）优先于"外部线高相关"（顺势/逆势），
两种信号都没有时返回 `inconclusive`，空输入不报错。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_simulator import trend


def test_classify_trend_empty_history_is_inconclusive():
    judgement = trend.classify_trend([], "cash", "self_line")
    assert judgement.verdict == "inconclusive"
    assert judgement.evidence == []
    assert judgement.target_field == "cash"
    assert judgement.self_line_id == "self_line"


def test_classify_trend_no_self_line_id_skips_changing_trend_check():
    history = [
        {
            "step": 1,
            "causal_links": [
                {"source_line_id": "econ", "line_id": "tech", "affected_fields": ["cash"]}
            ],
        }
    ]
    judgement = trend.classify_trend(history, "cash", "")
    assert judgement.verdict != "changing_trend"


def test_classify_trend_detects_changing_trend_when_self_line_influences_other_line():
    history = [
        {
            "step": 3,
            "causal_links": [
                {
                    "source_line_id": "self_line",
                    "line_id": "econ_line",
                    "driver": "个人决策",
                    "effect": "带动了行业趋势",
                    "affected_fields": ["market_share"],
                }
            ],
        }
    ]
    judgement = trend.classify_trend(history, "market_share", "self_line")
    assert judgement.verdict == "changing_trend"
    assert len(judgement.evidence) == 1
    assert judgement.evidence[0]["from_line"] == "self_line"
    assert judgement.evidence[0]["to_line"] == "econ_line"


def test_classify_trend_ignores_same_line_internal_links_as_changing_trend():
    history = [
        {
            "step": 1,
            "causal_links": [
                {"source_line_id": "self_line", "line_id": "self_line", "affected_fields": ["mood"]}
            ],
        }
    ]
    judgement = trend.classify_trend(history, "mood", "self_line")
    assert judgement.verdict != "changing_trend"


def test_classify_trend_with_trend_when_external_source_dominates():
    history = []
    for i in range(4):
        history.append(
            {
                "step": i,
                "causal_links": [
                    {
                        "source_line_id": "econ_line",
                        "line_id": "econ_line",
                        "driver": f"经济事件{i}",
                        "effect": "影响现金",
                        "affected_fields": ["cash"],
                    }
                ],
            }
        )
    judgement = trend.classify_trend(history, "cash", "self_line")
    assert judgement.verdict == "with_trend"
    assert judgement.caveat is not None


def test_classify_trend_inconclusive_when_no_dominant_external_source():
    history = [
        {
            "step": 0,
            "causal_links": [
                {"source_line_id": "a", "line_id": "a", "affected_fields": ["cash"]},
                {"source_line_id": "b", "line_id": "b", "affected_fields": ["cash"]},
                {"source_line_id": "c", "line_id": "c", "affected_fields": ["cash"]},
            ],
        }
    ]
    judgement = trend.classify_trend(history, "cash", "self_line")
    assert judgement.verdict == "inconclusive"


def test_trend_judgement_to_dict_round_trip_keys():
    judgement = trend.classify_trend([], "cash", "self_line")
    d = judgement.to_dict()
    assert set(d.keys()) == {
        "target_field", "self_line_id", "verdict", "verdict_label", "evidence", "caveat",
    }
