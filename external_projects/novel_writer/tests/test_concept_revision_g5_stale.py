"""回归：G5 托管下，构思修订标记的下游步骤不得被「上游产物变化」的自动清除吞掉，必须真正重跑。"""

import pytest

from novel_writer import config, state as st, tool_api
from novel_writer import concept_revision as CR
from novel_writer import ondemand as OD
from novel_writer.pipeline import build_plan
from novel_writer.workspace import Workspace

from fakes import FakeInvoker


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")


def _ws(tmp_path) -> Workspace:
    r = tool_api.create_novel("G5修订", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear="G5", total_chapters_planned=3)
    return Workspace(r["data"]["workspace"])


def _two_step_ids(ws):
    ids = [s.id for s in build_plan(ws)]
    return ids[0], ids[1]


def test_g5_clears_ordinary_upstream_stale_but_not_revision_stale(tmp_path):
    ws = _ws(tmp_path)
    plan = build_plan(ws)
    a, b = _two_step_ids(ws)
    # 普通的上游变化（作者手改等）：G5 照常自动清除，不重跑
    st.update_step(ws, a, status=st.ST_STALE, note=st._UPSTREAM_NOTE)
    # 构思修订影响：G5 不得清除
    st.update_step(ws, b, status=st.ST_STALE, note=st.REVISION_NOTE)
    cleared = st.auto_clear_upstream_stale(ws, plan)
    assert a in cleared and b not in cleared
    steps = st.load_state(ws)["steps"]
    assert steps[b]["status"] == st.ST_STALE           # 修订影响的步骤仍需重跑
    assert steps[a]["status"] == st.ST_PASSED


def test_mark_revision_stale_relabels_all_stale_and_keeps_status(tmp_path):
    ws = _ws(tmp_path)
    a, b = _two_step_ids(ws)
    st.update_step(ws, a, status=st.ST_STALE, note=st._UPSTREAM_NOTE)
    st.update_step(ws, b, status=st.ST_PASSED, note="")
    done = st.mark_revision_stale(ws)
    assert done == [a]
    steps = st.load_state(ws)["steps"]
    assert steps[a]["status"] == st.ST_STALE and steps[a]["note"] == st.REVISION_NOTE
    assert steps[b]["status"] == st.ST_PASSED          # 已通过的不动


def test_concept_revision_marks_downstream_so_g5_cannot_clear_them(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    (ws.path("theme_design")).parent.mkdir(parents=True, exist_ok=True)
    ws.path("theme_design").write_text("原主题", encoding="utf-8")
    rid = OD.create_request(ws, "concept", direction="把复仇改为救赎", scopes=["theme"])["id"]
    calls = []
    real = st.mark_revision_stale
    monkeypatch.setattr(st, "mark_revision_stale", lambda w: calls.append(1) or real(w))
    fake = FakeInvoker(ws.root, writers={
        "theme": lambda w: w.path("theme_design").write_text("新主题：救赎", encoding="utf-8")})
    from test_concept_revision import _concept_step
    CR.h_concept(ws, _concept_step(rid), {}, invoker=fake)
    assert calls, "修订完成后必须把下游标记为修订影响"
    plan = build_plan(ws)
    st.update_step(ws, plan[0].id, status=st.ST_STALE, note=st._UPSTREAM_NOTE)  # 模拟一个下游步骤
    st.mark_revision_stale(ws)
    st.auto_clear_upstream_stale(ws, plan)
    steps = st.load_state(ws)["steps"]
    assert steps[plan[0].id]["status"] == st.ST_STALE
