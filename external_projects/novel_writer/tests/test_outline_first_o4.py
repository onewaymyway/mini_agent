"""大纲先行 O4：简述修改后已写章节的标记与处理（ask / auto_rewrite）。"""

import copy
import sys
from pathlib import Path

import pytest

from novel_writer import brief as BR
from novel_writer import brief_review as BRV
from novel_writer import brief_sync as BSY
from novel_writer import ondemand, pipeline, tool_api
from novel_writer import state as st_state
from novel_writer.revision_flow.common import FlowError

sys.path.insert(0, str(Path(__file__).parent))
from test_outline_first_o3 import Scripted, _all_ok, _check_step, isolated, ws  # noqa: E402,F401
from test_outline_first_o3 import BC  # noqa: E402


def _passed_check(ws, n=1):
    """让第 n 章的关键内容核对通过（会记录写作所依据的简述）。"""
    BC.h_brief_check(ws, _check_step(n), {}, invoker=Scripted([_all_ok(BR.load_brief(ws, n))]))


def _edit(ws, n, **key_over):
    b = copy.deepcopy(BR.load_brief(ws, n))
    b["key"].update(key_over or {"must_hook": "门外有两个人"})
    r = BRV.save_edit(ws, n, b, reason="测试修改")
    assert r["ok"], r
    return b


def _write_chapter(ws, n):
    p = ws.chapter_path(n, f"章{n}", 3)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"# 第{n}章\n\n" + "正文" * 200 + "\n", encoding="utf-8")


def test_unchanged_brief_not_affected(ws):
    _passed_check(ws)
    assert BSY.affected(ws) == []


def test_edit_marks_written_chapter_with_diff(ws):
    _passed_check(ws)
    _edit(ws, 1)
    rows = BSY.affected(ws)
    assert [r["chapter"] for r in rows] == [1]
    assert any("章末钩子" in d for d in rows[0]["diff"])


def test_chapter_without_text_never_affected(ws):
    # 第2章没有正文：即使简述改了也不算「已写章节」
    BSY.record_baseline(ws, [2])
    _edit(ws, 2)
    assert BSY.affected(ws) == []


def test_keep_clears_and_updates_basis(ws):
    _passed_check(ws)
    _edit(ws, 1)
    assert BSY.keep(ws, 1)["action"] == "keep"
    assert BSY.affected(ws) == []
    _edit(ws, 1, must_hook="再改一次")          # 再次变更会重新标记
    assert [r["chapter"] for r in BSY.affected(ws)] == [1]


def test_ignore_only_silences_this_version(ws):
    _passed_check(ws)
    _edit(ws, 1)
    BSY.ignore(ws, 1)
    assert BSY.affected(ws) == []
    _edit(ws, 1, must_hook="第二次变更")
    assert [r["chapter"] for r in BSY.affected(ws)] == [1]


def test_handling_requires_pending_change(ws):
    _passed_check(ws)
    with pytest.raises(FlowError):
        BSY.keep(ws, 1)


def test_rewrite_creates_cycle_request_with_diff_but_does_not_call_model(ws):
    _passed_check(ws)
    _edit(ws, 1)
    res = BSY.rewrite(ws, 1)
    reqs = ondemand.list_requests(ws, "revision")
    assert len(reqs) == 1 and reqs[0]["chapter"] == 1 and reqs[0]["mode"] == "cycle"
    assert "章末钩子" in reqs[0]["instruction"] and res["action"] == "rewrite"
    assert BSY.affected(ws) == []                     # 已提交，不再重复提示
    assert BSY.decisions(ws)["1"]["action"] == "rewrite"


def test_baseline_fallback_when_no_written_record(ws):
    BSY.record_baseline(ws, [1])
    _edit(ws, 1)
    assert [r["chapter"] for r in BSY.affected(ws)] == [1]
    assert BSY.affected(ws)[0]["basis"] == "baseline"


def test_baseline_does_not_override_written(ws):
    _passed_check(ws)
    _edit(ws, 1)
    BSY.record_baseline(ws, [1])                      # 已有 written 记录，不覆盖
    assert BSY.affected(ws)[0]["basis"] == "written"


def test_adopt_current_as_written_for_legacy_books(ws):
    assert BSY.adopt_current_as_written(ws) == [1]
    _edit(ws, 1)
    assert [r["chapter"] for r in BSY.affected(ws)] == [1]


