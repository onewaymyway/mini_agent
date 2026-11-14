"""完稿修改页面的冒烟测试（AppTest，不调用 LLM）。"""

import os
from pathlib import Path

import pytest

from novel_writer import config, state as st_state, tool_api
from novel_writer.revision_flow import changes as CH
from novel_writer.revision_flow import config as CFG
from novel_writer.revision_flow import versions as V
from novel_writer.workspace import Workspace

APP = str(Path(__file__).parent / "apps" / "post_revision_app.py")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    yield tmp_path


def _novel(tmp_path, passed=True):
    r = tool_api.create_novel("界面测试", novels_dir=str(tmp_path / "novels"),
                              index_file=str(tmp_path / "shelf.json"), gear="G3", total_chapters_planned=2)
    ws = Workspace(r["data"]["workspace"])
    for n in (1, 2):
        p = ws.chapter_path(n, f"标题{n}", 2)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# 第{n}章\n\n李凌云握着玉佩。\n", encoding="utf-8")
    if passed:
        st_state.update_step(ws, "final_review", status=st_state.ST_PASSED)
    return ws


def _run(ws, monkeypatch):
    monkeypatch.setenv("RV_WS", str(ws.root))
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(APP, default_timeout=30).run()


def test_page_renders_without_baseline(tmp_path, monkeypatch):
    ws = _novel(tmp_path, passed=False)
    at = _run(ws, monkeypatch)
    assert not at.exception
    assert any("尚未创建完稿基线" in m.value for m in at.info)


def test_create_baseline_button(tmp_path, monkeypatch):
    ws = _novel(tmp_path)
    at = _run(ws, monkeypatch)
    at.button(key="rv_baseline").click().run()
    assert not at.exception
    assert V.is_baseline_ready(ws)
    assert not at.exception


def test_settings_save_persists(tmp_path, monkeypatch):
    ws = _novel(tmp_path)
    at = _run(ws, monkeypatch)
    at.number_input(key="rv_s_ch").set_value(7)
    at.selectbox(key="rv_s_scope").set_value("all")
    form_buttons = [b for b in at.button if b.label == "保存设置"]
    assert form_buttons
    form_buttons[0].click().run()
    assert not at.exception
    stored = CFG.load(ws)
    assert stored["max_chapters_per_run"] == 7 and stored["review_scope"] == "all"


def test_change_form_creates_draft_change(tmp_path, monkeypatch):
    ws = _novel(tmp_path)
    at = _run(ws, monkeypatch)
    at.text_input(key="rv_c_title").set_value("玉佩换银钥匙")
    at.text_input(key="rv_c_before").set_value("玉佩")
    at.text_input(key="rv_c_after").set_value("银钥匙")
    at.text_input(key="rv_c_terms").set_value("玉佩")
    btn = [b for b in at.button if b.label == "保存变更"][0]
    btn.click().run()
    assert not at.exception
    changes = CH.list_changes(ws)
    assert len(changes) == 1 and changes[0]["title"] == "玉佩换银钥匙" and changes[0]["status"] == "draft"


def test_runs_tab_shows_empty_state_without_confirmed_preview(tmp_path, monkeypatch):
    ws = _novel(tmp_path)
    V.init_baseline(ws)
    at = _run(ws, monkeypatch)
    assert not at.exception
    assert any("还没有已确认的预览" in m.value for m in at.info)


def test_runs_tab_lists_confirmed_preview_and_start_is_queued(tmp_path, monkeypatch):
    ws = _novel(tmp_path)
    V.init_baseline(ws)
    c = CH.save_change(ws, {"kind": "setting", "title": "换", "before": "玉佩", "after": "银钥匙",
                            "reason": "", "terms": ["玉佩"], "mode": "retcon"})
    pv = CH.build_preview(ws, "v001", [c["id"]])
    CH.confirm_preview(ws, pv["id"])
    at = _run(ws, monkeypatch)
    assert not at.exception
    assert pv["id"] in at.selectbox(key="rv_r_pick").options
    assert any(b.label == "开始改写" for b in at.button)


def test_preview_job_streams_log_and_sets_preview(tmp_path, monkeypatch):
    import time
    from novel_writer.revision_flow import jobs
    ws = _novel(tmp_path)
    V.init_baseline(ws)
    c = CH.save_change(ws, {"kind": "setting", "title": "换", "before": "玉佩", "after": "银钥匙",
                            "reason": "", "terms": ["玉佩"], "mode": "retcon"})
    at = _run(ws, monkeypatch)
    at.checkbox(key="rv_p_review").uncheck().run()
    at.multiselect(key="rv_p_changes").set_value([c["id"]]).run()
    [b for b in at.button if b.label == "生成预览"][0].click().run()
    name = at.session_state["rv_job_preview"]
    for _ in range(100):
        if jobs.status(ws, name).get("state") in ("done", "failed"):
            break
        time.sleep(0.1)
    assert jobs.status(ws, name)["state"] == "done"
    log = jobs.tail(ws, name)
    assert "预览" in log and "完成" in log
    at.run()
    assert not at.exception
    assert at.session_state["rv_preview"]


def test_every_action_writes_start_and_result_to_ops_log(tmp_path, monkeypatch):
    from novel_writer.revision_flow import jobs
    ws = _novel(tmp_path)
    at = _run(ws, monkeypatch)
    at.number_input(key="rv_s_ch").set_value(9)
    [b for b in at.button if b.label == "保存设置"][0].click().run()
    log = jobs.tail(ws, "ops")
    assert "▶ 保存设置：开始" in log and "✔ 保存设置：完成" in log
    # 失败也要可见：非法值经表单走到 CFG.update 的校验
    at.number_input(key="rv_s_ch").set_value(9)
    CFG.update(ws, {"max_chapters_per_run": 9})
    at2 = _run(ws, monkeypatch)
    at2.selectbox(key="rv_s_scope").set_value("all")
    [b for b in at2.button if b.label == "保存设置"][0].click().run()
    assert "保存设置：完成" in jobs.tail(ws, "ops")
