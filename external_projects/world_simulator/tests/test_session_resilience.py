"""tests/test_session_resilience.py — 第十九轮"浏览器切走太久回来被
重置到列表页/连续推进任务被打断"问题的两个修复点单测：
- `app._restore_view_from_query_params()`/`app._sync_query_params_
  from_session_state()`：导航状态（`view`/`sim_id`）在 URL 查询参数
  里的读写。
- `app._persist_pending_autorun()`：连续推进任务进度落盘到
  `manifest.settings["pending_autorun"]`。

`st.session_state`/`st.query_params` 在这几个函数里只被当成普通的
dict-like 对象使用（`in`/`.get`/`[]=`/`del`），测试里用真的 `dict`
（`query_params` 用一个薄的子类补上 `.get()` 缺省行为，标准 `dict`
本身已经够用）直接替换掉，不需要拉起真实的 Streamlit 运行时——这几个
函数的实现本来就不依赖除了这几个 dict 操作之外的任何 Streamlit
特性，用真 dict 替换是对它们行为最直接的验证方式，也是这个代码库里
`app.py` 现有测试（`test_perf_caching.py` 等）一贯的"只 monkeypatch
必要的全局，不整套拉起 AppTest"的做法。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app  # noqa: E402

import world_simulator.engine as engine_mod  # noqa: E402
from world_simulator.store import SimStore  # noqa: E402


@pytest.fixture()
def fresh_session_state(monkeypatch):
    state: dict = {}
    monkeypatch.setattr(app.st, "session_state", state)
    return state


@pytest.fixture()
def fresh_query_params(monkeypatch):
    params: dict = {}
    monkeypatch.setattr(app.st, "query_params", params)
    return params


# ── _restore_view_from_query_params ─────────────────────────────────

def test_restore_from_query_params_populates_fresh_session(fresh_session_state, fresh_query_params):
    fresh_query_params["view"] = "detail"
    fresh_query_params["sim_id"] = "sim_abc"
    app._restore_view_from_query_params()
    assert fresh_session_state["view"] == "detail"
    assert fresh_session_state["sim_id"] == "sim_abc"


def test_restore_from_query_params_falls_back_to_list_when_invalid(fresh_session_state, fresh_query_params):
    fresh_query_params["view"] = "not_a_real_view"
    app._restore_view_from_query_params()
    assert fresh_session_state["view"] == "list"
    assert "sim_id" not in fresh_session_state


def test_restore_from_query_params_falls_back_to_list_when_empty(fresh_session_state, fresh_query_params):
    app._restore_view_from_query_params()
    assert fresh_session_state["view"] == "list"


def test_restore_from_query_params_does_not_overwrite_existing_session(fresh_session_state, fresh_query_params):
    """已经在正常跑的会话（`session_state` 里已经有 `view`）不应该被
    URL 覆盖——只有"全新会话"才需要从 URL 恢复。"""
    fresh_session_state["view"] = "create"
    fresh_query_params["view"] = "detail"
    fresh_query_params["sim_id"] = "sim_abc"
    app._restore_view_from_query_params()
    assert fresh_session_state["view"] == "create"
    assert "sim_id" not in fresh_session_state


def test_restore_from_query_params_requires_sim_id_to_be_non_empty(fresh_session_state, fresh_query_params):
    fresh_query_params["view"] = "detail"
    fresh_query_params["sim_id"] = ""
    app._restore_view_from_query_params()
    assert fresh_session_state["view"] == "detail"
    assert "sim_id" not in fresh_session_state


# ── _sync_query_params_from_session_state ───────────────────────────

def test_sync_query_params_writes_view_and_sim_id_for_detail(fresh_session_state, fresh_query_params):
    fresh_session_state["sim_id"] = "sim_abc"
    app._sync_query_params_from_session_state("detail")
    assert fresh_query_params["view"] == "detail"
    assert fresh_query_params["sim_id"] == "sim_abc"


def test_sync_query_params_clears_sim_id_for_list_view(fresh_session_state, fresh_query_params):
    fresh_query_params["view"] = "detail"
    fresh_query_params["sim_id"] = "sim_abc"
    app._sync_query_params_from_session_state("list")
    assert fresh_query_params["view"] == "list"
    assert "sim_id" not in fresh_query_params


def test_sync_query_params_does_not_include_sim_id_for_non_detail_views(fresh_session_state, fresh_query_params):
    fresh_session_state["sim_id"] = "sim_abc"  # 残留的旧值，不应该被泄漏到非 detail/game 视图
    app._sync_query_params_from_session_state("compare")
    assert fresh_query_params["view"] == "compare"
    assert "sim_id" not in fresh_query_params


def test_sync_query_params_includes_sim_id_for_game_view(fresh_session_state, fresh_query_params):
    fresh_session_state["sim_id"] = "sim_abc"
    app._sync_query_params_from_session_state("game")
    assert fresh_query_params["sim_id"] == "sim_abc"


def test_restore_then_sync_round_trips(fresh_session_state, fresh_query_params):
    """完整验证"URL 恢复导航状态"这个场景：模拟一次全新会话从 URL
    恢复，再把恢复后的状态同步回 URL，应该和恢复前一致（幂等）。"""
    fresh_query_params["view"] = "detail"
    fresh_query_params["sim_id"] = "sim_abc"
    app._restore_view_from_query_params()
    app._sync_query_params_from_session_state(fresh_session_state["view"])
    assert fresh_query_params["view"] == "detail"
    assert fresh_query_params["sim_id"] == "sim_abc"


# ── _persist_pending_autorun ─────────────────────────────────────────

def _make_manifest(tmp_path, sim_id="sim_a"):
    return engine_mod.materialize_simulation(
        tmp_path, template="life_sim", intent="意图", title="标题", summary="摘要",
        vars={"age": 22}, options=[],
    )


def test_persist_pending_autorun_writes_progress_to_manifest_settings(tmp_path, monkeypatch):
    manifest = _make_manifest(tmp_path)
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)

    app._persist_pending_autorun(
        manifest.sim_id, {"sim_id": manifest.sim_id, "target": 50, "done": 23, "remaining": 27},
    )

    store = SimStore.for_root(tmp_path, manifest.sim_id)
    reloaded = store.load_manifest()
    assert reloaded.settings["pending_autorun"] == {"target": 50, "done": 23, "remaining": 27}


def test_persist_pending_autorun_none_clears_it(tmp_path, monkeypatch):
    manifest = _make_manifest(tmp_path)
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)

    app._persist_pending_autorun(
        manifest.sim_id, {"sim_id": manifest.sim_id, "target": 5, "done": 1, "remaining": 4},
    )
    app._persist_pending_autorun(manifest.sim_id, None)

    store = SimStore.for_root(tmp_path, manifest.sim_id)
    reloaded = store.load_manifest()
    assert reloaded.settings["pending_autorun"] is None


def test_persist_pending_autorun_swallows_engine_error_for_missing_sim(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    # 实例不存在，`update_settings()` 会抛 `SimEngineError`——不应该
    # 往外传，落盘失败不该打断当前这次页面渲染/推进本身。
    app._persist_pending_autorun("does_not_exist", {"target": 1, "done": 0, "remaining": 1})
