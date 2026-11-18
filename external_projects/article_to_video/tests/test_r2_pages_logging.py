"""R2：全页面实时日志——服务层日志点、大纲后台化、页面底部日志面板、整章重置日志。"""
import time

import pytest

from article_to_video import joblog
from article_to_video.planning.runner import load_spine, plan_outline
from article_to_video.script import writer as w
from conftest import PROJECT_DIR
from helpers_p1 import make_project
from helpers_p2 import ScriptLLM, good_script, ready_project
from ui import jobs


def _wait(pdir, timeout=20.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        job = jobs.status(pdir)
        if job and job["state"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("后台任务未在限定时间内结束")


def _messages(pdir, scopes=None):
    return [r["message"] for r in joblog.tail(pdir, 500, scopes)]


# ---------- 服务层日志点 ----------

def test_writer_logs_model_calls_and_validation_under_bound_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = ready_project(tmp_path)
    with joblog.bind(pdir, "script"):
        w.write_part(pdir, "P01", ScriptLLM(good_script))
        w.write_part(pdir, "P01", ScriptLLM(good_script))      # 第二次：输入未变，复用
    msgs = _messages(pdir, ["script"])
    assert any("调用模型写稿（第 1 次" in m for m in msgs)
    assert any("稿件通过校验" in m for m in msgs)
    assert any("复用已有口播稿" in m for m in msgs)


def test_writer_logs_retry_when_output_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = ready_project(tmp_path)
    bad = {"n": 0}

    def responder(prompt):
        bad["n"] += 1
        if bad["n"] == 1:
            return "这不是 JSON"
        return good_script(prompt)

    with joblog.bind(pdir, "script"):
        w.write_part(pdir, "P01", ScriptLLM(responder))
    msgs = _messages(pdir, ["script"])
    assert any("JSON 解析失败" in m or "将重试" in m for m in msgs)
    assert any("调用模型写稿（第 2 次" in m for m in msgs)


def test_image_fallback_is_logged_when_no_provider(tmp_path, monkeypatch):
    from article_to_video.visual.images import ensure_illustration
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = tmp_path / "p"
    pdir.mkdir()
    scene = {"id": "S01", "type": "illustration", "image": {"subject": "蒸汽机"}}
    with joblog.bind(pdir, "render"):
        out = ensure_illustration(pdir, "P01", scene, provider=None, style_anchor="风格",
                                  orientation="landscape", size="1024x1024")
    assert out["ok"] is False
    rows = joblog.tail(pdir, 10, ["render"])
    assert any(r["level"] == "warn" and "未配置生图服务" in r["message"] for r in rows)


# ---------- 大纲后台化 ----------

def test_outline_job_writes_scope_logs_and_finishes(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    assert jobs.start(pdir, "生成大纲（离线规则）", lambda: plan_outline(pdir, engine="rules", force=True),
                      scope="outline")
    job = _wait(pdir)
    assert job["state"] == "done"
    assert load_spine(pdir) is not None
    msgs = _messages(pdir, ["outline"])
    assert any("开始生成大纲" in m for m in msgs)
    assert any("大纲已保存" in m for m in msgs)
    assert any("后台任务完成" in m for m in msgs)
    assert not any("开始生成大纲" in m for m in _messages(pdir, ["script"]))   # scope 互不混淆


def test_outline_job_error_is_reported_with_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = ready_project(tmp_path)          # 已确认的大纲，不允许无声覆盖
    assert jobs.start(pdir, "生成大纲", lambda: plan_outline(pdir, engine="rules"), scope="outline")
    job = _wait(pdir)
    assert job["state"] == "error" and "已确认" in job["message"]
    rows = joblog.tail(pdir, 20, ["outline"])
    assert any(r["level"] == "error" and "已确认" in r["message"] for r in rows)


# ---------- 页面底部日志面板 ----------

PANEL_TITLES = {
    "🗺️ 大纲": "大纲运行日志",
    "📝 口播稿": "写稿运行日志",
    "🔊 配音": "配音运行日志",
    "🎞️ 分镜": "分镜与渲染运行日志",
    "🎬 合成导出": "合成导出运行日志",
    "📚 项目": "最近日志",
}


@pytest.mark.parametrize("page", list(PANEL_TITLES))
def test_each_business_page_ends_with_log_panel(tmp_path, monkeypatch, page):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    ready_project(tmp_path)
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=90).run()
    at.sidebar.radio[0].set_value(page).run()
    assert not at.exception, [e.value for e in at.exception]
    assert any(PANEL_TITLES[page] in m.value for m in at.markdown)


def test_outline_generate_button_starts_background_job_from_ui(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)           # 没有大纲
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=90).run()
    at.sidebar.radio[0].set_value("🗺️ 大纲").run()
    gen = next(b for b in at.button if b.label.startswith("生成 / 重新生成"))
    gen.click().run()
    assert not at.exception, [e.value for e in at.exception]
    job = _wait(pdir)
    assert job["state"] == "done", job
    assert load_spine(pdir) is not None


# ---------- 整章重置写日志 ----------

def test_reset_part_is_logged_to_pipeline_scope(tmp_path, monkeypatch):
    from article_to_video.pipeline.reset import reset_part
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = ready_project(tmp_path)
    w.write_part(pdir, "P01", ScriptLLM(good_script))
    removed = reset_part(pdir, "P01", from_stage="script", confirm=True)
    joblog.log(pdir, "pipeline", f"整章重置：P01 从「script」起删除 {len(removed)} 项", level="warn")
    assert any("整章重置" in m for m in _messages(pdir, ["pipeline"]))
