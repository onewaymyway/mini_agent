"""修改意见自动重跑测试：托管挡（G5）下，只有根据修改意见产生的需重跑步骤才自动重跑；其它情况仍需确认。"""

import pytest

from novel_writer import concept_revision as CR
from novel_writer import config
from novel_writer import ondemand as OD
from novel_writer import state as st
from novel_writer import steps as S
from novel_writer import tool_api
from novel_writer.pipeline import build_plan
from novel_writer.runner import DEFAULT_PY_HANDLERS
from novel_writer.worker import run_worker
from novel_writer.workspace import Workspace

from fakes import FakeInvoker


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    yield tmp_path


def _ws(tmp_path) -> Workspace:
    r = tool_api.create_novel("修改自动重跑", novels_dir=str(tmp_path / "novels"),
                              index_file=str(tmp_path / "shelf.json"), gear="G5", total_chapters_planned=3,
                              pause_after_ideation=True)
    ws = Workspace(r["data"]["workspace"])
    ws.save_settings({**ws.settings(), "gear": "G5"})
    return ws


def _prepare_stale_pacing(ws):
    """审核之前的步骤全部已通过；节奏设计因上游变化变为需重跑；构思审核也需要重跑。"""
    plan = build_plan(ws)
    review_at = [s.id for s in plan].index(S.IDEATION_REVIEW_ID)
    for step in plan[:review_at]:
        st.update_step(ws, step.id, status=st.ST_PASSED)
    st.update_step(ws, "design_pacing", status=st.ST_STALE, note=f"{st._UPSTREAM_NOTE}（测试）")
    st.update_step(ws, S.IDEATION_REVIEW_ID, status=st.ST_STALE, note="测试")


def test_stale_still_asks_for_confirmation_without_revision(tmp_path):
    """没有修改意见时，托管挡下的需重跑仍然要求确认（原有保护不变）。"""
    ws = _ws(tmp_path)
    _prepare_stale_pacing(ws)
    res = run_worker(ws, mode="until_gate", invoker=FakeInvoker(ws.root), handlers=DEFAULT_PY_HANDLERS,
                     heartbeat_interval=0.01)
    assert res["reason"] == "stale_confirm", res


def test_stale_auto_reruns_when_revision_armed(tmp_path):
    ws = _ws(tmp_path)
    _prepare_stale_pacing(ws)
    st.arm_revision_autorun(ws)
    res = run_worker(ws, mode="until_gate", invoker=FakeInvoker(ws.root), handlers=DEFAULT_PY_HANDLERS,
                     heartbeat_interval=0.01)
    assert res["reason"] != "stale_confirm", res
    assert st.get_step_state(ws, "design_pacing")["status"] == st.ST_PASSED


def test_revision_flag_is_cleared_after_the_run(tmp_path):
    ws = _ws(tmp_path)
    _prepare_stale_pacing(ws)
    st.arm_revision_autorun(ws)
    run_worker(ws, mode="until_gate", invoker=FakeInvoker(ws.root), handlers=DEFAULT_PY_HANDLERS,
               heartbeat_interval=0.01)
    assert st.read_control(ws)["revision_autorun"] is False


def test_concept_revision_arms_the_flag(tmp_path):
    ws = _ws(tmp_path)
    theme = ws.path("theme_design")
    theme.parent.mkdir(parents=True, exist_ok=True)
    theme.write_text("原主题", encoding="utf-8")
    rid = OD.create_request(ws, "concept", direction="改为救赎", scopes=["theme"])["id"]
    step = S.StepDef(id=f"cpt.{rid}", title="x", stage="ondemand", category=S.CAT_REVISE,
                     executor=S.EXECUTOR_PYTHON, handler="concept", chapter=0,
                     extra={"request": f"concept_{rid}", "kind": "concept"})
    fake = FakeInvoker(ws.root, writers={"theme": lambda w: w.path("theme_design").write_text("新", encoding="utf-8")})
    CR.h_concept(ws, step, {}, invoker=fake)
    assert st.read_control(ws)["revision_autorun"] is True
