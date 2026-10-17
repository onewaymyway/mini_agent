"""M5 测试：挡位预设落地（表驱动）、预算/批量推进、收件箱、手写旁路、AI 选段辅助、批量入口。

桩执行器下运行，不调用真实 LLM。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fakes import STUB_HANDLERS, FakeInvoker, approve_waiting, make_blueprint, set_settings, statuses
from novel_writer import batch, config, gates as G, inbox, ondemand, tool_api
from novel_writer import pipeline as P
from novel_writer import settings as settings_mod
from novel_writer import state as st
from novel_writer import steps as S
from novel_writer.fsutil import atomic_write_json
from novel_writer.runner import DEFAULT_PY_HANDLERS
from novel_writer.worker import run_worker
from novel_writer.workspace import Workspace

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    yield tmp_path


def new_book(tmp_path, *, gear="G0", total=4, title="测试书"):
    r = tool_api.create_novel(title, novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear=gear, total_chapters_planned=total)
    assert r["ok"], r
    ws = Workspace(r["data"]["workspace"])
    atomic_write_json(ws.path("blueprint"), make_blueprint(total))
    return ws


def by_id(ws):
    return {s.id: s for s in P.build_plan(ws)}


# ── 一、挡位预设落地（表驱动，四个挡位）─────────────────────────────────────

# (挡位, 步骤类别, 期望闸门) —— 对照方案 6.2 表格
GEAR_TABLE = [
    ("G0", S.CAT_INFRA, "auto"),
    ("G1", S.CAT_INFRA, "auto"),
    ("G2", S.CAT_INFRA, "auto"),
    ("G3", S.CAT_INFRA, "auto"),
    ("G0", S.CAT_DESIGN, "manual"),
    ("G1", S.CAT_DESIGN, "review"),
    ("G2", S.CAT_DESIGN, "review"),
    ("G3", S.CAT_DESIGN, "review"),
    ("G0", S.CAT_BUILD, "manual"),
    ("G1", S.CAT_BUILD, "review"),
    ("G2", S.CAT_BUILD, "auto"),
    ("G3", S.CAT_BUILD, "auto"),
    ("G0", S.CAT_CHECKPOINT, "review"),
    ("G3", S.CAT_CHECKPOINT, "review"),
    ("G0", S.CAT_CHAPTER_PREP, "manual"),
    ("G1", S.CAT_CHAPTER_PREP, "auto"),
    ("G3", S.CAT_CHAPTER_PREP, "auto"),
    ("G0", S.CAT_CHAPTER_WRITE, "manual"),
    ("G1", S.CAT_CHAPTER_WRITE, "review"),
    ("G2", S.CAT_CHAPTER_WRITE, "auto"),
    ("G3", S.CAT_CHAPTER_WRITE, "auto"),
    ("G0", S.CAT_CHAPTER_REVIEW, "manual"),
    ("G1", S.CAT_CHAPTER_REVIEW, "auto"),
    ("G3", S.CAT_CHAPTER_REVIEW, "auto"),
    ("G0", S.CAT_ACT, "manual"),
    ("G1", S.CAT_ACT, "review"),
    ("G2", S.CAT_ACT, "review"),
    ("G3", S.CAT_ACT, "auto"),
    ("G0", S.CAT_REVISE, "manual"),
    ("G1", S.CAT_REVISE, "manual"),
    ("G2", S.CAT_REVISE, "manual"),
    ("G3", S.CAT_REVISE, "manual"),
]


def _probe(category: str, step_id: str = "probe", chapter: int = 0, stage: str = "p1") -> S.StepDef:
    return S.StepDef(id=step_id, title="probe", stage=stage, category=category,
                     executor=S.EXECUTOR_PYTHON, handler="x", chapter=chapter)


@pytest.mark.parametrize("gear,category,expected", GEAR_TABLE)
def test_gear_preset_matrix(gear, category, expected):
    settings = settings_mod.validate_settings({"gear": gear})
    decision = G.resolve_gate(_probe(category), settings)
    assert decision.gate == expected, (gear, category, decision)


def test_opening_chapters_always_reviewed_from_g1_up():
    # G1 的写作在挡位表里已是 review，来源记为 gear；G2/G3 原本是 auto，由最低审批规则提升为 review
    expected_source = {"G1": "gear", "G2": "opening", "G3": "opening"}
    for gear, source in expected_source.items():
        settings = settings_mod.validate_settings({"gear": gear})
        for n in (1, 2, 3):
            d = G.resolve_gate(_probe(S.CAT_CHAPTER_WRITE, f"ch0{n}.write_chapter", n, "p2"), settings)
            assert (d.gate, d.source) == ("review", source), (gear, n, d)
        # 第 4 章起：G1 写作仍是审批（挡位表），G2/G3 自动
        d4 = G.resolve_gate(_probe(S.CAT_CHAPTER_WRITE, "ch04.write_chapter", 4, "p3"), settings)
        assert d4.gate == ("review" if gear == "G1" else "auto"), (gear, d4)


def test_opening_rule_keeps_g0_manual():
    settings = settings_mod.validate_settings({"gear": "G0"})
    d = G.resolve_gate(_probe(S.CAT_CHAPTER_WRITE, "ch01.write_chapter", 1, "p2"), settings)
    assert d.gate == "manual"


def test_act_review_stays_reviewed_in_g3_but_other_act_steps_auto():
    settings = settings_mod.validate_settings({"gear": "G3"})
    assert G.resolve_gate(_probe(S.CAT_ACT, "act_review:1", 0, "act"), settings).gate == "review"
    assert G.resolve_gate(_probe(S.CAT_ACT, "act_style:1", 0, "act"), settings).gate == "auto"


def test_explicit_override_wins_over_minimum_review_rules():
    # 最低审批规则只提高严格度，不覆盖作者的显式覆盖
    settings = settings_mod.validate_settings({
        "gear": "G3", "step_overrides": {"act_review:1": "auto", "ch01.write_chapter": "auto"},
    })
    assert G.resolve_gate(_probe(S.CAT_ACT, "act_review:1", 0, "act"), settings).gate == "auto"
    d = G.resolve_gate(_probe(S.CAT_CHAPTER_WRITE, "ch01.write_chapter", 1, "p2"), settings)
    assert (d.gate, d.source) == ("auto", "override")


def test_checkpoints_cannot_be_loosened_by_override():
    settings = settings_mod.validate_settings({"gear": "G3", "step_overrides": {"confirm_first3": "auto"}})
    d = G.resolve_gate(_probe(S.CAT_CHECKPOINT, "confirm_first3", 0, "p2"), settings)
    assert d.gate == "review" and d.source == "forced"


def test_every_gear_has_a_run_scope_description():
    assert set(G.RUN_SCOPE) == set(settings_mod.GEARS)
    assert {v["scope"] for v in G.RUN_SCOPE.values()} == {"single", "checkpoint", "batch", "budget"}


def test_real_pipeline_gates_for_g3_book(tmp_path):
    ws = new_book(tmp_path, gear="G3", total=5)
    settings = ws.settings()
    plan = by_id(ws)
    assert G.resolve_gate(plan["plan_blueprint"], settings).gate == "review"
    assert G.resolve_gate(plan["ch02.write_chapter"], settings).source == "opening"
    assert G.resolve_gate(plan["ch04.write_chapter"], settings).gate == "auto"
    assert G.resolve_gate(plan["confirm_first3"], settings).gate == "review"


# ── 二、预算与批量推进 ──────────────────────────────────────────────────────


def test_batch_dry_run_only_lists_auto_gears(tmp_path):
    g3 = new_book(tmp_path, gear="G3", title="甲")
    g0 = new_book(tmp_path, gear="G0", title="乙")
    res = batch.advance_all_auto(novels_dir=tmp_path / "novels", index_file=tmp_path / "shelf.json", dry_run=True)
    assert res["ok"] and res["dry_run"] is True
    rows = {r["workspace"]: r for r in res["novels"]}
    assert rows[str(g3.root)]["action"] == "would_run"
    assert rows[str(g0.root)]["action"] == "skipped"
    assert "挡位" in rows[str(g0.root)]["reason"]
    # 预览不写任何状态
    assert st.load_state(g3)["steps"] == {}


def test_batch_runs_each_auto_book_at_most_n_chapters_and_leaves_manual_books_alone(tmp_path):
    g3 = new_book(tmp_path, gear="G3", title="甲")
    g0 = new_book(tmp_path, gear="G0", title="乙")
    inv = FakeInvoker(g3.root)
    res = batch.advance_all_auto(novels_dir=tmp_path / "novels", index_file=tmp_path / "shelf.json",
                                 invoker=inv, handlers=STUB_HANDLERS, max_chapters=1)
    rows = {r["workspace"]: r for r in res["novels"]}
    assert rows[str(g3.root)]["action"] == "ran"
    assert rows[str(g3.root)]["reason"] == "waiting_review"  # 蓝图确认是强制检查点
    assert rows[str(g0.root)]["action"] == "skipped"
    assert st.load_state(g0)["steps"] == {}
    assert res["counts"] == {"ran": 1, "skipped": 1}


def test_batch_skips_book_with_live_worker(tmp_path):
    g3 = new_book(tmp_path, gear="G3")
    from datetime import datetime, timezone
    atomic_write_json(g3.path("studio_worker"), {"pid": os.getpid(), "state": "running",
                                                 "heartbeat": datetime.now(timezone.utc).astimezone().isoformat()})
    res = batch.advance_all_auto(novels_dir=tmp_path / "novels", index_file=tmp_path / "shelf.json", dry_run=True)
    assert res["novels"][0]["action"] == "skipped" and "worker" in res["novels"][0]["reason"]


def test_batch_rejects_bad_max_chapters(tmp_path):
    res = tool_api.advance_all_auto(novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                                    max_chapters=0, dry_run=True)
    assert not res["ok"] and res["error"]["type"] == "validation_error"


def test_worker_chapter_limit_only_tightens_budget(tmp_path):
    ws = new_book(tmp_path, gear="G3")
    from novel_writer.worker import Budget
    assert Budget(ws.settings(), chapter_limit=1).max_chapters == 1
    set_settings(ws, budget={"max_chapters_per_run": 2})
    assert Budget(ws.settings(), chapter_limit=5).max_chapters == 2  # 不能放宽设置
    assert Budget(ws.settings(), chapter_limit=1).max_chapters == 1


def test_budget_pause_is_visible_in_inbox(tmp_path):
    ws = new_book(tmp_path, gear="G3", total=6)
    atomic_write_json(ws.path("blueprint"), make_blueprint(6))
    set_settings(ws, budget={"max_chapters_per_run": 1})
    inv = FakeInvoker(ws.root)
    for _ in range(100):
        if statuses(ws)["ch03.sync_plot"] == st.ST_PASSED:
            break
        assert run_worker(ws, mode="until_gate", invoker=inv, handlers=STUB_HANDLERS,
                          heartbeat_interval=0.01)["reason"] == "waiting_review"
        approve_waiting(ws)
    last = run_worker(ws, mode="until_gate", invoker=inv, handlers=STUB_HANDLERS, heartbeat_interval=0.01)
    assert last["reason"] == "budget_chapters"
    data = inbox.collect(ws)
    assert data["counts"].get("budget_pause") == 1
    assert any("续跑" in it["message"] for it in data["items"] if it["kind"] == "budget_pause")


# ── 三、收件箱 ─────────────────────────────────────────────────────────────


def test_inbox_lists_pending_approval_and_writes_snapshot(tmp_path):
    ws = new_book(tmp_path, gear="G0")
    run_worker(ws, mode="until_gate", invoker=FakeInvoker(ws.root), handlers=STUB_HANDLERS, heartbeat_interval=0.01)
    snap = inbox.read_snapshot(ws)
    assert snap is not None, "worker 退出时应刷新收件箱快照"
    kinds = [(it["kind"], it["step_id"]) for it in snap["items"]]
    assert ("approve", "plan_blueprint") in kinds
    assert snap["blocking"] == 0 and snap["total"] >= 1


def test_inbox_reports_failed_step_as_blocking(tmp_path):
    ws = new_book(tmp_path, gear="G3")
    inv = FakeInvoker(ws.root, fail={"design_theme"})
    run_worker(ws, mode="until_gate", invoker=inv, handlers=STUB_HANDLERS, heartbeat_interval=0.01)
    approve_waiting(ws)
    run_worker(ws, mode="until_gate", invoker=inv, handlers=STUB_HANDLERS, heartbeat_interval=0.01)
    data = inbox.collect(ws)
    failed = [it for it in data["items"] if it["kind"] == "failed"]
    assert failed and failed[0]["step_id"] == "design_theme" and failed[0]["severity"] == "block"
    assert data["blocking"] >= 1


def test_inbox_aggregates_across_books(tmp_path):
    new_book(tmp_path, gear="G0", title="甲")
    new_book(tmp_path, gear="G0", title="乙")
    res = tool_api.inbox_all(novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"))
    assert res["ok"] and len(res["data"]["books"]) == 2
    assert {b["title"] for b in res["data"]["books"]} == {"甲", "乙"}


# ── 四、手写旁路 ───────────────────────────────────────────────────────────


def test_manual_bypass_requires_outputs(tmp_path):
    ws = new_book(tmp_path, gear="G0")
    res = tool_api.mark_manual(str(ws.root), "plan_blueprint")
    assert not res["ok"] and "缺少产出" in res["error"]["message"]
    # 没有声明产出的步骤不能旁路
    res2 = tool_api.mark_manual(str(ws.root), "ch01.machine_check")
    assert not res2["ok"] and "没有声明产出" in res2["error"]["message"]


def test_manual_bypass_marks_passed_and_pipeline_moves_on(tmp_path):
    ws = new_book(tmp_path, gear="G0")
    ws.path("blueprint").write_text("{}", encoding="utf-8")
    ws.path("blueprint_readable").write_text("# 大纲\n", encoding="utf-8")
    res = tool_api.mark_manual(str(ws.root), "plan_blueprint", note="我自己写的大纲")
    assert res["ok"], res
    entry = st.get_step_state(ws, "plan_blueprint")
    assert entry["status"] == st.ST_PASSED and entry["source"] == "manual"
    assert entry["note"] == "我自己写的大纲" and entry.get("input_hash")
    frontier = P.frontier(P.build_plan(ws), statuses(ws), ws.settings())
    assert frontier is not None and frontier.id != "plan_blueprint"


def test_manual_bypass_refuses_running_step(tmp_path):
    ws = new_book(tmp_path, gear="G0")
    st.update_step(ws, "plan_blueprint", status=st.ST_RUNNING)
    ws.path("blueprint").write_text("{}", encoding="utf-8")
    ws.path("blueprint_readable").write_text("x", encoding="utf-8")
    res = tool_api.mark_manual(str(ws.root), "plan_blueprint")
    assert not res["ok"] and "正在执行" in res["error"]["message"]


# ── 五、AI 选段辅助 ─────────────────────────────────────────────────────────


def _write_chapter(ws, n, text):
    path = ws.chapter_path(n, "测试", total_chapters=4)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _candidate_writer(text):
    def writer(ws):
        for req in ondemand.list_requests(ws, "assist"):
            if not ondemand.assist_candidate_path(ws, req["id"]).exists():
                p = ondemand.assist_candidate_path(ws, req["id"])
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text, encoding="utf-8")
    return writer


def test_request_assist_validates_selection(tmp_path):
    ws = new_book(tmp_path, gear="G0")
    _write_chapter(ws, 1, "雨下得很大。雨下得很大。他站在门口。\n")
    assert not tool_api.request_assist(str(ws.root), 1, "polish", "不存在的句子")["ok"]
    dup = tool_api.request_assist(str(ws.root), 1, "polish", "雨下得很大。")
    assert not dup["ok"] and "2 次" in dup["error"]["message"]
    assert not tool_api.request_assist(str(ws.root), 1, "magic", "他站在门口。")["ok"]
    assert not tool_api.request_assist(str(ws.root), 1, "polish", "   ")["ok"]
    ok = tool_api.request_assist(str(ws.root), 1, "polish", "他站在门口。", "更有画面感")
    assert ok["ok"] and ok["data"]["step"].startswith("asst.")
    # 续写允许选段重复出现（只需能找到）
    assert tool_api.request_assist(str(ws.root), 1, "continue", "雨下得很大。")["ok"]


def test_assist_runs_as_optional_step_and_adoption_replaces_selection(tmp_path):
    ws = new_book(tmp_path, gear="G0")
    chapter = _write_chapter(ws, 1, "前文。他站在门口。后文。\n")
    rid = tool_api.request_assist(str(ws.root), 1, "polish", "他站在门口。")["data"]["id"]
    step_id = f"asst.{rid}"
    assert step_id in by_id(ws) and by_id(ws)[step_id].extra.get("optional")

    inv = FakeInvoker(ws.root, writers={"asst.": _candidate_writer("他静静地立在门廊下。")})
    res = tool_api.run_novel(str(ws.root), mode="single", step_id=step_id, spawn=False)
    assert res["ok"]
    run_worker(ws, mode="single", invoker=inv, handlers=DEFAULT_PY_HANDLERS, heartbeat_interval=0.01)
    assert st.get_step_state(ws, step_id)["status"] == st.ST_PASSED
    listing = tool_api.assists(str(ws.root), 1)["data"]["assists"]
    assert listing[0]["candidate"] == "他静静地立在门廊下。"
    assert chapter.read_text(encoding="utf-8") == "前文。他站在门口。后文。\n", "候选稿不能直接改正文"

    adopted = tool_api.adopt_assist(str(ws.root), rid)
    assert adopted["ok"], adopted
    assert "他静静地立在门廊下。" in chapter.read_text(encoding="utf-8")
    assert "他站在门口。" not in chapter.read_text(encoding="utf-8")
    assert not tool_api.adopt_assist(str(ws.root), rid)["ok"], "同一候选稿不能采用两次"


def test_assist_continue_inserts_after_selection(tmp_path):
    ws = new_book(tmp_path, gear="G0")
    chapter = _write_chapter(ws, 1, "他推开门。屋里很暗。\n")
    rid = tool_api.request_assist(str(ws.root), 1, "continue", "他推开门。")["data"]["id"]
    inv = FakeInvoker(ws.root, writers={"asst.": _candidate_writer("一股潮气扑面而来。")})
    tool_api.run_novel(str(ws.root), mode="single", step_id=f"asst.{rid}", spawn=False)
    run_worker(ws, mode="single", invoker=inv, handlers=DEFAULT_PY_HANDLERS, heartbeat_interval=0.01)
    assert tool_api.adopt_assist(str(ws.root), rid)["ok"]
    text = chapter.read_text(encoding="utf-8")
    assert text.index("他推开门。") < text.index("一股潮气扑面而来。") < text.index("屋里很暗。")


def test_adopting_marks_downstream_stale(tmp_path):
    ws = new_book(tmp_path, gear="G0")
    _write_chapter(ws, 1, "他站在门口。\n")
    rid = tool_api.request_assist(str(ws.root), 1, "polish", "他站在门口。")["data"]["id"]
    inv = FakeInvoker(ws.root, writers={"asst.": _candidate_writer("他立于门前。")})
    tool_api.run_novel(str(ws.root), mode="single", step_id=f"asst.{rid}", spawn=False)
    run_worker(ws, mode="single", invoker=inv, handlers=DEFAULT_PY_HANDLERS, heartbeat_interval=0.01)
    # 审稿步骤已通过（记录了采用前的输入哈希）；采用候选稿改动了正文，它必须被标为需重跑
    review = by_id(ws)["ch01.review"]
    st.update_step(ws, "ch01.review", status=st.ST_PASSED, input_hash=st.input_hash(ws, review))
    assert tool_api.adopt_assist(str(ws.root), rid)["ok"]
    assert st.get_step_state(ws, "ch01.review")["status"] == st.ST_STALE


# ── 六、批量入口脚本（独立进程）─────────────────────────────────────────────


def _run(*args, data_dir):
    env = dict(os.environ)
    env["NOVEL_WRITER_DATA_DIR"] = str(data_dir)
    return subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True, timeout=120, env=env)


def test_advance_all_auto_entrypoint_dry_run(tmp_path):
    data = tmp_path / "data"
    ws_path = tmp_path / "book_g3"
    p = _run("entrypoints/create_novel.py", "--title", "批量入口", "--workspace", str(ws_path), "--gear", "G3",
             data_dir=data)
    assert p.returncode == 0, p.stdout + p.stderr
    p2 = _run("entrypoints/advance_all_auto.py", "--dry-run", data_dir=data)
    assert p2.returncode == 0, p2.stdout + p2.stderr
    out = json.loads(p2.stdout.strip().splitlines()[-1])
    assert out["dry_run"] is True
    assert any(row["action"] == "would_run" for row in out["novels"])


def test_advance_all_auto_entrypoint_rejects_bad_args(tmp_path):
    p = _run("entrypoints/advance_all_auto.py", "--max-chapters", "0", "--dry-run", data_dir=tmp_path)
    assert p.returncode != 0


def test_project_yaml_registers_batch_entrypoint_without_cron():
    import yaml

    manifest = yaml.safe_load((ROOT / "project.yaml").read_text(encoding="utf-8"))
    assert "advance_all_auto" in manifest["entrypoints"]
    # 方案 6.5：批量/定时默认关闭——manifest 里不得出现 cron 相关配置
    assert "cron" not in json.dumps(manifest).lower()


def test_spawn_is_suppressed_in_tests(tmp_path):
    res = tool_api.spawn_advance_all_auto(max_chapters=1)
    assert res["ok"] and res["data"]["spawned_pid"] is None


# ── 七、Streamlit 页面（驾驶舱收件箱 / 写作台 AI 辅助 / 书架批量按钮）──────────


def test_ui_m5_cockpit_writing_and_shelf(tmp_path):
    from streamlit.testing.v1 import AppTest

    ws = new_book(tmp_path, gear="G3", total=4)
    _write_chapter(ws, 1, "他站在门口。\n")
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    at.session_state["ws"] = str(ws.root)
    at.sidebar.radio[0].set_value("🧭 驾驶舱").run()
    assert not at.exception, at.exception
    # 新书还没有运行记录：收件箱应显示空状态说明
    assert any("收件箱" in c.value for c in at.caption), "驾驶舱应显示收件箱（空状态）"

    at.selectbox(key="wr_chapter").set_value(1).run()
    assert not at.exception, at.exception
    at.text_area(key="as_sel_1").input("他站在门口。").run()
    at.button(key="as_go_1").click().run()
    assert not at.exception, at.exception
    listing = tool_api.assists(str(ws.root), 1)["data"]["assists"]
    assert len(listing) == 1 and listing[0]["mode"] == "polish"

    at.sidebar.radio[0].set_value("📚 书架").run()
    assert not at.exception, at.exception
    assert any("批量推进" in e.label for e in at.expander)