# ─────────────────────────── 策略 ───────────────────────────

def test_ask_policy_never_submits(ws):
    _passed_check(ws)
    _edit(ws, 1)
    out = BSY.apply_policy(ws)
    assert out["submitted"] == [] and ondemand.list_requests(ws, "revision") == []
    assert [r["chapter"] for r in BSY.affected(ws)] == [1]


def test_auto_rewrite_covers_all_affected_chapters(ws):
    s = ws.settings()
    ws.save_settings({**s, "brief_change_policy": "auto_rewrite"})
    for n in (2, 3):
        _write_chapter(ws, n)
    BSY.adopt_current_as_written(ws)
    for n in (1, 2, 3):
        _edit(ws, n)
    out = BSY.apply_policy(ws)
    assert out["submitted"] == [1, 2, 3] and out["failed"] == {}
    assert sorted(r["chapter"] for r in ondemand.list_requests(ws, "revision")) == [1, 2, 3]
    assert BSY.affected(ws) == []


def test_auto_rewrite_isolates_single_chapter_failure(ws, monkeypatch):
    ws.save_settings({**ws.settings(), "brief_change_policy": "auto_rewrite"})
    for n in (2, 3):
        _write_chapter(ws, n)
    BSY.adopt_current_as_written(ws)
    for n in (1, 2, 3):
        _edit(ws, n)
    real = BSY.rewrite

    def flaky(w, n):
        if n == 2:
            raise FlowError("模拟失败")
        return real(w, n)
    monkeypatch.setattr(BSY, "rewrite", flaky)
    out = BSY.apply_policy(ws)
    assert out["submitted"] == [1, 3] and "模拟失败" in out["failed"][2]


# ─────────────────────────── 步骤状态 ───────────────────────────

def _mark_passed(ws, step_id):
    step = {s.id: s for s in pipeline.build_plan(ws)}[step_id]
    st_state.update_step(ws, step_id, status=st_state.ST_PASSED, input_hash=st_state.input_hash(ws, step),
                         input_digests=st_state.input_digests(ws, step))


def _stale_after_edit(ws):
    _mark_passed(ws, "ch01.write_chapter")
    _mark_passed(ws, "ch01.brief_check")
    _passed_check(ws)
    _edit(ws, 1)
    st_state.mark_stale(ws, pipeline.build_plan(ws))
    return {sid: st_state.get_step_state(ws, sid)["status"] for sid in ("ch01.write_chapter", "ch01.brief_check")}


def test_edit_makes_write_and_check_stale_and_keep_rebaselines(ws):
    status = _stale_after_edit(ws)
    assert status["ch01.write_chapter"] == st_state.ST_STALE      # 不处理就会整章重写
    BSY.keep(ws, 1)
    assert st_state.get_step_state(ws, "ch01.write_chapter")["status"] == st_state.ST_PASSED
    assert st_state.get_step_state(ws, "ch01.brief_check")["status"] == st_state.ST_PASSED


def test_rewrite_rebaselines_write_but_leaves_check_to_verify(ws):
    _stale_after_edit(ws)
    BSY.rewrite(ws, 1)
    assert st_state.get_step_state(ws, "ch01.write_chapter")["status"] == st_state.ST_PASSED
    assert st_state.get_step_state(ws, "ch01.brief_check")["status"] == st_state.ST_STALE


def test_check_pass_after_rewrite_updates_written_basis(ws):
    _passed_check(ws)
    _edit(ws, 1)
    BSY.rewrite(ws, 1)
    _passed_check(ws)                                  # 重写后核对通过：以新简述为依据
    assert BSY.affected(ws) == [] and "1" not in BSY.decisions(ws)


# ─────────────────────────── 接口 ───────────────────────────

def test_tool_api_list_and_resolve(ws):
    _passed_check(ws)
    _edit(ws, 1)
    r = tool_api.brief_changed_chapters(str(ws.root))
    assert r["ok"] and [c["chapter"] for c in r["data"]["chapters"]] == [1]
    assert tool_api.resolve_brief_change(str(ws.root), 1, "bogus")["ok"] is False
    assert tool_api.resolve_brief_change(str(ws.root), 1, "keep")["ok"] is True
    assert tool_api.brief_changed_chapters(str(ws.root))["data"]["chapters"] == []
