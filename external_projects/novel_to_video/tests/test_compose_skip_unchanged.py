"""章节合成指纹：已完成且输入未变的章节跳过 C-9，队列直接跳过；修改设置后重新合成。"""

import pytest

from novel_to_video import tool_api
from novel_to_video.steps import chapter_compose as cc

from test_chapter_pipeline import project  # noqa: F401  (fixture)
from test_recompose import _finish


def _no_join(*a, **k):
    raise AssertionError("输入未变化，不应重新拼接")


def test_done_chapter_is_up_to_date(project):
    out = _finish(project)
    assert cc.is_up_to_date(project, 1)
    before = out.read_bytes()
    res = cc.run_compose(project, 1)
    assert res.get("skipped") is True
    assert out.read_bytes() == before


def test_unchanged_chapter_skips_join(project, monkeypatch):
    _finish(project)
    monkeypatch.setattr(cc, "_join", _no_join)
    assert cc.run_compose(project, 1).get("skipped") is True


def test_subtitle_change_invalidates_and_recomposes(project):
    _finish(project)
    assert tool_api.set_subtitles(str(project), False)["ok"]
    assert not cc.is_up_to_date(project, 1)
    res = cc.run_compose(project, 1)
    assert not res.get("skipped")
    assert res["subtitles"]["enabled"] is False
    assert cc.is_up_to_date(project, 1)


def test_force_recompose_ignores_cache(project):
    _finish(project)
    res = cc.run_compose(project, 1, force=True)
    assert not res.get("skipped")


def test_queue_skips_done_unchanged_without_engine(project, monkeypatch):
    _finish(project)

    def boom(*a, **k):
        raise AssertionError("已完成章节不应进入流水线")
    monkeypatch.setattr(tool_api, "run_chapter", boom)
    out = tool_api._run_queue_locked(str(project), [1], gear="G0")
    assert out["ok"], out
    assert out["data"]["skipped_unchanged"] == [1]
    assert out["data"]["results"] == {}
