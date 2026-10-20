"""M4 测试：幕末步骤、快照、修订 L1–L4（含 L3/L4 下游触发）、灵感评估、设置矩阵、度量与步骤详情。

桩执行器下运行，不调用真实 LLM。
"""

from __future__ import annotations

import json

import pytest

from fakes import FakeInvoker, drive_to_completion, make_blueprint, statuses, put_blueprint
from novel_writer import config, tool_api
from novel_writer import pipeline as P
from novel_writer import state as st
from novel_writer.fsutil import atomic_write_json
from novel_writer.runner import DEFAULT_PY_HANDLERS
from novel_writer.worker import run_worker
from novel_writer.workspace import Workspace


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    yield tmp_path


def book(tmp_path, total=4, acts=None):
    r = tool_api.create_novel("雨夜钟声", novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear="G3", total_chapters_planned=total)
    ws = Workspace(r["data"]["workspace"])
    bp = make_blueprint(total)
    if acts is not None:
        bp["structure"]["acts"] = acts
    put_blueprint(ws, bp)
    return ws


def step_ids(ws):
    return [s.id for s in P.build_plan(ws)]


def run_single(ws, inv, step_id):
    assert tool_api.run_novel(str(ws.root), mode="single", step_id=step_id, spawn=False)["ok"]
    return run_worker(ws, mode="single", invoker=inv, handlers=DEFAULT_PY_HANDLERS, heartbeat_interval=0.01)


# ── 幕末 / 快照 ─────────────────────────────────────────────────────────────


def test_act_and_snapshot_steps_placed_after_their_chapters(isolated):
    ws = book(isolated, total=10, acts=[{"act": 1, "chapter_range": [1, 4]}, {"act": 2, "chapter_range": [5, 10]}])
    ids = step_ids(ws)
    assert ids.index("act_review:1") > ids.index("ch04.sync_plot")
    assert ids.index("act_review:1") < ids.index("ch05.word_quota") < ids.index("act_review:2")
    assert {"act_style:1", "act_pacing:1", "act_theme:1", "act_worldcheck:1", "act_ideas:1", "act_simulate:1"} <= set(ids)
    assert "snapshot:10" in ids and "snapshot:5" not in ids


def test_full_run_executes_act_steps_and_skips_optional_simulation(isolated):
    ws = book(isolated, total=4, acts=[{"act": 1, "chapter_range": [1, 2]}, {"act": 2, "chapter_range": [3, 4]}])
    inv = FakeInvoker(ws.root)
    drive_to_completion(ws, inv, handlers=DEFAULT_PY_HANDLERS)
    s = statuses(ws)
    assert s["act_review:1"] == st.ST_PASSED and s["act_ideas:2"] == st.ST_PASSED
    assert "act_simulate:1" not in inv.calls  # 可选且手动：不阻塞、不自动运行
    assert ws.path("act_health_report", act=1).is_file()


# ── 修订 L1–L4 ─────────────────────────────────────────────────────────────


def _diag_writer(ws):
    p = ws.path("revision_diag", nn=1)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# 诊断\n- 第3段节奏拖沓\n", encoding="utf-8")


def _finished_book(tmp_path, total=3):
    ws = book(tmp_path, total=total)
    inv = FakeInvoker(ws.root, writers={".diagnose": _diag_writer})
    drive_to_completion(ws, inv, handlers=DEFAULT_PY_HANDLERS)
    return ws, inv


def test_revision_request_becomes_optional_step_not_run_automatically(isolated):
    ws, _ = _finished_book(isolated)
    r = tool_api.request_revision(str(ws.root), 1, "L1", "润色第一段")
    assert r["ok"], r
    plan = {s.id: s for s in P.build_plan(ws)}
    step = plan[r["data"]["step"]]
    assert step.extra.get("optional") is True and step.category == "revise"


