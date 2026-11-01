"""实时日志与后台任务测试：日志读写与容错、素材生成日志、停止请求、锁冲突、任务生命周期、界面渲染。"""

import json
import time
from pathlib import Path

import pytest

from conftest import build_export
from novel_to_video import jobs, joblog, runlock, runtime
from novel_to_video import tool_api
from novel_to_video.providers.image import StubImageProvider
from novel_to_video.providers.text import RuleTextLLM
from novel_to_video.steps import global_visual as gv


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_TO_VIDEO_PROVIDERS", "stub")
    src = build_export(tmp_path / "src", chapters=(1,))
    res = tool_api.create_project_from_source(str(tmp_path / "out"), str(src), title="t")
    return Path(res["data"]["project_dir"])


def _wait(pdir: Path, timeout: float = 60) -> dict:
    deadline = time.time() + timeout
    st = {}
    while time.time() < deadline:
        st = jobs.status(pdir)
        if st.get("state") in ("done", "stopped", "failed"):
            return st
        time.sleep(0.2)
    raise AssertionError(f"任务未在 {timeout}s 内结束：{st}")


# ---------------------------------------------------------------- joblog

def test_log_and_tail_with_scope_filter(proj):
    joblog.log(proj, "assets", "甲", level="ok")
    joblog.log(proj, "chapter", "乙")
    joblog.log(proj, "assets", "丙", level="error")
    rows = joblog.tail(proj, limit=10, scopes=["assets"])
    assert [r["message"] for r in rows] == ["甲", "丙"]
    assert rows[1]["level"] == "error"


def test_tail_limit_keeps_latest(proj):
    for i in range(30):
        joblog.log(proj, "assets", f"m{i}")
    rows = joblog.tail(proj, limit=5)
    assert [r["message"] for r in rows] == [f"m{i}" for i in range(25, 30)]


def test_tail_tolerates_truncated_last_line(proj):
    joblog.log(proj, "assets", "完整")
    with (proj / joblog.LOG_FILE).open("a", encoding="utf-8") as f:
        f.write('{"ts": "2026-11-01T10:00:00", "scope": "assets", "mess')  # 进程被截断
    rows = joblog.tail(proj)
    assert [r["message"] for r in rows] == ["完整"]


def test_format_row_marks_levels(proj):
    line = joblog.format_row({"ts": "2026-11-01T10:20:30", "level": "error", "message": "失败"})
    assert line.startswith("10:20:30") and "✘" in line and line.endswith("失败")


# ---------------------------------------------------------------- 素材生成的日志

def test_g2_and_g3_write_progress_logs(proj):
    gv.visual_text_pass(proj, RuleTextLLM())
    gv.portrait_pass(proj, StubImageProvider())
    msgs = [r["message"] for r in joblog.tail(proj, scopes=["assets"])]
    assert any(m.startswith("G-2 文本设定补全开始") for m in msgs)
    assert any(m.startswith("G-2 补全：林远") for m in msgs)
    assert any(m.startswith("G-3 完成：林远") for m in msgs)


def test_portrait_stop_request_halts_between_entities(proj):
    gv.visual_text_pass(proj, RuleTextLLM())
    runtime.request_stop(proj)  # 未运行时只写停止标记
    summary = gv.portrait_pass(proj, StubImageProvider())
    assert summary["stopped"] == "用户请求停止"
    assert summary["done"] == []
    assert any("已停止" in r["message"] for r in joblog.tail(proj, scopes=["assets"]))
    runtime.clear_stop(proj)


def test_text_pass_stop_request_halts(proj):
    runtime.request_stop(proj)
    summary = gv.visual_text_pass(proj, RuleTextLLM())
    assert summary["stopped"] == "用户请求停止" and summary["done"] == []
    runtime.clear_stop(proj)


# ---------------------------------------------------------------- 后台任务生命周期

def test_job_lifecycle_logs_start_and_finish(proj):
    r = tool_api.start_visual_text_background(str(proj))
    assert r["ok"], r
    st = _wait(proj)
    assert st["state"] == "done"
    assert st["summary"].get("done") == 3  # 2 个角色 + 1 个地点
    assert not runlock.inspect(proj)["held"]
    msgs = [x["message"] for x in joblog.tail(proj, scopes=["assets"])]
    assert msgs[0].startswith("开始：G-2")
    assert msgs[-1].startswith("完成：")


