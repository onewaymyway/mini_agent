"""新方案一键流程：各挡位的检查点、G3 全自动、可重入运行。"""
import pytest

from article_to_video.deck.pipeline import deck_manual_checkpoints, load_run_status, run_deck_pipeline
from article_to_video.deck.store import confirm_deck, deck_status
from article_to_video.deck.script import confirm_script, script_status
from article_to_video.pipeline.gates import PipelineError
from article_to_video.project_store import write_json_atomic
from article_to_video.providers.tts import SilenceTTS
from article_to_video.source.md_parse import parse_markdown
from helpers_p2 import make_project
from test_deck_phase2 import FakeLLM, FIXTURE


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    blocks = parse_markdown(FIXTURE.read_text(encoding="utf-8"))["blocks"]
    write_json_atomic(pdir / "source" / "blocks.json", {"blocks": blocks})
    write_json_atomic(pdir / "plan" / "spine.json",
                      {"title": "测试文章", "confirmed": True,
                       "parts": [{"id": "P01", "title": "引言", "role": "claim", "one_line": "", "weight": 1.0,
                                  "budget_minutes": 6.0, "section_ids": ["s01"]}]})
    return pdir


def test_manual_checkpoints_follow_the_gears():
    assert deck_manual_checkpoints("G0") == {"plan", "script", "export"}
    assert deck_manual_checkpoints("G1") == {"export"}
    assert deck_manual_checkpoints("G2") == set()
    assert deck_manual_checkpoints("G3") == set()


def test_g2_without_budget_is_refused(project):
    with pytest.raises(PipelineError):
        run_deck_pipeline(project, gear="G2", llm=FakeLLM(), tts=SilenceTTS())


def test_g0_stops_at_the_plan_checkpoint(project):
    st = run_deck_pipeline(project, gear="G0", llm=FakeLLM(), tts=SilenceTTS())
    assert st["state"] == "waiting" and st["waiting"]["stage"] == "plan"
    assert deck_status(project, "P01")["exists"] and not deck_status(project, "P01")["confirmed"]
    assert load_run_status(project)["state"] == "waiting"


def test_g0_waits_for_script_confirmation_after_the_plan_is_confirmed(project):
    run_deck_pipeline(project, gear="G0", llm=FakeLLM(), tts=SilenceTTS())
    confirm_deck(project, "P01")
    st = run_deck_pipeline(project, gear="G0", llm=FakeLLM(), tts=SilenceTTS())
    assert st["state"] == "waiting" and st["waiting"]["stage"] == "script"


def test_g3_runs_everything_unattended_and_exports(project):
    st = run_deck_pipeline(project, gear="G3", llm=FakeLLM(), tts=SilenceTTS())
    assert st["state"] == "done" and st["waiting"] is None
    assert [s["stage"] for s in st["steps"]] == ["plan", "script", "voice", "compose", "export"]
    assert deck_status(project, "P01")["confirmed"] and script_status(project, "P01")["confirmed"]
    assert st["outputs"] and (project / "deck" / "exports" / st["outputs"][0]).is_file()


def test_g1_auto_confirms_plan_and_script_but_waits_for_approval(project):
    st = run_deck_pipeline(project, gear="G1", llm=FakeLLM(), tts=SilenceTTS())
    assert st["state"] == "waiting" and st["waiting"]["stage"] == "export"
    assert deck_status(project, "P01")["confirmed"] and script_status(project, "P01")["confirmed"]
    st2 = run_deck_pipeline(project, gear="G1", approve=True, llm=FakeLLM(), tts=SilenceTTS())
    assert st2["state"] == "done"


def test_missing_model_fails_clearly(project):
    st = run_deck_pipeline(project, gear="G3", llm=None, tts=SilenceTTS())
    assert st["state"] == "failed" and "文本模型" in st["error"]