def test_l1_revision_reviews_only(isolated):
    ws, _ = _finished_book(isolated)
    rid = tool_api.request_revision(str(ws.root), 1, "L1", "润色")["data"]["id"]
    inv = FakeInvoker(ws.root, writers={".diagnose": _diag_writer})
    r = run_single(ws, inv, f"rev.{rid}")
    assert r["reason"] == "single_done" and st.get_step_state(ws, f"rev.{rid}")["status"] == st.ST_PASSED
    assert f"rev.{rid}.review" in inv.calls
    assert not any(c.endswith(".archive") or c.endswith(".sync") or c.endswith(".sync_plot") for c in inv.calls if rid in c)


def test_l3_revision_triggers_archive_and_state_sync(isolated):
    ws, _ = _finished_book(isolated)
    rid = tool_api.request_revision(str(ws.root), 1, "L3", "改掉第一章的伏笔")["data"]["id"]

    def new_summary(w):  # 修订后的概述：与旧内容不同，下游输入随之变化
        w.path("chapter_summary", nn=1).write_text("修订后的第一章概述：伏笔改为旧钟声。", encoding="utf-8")

    def new_characters(w):
        atomic_write_json(w.path("characters"), {"李凌云": {"状态": "已发现旧钟"}})

    inv = FakeInvoker(ws.root, writers={".diagnose": _diag_writer,
                                        f"rev.{rid}.archive": new_summary, f"rev.{rid}.sync": new_characters})
    run_single(ws, inv, f"rev.{rid}")
    calls = [c for c in inv.calls if rid in c]
    assert f"rev.{rid}.archive" in calls and f"rev.{rid}.sync" in calls and f"rev.{rid}.sync_plot" in calls
    result = json.loads(ws.path("studio_requests").joinpath(f"revision_{rid}.json").read_text(encoding="utf-8"))["result"]
    assert result["outcome"] == "修订完成"
    assert "ch02.context_pack" in result["stale"]  # 第 2 章的上下文包依赖第 1 章概述，应被标为需重跑


def test_l4_waits_for_confirmation_then_marks_downstream_stale(isolated):
    ws, _ = _finished_book(isolated)
    rid = tool_api.request_revision(str(ws.root), 2, "L4", "主线改为悲剧结局")["data"]["id"]
    inv = FakeInvoker(ws.root)
    r = run_single(ws, inv, f"rev.{rid}")
    assert r["reason"] == "waiting_user" and st.get_step_state(ws, f"rev.{rid}")["status"] == st.ST_WAITING_USER
    assert not any(".write" in c for c in inv.calls)  # 未确认前不改写任何正文
    assert tool_api.answer_step(str(ws.root), f"rev.{rid}", {"decision": "确认，标记受影响步骤"})["ok"]
    run_single(ws, inv, f"rev.{rid}")
    s = statuses(ws)
    assert s["ch02.write_chapter"] == st.ST_STALE and s["ch03.sync_plot"] == st.ST_STALE
    assert s["ch01.write_chapter"] == st.ST_PASSED  # 第 2 章及之后；第 1 章不受影响


def test_l4_cancel_changes_nothing(isolated):
    ws, _ = _finished_book(isolated)
    rid = tool_api.request_revision(str(ws.root), 2, "L4", "试试")["data"]["id"]
    inv = FakeInvoker(ws.root)
    run_single(ws, inv, f"rev.{rid}")
    tool_api.answer_step(str(ws.root), f"rev.{rid}", {"decision": "取消"})
    run_single(ws, inv, f"rev.{rid}")
    assert statuses(ws)["ch02.write_chapter"] == st.ST_PASSED


# ── 灵感箱 ─────────────────────────────────────────────────────────────────


def test_idea_evaluation_records_decision(isolated):
    ws, _ = _finished_book(isolated)
    iid = tool_api.add_idea(str(ws.root), "雨夜的钟声其实来自旧教堂")["data"]["id"]
    inv = FakeInvoker(ws.root, extra_receipt={".evaluate": {"decision": "整合", "target_chapter": 3}})
    run_single(ws, inv, f"idea.{iid}")
    row = tool_api.ideas(str(ws.root))["data"]["ideas"][0]
    assert row["decision"] == "整合" and row["target_chapter"] == 3 and row["state"] == st.ST_PASSED


