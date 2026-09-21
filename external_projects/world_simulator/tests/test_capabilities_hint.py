"""tests/test_capabilities_hint.py — 第十一轮 2.4 节：能力清单接入
决策生成 prompt，`spec_generator.resolve_capabilities_hint()` 纯
函数单测。

写法仿照 `test_causal_graph.py` 里 `resolve_causal_graph_hint()`
的测试风格：history 用纯 dict 列表构造（`resolve_capabilities_hint()`
同时兼容 `SimState` 对象和纯 dict，两种取值路径都要覆盖），只测
"聚合成提示文案"这一层，不重复测底层聚合逻辑本身。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

WORLD_SIM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORLD_SIM_ROOT))

from world_simulator import spec_generator as sg  # noqa: E402


def test_resolve_capabilities_hint_empty_without_history():
    assert sg.resolve_capabilities_hint([]) == ""
    assert sg.resolve_capabilities_hint(None) == ""


def test_resolve_capabilities_hint_empty_when_no_capability_field():
    """旧数据/从未出现过 `capabilities_gained` 记录时返回空字符串，
    向后兼容——`advance_step.yaml`/`decision_generate.yaml` 里这句
    提示为空时不影响 prompt 组装行为。"""
    history = [{"capabilities_gained": []}, {"vars": {"money": 100}}]
    assert sg.resolve_capabilities_hint(history) == ""


def test_resolve_capabilities_hint_skips_malformed_and_empty_capability():
    history = [
        {
            "capabilities_gained": [
                "不是字典，应该被跳过",
                {"enables": ["没有 capability，应该被跳过"]},
                {"capability": "能够自动生成周报"},
            ]
        }
    ]
    hint = sg.resolve_capabilities_hint(history)
    assert "能够自动生成周报" in hint
    assert "没有 capability" not in hint


def test_resolve_capabilities_hint_dedupes_by_exact_capability_string_keeps_latest():
    history = [
        {"capabilities_gained": [{"capability": "能够自动生成周报", "maturity_stage": "lab"}]},
        {"capabilities_gained": [{"capability": "能够自动生成周报", "maturity_stage": "developer"}]},
    ]
    hint = sg.resolve_capabilities_hint(history)
    assert hint.count("能够自动生成周报") == 1
    assert "开发者可用" in hint
    assert "实验室可行" not in hint


def test_resolve_capabilities_hint_does_not_fuzzy_merge():
    history = [
        {"capabilities_gained": [{"capability": "新能力 A"}]},
        {"capabilities_gained": [{"capability": "能力 A（升级版）"}]},
    ]
    hint = sg.resolve_capabilities_hint(history)
    assert "新能力 A" in hint
    assert "能力 A（升级版）" in hint


def test_resolve_capabilities_hint_includes_enables_and_unknown_stage_as_is():
    history = [
        {
            "capabilities_gained": [
                {
                    "capability": "能够自动生成周报",
                    "enables": ["更快的复盘", "更透明的进度同步"],
                    "maturity_stage": "未来某个新阶段",
                }
            ]
        }
    ]
    hint = sg.resolve_capabilities_hint(history)
    assert "更快的复盘、更透明的进度同步" in hint
    assert "未来某个新阶段" in hint


def test_resolve_capabilities_hint_accepts_simstate_like_objects():
    """引擎实际调用时 history 是 `SimState` 对象列表（`store.load_
    history()` 的返回值），不是纯 dict——确认 `getattr` 取值路径同样
    工作。"""
    history = [SimpleNamespace(capabilities_gained=[{"capability": "能够自动生成周报"}])]
    hint = sg.resolve_capabilities_hint(history)
    assert "能够自动生成周报" in hint


def test_resolve_capabilities_hint_mentions_it_is_reference_only():
    """措辞必须体现"仅作参考、不强制"——不是代码层面强制映射，见
    方案 2.4 节"做法"一段。"""
    history = [{"capabilities_gained": [{"capability": "能够自动生成周报"}]}]
    hint = sg.resolve_capabilities_hint(history)
    assert "不代表" in hint or "仅" in hint
