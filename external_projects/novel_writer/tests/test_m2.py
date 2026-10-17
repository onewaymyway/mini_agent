"""M2 测试：向导意图、规划/设定库/线索的视图与保存、向导→建档全链路、Streamlit 页面冒烟。

全部使用桩执行器与临时数据目录（monkeypatch 了 config.NOVELS_DIR / SHELF_INDEX_FILE），不调用真实 LLM。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fakes import FakeInvoker, drive_to_completion, make_blueprint, statuses
from novel_writer import config, intake as I, tool_api
from novel_writer import pipeline as P
from novel_writer import state as st
from novel_writer.fsutil import atomic_write_json
from novel_writer.workspace import Workspace

ROOT = Path(__file__).resolve().parent.parent
INTAKE = {
    "genre": "悬疑推理", "theme": "真相与代价", "protagonist": "落魄侦探", "desire": "洗清冤屈",
    "obstacle": "旧案卷宗被毁", "conflict": "正义与亲情", "ending": "BE（悲剧）",
    "fragments": "雨夜的钟声", "reference_style": "短句、冷峻的叙述",
}


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    yield tmp_path


def new_novel(tmp_path, title="雨夜钟声"):
    r = tool_api.create_novel(title, novels_dir=str(tmp_path / "novels"), index_file=str(tmp_path / "shelf.json"),
                              gear="G3", total_chapters_planned=4)
    assert r["ok"], r
    root = r["data"]["workspace"]
    return root, Workspace(root)


# ── 意图 ───────────────────────────────────────────────────────────────────


def test_intake_clean_rejects_unknown_fields_and_builds_note():
    clean = I.clean_intake({**INTAKE, "genre": "  悬疑推理  "})
    assert clean["genre"] == "悬疑推理" and clean["notes"] == ""
    with pytest.raises(I.IntakeError):
        I.clean_intake({"unknown": "x"})
    note = I.intake_note(clean)
    assert "题材：悬疑推理" in note and "BE（悲剧）" in note and "短句、冷峻" in note
    assert I.intake_note({k: "" for k in I.INTAKE_KEYS}) == ""


def test_save_intake_writes_json_and_note(isolated_data):
    root, ws = new_novel(isolated_data)
    assert tool_api.save_intake(root, INTAKE)["ok"]
    assert tool_api.get_intake(root)["data"]["intake"]["protagonist"] == "落魄侦探"
    assert "洗清冤屈" in ws.path("intake_note").read_text(encoding="utf-8")
    assert not tool_api.save_intake(root, {"bogus": 1})["ok"]


# ── 规划 ───────────────────────────────────────────────────────────────────


def test_planning_view_without_blueprint(isolated_data):
    root, _ = new_novel(isolated_data)
    v = tool_api.planning(root)["data"]
    assert v["exists"] is False and v["main_characters"] == []


def test_planning_view_and_chapter_edit_marks_stale(isolated_data):
    root, ws = new_novel(isolated_data)
    atomic_write_json(ws.path("blueprint"), make_blueprint(4))
    v = tool_api.planning(root)["data"]
    assert v["exists"] and len(v["chapters"]) == 4 and "李凌云" in v["main_characters"]

    # 先把 design_theme 标为已通过（它依赖蓝图）
    dt = P.build_plan(ws)[2]
    st.update_step(ws, "design_theme", status=st.ST_PASSED, input_hash=st.input_hash(ws, dt),
                   input_digests=st.input_digests(ws, dt))
    rows = [dict(c) for c in v["chapters"]]
    rows[0]["title"] = "新的开篇"
    rows[1]["characters_present"] = "李凌云、新角色"
    res = tool_api.save_chapters(root, rows)
    assert res["ok"], res
    assert res["data"]["changed_chapters"] == [1, 2]
    assert "design_theme" in res["data"]["stale"]
    saved = json.loads(ws.path("blueprint").read_text(encoding="utf-8"))
    assert saved["chapters"][0]["title"] == "新的开篇"
    assert "新角色" in saved["chapters"][1]["characters_present"]
    assert "第 1, 2 章" in ws.path("blueprint_changelog").read_text(encoding="utf-8")


def test_chapter_edit_rejects_add_remove_and_bad_numbers(isolated_data):
    root, ws = new_novel(isolated_data)
    atomic_write_json(ws.path("blueprint"), make_blueprint(3))
    rows = [dict(c) for c in tool_api.planning(root)["data"]["chapters"]]
    assert not tool_api.save_chapters(root, rows[:2])["ok"]          # 删除
    assert not tool_api.save_chapters(root, rows + [{**rows[0], "id": 9}])["ok"]  # 增加
    bad = [dict(c) for c in rows]
    bad[0]["word_budget"] = "很多"
    assert not tool_api.save_chapters(root, bad)["ok"]
    empty_title = [dict(c) for c in rows]
    empty_title[0]["title"] = " "
    assert not tool_api.save_chapters(root, empty_title)["ok"]


def test_planning_main_character_change_is_reported(isolated_data):
    root, ws = new_novel(isolated_data)
    atomic_write_json(ws.path("blueprint"), make_blueprint(4))
    rows = [dict(c) for c in tool_api.planning(root)["data"]["chapters"]]
    rows[0]["characters_present"] = "李凌云"  # 王道长 只剩 2 章 → 不再是主要角色
    rows[1]["characters_present"] = "李凌云"
    res = tool_api.save_chapters(root, rows)["data"]
    assert res["main_characters_changed"] is True and "王道长" not in res["main_characters"]


# ── 设定库 / 线索 ─────────────────────────────────────────────────────────


def test_card_save_and_codex_view(isolated_data):
    root, ws = new_novel(isolated_data)
    atomic_write_json(ws.path("blueprint"), make_blueprint(4))
    v = tool_api.codex(root)["data"]
    assert [c["name"] for c in v["cards"]] == ["李凌云", "王道长"]
    assert all(c["text"] is None for c in v["cards"])
    assert tool_api.save_card(root, "李凌云", "# 李凌云\n冷静、克制")["ok"]
    v2 = tool_api.codex(root)["data"]
    assert "冷静" in v2["cards"][0]["text"]
    assert not tool_api.save_card(root, "李凌云", "   ")["ok"]
    assert tool_api.save_card(root, "a/b", "x")["ok"] is True  # 非法字符被清理，不会写到目录外
    assert not (ws.root / "a").exists() and not any(p.name == "b.md" for p in ws.root.rglob("*.md") if p.parent == ws.root)


def test_threads_view_reads_tracker(isolated_data):
    root, ws = new_novel(isolated_data)
    assert tool_api.threads(root)["data"]["plot_tracker"] is None
    atomic_write_json(ws.path("plot_tracker"), {"foreshadowing": [{"id": "F1", "status": "埋下"}]})
    ws.path("foreshadowing_list").write_text("# 伏笔\n- F1 埋下\n", encoding="utf-8")
    d = tool_api.threads(root)["data"]
    assert d["plot_tracker"]["foreshadowing"][0]["id"] == "F1" and "F1" in d["foreshadowing_md"]


def test_step_gate_override_roundtrip(isolated_data):
    root, _ = new_novel(isolated_data)
    assert tool_api.set_step_gate(root, "design_theme", "skip")["ok"]
    assert tool_api.get_novel(root)["data"]["settings"]["step_overrides"]["design_theme"] == "skip"
    assert tool_api.set_step_gate(root, "design_theme", None)["ok"]
    assert "design_theme" not in tool_api.get_novel(root)["data"]["settings"]["step_overrides"]
    assert not tool_api.set_step_gate(root, "nope", "auto")["ok"]


# ── 全链路：向导 → 蓝图 → 建档 → 前三章确认 ─────────────────────────────────


def test_wizard_to_build_chain_with_stub_planner(isolated_data):
    root, ws = new_novel(isolated_data)
    assert tool_api.save_intake(root, INTAKE)["ok"]
    # 桩规划器：planner 的产出由 make_blueprint 代替（真实 LLM 下由 plan_blueprint 写出）
    atomic_write_json(ws.path("blueprint"), make_blueprint(4))
    inv = FakeInvoker(ws.root)
    drive_to_completion(ws, inv)
    s = statuses(ws)
    assert s["plan_blueprint"] == st.ST_PASSED and s["build_characters"] == st.ST_PASSED
    assert "悬疑推理" in inv.prompts["plan_blueprint"][0]          # 意图注入了 prompt
    view = tool_api.codex(root)["data"]
    assert [c["name"] for c in view["cards"]] == ["李凌云", "王道长"]
    assert tool_api.threads(root)["ok"]


# ── Streamlit 页面冒烟（AppTest）────────────────────────────────────────────


def _app(monkeypatch=None):
    from streamlit.testing.v1 import AppTest

    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)


def test_app_pages_render_without_exception(isolated_data):
    at = _app().run()
    assert not at.exception, at.exception
    for label in ["➕ 新建小说", "🧩 Skill 库", "📚 书架"]:
        at.sidebar.radio[0].set_value(label).run()
        assert not at.exception, (label, at.exception)


def test_wizard_form_creates_novel_and_saves_intake(isolated_data):
    at = _app().run()
    at.sidebar.radio[0].set_value("➕ 新建小说").run()
    by_label = {w.label: w for w in at.text_input}
    by_label["书名 *"].set_value("测试新书")
    by_label["工作目录（留空 = 默认 data/novels/<书名>_<时间戳>/）"].set_value(str(isolated_data / "wiz" / "书"))
    by_label["题材"].set_value("悬疑推理")
    by_label["主角（身份与处境）"].set_value("落魄侦探")
    at.button[0].click().run()  # 第一个按钮是表单的「创建小说」
    assert not at.exception, at.exception
    created = list((isolated_data / "wiz").glob("*"))
    assert created, at.error
    ws = Workspace(created[0])
    assert ws.is_initialized() and "悬疑推理" in ws.path("intake_note").read_text(encoding="utf-8")


def test_cockpit_approve_flow_via_ui(isolated_data):
    root, ws = new_novel(isolated_data)
    atomic_write_json(ws.path("blueprint"), make_blueprint(4))
    # 让 plan_blueprint 停在待批准
    st.update_step(ws, "init_workspace", status=st.ST_PASSED)
    st.update_step(ws, "plan_blueprint", status=st.ST_WAITING_REVIEW, input_hash=st.input_hash(ws, P.build_plan(ws)[1]))
    at = _app().run()
    at.session_state["ws"] = root
    at.sidebar.radio[0].set_value("🧭 驾驶舱").run()
    assert not at.exception, at.exception
    at.selectbox(key="cp_step_pick").set_value("plan_blueprint").run()
    assert not at.exception, at.exception
    at.button(key="cp_approve_plan_blueprint").click().run()
    assert not at.exception, at.exception
    assert statuses(ws)["plan_blueprint"] == st.ST_PASSED


def test_cockpit_and_planning_render_with_blueprint(isolated_data):
    root, ws = new_novel(isolated_data)
    atomic_write_json(ws.path("blueprint"), make_blueprint(4))
    at = _app().run()
    at.session_state["ws"] = root
    at.sidebar.radio[0].set_value("🧭 驾驶舱").run()
    assert not at.exception, at.exception
    assert any("雨夜钟声" in h.value for h in at.header)
    assert len(at.tabs) == 10
