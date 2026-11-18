"""R8：大纲校验分组报告、章节卡片、被剔除条目的持久化与手动恢复。"""
import json
import re

import pytest

from article_to_video.planning.chunks import build_groups
from article_to_video.planning.errors import PlanError
from article_to_video.planning.runner import _context, load_digests, plan_outline
from article_to_video.validators.coverage import validate_spine
from article_to_video.validators.fidelity import dropped_entries
from article_to_video.validators.outline_report import outline_report
from conftest import PROJECT_DIR
from helpers_p1 import ScriptedLLM, covering_spine, good_digest, make_project

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402


def _spine(parts, target=10.0, cuts=(), golden=(), hooks=("h",)):
    return {"target_minutes": target, "parts": parts, "cuts": list(cuts), "golden_quotes": list(golden),
            "hook_candidates": list(hooks), "selected_hook": 0}


def _part(pid, sids, budget, title="章"):
    return {"id": pid, "title": title, "role": "argument", "one_line": "一句", "section_ids": sids,
            "weight": 1.0, "budget_minutes": budget}


def _cat(report, key):
    return next(c for c in report["categories"] if c["key"] == key)


def test_report_groups_issues_by_category_and_matches_gate():
    sp = _spine([_part("P01", ["s01"], 5.0), _part("P02", ["s02"], 5.0)], target=10)
    rep = outline_report(sp, ["s01", "s02", "s03"], [])
    assert rep["ok"] is False                                   # s03 未覆盖：门禁不通过
    assert _cat(rep, "coverage")["status"] == "error"
    assert _cat(rep, "duration")["status"] == "ok"
    assert [c["key"] for c in rep["categories"]] == ["coverage", "duration", "fidelity", "structure"]
    assert rep["ok"] == (not validate_spine(sp, ["s01", "s02", "s03"], [])["errors"])


def test_report_warns_about_short_parts_and_total_mismatch():
    sp = _spine([_part("P01", ["s01"], 0.2), _part("P02", ["s02"], 5.0)], target=10)
    rep = outline_report(sp, ["s01", "s02"], [])
    dur = _cat(rep, "duration")
    assert dur["status"] == "warn"
    texts = " ".join(i["text"] for i in dur["items"])
    assert "过短" in texts and "不一致" in texts
    assert rep["ok"] is True                                    # 时长只是提示，不阻止确认


def test_report_lists_dropped_digest_items_as_fidelity_warning():
    sp = _spine([_part("P01", ["s01"], 5.0), _part("P02", ["s02"], 5.0)], target=10)
    digests = [{"group_id": "g01", "dropped": [{"kind": "quote", "text": "编造"}]}]
    rep = outline_report(sp, ["s01", "s02"], digests)
    fid = _cat(rep, "fidelity")
    assert fid["status"] == "warn" and "g01" in fid["items"][0]["text"]
    assert rep["dropped_total"] == 1


def test_dropped_entries_identifies_unmatched_quotes_and_numbers():
    src = "核心是通用计算，规模是3000亿元。"
    obj = {"quotable": ["核心是通用计算", "编造的金句XYZ"], "key_numbers": ["3000亿元", "9999亿"]}
    got = dropped_entries(obj, src)
    assert got == [{"kind": "quote", "text": "编造的金句XYZ"}, {"kind": "number", "text": "9999亿"}]


def test_llm_digest_persists_dropped_items(tmp_path):
    pdir = make_project(tmp_path)
    def responder(prompt):
        return json.dumps({"summary": "x", "core_claims": ["c"], "key_numbers": ["9999亿"],
                           "quotable": ["编造原话ABC"]}, ensure_ascii=False)
    plan_outline(pdir, engine="llm", llm=ScriptedLLM(responder, covering_spine))
    d = load_digests(pdir)[0]
    assert d["quotable"] == [] and d["key_numbers"] == []
    kinds = sorted(x["kind"] for x in d["dropped"])
    assert kinds == ["number", "quote"]


def _first_sentence(pdir, group_id):
    ctx = _context(pdir)
    g = next(g for g in build_groups(ctx["lines"], ctx["doc"]) if g["id"] == group_id)
    for line in g["text"].splitlines():
        t = re.sub(r"[>*`#|]", "", line).strip()
        if 10 <= len(t) <= 40:
            return t
    raise AssertionError("no sentence")



def _ctx_text(pdir, group_id):
    ctx = _context(pdir)
    return next(g for g in build_groups(ctx["lines"], ctx["doc"]) if g["id"] == group_id)["text"]



def test_outline_page_shows_report_cards_and_restore_section(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    plan_outline(pdir, engine="rules")
    from helpers_ui_nav import go_step
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=90).run()
    go_step(at, "🗺️ 大纲")
    assert not at.exception, [e.value for e in at.exception]
    subs = [s.value for s in at.subheader]
    assert "大纲校验" in subs and "章节" in subs
    # 规则版大纲没有被剔除的条目，因此不显示恢复区
    assert not any("未在原文中找到" in s for s in subs)
    assert len(at.markdown) > 0


def test_outline_page_shows_restore_section_when_items_dropped(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTICLE_TO_VIDEO_HOME", str(tmp_path))
    pdir = make_project(tmp_path)
    plan_outline(pdir, engine="llm", llm=ScriptedLLM(
        lambda p: json.dumps({"summary": "x", "core_claims": ["c"], "quotable": ["编造ABC"]}, ensure_ascii=False),
        covering_spine))
    from helpers_ui_nav import go_step
    at = AppTest.from_file(str(PROJECT_DIR / "app.py"), default_timeout=90).run()
    go_step(at, "🗺️ 大纲")
    assert not at.exception, [e.value for e in at.exception]
    assert any("未在原文中找到" in s.value for s in at.subheader)
    assert any(b.label == "加入金句" for b in at.button)
