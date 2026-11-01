"""失败必须带阶段与具体原因：未预期异常不能静默变成“failed”。"""
import json
from pathlib import Path

from novel_to_video import chapter_io as cio
from novel_to_video import chapter_pipeline as cp
from novel_to_video import joblog
from novel_to_video import batch as _batch
from novel_to_video.providers.tts import SilenceTTS
from novel_to_video.providers.video import StubVideoProvider
from novel_to_video.providers.image import StubImageProvider
import pytest

from conftest import build_export
from novel_to_video import tool_api
from novel_to_video.providers.text import RuleTextLLM
from novel_to_video.steps import global_visual as gv
from test_chapter_pipeline import CHAPTER_TEXT, ChapterLLM, SCRIPT  # reuse fixtures


@pytest.fixture
def proj(tmp_path):
    src = build_export(tmp_path / "src", chapters=(1,))
    (src / "正文" / "第001章 测试章1.md").write_text(CHAPTER_TEXT, encoding="utf-8")
    res = tool_api.create_project_from_source(str(tmp_path / "out"), str(src), title="测试")
    assert res["ok"], res
    p = Path(res["data"]["project_dir"])
    gv.visual_text_pass(p, RuleTextLLM())
    gv.portrait_pass(p, StubImageProvider())
    return p


def _run(proj, llm):
    return cp.run_chapter(proj, 1, text_llm=llm, image=StubImageProvider(),
                          video=StubVideoProvider(), tts=SilenceTTS())


def test_unexpected_exception_reports_stage_and_reason(proj, monkeypatch):
    # 现场：C-3 之后某阶段抛出 TypeError（不在已知异常列表里）
    def boom(*a, **k):
        raise TypeError("'NoneType' object is not subscriptable")
    monkeypatch.setattr(cp, "run_assets", boom)
    r = _run(proj, ChapterLLM())
    assert r["status"] == "failed"
    assert "C-5 补建角色" in r["message"]
    assert "TypeError" in r["message"] and "NoneType" in r["message"]


def test_unexpected_exception_is_written_to_chapter_log_with_traceback(proj, monkeypatch):
    def boom(*a, **k):
        raise TypeError("bad thing")
    monkeypatch.setattr(cp, "run_assets", boom)
    _run(proj, ChapterLLM())
    errs = [x for x in joblog.tail(proj, scopes=["chapter"]) if x["level"] == "error"]
    assert any("TypeError" in x["message"] and "bad thing" in x["message"] for x in errs)
    assert any("boom" in x["message"] for x in errs)   # 堆栈里要能看到出错的函数


def test_stage_is_marked_failed_with_the_same_reason(proj, monkeypatch):
    def boom(*a, **k):
        raise TypeError("bad thing")
    monkeypatch.setattr(cp, "run_assets", boom)
    _run(proj, ChapterLLM())
    st = cio.load_state(proj, 1)["stages"]["assets"]
    assert st["status"] == "failed" and "bad thing" in (st.get("error") or "")


def test_queue_line_includes_reason(proj):
    results = {1: {"status": "failed", "message": "C-5 补建角色：boom"}}
    def run_one(no):
        return results[no]
    _batch.run_queue(proj, [1], gear="G0", run_one=run_one)
    lines = [x["message"] for x in joblog.tail(proj, scopes=["queue"])]
    assert any("第 1 章结束 → failed：C-5 补建角色：boom" in m for m in lines), lines
