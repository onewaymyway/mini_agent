"""旧版本数据（没有伏笔增量文件、没有 plot_merge 记录）升级后，plot_merge 应被认领，而不是从第 1 章重跑。"""

from __future__ import annotations

from novel_writer import state as st
from novel_writer import tool_api
from novel_writer.fsutil import atomic_write_json
from test_m3 import isolated, new_book  # noqa: F401  (fixture)


def _book(isolated, quality="standard", total=3):
    ws = new_book(isolated, total=total)
    assert tool_api.update_settings(str(ws.root), {"quality": quality})["ok"]
    return ws


def test_standard_legacy_chapters_get_plot_merge_adopted(isolated):
    ws = _book(isolated)
    for n in (1, 2):
        st.update_step(ws, f"ch{n:02d}.sync_plot", status=st.ST_PASSED)
    adopted = st.adopt_legacy_steps(ws)
    assert set(adopted) == {"ch01.plot_merge", "ch02.plot_merge", "ch01.characters_refresh", "ch02.characters_refresh"}
    assert st.get_step_state(ws, "ch01.plot_merge")["status"] == st.ST_PASSED
    assert "旧版本" in st.get_step_state(ws, "ch01.plot_merge")["note"]
    assert st.get_step_state(ws, "ch03.plot_merge")["status"] != st.ST_PASSED  # 没写过的章不认领
    assert st.adopt_legacy_steps(ws) == []  # 幂等


def test_draft_legacy_uses_sync_as_anchor(isolated):
    ws = _book(isolated, quality="draft")
    st.update_step(ws, "ch01.sync", status=st.ST_PASSED)
    assert {"ch01.plot_merge", "ch01.characters_refresh"} <= set(st.adopt_legacy_steps(ws))


def test_standard_new_flow_midway_is_not_adopted_when_only_sync_passed(isolated):
    """标准档新流程：人物同步（sync）通过、伏笔同步（sync_plot）还没跑，不能认领。"""
    ws = _book(isolated)
    st.update_step(ws, "ch01.sync", status=st.ST_PASSED)
    assert st.adopt_legacy_steps(ws) == []


def test_new_flow_with_delta_file_is_never_adopted(isolated):
    """新流程：增量文件存在，plot_merge 必须真实运行。"""
    ws = _book(isolated)
    st.update_step(ws, "ch01.sync_plot", status=st.ST_PASSED)
    p = ws.path("plot_delta", nn=1)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(p, {"planted": [], "advanced": [], "resolved": []})
    assert "ch01.plot_merge" not in st.adopt_legacy_steps(ws)
    assert st.get_step_state(ws, "ch01.plot_merge")["status"] != st.ST_PASSED


def test_previous_failure_from_missing_delta_is_recovered(isolated):
    """用户已经按旧逻辑跑过一次并失败（增量缺失）：认领应覆盖这条失败记录。"""
    ws = _book(isolated)
    st.update_step(ws, "ch01.sync_plot", status=st.ST_PASSED)
    st.update_step(ws, "ch01.plot_merge", status=st.ST_FAILED, error="ChapterOpError: 第 1 章伏笔增量缺失或损坏")
    assert "ch01.plot_merge" in st.adopt_legacy_steps(ws)
    assert st.get_step_state(ws, "ch01.plot_merge")["status"] == st.ST_PASSED


def test_unrelated_failure_is_not_swallowed(isolated):
    ws = _book(isolated)
    st.update_step(ws, "ch01.sync_plot", status=st.ST_PASSED)
    st.update_step(ws, "ch01.plot_merge", status=st.ST_FAILED, error="伏笔合并失败：账本损坏")
    assert "ch01.plot_merge" not in st.adopt_legacy_steps(ws)
    assert st.get_step_state(ws, "ch01.plot_merge")["status"] == st.ST_FAILED


def test_passed_or_stale_plot_merge_is_untouched(isolated):
    ws = _book(isolated)
    st.update_step(ws, "ch01.sync_plot", status=st.ST_PASSED)
    st.update_step(ws, "ch01.plot_merge", status=st.ST_STALE, note="上游产物已变化，需要重跑")
    assert "ch01.plot_merge" not in st.adopt_legacy_steps(ws)
    assert st.get_step_state(ws, "ch01.plot_merge")["status"] == st.ST_STALE


