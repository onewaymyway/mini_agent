"""tests/test_capability_kind_coevolution.py — 第十二轮方案第 5 节：
技术/组织/制度协同演化观察，`app.py::_collect_capability_kind_
coevolution()` 纯函数单测。

写法仿照 `test_capability_maturity_timeline.py`：只测不依赖
Streamlit 运行时的纯数据聚合部分，折叠区本身的展示留给人工验收。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app  # noqa: E402  （world_simulator 项目根下的 Streamlit 入口）


def _state(step, capabilities_gained, time_label=""):
    return SimpleNamespace(step=step, time_label=time_label, capabilities_gained=capabilities_gained)


def test_empty_history_returns_empty():
    assert app._collect_capability_kind_coevolution([]) == []


def test_no_coevolution_when_only_one_kind_present():
    """全程只有一种类型，不产生任何观察——不为了"看起来有内容"而
    制造虚假关联。"""
    history = [
        _state(1, [{"capability": "能力甲", "capability_kind": "technology"}]),
        _state(2, [{"capability": "能力乙", "capability_kind": "technology"}]),
    ]
    assert app._collect_capability_kind_coevolution(history) == []


def test_no_coevolution_when_default_technology_kind_only():
    """未声明 `capability_kind` 一律按 `"technology"` 兜底，多条
    缺省记录仍然只算一种类型，不产生观察。"""
    history = [
        _state(1, [{"capability": "能力甲"}]),
        _state(2, [{"capability": "能力乙"}]),
    ]
    assert app._collect_capability_kind_coevolution(history) == []


def test_detects_coevolution_within_default_window():
    """默认窗口 3 步内出现技术 + 制度两种类型，应该命中一次观察。"""
    history = [
        _state(1, [{"capability": "新的生产工具", "capability_kind": "technology"}]),
        _state(2, [{"capability": "新的绩效考核制度", "capability_kind": "institution"}]),
    ]
    observations = app._collect_capability_kind_coevolution(history)
    assert len(observations) == 1
    obs = observations[0]
    assert obs["window_end_step"] == 2
    assert obs["kinds"] == ["institution", "technology"]
    assert {e["capability"] for e in obs["entries"]} == {"新的生产工具", "新的绩效考核制度"}


def test_no_coevolution_when_steps_outside_window():
    """两种类型相隔超出窗口宽度，不应该被判定为协同。"""
    history = [
        _state(1, [{"capability": "新的生产工具", "capability_kind": "technology"}]),
        _state(10, [{"capability": "新的绩效考核制度", "capability_kind": "institution"}]),
    ]
    assert app._collect_capability_kind_coevolution(history, window=3) == []


def test_window_boundary_is_inclusive():
    """window=3 时，相隔恰好 2 步（区间宽度 3）应该命中；相隔 3 步
    （区间宽度 4）不应该命中——精确测试窗口的闭区间边界。"""
    history_hit = [
        _state(1, [{"capability": "新的生产工具", "capability_kind": "technology"}]),
        _state(3, [{"capability": "新的绩效考核制度", "capability_kind": "institution"}]),
    ]
    assert len(app._collect_capability_kind_coevolution(history_hit, window=3)) == 1

    history_miss = [
        _state(1, [{"capability": "新的生产工具", "capability_kind": "technology"}]),
        _state(4, [{"capability": "新的绩效考核制度", "capability_kind": "institution"}]),
    ]
    assert app._collect_capability_kind_coevolution(history_miss, window=3) == []


def test_custom_window_widens_detection():
    """自定义更宽的窗口可以检测到默认窗口检测不到的跨类型变化。"""
    history = [
        _state(1, [{"capability": "新的生产工具", "capability_kind": "technology"}]),
        _state(10, [{"capability": "新的绩效考核制度", "capability_kind": "institution"}]),
    ]
    assert app._collect_capability_kind_coevolution(history, window=3) == []
    observations = app._collect_capability_kind_coevolution(history, window=10)
    assert len(observations) == 1


def test_skips_malformed_and_empty_capability_entries():
    history = [
        _state(1, ["不是字典，应该被跳过", {"enables": ["没有 capability，应该被跳过"]}]),
        _state(1, [{"capability": "能力甲", "capability_kind": "technology"}]),
        _state(2, [{"capability": "能力乙", "capability_kind": "organization"}]),
    ]
    observations = app._collect_capability_kind_coevolution(history)
    assert len(observations) == 1
    assert {e["capability"] for e in observations[0]["entries"]} == {"能力甲", "能力乙"}


def test_skips_state_with_missing_step():
    """`step` 为 `None` 的状态直接跳过，不参与统计（理论上不应该
    出现，但要保证不报错）。"""
    history = [
        SimpleNamespace(
            step=None,
            time_label="",
            capabilities_gained=[{"capability": "能力甲", "capability_kind": "technology"}],
        ),
        _state(1, [{"capability": "能力乙", "capability_kind": "institution"}]),
    ]
    assert app._collect_capability_kind_coevolution(history) == []


def test_three_kinds_in_one_window_all_reported():
    history = [
        _state(1, [{"capability": "新的生产工具", "capability_kind": "technology"}]),
        _state(2, [{"capability": "新的跨部门协作流程", "capability_kind": "organization"}]),
        _state(3, [{"capability": "新的绩效考核制度", "capability_kind": "institution"}]),
    ]
    observations = app._collect_capability_kind_coevolution(history)
    # 第 3、4、5 各以自身为窗口右端点各产出一次观察（历史只到第 3 步，
    # 所以这里检查以第 3 步为右端点的那次观察包含三种类型）。
    last = [o for o in observations if o["window_end_step"] == 3][0]
    assert last["kinds"] == ["institution", "organization", "technology"]
