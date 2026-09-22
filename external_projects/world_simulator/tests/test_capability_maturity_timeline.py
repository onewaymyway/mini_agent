"""tests/test_capability_maturity_timeline.py — 第十一轮 2.3 节：
能力成熟度时间线，`app.py::_collect_capability_maturity_timeline()`
纯函数单测。

写法仿照 `test_problem_graph_visual.py`（`_collect_problem_graph_
nodes()` 的姐妹实现同款测试风格）：只测不依赖 Streamlit 运行时的
纯数据聚合部分，折叠区本身的展示留给人工验收。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app  # noqa: E402  （world_simulator 项目根下的 Streamlit 入口）


def _state(capabilities_gained):
    return SimpleNamespace(capabilities_gained=capabilities_gained)


def test_collect_timeline_empty_history_returns_empty():
    assert app._collect_capability_maturity_timeline([]) == []


def test_collect_timeline_skips_malformed_and_empty_capability():
    history = [
        _state([
            "不是字典，应该被跳过",
            {"enables": ["没有 capability，应该被跳过"]},
            {"capability": "能够自动生成周报"},
        ])
    ]
    nodes = app._collect_capability_maturity_timeline(history)
    assert [n["capability"] for n in nodes] == ["能够自动生成周报"]


def test_collect_timeline_default_missing_maturity_stage():
    """旧数据/没给 maturity_stage 时不报错，时间线为空列表。"""
    history = [_state([{"capability": "能够自动生成周报"}])]
    nodes = app._collect_capability_maturity_timeline(history)
    assert nodes[0]["maturity_stages"] == []
    assert nodes[0]["latest_maturity_stage"] == ""


def test_collect_timeline_merges_by_exact_capability_string_and_keeps_order():
    history = [
        _state([{"capability": "能够自动生成周报", "maturity_stage": "lab"}]),
        _state([{"capability": "能够自动生成周报", "maturity_stage": "developer"}]),
        _state([{"capability": "能够自动生成周报", "maturity_stage": "consumer"}]),
    ]
    nodes = app._collect_capability_maturity_timeline(history)
    assert len(nodes) == 1
    assert nodes[0]["maturity_stages"] == ["lab", "developer", "consumer"]
    assert nodes[0]["latest_maturity_stage"] == "consumer"
    assert nodes[0]["latest"]["maturity_stage"] == "consumer"


def test_collect_timeline_does_not_fuzzy_merge_differently_worded_capabilities():
    """"新能力 A" 和 "能力 A（升级版）" 是刻意不合并的两个不同能力
    （按方案"风险"一节的取舍，不做模糊匹配/语义归并）。"""
    history = [
        _state([{"capability": "新能力 A", "maturity_stage": "lab"}]),
        _state([{"capability": "能力 A（升级版）", "maturity_stage": "developer"}]),
    ]
    nodes = app._collect_capability_maturity_timeline(history)
    assert len(nodes) == 2
    assert [n["capability"] for n in nodes] == ["新能力 A", "能力 A（升级版）"]
    assert nodes[0]["maturity_stages"] == ["lab"]
    assert nodes[1]["maturity_stages"] == ["developer"]


def test_collect_timeline_keeps_repeated_identical_stage_without_dedup():
    """同一能力多次给出相同阶段不去重——"反复确认还在这个阶段"本身
    也是有效信息。"""
    history = [
        _state([{"capability": "能够自动生成周报", "maturity_stage": "developer"}]),
        _state([{"capability": "能够自动生成周报", "maturity_stage": "developer"}]),
    ]
    nodes = app._collect_capability_maturity_timeline(history)
    assert nodes[0]["maturity_stages"] == ["developer", "developer"]


def test_collect_timeline_multiple_capabilities_independent_timelines():
    history = [
        _state([
            {"capability": "能力甲", "maturity_stage": "lab"},
            {"capability": "能力乙", "maturity_stage": "expert"},
        ]),
        _state([{"capability": "能力甲", "maturity_stage": "developer"}]),
    ]
    nodes = app._collect_capability_maturity_timeline(history)
    by_name = {n["capability"]: n for n in nodes}
    assert by_name["能力甲"]["maturity_stages"] == ["lab", "developer"]
    assert by_name["能力乙"]["maturity_stages"] == ["expert"]


# ── _capabilities_gained_html()：maturity_stage 标签后缀 ────────────


def test_capabilities_gained_html_appends_known_stage_label():
    state = _state([{"capability": "能够自动生成周报", "maturity_stage": "developer"}])
    html = app._capabilities_gained_html(state)
    assert "能够自动生成周报" in html
    assert "[开发者可用 · 技术]" in html


def test_capabilities_gained_html_appends_unknown_stage_value_as_is():
    state = _state([{"capability": "能够自动生成周报", "maturity_stage": "未来某个新阶段"}])
    html = app._capabilities_gained_html(state)
    assert "[未来某个新阶段 · 技术]" in html


def test_capabilities_gained_html_kind_suffix_only_when_stage_missing():
    """旧数据/没给 maturity_stage 时不追加阶段部分，但类型部分（第
    十二轮方案第 4 节，缺省按 technology 兜底）仍然展示，向后兼容
    的同时体现新增字段。"""
    state = _state([{"capability": "能够自动生成周报"}])
    html = app._capabilities_gained_html(state)
    assert "能够自动生成周报" in html
    assert "[技术]" in html
    assert "开发者可用" not in html


# ── _capabilities_gained_html()/_collect_capability_maturity_timeline()：
# capability_kind（第十二轮方案第 4 节）────────────────────────────


def test_capabilities_gained_html_uses_kind_icon_for_organization():
    state = _state([{"capability": "新的跨部门协作流程", "capability_kind": "organization"}])
    html = app._capabilities_gained_html(state)
    assert "🏢" in html
    assert "[组织]" in html


def test_capabilities_gained_html_uses_kind_icon_for_institution():
    state = _state([{"capability": "新的绩效考核制度", "capability_kind": "institution"}])
    html = app._capabilities_gained_html(state)
    assert "📜" in html
    assert "[制度]" in html


def test_capabilities_gained_html_defaults_to_technology_icon():
    state = _state([{"capability": "新的生产工具"}])
    html = app._capabilities_gained_html(state)
    assert "🔧" in html
    assert "[技术]" in html


def test_capabilities_gained_html_unknown_kind_value_kept_as_is():
    """不认识的取值原样保留、不做校验（同 maturity_stage 既有取舍）。"""
    state = _state([{"capability": "某种新能力", "capability_kind": "未来某个新类型"}])
    html = app._capabilities_gained_html(state)
    assert "[未来某个新类型]" in html
    assert "🆙" in html  # 图标兜底不认识的取值


def test_capabilities_gained_html_first_occurrence_overrides_kind_icon():
    """first_occurrence 优先展示 ⭐，不与类型图标叠加。"""
    state = _state([
        {"capability": "新的绩效考核制度", "capability_kind": "institution", "first_occurrence": True}
    ])
    html = app._capabilities_gained_html(state)
    assert "⭐" in html
    assert "📜" not in html
    assert "[制度]" in html


def test_collect_timeline_capability_kind_defaults_to_technology():
    history = [_state([{"capability": "能够自动生成周报"}])]
    nodes = app._collect_capability_maturity_timeline(history)
    assert nodes[0]["capability_kind"] == "technology"


def test_collect_timeline_capability_kind_uses_latest_record():
    history = [
        _state([{"capability": "能力甲", "capability_kind": "technology"}]),
        _state([{"capability": "能力甲", "capability_kind": "institution"}]),
    ]
    nodes = app._collect_capability_maturity_timeline(history)
    assert nodes[0]["capability_kind"] == "institution"
