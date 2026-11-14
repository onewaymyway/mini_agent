"""大纲先行 O2：版本历史、编辑校验、回退、确认失效与冻结、单章反馈改写、整幕重排。"""

import json
import re
import sys
from pathlib import Path

import pytest

from novel_writer import blueprint_store as BS
from novel_writer import brief as BR
from novel_writer import brief_review as BRV
from novel_writer import config, state as st, tool_api
from novel_writer.revision_flow.common import FlowError
from novel_writer.workspace import Workspace

sys.path.insert(0, str(Path(__file__).parent))
from test_outline_first_o1 import ActInvoker, BLUEPRINT, _act_json, good  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NOVELS_DIR", tmp_path / "novels")
    monkeypatch.setattr(config, "SHELF_INDEX_FILE", tmp_path / "shelf.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("NOVEL_WRITER_NO_SPAWN", "1")
    yield tmp_path


@pytest.fixture
def ws(tmp_path, monkeypatch):
    r = tool_api.create_novel("简述确认", novels_dir=str(tmp_path / "novels"),
                              index_file=str(tmp_path / "shelf.json"), gear="G3", total_chapters_planned=3,
                              writing_mode="outline_first")
    w = Workspace(r["data"]["workspace"])
    monkeypatch.setattr(BS, "load_for_ws", lambda _ws, strict=False: BLUEPRINT)
    monkeypatch.setattr(BR, "known_names", lambda _ws: ["李凌云"])
    monkeypatch.setattr(BR, "known_foreshadow", lambda _ws: ["F1"])
    BR.generate_act(w, 1, 1, 3, ActInvoker([_act_json([1, 2, 3])]))
    return w


def _edit_payload(**over):
    b = {"title": "新章名", "narrative": "乙" * 350,
         "key": {"must_events": ["事件甲"], "must_states": [{"who": "李凌云", "change": "变化"}],
                 "must_foreshadow": ["F1"], "must_hook": "新的钩子"}}
    b.update(over)
    return b


# ─────────────────────────── 编辑与版本 ───────────────────────────

def test_valid_edit_snapshots_old_version_and_saves(ws):
    old_title = BR.load_brief(ws, 2)["title"]
    r = BRV.save_edit(ws, 2, _edit_payload(), reason="改写开头")
    assert r["ok"] and r["version"] == 1
    assert BR.load_brief(ws, 2)["title"] == "新章名"
    hist = BRV.versions(ws, 2)
    assert len(hist) == 1 and hist[0]["reason"] == "改写开头"
    assert BRV.load_version(ws, 2, 1)["title"] == old_title


def test_invalid_edit_writes_nothing(ws):
    before = BR.load_brief(ws, 2)
    r = BRV.save_edit(ws, 2, _edit_payload(narrative="短"), reason="x")
    assert not r["ok"] and any("叙述简述" in e for e in r["errors"])
    assert BR.load_brief(ws, 2) == before and BRV.versions(ws, 2) == []


def test_edit_rejects_unknown_character(ws):
    r = BRV.save_edit(ws, 1, _edit_payload(key={"must_events": ["a"], "must_states": [{"who": "路人", "change": "x"}],
                                                "must_foreshadow": [], "must_hook": "h"}), reason="x")
    assert not r["ok"] and any("路人" in e for e in r["errors"])


def test_restore_returns_old_content_and_records_new_version(ws):
    original = BR.load_brief(ws, 3)
    BRV.save_edit(ws, 3, _edit_payload(), reason="改")
    r = BRV.restore(ws, 3, 1)
    assert r["ok"]
    restored = BR.load_brief(ws, 3)
    assert restored["title"] == original["title"] and restored["narrative"] == original["narrative"]
    assert [v["reason"] for v in BRV.versions(ws, 3)][-1].startswith("回退到 v001")


def test_edit_makes_review_stale_when_it_was_passed(ws):
    st.update_step(ws, BR.BRIEF_REVIEW_ID, status=st.ST_WAITING_REVIEW)
    BRV.confirm(ws)
    assert st.get_step_state(ws, BR.BRIEF_REVIEW_ID)["status"] == st.ST_PASSED
    BRV.save_edit(ws, 1, _edit_payload(), reason="改")
    assert st.get_step_state(ws, BR.BRIEF_REVIEW_ID)["status"] == st.ST_STALE
    assert BRV.confirmation_status(ws)["confirmed"] is False


# ─────────────────────────── 确认与冻结 ───────────────────────────

def test_confirm_requires_waiting_review(ws):
    with pytest.raises(FlowError, match="尚未就绪"):
        BRV.confirm(ws)


def test_confirm_freezes_and_detects_later_changes(ws):
    st.update_step(ws, BR.BRIEF_REVIEW_ID, status=st.ST_WAITING_REVIEW)
    out = BRV.confirm(ws)
    assert out["chapters"] == 3
    assert st.get_step_state(ws, BR.BRIEF_REVIEW_ID)["status"] == st.ST_PASSED
    status = BRV.confirmation_status(ws)
    assert status["confirmed"] and status["changed"] == []
    # 绕过界面直接改文件：哈希对不上，确认状态应报告变化
    p = BR.brief_json_path(ws, 2)
    data = json.loads(p.read_text(encoding="utf-8"))
    data["key"]["must_hook"] = "被改了"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert BRV.confirmation_status(ws)["changed"] == [2]


def test_confirm_refused_when_a_chapter_is_invalid(ws):
    st.update_step(ws, BR.BRIEF_REVIEW_ID, status=st.ST_WAITING_REVIEW)
    BR.brief_json_path(ws, 3).unlink()
    with pytest.raises(FlowError, match="未通过校验"):
        BRV.confirm(ws)


# ─────────────────────────── 单章反馈改写 ───────────────────────────

class RewriteInvoker:
    def __init__(self, brief=None, affected=(), text=None):
        self.brief, self.affected, self.text = brief, list(affected), text
        self.prompts = []

    def __call__(self, *, skill, prompt, export_dir, step_id):
        self.prompts.append(prompt)
        out = re.search(r"写入文件：(\S+)", prompt).group(1)
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        if self.text is not None:
            Path(out).write_text(self.text, encoding="utf-8")
        else:
            Path(out).write_text(json.dumps({"brief": self.brief, "affected": self.affected}, ensure_ascii=False),
                                 encoding="utf-8")


def test_feedback_rewrite_saves_new_version_and_reports_neighbors(ws):
    new = good(2, title="改后的第二章", narrative="丙" * 340)
    inv = RewriteInvoker(brief=new, affected=[3, 1, 2, 99])
    res = BRV.rewrite_chapter(ws, 2, "让这一章更紧张", inv)
    assert res["version"] == 1 and res["affected"] == [1, 3]  # 2 本章自身、99 越界，都被过滤
    assert BR.load_brief(ws, 2)["title"] == "改后的第二章"
    assert "让这一章更紧张" in inv.prompts[0] and "当前简述" in inv.prompts[0]


def test_feedback_rewrite_retries_then_fails_without_writing(ws):
    before = BR.load_brief(ws, 2)
    bad = good(2, narrative="短")
    with pytest.raises(FlowError, match="未通过校验"):
        BRV.rewrite_chapter(ws, 2, "改", RewriteInvoker(brief=bad))
    assert BR.load_brief(ws, 2) == before and BRV.versions(ws, 2) == []


def test_feedback_rewrite_needs_feedback_and_invoker(ws):
    with pytest.raises(FlowError, match="修改意见"):
        BRV.rewrite_chapter(ws, 1, "  ", RewriteInvoker(brief=good(1)))
    with pytest.raises(FlowError, match="执行器"):
        BRV.rewrite_chapter(ws, 1, "改", None)


# ─────────────────────────── 整幕重排 ───────────────────────────

def test_regenerate_act_snapshots_everything_and_applies_feedback(ws):
    inv = ActInvoker([_act_json([1, 2, 3])])
    out = BRV.regenerate_act(ws, 1, 1, 3, "节奏再快一点", inv)
    assert out["chapters"] == [1, 2, 3]
    assert "节奏再快一点" in inv.prompts[0]
    for n in (1, 2, 3):
        assert len(BRV.versions(ws, n)) == 1 and BRV.versions(ws, n)[0]["reason"].startswith("整幕重排前")


def test_regenerate_act_invalidates_confirmation(ws):
    st.update_step(ws, BR.BRIEF_REVIEW_ID, status=st.ST_WAITING_REVIEW)
    BRV.confirm(ws)
    BRV.regenerate_act(ws, 1, 1, 3, "改", ActInvoker([_act_json([1, 2, 3])]))
    assert st.get_step_state(ws, BR.BRIEF_REVIEW_ID)["status"] == st.ST_STALE


# ─────────────────────────── 页面冒烟 ───────────────────────────

def test_briefs_page_renders_and_confirm_button_states(ws, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("BR_WS", str(ws.root))
    app = str(Path(__file__).parent / "apps" / "briefs_app.py")
    at = AppTest.from_file(app, default_timeout=30).run()
    assert not at.exception
    assert any(b.label == "确认全部简述" and b.disabled for b in at.button)
    st.update_step(ws, BR.BRIEF_REVIEW_ID, status=st.ST_WAITING_REVIEW)
    at = AppTest.from_file(app, default_timeout=30).run()
    assert not at.exception
    confirm = [b for b in at.button if b.label == "确认全部简述"][0]
    assert not confirm.disabled
    confirm.click().run()
    assert not at.exception
    assert st.get_step_state(ws, BR.BRIEF_REVIEW_ID)["status"] == st.ST_PASSED
