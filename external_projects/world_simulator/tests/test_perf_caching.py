"""tests/test_perf_caching.py — 第十二轮"目标树卡顿"分析后续：
world_simulator 详情页性能优化第一阶段，`app.py` 里几个
`st.cache_data` 缓存包装函数的单测。

不测试 Streamlit 缓存机制本身（那是 Streamlit 自己的职责），只测试
两件事：1）缓存包装函数的返回值跟对应的未缓存纯函数完全一致（缓存
不能改变结果）；2）`get_simulation_cached()` 在文件真的发生变化后能
读到最新内容（缓存不能读到过期数据）。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app  # noqa: E402


def _state(step, capabilities_gained=None, problems=None, time_label=""):
    return SimpleNamespace(
        step=step,
        time_label=time_label,
        capabilities_gained=capabilities_gained or [],
        problems=problems or [],
    )


def test_collect_problem_graph_nodes_cached_matches_uncached():
    history = [
        _state(1, problems=[{"id": "p1", "symptom": "问题甲", "status": "active"}]),
        _state(2, problems=[{"id": "p1", "symptom": "问题甲", "status": "solved"}]),
    ]
    direct = app._collect_problem_graph_nodes(history)
    cached = app._collect_problem_graph_nodes_cached("sim-a", "main", len(history), history)
    assert cached == direct


def test_collect_problem_graph_nodes_cached_distinguishes_sim_id():
    """不同 `sim_id` 即使历史长度相同，也不应该互相读到对方的缓存
    结果——这是缓存 key 设计里最容易出错、也最重要的正确性点。"""
    history_a = [_state(1, problems=[{"id": "p1", "symptom": "问题甲"}])]
    history_b = [_state(1, problems=[{"id": "p2", "symptom": "问题乙"}])]
    result_a = app._collect_problem_graph_nodes_cached("sim-a", "main", 1, history_a)
    result_b = app._collect_problem_graph_nodes_cached("sim-b", "main", 1, history_b)
    assert {n["_key"] for n in result_a} == {"p1"}
    assert {n["_key"] for n in result_b} == {"p2"}


def test_collect_capability_maturity_timeline_cached_matches_uncached():
    history = [_state(1, capabilities_gained=[{"capability": "能力甲", "maturity_stage": "lab"}])]
    direct = app._collect_capability_maturity_timeline(history)
    cached = app._collect_capability_maturity_timeline_cached("sim-a", "main", len(history), history)
    assert cached == direct


def test_collect_capability_kind_coevolution_cached_matches_uncached():
    history = [
        _state(1, capabilities_gained=[{"capability": "甲", "capability_kind": "technology"}]),
        _state(2, capabilities_gained=[{"capability": "乙", "capability_kind": "institution"}]),
    ]
    direct = app._collect_capability_kind_coevolution(history)
    cached = app._collect_capability_kind_coevolution_cached("sim-a", "main", len(history), 3, history)
    assert cached == direct


def test_collect_capability_kind_coevolution_cached_distinguishes_window():
    """同一份历史用不同 `window` 调用，缓存不能把两次结果搞混——
    `window` 必须是缓存 key 的一部分。用独立的 `sim_id` 避免跟本文件
    其它测试的缓存 key（`sim_id`+`branch`+`history_len`）撞在一起——
    `_history` 本身不参与哈希是设计使然（见 `_collect_problem_graph_
    nodes_cached()` 注释），测试之间就需要靠 `sim_id` 互相区分，跟
    生产环境下不同模拟实例天然有不同 `sim_id` 是一回事。"""
    history = [
        _state(1, capabilities_gained=[{"capability": "甲", "capability_kind": "technology"}]),
        _state(10, capabilities_gained=[{"capability": "乙", "capability_kind": "institution"}]),
    ]
    narrow = app._collect_capability_kind_coevolution_cached(
        "sim-window-test", "main", len(history), 3, history
    )
    wide = app._collect_capability_kind_coevolution_cached(
        "sim-window-test", "main", len(history), 10, history
    )
    assert narrow == []
    assert len(wide) == 1


def test_get_simulation_cached_reflects_file_changes(tmp_path, monkeypatch):
    """`get_simulation_cached()` 在 `state_history.jsonl` 真的被改写后
    （mtime/size 变化），下一次调用要读到新内容，不能停留在缓存的
    旧快照上——这是"缓存 key 用 mtime+size 判断文件是否变化"这个设计
    的核心正确性保证。"""
    from world_simulator.store import SimStore
    from world_simulator.state_model import SimManifest, SimState

    data_dir = tmp_path / "data"
    sim_id = "sim_cache_test"
    store = SimStore.for_root(data_dir, sim_id)

    manifest = SimManifest(
        sim_id=sim_id, template="custom", intent="缓存测试", title="缓存测试",
        created_at="2026-01-01T00:00:00+00:00", updated_at="2026-01-01T00:00:00+00:00",
        status="active", branch="main",
    )
    store.save_manifest(manifest)
    state1 = SimState(step=1, time_label="第一步", summary="")
    store.append_state(state1, branch="main")

    _m1, _c1, history1 = app.get_simulation_cached(data_dir, sim_id)
    assert [s.step for s in history1] == [1]

    # mtime 精度在某些文件系统上是秒级，强制往后推一点，避免同一秒内
    # 写两次导致 mtime 完全相同、缓存误判"文件没变"。
    time.sleep(0.01)
    state2 = SimState(step=2, time_label="第二步", summary="")
    store.append_state(state2, branch="main")

    _m2, _c2, history2 = app.get_simulation_cached(data_dir, sim_id)
    assert [s.step for s in history2] == [1, 2]
