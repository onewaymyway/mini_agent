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


def test_revision_marks_every_passed_step_after_the_start_and_nothing_before(tmp_path):
    """整体调整：修订影响范围（起点）之后的全部已通过流程都要重跑；起点及之前的不动。"""
    ws = _ws(tmp_path)
    plan = build_plan(ws)
    start = plan[3].id
    for i in (1, 2, 3, 4, 6):
        st.update_step(ws, plan[i].id, status=st.ST_PASSED, note="")
    done = st.mark_revision_stale(ws, plan, [start])
    steps = st.load_state(ws)["steps"]
    assert plan[4].id in done and plan[6].id in done          # 起点之后：全部重跑
    assert steps[plan[4].id]["status"] == st.ST_STALE and steps[plan[4].id]["note"] == st.REVISION_NOTE
    assert steps[plan[1].id]["status"] == st.ST_PASSED        # 起点之前：不动
    assert steps[plan[2].id]["status"] == st.ST_PASSED
    assert steps[plan[3].id]["status"] == st.ST_PASSED        # 起点本身刚运行完，保持通过


def test_revision_skips_request_steps_and_ideation_review(tmp_path):
    ws = _ws(tmp_path)
    plan = build_plan(ws)
    st.update_step(ws, plan[1].id, status=st.ST_PASSED, note="")
    st.update_step(ws, "cpt.rx", status=st.ST_PASSED, note="")
    st.mark_revision_stale(ws, plan, [])
    steps = st.load_state(ws)["steps"]
    assert steps.get("cpt.rx", {}).get("status") == st.ST_PASSED
    assert steps[plan[1].id]["status"] == st.ST_STALE         # 无起点：全部已通过步骤都标记


def test_revision_stale_survives_g5_auto_clear(tmp_path):
    ws = _ws(tmp_path)
    plan = build_plan(ws)
    st.update_step(ws, plan[5].id, status=st.ST_PASSED, note="")
    st.mark_revision_stale(ws, plan, [plan[1].id])
    cleared = st.auto_clear_upstream_stale(ws, plan)
    assert plan[5].id not in cleared
    assert st.load_state(ws)["steps"][plan[5].id]["status"] == st.ST_STALE


def test_concept_revision_marks_downstream_so_g5_cannot_clear_them(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    (ws.path("theme_design")).parent.mkdir(parents=True, exist_ok=True)
    ws.path("theme_design").write_text("原主题", encoding="utf-8")
    rid = OD.create_request(ws, "concept", direction="把复仇改为救赎", scopes=["theme"])["id"]
    calls = []
    real = st.mark_revision_stale
    monkeypatch.setattr(st, "mark_revision_stale", lambda *a, **k: calls.append(1) or real(*a, **k))
    fake = FakeInvoker(ws.root, writers={
        "theme": lambda w: w.path("theme_design").write_text("新主题：救赎", encoding="utf-8")})
    from test_concept_revision import _concept_step
    CR.h_concept(ws, _concept_step(rid), {}, invoker=fake)
    assert calls, "修订完成后必须把下游标记为修订影响"
    plan = build_plan(ws)
    st.update_step(ws, plan[2].id, status=st.ST_PASSED, note="")                 # 一个下游已通过的步骤
    st.mark_revision_stale(ws, plan, [plan[0].id])                              # 修订起点是 plan[0]
    st.auto_clear_upstream_stale(ws, plan)
    steps = st.load_state(ws)["steps"]
    assert steps[plan[2].id]["status"] == st.ST_STALE                           # G5 清不掉修订影响


def test_theme_revision_makes_every_later_flow_rerun(tmp_path):
    """端到端形态：修订完成后，修订步骤之后的每一个已通过流程都标为需重跑（不再只重跑第一步）。"""
    from novel_writer import steps as S
    from test_concept_revision import _concept_step

    ws = _ws(tmp_path)
    plan = build_plan(ws)
    for s_ in plan:
        if not s_.id.startswith("cpt.") and s_.id != S.IDEATION_REVIEW_ID:
            st.update_step(ws, s_.id, status=st.ST_PASSED, note="")
    (ws.path("theme_design")).parent.mkdir(parents=True, exist_ok=True)
    ws.path("theme_design").write_text("原主题", encoding="utf-8")
    rid = OD.create_request(ws, "concept", direction="整体改为救赎主线", scopes=["theme"])["id"]
    fake = FakeInvoker(ws.root, writers={
        "theme": lambda w: w.path("theme_design").write_text("新主题：救赎", encoding="utf-8")})
    CR.h_concept(ws, _concept_step(rid), {}, invoker=fake)

    producer = next(i for i, s_ in enumerate(plan) if s_.id == S.design_theme_step().id)
    steps = st.load_state(ws)["steps"]
    later = [s_.id for s_ in plan[producer + 1:]
             if not s_.id.startswith("cpt.") and s_.id != S.IDEATION_REVIEW_ID]
    assert later, "测试计划里应当存在主题之后的步骤"
    not_rerun = [sid for sid in later if steps[sid]["status"] != st.ST_STALE or steps[sid]["note"] != st.REVISION_NOTE]
    assert not not_rerun, f"这些后续流程没有被标为重跑：{not_rerun[:8]}"


def test_blueprint_revision_keeps_init_and_plan_blueprint_and_reruns_downstream(tmp_path):
    """回归（用户报告）：蓝图修订本身已写入蓝图，之后 init_workspace / plan_blueprint 不得重跑，
    blueprint_split 及之后的流程才需要重跑。"""
    from novel_writer import steps as S

    ws = _ws(tmp_path)
    plan = build_plan(ws)
    ids = [s_.id for s_ in plan]
    assert "init_workspace" in ids and "plan_blueprint" in ids and "blueprint_split" in ids
    for s_ in plan:
        if not s_.id.startswith("cpt.") and s_.id != S.IDEATION_REVIEW_ID:
            st.update_step(ws, s_.id, status=st.ST_PASSED, note="")

    start = CR._revision_start_ids(ws, ["blueprint"])
    assert start == ["plan_blueprint"], start           # 必须是计划中的原始 id，不能是 cpt.* 副本
    st.mark_revision_stale(ws, plan, start)

    steps = st.load_state(ws)["steps"]
    assert steps["init_workspace"]["status"] == st.ST_PASSED
    assert steps["plan_blueprint"]["status"] == st.ST_PASSED
    assert steps["blueprint_split"]["status"] == st.ST_STALE
    later = ids[ids.index("blueprint_split") + 1:]
    not_marked = [sid for sid in later if not sid.startswith("cpt.") and sid != S.IDEATION_REVIEW_ID
                  and steps[sid]["status"] != st.ST_STALE]
    assert not not_marked, f"蓝图之后的这些流程应当重跑：{not_marked[:8]}"


def test_scope_revision_ids_are_original_plan_ids_for_every_scope(tmp_path):
    ws = _ws(tmp_path)
    plan_ids = {s_.id for s_ in build_plan(ws)}
    for scope in ("blueprint", "theme", "pacing", "world", "opening"):
        for sid in CR._revision_start_ids(ws, [scope]):
            assert sid in plan_ids, f"{scope} 的起点 {sid} 不在计划中"
