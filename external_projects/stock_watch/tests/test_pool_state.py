"""tests/test_pool_state.py — 候选池状态机的纯逻辑单元测试。

对应 `next_doc/stock_watch_pool_state_tracking_and_kanban_plan.md` 阶段2：
`change_state`/`compute_state_returns`/`backfill_entry_price`/
`enforce_max_size` 的状态保护逻辑，均不依赖网络，用固定 mock 数据跑通
（与仓库既有的 `tests/test_offline_logic.py` 同样的约定）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from stock_watch.candidate_pool import (  # noqa: E402
    CandidateEntry,
    StateEvent,
    backfill_entry_price,
    change_state,
    compute_state_returns,
    enforce_max_size,
)


def _entry(code="600519", name="贵州茅台", state="watching", score=10.0):
    return CandidateEntry(
        code=code, name=name, state=state, score=score,
        first_seen="2026-08-01T00:00:00+00:00", last_seen="2026-08-01T00:00:00+00:00",
        state_history=[StateEvent(state=state, entered_at="2026-08-01T00:00:00+00:00")],
    )


def test_change_state_appends_new_event_and_updates_state():
    pool = {"600519": _entry()}
    entry = change_state(pool, "600519", "focused", price_at_entry=1700.0, note="放量突破")
    assert entry.state == "focused"
    assert len(entry.state_history) == 2
    assert entry.state_history[-1].price_at_entry == 1700.0
    assert entry.state_history[-1].note == "放量突破"


def test_change_state_same_state_does_not_append_new_event():
    pool = {"600519": _entry()}
    change_state(pool, "600519", "watching", note="备注更新")
    entry = pool["600519"]
    assert len(entry.state_history) == 1
    assert entry.state_history[-1].note == "备注更新"


def test_change_state_unknown_code_raises_keyerror():
    with pytest.raises(KeyError):
        change_state({}, "999999", "focused")


def test_change_state_invalid_state_raises_valueerror():
    pool = {"600519": _entry()}
    with pytest.raises(ValueError):
        change_state(pool, "600519", "not_a_real_state")


def test_backfill_entry_price_only_fills_first_empty_event():
    entry = _entry()
    entry.state_history[0].price_at_entry = None
    backfill_entry_price(entry, 1680.0)
    assert entry.state_history[0].price_at_entry == 1680.0
    # 再次回填不覆盖已有值
    backfill_entry_price(entry, 9999.0)
    assert entry.state_history[0].price_at_entry == 1680.0


def test_compute_state_returns_handles_missing_prices():
    entry = _entry()
    entry.state_history[0].price_at_entry = 1600.0
    entry.state_history.append(
        StateEvent(state="focused", entered_at="2026-08-10T00:00:00+00:00", price_at_entry=None)
    )
    results = compute_state_returns(entry, current_price=1760.0)
    assert len(results) == 2
    assert results[0].change_pct == pytest.approx(10.0)
    assert results[1].change_pct is None  # price_at_entry 缺失


def test_compute_state_returns_handles_missing_current_price():
    entry = _entry()
    entry.state_history[0].price_at_entry = 1600.0
    results = compute_state_returns(entry, current_price=None)
    assert results[0].change_pct is None


def test_enforce_max_size_protects_non_watching_states():
    pool = {
        "1": _entry(code="1", state="watching", score=100.0),
        "2": _entry(code="2", state="watching", score=90.0),
        "3": _entry(code="3", state="focused", score=1.0),   # 分数低但受保护
    }
    result = enforce_max_size(pool, max_size=2)
    assert set(result) == {"1", "3"}  # watching 里分数最高的 "1" + 受保护的 "3"


def test_enforce_max_size_keeps_pool_unchanged_when_under_limit():
    pool = {"1": _entry(code="1")}
    result = enforce_max_size(pool, max_size=10)
    assert result is pool
