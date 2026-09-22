"""tests/test_confidence_label.py — 第十二轮方案第 6 节：
Fact/Assumption/Hypothesis/Prediction 类型标签（现状核实后缩小为
纯展示层标签映射），`app.py::_confidence_label()` 纯函数单测。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app  # noqa: E402  （world_simulator 项目根下的 Streamlit 入口）


def test_confirmed_maps_to_chinese_label():
    assert app._confidence_label("confirmed") == "已证实事实"


def test_supported_maps_to_chinese_label():
    assert app._confidence_label("supported") == "有依据的判断"


def test_hypothesis_maps_to_chinese_label():
    assert app._confidence_label("hypothesis") == "待验证假设"


def test_speculative_maps_to_chinese_label():
    assert app._confidence_label("speculative") == "推测"


def test_unknown_value_falls_back_to_original_value():
    """未知取值原样展示英文原值本身，不报错（同 `_MATURITY_STAGE_
    LABELS`/`_CAPABILITY_KIND_LABELS` 的既有兜底风格）。"""
    assert app._confidence_label("未来某个新枚举值") == "未来某个新枚举值"


def test_empty_value_falls_back_to_empty_string():
    assert app._confidence_label("") == ""
    assert app._confidence_label(None) == ""


def test_all_known_enum_values_covered():
    """`knowledge_base.py::_VALID_CONFIDENCE` 四个已知枚举值都要有
    对应的中文标签（防止未来新增/改名枚举值时忘记同步映射表）。"""
    from world_simulator import knowledge_base as knowledge_base_mod

    for value in knowledge_base_mod._VALID_CONFIDENCE:
        label = app._confidence_label(value)
        assert label != value, f"{value} 应该有对应的中文标签，而不是原样返回"
