import json

import pytest

from article_to_video.planning.errors import PlanError
from article_to_video.planning.runner import confirm_spine, load_spine, plan_outline, save_spine
from article_to_video.planning import outline_edit as oe
from article_to_video.project_store import load_project
from helpers_p1 import ScriptedLLM, covering_spine, good_digest, make_project


def test_rules_plan_writes_files_and_updates_stage(tmp_path):
    pdir = make_project(tmp_path)
    spine = plan_outline(pdir, engine="rules")
    assert (pdir / "plan" / "spine.json").is_file()
    assert (pdir / "plan" / "digests.json").is_file()
    assert (pdir / "plan" / "groups.json").is_file()
    proj = load_project(pdir)
    assert proj["stage"] == "P1_outlined"
    assert proj["plan"]["spine_confirmed"] is False
    assert len(spine["parts"]) >= 5


def test_groups_json_does_not_store_article_text(tmp_path):
    pdir = make_project(tmp_path)
    plan_outline(pdir, engine="rules")
    assert '"text"' not in (pdir / "plan" / "groups.json").read_text(encoding="utf-8")


def test_confirmed_outline_is_not_overwritten_without_force(tmp_path):
    pdir = make_project(tmp_path)
    plan_outline(pdir, engine="rules")
    confirm_spine(pdir)
    with pytest.raises(PlanError):
        plan_outline(pdir, engine="rules")
    plan_outline(pdir, engine="rules", force=True)
    assert load_spine(pdir)["confirmed"] is False


def test_confirm_requires_existing_valid_outline(tmp_path):
    pdir = make_project(tmp_path)
    with pytest.raises(PlanError):
        confirm_spine(pdir)


def test_save_edit_unconfirms_and_rejects_invalid(tmp_path):
    pdir = make_project(tmp_path)
    plan_outline(pdir, engine="rules")
    confirm_spine(pdir)
    s = load_spine(pdir)
    edited = oe.rename_part(s, 0, "改名")
    saved = save_spine(pdir, edited)
    assert saved["confirmed"] is False and saved["parts"][0]["title"] == "改名"
    broken = json.loads(json.dumps(s))
    broken["parts"][0]["section_ids"] = []
    with pytest.raises(PlanError):
        save_spine(pdir, broken)
    assert load_spine(pdir)["parts"][0]["title"] == "改名"  # 失败不写入


def test_llm_plan_success_path(tmp_path):
    pdir = make_project(tmp_path)
    llm = ScriptedLLM(good_digest, covering_spine)
    spine = plan_outline(pdir, engine="llm", llm=llm)
    assert spine["engine"] == "llm"
    assert llm.calls["digest"] >= 1 and llm.calls["spine"] == 1


def test_llm_digest_retries_once_then_succeeds(tmp_path):
    pdir = make_project(tmp_path)
    state = {"n": 0}

    def flaky(prompt):
        state["n"] += 1
        if state["n"] == 1:
            return "这不是 JSON"
        return good_digest(prompt)

    llm = ScriptedLLM(flaky, covering_spine)
    plan_outline(pdir, engine="llm", llm=llm)
    assert state["n"] > 1


def test_llm_digest_failure_writes_nothing_and_archives_raw(tmp_path):
    pdir = make_project(tmp_path)
    llm = ScriptedLLM(lambda p: '{"summary": "x", "core_claims": ["c"], "quotable": ["编造原话ABC"]}',
                      covering_spine)
    with pytest.raises(PlanError) as exc:
        plan_outline(pdir, engine="llm", llm=llm)
    assert "逐字" in str(exc.value)
    assert load_spine(pdir) is None
    assert not (pdir / "plan" / "digests.json").exists()
    assert list((pdir / "plan").glob("raw_g01*.txt"))


def test_llm_spine_missing_section_retries_then_fails(tmp_path):
    pdir = make_project(tmp_path)
    llm = ScriptedLLM(good_digest, lambda p: json.dumps({
        "thesis": "t", "hook_candidates": ["h"], "selected_hook": 0,
        "parts": [{"title": "只有一章", "section_ids": ["s01"], "weight": 1}], "cuts": []}))
    with pytest.raises(PlanError) as exc:
        plan_outline(pdir, engine="llm", llm=llm)
    assert llm.calls["spine"] == 2
    assert "未被任何章" in str(exc.value)
    assert load_spine(pdir) is None


def test_llm_digests_are_resumed_when_input_unchanged(tmp_path):
    pdir = make_project(tmp_path)
    llm1 = ScriptedLLM(good_digest, covering_spine)
    plan_outline(pdir, engine="llm", llm=llm1, force=True)
    n_first = llm1.calls["digest"]
    llm2 = ScriptedLLM(good_digest, covering_spine)
    plan_outline(pdir, engine="llm", llm=llm2, force=True)
    assert llm2.calls["digest"] == 0 and n_first > 0


def test_llm_engine_requires_provider(tmp_path):
    with pytest.raises(PlanError):
        plan_outline(make_project(tmp_path), engine="llm", llm=None)
