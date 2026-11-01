"""章节运行的过程日志：文本模型、逐镜视频、逐段配音、耗时与失败原因都写入日志，章节页可见。"""

import time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from conftest import build_export
from novel_to_video import joblog, runtime, tool_api
from novel_to_video.providers.image import StubImageProvider
from novel_to_video.providers.text import RuleTextLLM
from novel_to_video.steps import global_visual as gv

from test_phase3 import CH

APP = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_TO_VIDEO_PROVIDERS", "stub")
    src = build_export(tmp_path / "src", chapters=(1,))
    (src / "正文" / "第001章 测试章1.md").write_text(CH.format(n=1), encoding="utf-8")
    res = tool_api.create_project_from_source(str(tmp_path / "out"), str(src), title="t")
    p = Path(res["data"]["project_dir"])
    gv.visual_text_pass(p, RuleTextLLM())
    gv.portrait_pass(p, StubImageProvider())
    return p


def _wait_queue(p: Path, timeout: float = 180) -> dict:
    deadline = time.time() + timeout
    st = {}
    while time.time() < deadline:
        st = runtime.read_state(p)
        if st.get("state") in ("done", "halted", "failed"):
            return st
        time.sleep(0.3)
    raise AssertionError(f"队列未结束：{st}")


def test_timed_logs_start_finish_and_failure(proj):
    with joblog.timed(proj, "chapter", "测试步骤"):
        pass
    with pytest.raises(ValueError):
        with joblog.timed(proj, "chapter", "会失败的步骤"):
            raise ValueError("原因说明")
    rows = joblog.tail(proj, scopes=["chapter"])
    msgs = [r["message"] for r in rows]
    assert any(m.startswith("测试步骤…") for m in msgs)
    assert any(m.startswith("测试步骤 完成（") for m in msgs)
    fail = [r for r in rows if r["level"] == "error"]
    assert fail and "原因说明" in fail[-1]["message"]


def test_chapter_run_writes_every_stage(proj):
    r = tool_api.start_queue_background(str(proj), [1], gear="G0")
    assert r["ok"], r
    st = _wait_queue(proj)
    assert st["state"] == "done", st
    msgs = [x["message"] for x in joblog.tail(proj, scopes=["chapter", "queue"])]
    assert any("C-1 调用文本模型生成剧本" in m for m in msgs), msgs
    assert any("C-3 调用文本模型规划镜头" in m for m in msgs), msgs
    assert any("C-6 配音 s01/" in m for m in msgs), msgs
    assert any("C-8 镜头 s01：提交视频生成" in m for m in msgs), msgs
    assert any("C-8 镜头 s01 完成" in m for m in msgs), msgs
    assert any("结束：waiting_review" in m for m in msgs), msgs
    assert any(m.startswith("队列：第 1 章结束") for m in msgs), msgs


def test_chapter_page_shows_run_log(proj):
    r = tool_api.start_queue_background(str(proj), [1], gear="G0")
    assert r["ok"]
    _wait_queue(proj)
    at = AppTest.from_file(APP, default_timeout=180).run()
    at.sidebar.radio[0].set_value("🎬 章节生产").run()
    at.text_input(key="chap_root").input(str(proj.parent)).run()
    assert not at.exception
    shown = " ".join(c.value for c in at.code)
    assert "C-1 调用文本模型生成剧本" in shown, shown[:300]
    assert "C-8 镜头 s01" in shown