def test_idea_unknown_decision_falls_back_to_hold(isolated):
    ws, _ = _finished_book(isolated)
    iid = tool_api.add_idea(str(ws.root), "随便一个点子")["data"]["id"]
    inv = FakeInvoker(ws.root, extra_receipt={".evaluate": {"decision": "也许吧"}})
    run_single(ws, inv, f"idea.{iid}")
    assert tool_api.ideas(str(ws.root))["data"]["ideas"][0]["decision"] == "暂存"


# ── 设置矩阵 ───────────────────────────────────────────────────────────────


def test_matrix_save_changes_effective_gate_and_respects_checkpoints(isolated):
    ws = book(isolated, total=2)
    m = tool_api.matrix(str(ws.root))["data"]
    rows = {r["id"]: r for r in m["rows"]}
    assert rows["design_theme"]["effective"] == "review" and rows["plan_blueprint"]["effective"] == "review"
    r = tool_api.save_matrix(str(ws.root), {"design_theme": "auto", "plan_blueprint": "auto"}, {"p3": "G0"},
                             {"budget": {"max_chapters_per_run": 5}, "banned_words": ["仿佛"]})
    assert r["ok"], r
    m2 = tool_api.matrix(str(ws.root))["data"]
    rows2 = {r["id"]: r for r in m2["rows"]}
    assert rows2["design_theme"]["effective"] == "auto" and rows2["design_theme"]["source"] == "override"
    assert rows2["plan_blueprint"]["effective"] == "review"  # 检查点不能被降级
    assert m2["budget"]["max_chapters_per_run"] == 5 and m2["banned_words"] == ["仿佛"]
    assert rows2["ch01.word_quota"]["effective"] == "auto" or rows2["ch01.word_quota"]["source"] == "stage_gear"


def test_matrix_rejects_invalid_values(isolated):
    ws = book(isolated, total=2)
    assert not tool_api.save_matrix(str(ws.root), {"design_theme": "bogus"}, {}, {})["ok"]
    assert not tool_api.save_matrix(str(ws.root), {}, {"p1": "G9"}, {})["ok"]


# ── 度量 / 步骤详情 ────────────────────────────────────────────────────────


def test_metrics_and_step_detail(isolated):
    ws, _ = _finished_book(isolated)
    m = tool_api.metrics(str(ws.root))["data"]
    assert m["total_runs"] > 0 and any(c["skill"] == "chapter-writer" for c in m["usage_by_skill"])
    assert all("revise_rounds" in c for c in m["chapters"])
    d = tool_api.step_detail(str(ws.root), "ch01.write_chapter")["data"]
    assert d["runs"] == 1 and "第1章" in (d["prompt"] or "") and d["receipt"]["status"] == "done"
    assert tool_api.step_detail(str(ws.root), "ch09.write_chapter")["data"]["runs"] == 0  # 从未运行


def test_revision_request_validation(isolated):
    ws, _ = _finished_book(isolated)
    assert not tool_api.request_revision(str(ws.root), 1, "L9", "x")["ok"]
    assert not tool_api.request_revision(str(ws.root), 9, "L1", "x")["ok"]
    assert not tool_api.request_revision(str(ws.root), 1, "L1", "  ")["ok"]
    assert not tool_api.add_idea(str(ws.root), "")["ok"]


# ── Streamlit 页面冒烟（灵感箱 / 度量 / 设置 / 审校的修订表单）──────────────────


def test_ui_m4_pages_render(isolated):
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    app = Path(__file__).resolve().parent.parent / "app.py"
    ws, _ = _finished_book(isolated)
    tool_api.add_idea(str(ws.root), "一个待评估的点子")
    at = AppTest.from_file(str(app), default_timeout=90).run()
    at.session_state["ws"] = str(ws.root)
    at.sidebar.radio[0].set_value("🧭 驾驶舱").run()
    assert not at.exception, at.exception
    assert len(at.tabs) == 10
    at.selectbox(key="rv_chapter").set_value(1).run()
    assert not at.exception, at.exception
    at.button(key="rq_submit_1").click().run()  # 空修订要求：应给出错误而不是崩溃
    assert not at.exception, at.exception
    assert any("修订要求" in e.value for e in at.error)
    assert not at.exception, at.exception