def test_pipeline_status_does_not_queue_chapter_one_merge(isolated):
    ws = _book(isolated)
    for n in (1, 2, 3):
        st.update_step(ws, f"ch{n:02d}.sync_plot", status=st.ST_PASSED)
    res = tool_api.pipeline_status(str(ws.root))
    rows = {r["id"]: r for r in res["data"]["steps"]}
    assert all(rows[f"ch{n:02d}.plot_merge"]["status"] == st.ST_PASSED for n in (1, 2, 3))


def test_legacy_characters_refresh_adopted_but_real_failure_kept(isolated):
    ws = _book(isolated)
    st.update_step(ws, "ch01.sync_plot", status=st.ST_PASSED)
    st.update_step(ws, "ch02.sync_plot", status=st.ST_PASSED)
    st.update_step(ws, "ch02.characters_refresh", status=st.ST_FAILED, error="人物档案校验失败")
    st.adopt_legacy_steps(ws)
    assert st.get_step_state(ws, "ch01.characters_refresh")["status"] == st.ST_PASSED
    assert st.get_step_state(ws, "ch02.characters_refresh")["status"] == st.ST_FAILED


def test_characters_refresh_not_adopted_in_new_flow(isolated):
    ws = _book(isolated)
    st.update_step(ws, "ch01.sync", status=st.ST_PASSED)  # 标准档新流程：刷新紧随其后，不能认领
    st.adopt_legacy_steps(ws)
    assert st.get_step_state(ws, "ch01.characters_refresh")["status"] != st.ST_PASSED


def test_refresh_adopted_even_when_plot_merge_already_passed(isolated):
    """回归：plot_merge 已有记录时，不能因此漏掉同章的 characters_refresh。"""
    ws = _book(isolated)
    st.update_step(ws, "ch01.sync_plot", status=st.ST_PASSED)
    st.update_step(ws, "ch01.plot_merge", status=st.ST_PASSED)
    st.adopt_legacy_steps(ws)
    assert st.get_step_state(ws, "ch01.characters_refresh")["status"] == st.ST_PASSED


def test_earlier_chapters_adopted_when_later_chapter_has_progress(isolated):
    """旧数据里没有 sync_plot 记录的章节：只要后面的章节已有真实进度，本章就算旧版本已完成。"""
    ws = _book(isolated, total=5)
    st.update_step(ws, "ch04.write_chapter", status=st.ST_PASSED)
    st.adopt_legacy_steps(ws)
    for n in (1, 2, 3):
        assert st.get_step_state(ws, f"ch{n:02d}.characters_refresh")["status"] == st.ST_PASSED
        assert st.get_step_state(ws, f"ch{n:02d}.plot_merge")["status"] == st.ST_PASSED
    assert st.get_step_state(ws, "ch04.characters_refresh")["status"] != st.ST_PASSED  # 当前章不认领


def test_older_patrols_are_not_extracted_only_the_latest(isolated):
    """升级前已完成的较早巡检（第 10、20 章）不提取整改项；最新的第 30 章照常提取。"""
    ws = _book(isolated, quality="premium", total=3)
    for n in (10, 20, 30):
        st.update_step(ws, f"patrol:{n}", status=st.ST_PASSED)
    adopted = set(st.adopt_legacy_steps(ws))
    assert {"patrol_extract:10", "patrol_apply:10", "patrol_extract:20", "patrol_apply:20"} <= adopted
    assert not any(a.endswith(":30") for a in adopted)
    assert st.get_step_state(ws, "patrol_extract:30")["status"] != st.ST_PASSED
    assert st.adopt_legacy_steps(ws) == []


def test_new_flow_extract_in_progress_is_not_adopted(isolated):
    """新流程：只有一次巡检、提取尚未运行，不能被认领。真实运行过的提取记录也不被覆盖。"""
    ws = _book(isolated, quality="premium", total=3)
    st.update_step(ws, "patrol:10", status=st.ST_PASSED)
    assert st.adopt_legacy_steps(ws) == []
    st.update_step(ws, "patrol:20", status=st.ST_PASSED)
    st.update_step(ws, "patrol_extract:10", status=st.ST_FAILED, error="真实失败")
    st.adopt_legacy_steps(ws)
    assert st.get_step_state(ws, "patrol_extract:10")["status"] == st.ST_FAILED
