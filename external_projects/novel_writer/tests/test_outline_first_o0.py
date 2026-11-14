"""大纲先行 O0：模式开关、计划接入、闸门与工具接口。直写模式的计划必须保持不变。"""

import pytest

from novel_writer import config, gates as G, pipeline, settings as SET, state as st, tool_api
from novel_writer import brief as BR
from novel_writer.workspace import Workspace


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    yield tmp_path


def _create(tmp_path, mode="direct", total=3):
    r = tool_api.create_novel("大纲先行测试", novels_dir=str(tmp_path / "novels"),
                              index_file=str(tmp_path / "shelf.json"), gear="G3",
                              total_chapters_planned=total, writing_mode=mode)
    assert r["ok"], r
    return Workspace(r["data"]["workspace"])


def test_settings_defaults_and_validation():
    d = SET.validate_settings({})
    assert d["writing_mode"] == "direct" and d["brief_change_policy"] == "ask"
    with pytest.raises(SET.SettingsError):
        SET.validate_settings({"writing_mode": "outline"})
    with pytest.raises(SET.SettingsError):
        SET.validate_settings({"brief_change_policy": "always"})
    assert SET.validate_settings({"writing_mode": "outline_first", "brief_change_policy": "auto_rewrite"})


def test_direct_plan_has_no_brief_steps(tmp_path):
    ws = _create(tmp_path, "direct")
    ids = [s.id for s in pipeline.build_plan(ws)]
    assert not [i for i in ids if i.startswith("brief_")]


def test_outline_first_plan_inserts_brief_steps_before_chapters(tmp_path):
    ws = _create(tmp_path, "outline_first")
    plan = pipeline.build_plan(ws)
    ids = [s.id for s in plan]
    drafts = [i for i, s in enumerate(plan) if s.id.startswith(BR.BRIEF_DRAFT_ID + ":")]
    i_review = ids.index(BR.BRIEF_REVIEW_ID)
    first_chapter = next(i for i, s in enumerate(plan) if s.chapter == 1)
    assert drafts and max(drafts) < i_review < first_chapter
    assert plan[i_review].deps == (plan[max(drafts)].id,)
    # 第一章的首步依赖简述确认
    assert BR.BRIEF_REVIEW_ID in plan[first_chapter].deps


def test_brief_review_is_forced_manual_gate_even_on_autopilot(tmp_path):
    ws = _create(tmp_path, "outline_first")
    step = next(s for s in pipeline.build_plan(ws) if s.id == BR.BRIEF_REVIEW_ID)
    for gear in ("G0", "G5"):
        settings = {**ws.settings(), "gear": gear}
        assert G.resolve_gate(step, settings).gate == G.GATE_REVIEW
    skip = {**ws.settings(), "step_overrides": {BR.BRIEF_REVIEW_ID: G.GATE_SKIP}}
    assert G.resolve_gate(step, skip).gate == G.GATE_SKIP


def test_brief_draft_handler_is_registered_and_review_is_checkpoint(tmp_path):
    from novel_writer import runner
    assert BR.BRIEF_DRAFT_ID in runner.DEFAULT_PY_HANDLERS
    assert next(s for s in pipeline.build_plan(_create(tmp_path, "outline_first"))
                if s.id == BR.BRIEF_REVIEW_ID).handler == "checkpoint"


def test_create_persists_mode_and_direct_is_default(tmp_path):
    assert _create(tmp_path, "outline_first").settings()["writing_mode"] == "outline_first"
    ws2 = _create(tmp_path / "b", "direct")
    assert ws2.settings()["writing_mode"] == "direct"


def test_switch_mode_allowed_before_start_and_refused_after(tmp_path):
    ws = _create(tmp_path, "direct")
    r = tool_api.set_writing_mode(str(ws.root), "outline_first")
    assert r["ok"] and r["data"]["writing_mode"] == "outline_first"
    st.update_step(ws, "init_workspace", status=st.ST_PASSED)
    r2 = tool_api.set_writing_mode(str(ws.root), "direct")
    assert not r2["ok"] and r2["error"]["type"] == "validation_error"
    assert "不能切换" in r2["error"]["message"]
    assert ws.settings()["writing_mode"] == "outline_first"


def test_switch_rejects_unknown_mode(tmp_path):
    ws = _create(tmp_path, "direct")
    r = tool_api.set_writing_mode(str(ws.root), "bogus")
    assert not r["ok"]