def test_job_refused_while_project_locked(proj):
    runlock.acquire(proj)
    try:
        r = tool_api.start_visual_text_background(str(proj))
        assert not r["ok"] and "正在运行" in r["error"]["message"]
    finally:
        runlock.release(proj)


def test_job_stop_while_running_marks_stopped(proj):
    gv.visual_text_pass(proj, RuleTextLLM())

    def fn():
        runtime.request_stop(proj)     # 在任务内部请求停止（模拟用户点击停止）
        return gv.portrait_pass(proj, StubImageProvider())

    res = jobs.start(proj, name="t", label="测试停止", scope="assets", fn=fn)
    assert res["ok"]
    st = _wait(proj)
    assert st["state"] == "stopped"
    assert not runlock.inspect(proj)["held"]


def test_job_exception_is_logged_not_swallowed(proj):
    def boom():
        raise ValueError("模拟异常")

    res = jobs.start(proj, name="t", label="异常测试", scope="assets", fn=boom)
    assert res["ok"]
    st = _wait(proj)
    assert st["state"] == "failed" and "模拟异常" in st["error"]
    errs = [r for r in joblog.tail(proj, scopes=["assets"]) if r["level"] == "error"]
    assert errs and "模拟异常" in errs[-1]["message"]
    assert not runlock.inspect(proj)["held"]


def test_request_stop_refused_when_idle(proj):
    r = tool_api.job_stop(str(proj))
    assert not r["ok"]


def test_gender_job_logs_each_character(proj):
    r = tool_api.start_gender_suggest_background(str(proj))
    assert r["ok"]
    st = _wait(proj)
    assert st["state"] == "done"
    rows = joblog.tail(proj, scopes=["gender"])
    assert any("性别线索检索开始" in x["message"] for x in rows)
    assert sum(1 for x in rows if "建议" in x["message"]) >= 2


# ---------------------------------------------------------------- 界面

def test_assets_page_renders_log_panel(proj, tmp_path):
    from streamlit.testing.v1 import AppTest
    app = str(Path(__file__).resolve().parent.parent / "app.py")
    at = AppTest.from_file(app, default_timeout=120).run()
    at.sidebar.radio[0].set_value("🎭 全书素材").run()
    at.text_input(key="assets_root").input(str(proj.parent)).run()
    assert not at.exception
    assert any("素材生成日志" in m.value for m in at.markdown)


def test_final_and_gender_pages_render_log_panels(proj):
    from streamlit.testing.v1 import AppTest
    app = str(Path(__file__).resolve().parent.parent / "app.py")
    at = AppTest.from_file(app, default_timeout=120).run()
    at.sidebar.radio[0].set_value("🎞️ 全书合成").run()
    at.text_input(key="final_root").input(str(proj.parent)).run()
    assert not at.exception
    assert any("全书合成日志" in m.value for m in at.markdown)
    at.sidebar.radio[0].set_value("⚧ 性别确认").run()
    at.text_input(key="gen_root").input(str(proj.parent)).run()
    assert not at.exception
    assert any("性别检索日志" in m.value for m in at.markdown)


# ---------------------------------------------------------------- G-3 运行信息进入界面日志

def test_g3_background_shows_skill_messages_in_job_log(proj, monkeypatch, capsys):
    """回归：G-3 的 skill 输出（原先只打印到命令行）必须写入任务日志，界面可见；命令行输出仍保留。"""
    import requests
    from novel_to_video.providers import skill_bridge

    monkeypatch.delenv("NOVEL_TO_VIDEO_PROVIDERS", raising=False)  # 使用真实 provider 路径（经 skill）
    monkeypatch.setenv("AGNES_API_KEYS", "k1")
    skill_bridge._CACHE.clear()

    class Resp:
        status_code = 401
        text = "invalid api key"
        ok = False

        def json(self):
            return {"error": "invalid api key"}

    monkeypatch.setattr(requests.Session, "post", lambda self, url, **kw: Resp())
    gv.visual_text_pass(proj, RuleTextLLM())

    r = tool_api.start_portraits_background(str(proj))
    assert r["ok"], r
    st = _wait(proj)
    assert st["state"] in ("done", "stopped")

    msgs = [x["message"] for x in joblog.tail(proj, scopes=["assets"])]
    assert any(m.startswith("图像接口请求") for m in msgs), msgs
    assert any("不可重试错误" in m for m in msgs), msgs
    assert any(m.startswith("G-3 失败") for m in msgs), msgs
    # 命令行输出仍然保留
    assert "不可重试错误" in capsys.readouterr().out
    skill_bridge._CACHE.clear()
