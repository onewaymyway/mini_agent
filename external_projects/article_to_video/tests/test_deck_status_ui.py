"""新方案的状态与失败原因（界面共用的纯函数）、规划器不丢内容、两个界面模块可导入。"""
import pytest

from article_to_video.deck import status as DS
from article_to_video.deck.plan import plan_slides
from article_to_video.deck.store import confirm_deck, plan_deck
from article_to_video.project_store import write_json_atomic
from article_to_video.source.md_parse import parse_markdown
from helpers_p2 import make_project
from test_deck_phase2 import FIXTURE


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    blocks = parse_markdown(FIXTURE.read_text(encoding="utf-8"))["blocks"]
    write_json_atomic(pdir / "source" / "blocks.json", {"blocks": blocks})
    write_json_atomic(pdir / "plan" / "spine.json",
                      {"title": "t", "confirmed": True,
                       "parts": [{"id": "P01", "title": "章", "role": "claim", "one_line": "", "weight": 1.0,
                                  "budget_minutes": 6.0, "section_ids": ["s01"]}]})
    return pdir


def test_state_moves_from_not_generated_to_pending_to_confirmed(project):
    assert DS.part_state(project, "plan", "P01")["label"] == "⬜ 未生成"
    plan_deck(project, "P01")
    assert DS.part_state(project, "plan", "P01")["label"] == "🟡 待确认"
    confirm_deck(project, "P01")
    assert DS.part_state(project, "plan", "P01")["label"] == "✅ 已确认"


def test_a_failure_reason_is_shown_and_cleared_on_success(project):
    DS.record_error(project, "script", "P01", "未配置文本模型")
    st = DS.part_state(project, "script", "P01")
    assert st["label"] == "❌ 失败" and st["detail"] == "未配置文本模型"
    DS.record_error(project, "script", "P01", None)
    assert DS.part_state(project, "script", "P01")["label"] == "⬜ 未生成"


def test_stale_when_the_source_changes_after_planning(project):
    plan_deck(project, "P01")
    confirm_deck(project, "P01")
    blocks = parse_markdown(FIXTURE.read_text(encoding="utf-8"))["blocks"]
    changed = [dict(b) for b in blocks]
    changed[1] = dict(changed[1], plain="另一段原文内容")
    write_json_atomic(project / "source" / "blocks.json", {"blocks": changed})
    assert DS.part_state(project, "plan", "P01")["label"] == "⚠️ 已过期"


def test_planner_never_drops_content_it_cannot_classify():
    blocks = [{"id": "h", "kind": "heading", "level": 2, "text": "甲", "section": "s01"},
              {"id": "c1", "kind": "code", "shape": "text", "hint": "formula", "items": [],
               "content": "E = mc^2", "section": "s01"},
              {"id": "c2", "kind": "code", "shape": "text", "hint": None, "items": [],
               "content": "第一行\n第二行", "section": "s01"}]
    deck = plan_slides({"id": "P01", "title": "t", "section_ids": ["s01"]}, blocks, "P01")
    texts = [x for s in deck["slides"] for e in s["elements"] for x in (e.get("items") or [e.get("text", "")])]
    assert "第一行" in texts and "第二行" in texts


def test_both_scheme_interfaces_import_without_each_other():
    import ui.cockpit_page as cp
    import ui.deck_cockpit as dc
    assert callable(cp.render_cockpit_page) and callable(dc.render_control)
    assert "deck" not in [x for x in dir(cp) if x == "_deck_panel"]


def test_new_scheme_overview_lists_only_its_own_stages(project):
    from ui.deck_cockpit import overview_rows
    names = [r["步骤"] for r in overview_rows(project)]
    assert names == ["🗺️ 大纲", "📑 幻灯片规划", "✍️ 讲稿", "🎙️ 配音", "📤 合成导出"]
