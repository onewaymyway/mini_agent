"""R3：大纲默认使用模型；模型未就绪时明确报错/提示，绝不静默改用规则版。"""
import pytest

from article_to_video.pipeline.gates import PipelineError
from article_to_video.pipeline.run import run_pipeline
from article_to_video.planning import runner
from article_to_video.planning.errors import PlanError
from article_to_video.providers import text as text_mod
from conftest import PROJECT_DIR
from helpers_p1 import make_project


class BadLLM:
    """永远输出无法解析的内容。"""
    name = "bad"

    def __init__(self):
        self.calls = 0

    def ask(self, prompt, system=""):
        self.calls += 1
        return "这不是 JSON"


def test_plan_outline_defaults_to_model_and_refuses_without_llm(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    with pytest.raises(PlanError) as info:
        runner.plan_outline(pdir)                       # 不指定 engine：默认模型
    assert "文本模型" in str(info.value) and "离线规则" in str(info.value)
    assert not (pdir / "plan" / "spine.json").exists()  # 没有静默改用规则版


def test_model_output_failure_writes_nothing_and_does_not_fall_back(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    llm = BadLLM()
    with pytest.raises(PlanError):
        runner.plan_outline(pdir, llm=llm)              # 默认 engine=llm
    assert llm.calls >= 1
    assert not (pdir / "plan" / "spine.json").exists()
    assert not (pdir / "plan" / "digests.json").exists()


def test_pipeline_outline_defaults_to_model_and_fails_explicitly(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    msg = None
    try:
        status = run_pipeline(pdir, until="outline", gear="G1")         # 无模型、未指定大纲引擎
    except PipelineError as exc:
        msg = str(exc)
    else:
        assert status["state"] == "failed"
        msg = status["error"]
    assert "文本模型" in msg
    assert not (pdir / "plan" / "spine.json").exists()


def test_pipeline_offline_engine_still_works(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    status = run_pipeline(pdir, until="outline", gear="G1", outline_engine="rules")
    assert (pdir / "plan" / "spine.json").exists()
    assert status["state"] in ("done", "waiting")


def test_text_model_status_reports_reason_and_caches(monkeypatch):
    def broken(self):
        raise RuntimeError("缺少 API key")
    monkeypatch.setattr(text_mod.MiniAgentTextLLM, "_get_helper", broken)
    ok, why = text_mod.text_model_status(force=True)
    assert ok is False and "缺少 API key" in why
    # 缓存：未强制刷新时不再尝试读取配置
    monkeypatch.setattr(text_mod.MiniAgentTextLLM, "_get_helper", lambda self: 1 / 0)
    assert text_mod.text_model_status() == (False, why)
    monkeypatch.setattr(text_mod.MiniAgentTextLLM, "_get_helper", lambda self: object())
    assert text_mod.text_model_status(force=True)[0] is True


def test_outline_page_blocks_model_generation_when_model_unavailable(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    import ui.outline_page as op
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    make_project(tmp_path)
    monkeypatch.setattr(op, "text_model_status", lambda: (False, "缺少 API key"))
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=90).run()
    at.sidebar.radio[0].set_value("🗺️ 大纲").run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("文本模型未就绪" in w.value and "缺少 API key" in w.value for w in at.warning)
    gen = next(b for b in at.button if b.label.startswith("生成 / 重新生成"))
    assert gen.disabled is True
