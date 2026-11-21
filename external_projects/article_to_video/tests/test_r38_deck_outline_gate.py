"""R38：新方案开始前自动保证大纲（C0）已确认，G3 等挡位不再因“请先确认大纲”失败。"""
from pathlib import Path

import pytest

from article_to_video.deck import pipeline as dp
from article_to_video.planning.errors import PlanError


@pytest.fixture
def calls(monkeypatch):
    rec = {"plan": [], "confirm": []}
    state = {"spine": None}

    def fake_load(pdir):
        return state["spine"]

    def fake_plan(pdir, **kw):
        rec["plan"].append(kw)
        state["spine"] = {"confirmed": False, "parts": [{"id": "P01"}]}

    def fake_confirm(pdir, now=None):
        rec["confirm"].append(True)
        state["spine"] = {**state["spine"], "confirmed": True}

    monkeypatch.setattr(dp, "load_spine", fake_load)
    monkeypatch.setattr(dp, "plan_outline", fake_plan)
    monkeypatch.setattr(dp, "confirm_spine", fake_confirm)
    monkeypatch.setattr(dp, "effective_group", lambda pdir, g: {"outline_engine": "rules"})
    return rec, state


def test_missing_outline_is_generated_and_auto_confirmed_on_g3(tmp_path, calls):
    rec, state = calls
    assert dp.ensure_outline(Path(tmp_path), "G3") is None
    assert len(rec["plan"]) == 1 and rec["plan"][0]["engine"] == "rules"
    assert rec["confirm"] == [True] and state["spine"]["confirmed"]


def test_confirmed_outline_passes_without_work(tmp_path, calls):
    rec, state = calls
    state["spine"] = {"confirmed": True, "parts": []}
    assert dp.ensure_outline(Path(tmp_path), "G0") is None
    assert rec["plan"] == [] and rec["confirm"] == []


def test_manual_gear_waits_for_c0(tmp_path, calls):
    rec, state = calls
    reason = dp.ensure_outline(Path(tmp_path), "G0")          # G0 有 C0 人工检查点
    assert reason and "C0" in reason
    assert rec["confirm"] == [] and not state["spine"]["confirmed"]


def test_model_engine_without_llm_is_an_error(tmp_path, calls, monkeypatch):
    monkeypatch.setattr(dp, "effective_group", lambda pdir, g: {"outline_engine": "llm"})
    with pytest.raises(PlanError):
        dp.ensure_outline(Path(tmp_path), "G3", llm=None)
