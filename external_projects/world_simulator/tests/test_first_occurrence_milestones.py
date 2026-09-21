"""tests/test_first_occurrence_milestones.py — 第十二轮方案第 3 节：
Reality Renderer 最小版 + First Possible Event，
`app.py::_collect_first_occurrence_milestones()`/
`_capabilities_gained_html()` 单测。

写法仿照 `test_capability_maturity_timeline.py`：只测不依赖
Streamlit 运行时的纯数据聚合/HTML 拼接部分。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app  # noqa: E402  （world_simulator 项目根下的 Streamlit 入口）


def _state(capabilities_gained, step=0, time_label=""):
    return SimpleNamespace(
        capabilities_gained=capabilities_gained, step=step, time_label=time_label,
    )


# ── _collect_first_occurrence_milestones ────────────────────────────


def test_collect_milestones_empty_history_returns_empty():
    assert app._collect_first_occurrence_milestones([]) == []


def test_collect_milestones_no_first_occurrence_returns_empty():
    history = [
        _state([{"capability": "能够自动生成周报"}], step=1),
        _state([{"capability": "能够自动分析客户付费意愿", "first_occurrence": False}], step=2),
    ]
    assert app._collect_first_occurrence_milestones(history) == []


def test_collect_milestones_collects_true_only_and_keeps_order():
    history = [
        _state([{"capability": "A", "first_occurrence": False}], step=1),
        _state([{"capability": "B", "first_occurrence": True}], step=2, time_label="第二季度"),
        _state([{"capability": "C", "first_occurrence": True,
                  "behavior_change": "不再需要人工审核", "structural_impact": "催生了新岗位"}], step=3),
    ]
    milestones = app._collect_first_occurrence_milestones(history)
    assert [m["capability"] for m in milestones] == ["B", "C"]
    assert milestones[0]["step"] == 2
    assert milestones[0]["time_label"] == "第二季度"
    assert milestones[1]["behavior_change"] == "不再需要人工审核"
    assert milestones[1]["structural_impact"] == "催生了新岗位"


def test_collect_milestones_skips_malformed_and_empty_capability():
    history = [
        _state([
            "不是字典，应该被跳过",
            {"first_occurrence": True},  # 没有 capability，应该被跳过
            {"capability": "能够自动生成周报", "first_occurrence": True},
        ], step=1)
    ]
    milestones = app._collect_first_occurrence_milestones(history)
    assert [m["capability"] for m in milestones] == ["能够自动生成周报"]


# ── _capabilities_gained_html ────────────────────────────────────────


def test_capabilities_html_default_fields_empty_backward_compatible():
    """三个新字段都缺省时，渲染结果与第十一轮既有行为一致（无 ⭐、
    无首次达成字样、无额外详情行）。"""
    state = _state([{"capability": "能够自动生成周报"}])
    html = app._capabilities_gained_html(state)
    assert "🆙" in html
    assert "⭐" not in html
    assert "首次达成" not in html


def test_capabilities_html_first_occurrence_marks_star_and_label():
    state = _state([{"capability": "能够完全自动化生产", "first_occurrence": True}])
    html = app._capabilities_gained_html(state)
    assert "⭐" in html
    assert "首次达成" in html
    assert "🆙" not in html


def test_capabilities_html_includes_behavior_change_and_structural_impact():
    state = _state([{
        "capability": "能够自动分析客户付费意愿",
        "behavior_change": "销售团队不再逐一人工筛选线索",
        "structural_impact": "催生了一个新的专职岗位",
    }])
    html = app._capabilities_gained_html(state)
    assert "销售团队不再逐一人工筛选线索" in html
    assert "催生了一个新的专职岗位" in html


def test_capabilities_html_empty_when_no_capability_field():
    assert app._capabilities_gained_html(_state([])) == ""
