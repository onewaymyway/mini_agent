"""R1 确定性检查与问题池的测试：直接构造事实卡，不调用任何 LLM。"""

import json

import pytest

from novel_writer.revision_flow import config as CFG
from novel_writer.revision_flow import review_cards as RC
from novel_writer.revision_flow import review_checks as RK
from novel_writer.revision_flow import versions as V
from novel_writer.revision_flow.common import FlowError, save_json

from test_revision_review_cards import CardInvoker, _novel, isolated  # noqa: F401  (复用夹具)


def card(n, **kw):
    base = {"schema": 1, "chapter": n, "time_marker": "", "location_start": "", "location_end": "",
            "characters": [], "unregistered_characters": [], "events": [], "facts_stated": [], "foreshadow": [],
            "opening_state": "", "ending_state": "", "open_questions": [], "dropped": []}
    base.update(kw)
    return base


def fact(subject, attr, value, quote="原文"):
    return {"subject": subject, "attribute": attr, "value": value, "quote": quote}


def chars(*pairs):
    return [{"name": n, "role_in_chapter": "", "state_end": s} for n, s in pairs]


# ─────────────────────────── 各项检查 ───────────────────────────

def test_fact_conflict_flags_different_values_across_chapters_only():
    cards = {1: card(1, facts_stated=[fact("李凌云", "左手", "握剑", "他左手握剑")]),
             2: card(2, facts_stated=[fact("李凌云", "左手", "握剑", "左手握着剑")]),          # 同值：不报
             3: card(3, facts_stated=[fact("李凌云", "左手", "断了", "左手已经断了"),
                                      fact("李凌云", "年龄", "二十", "年方二十")]),
             4: card(4, facts_stated=[fact("王道长", "年龄", "七十", "七十岁"),
                                      fact("王道长", "年龄", "七十岁", "已是七十岁")])}        # 互相包含：不报
    out = RK.check_fact_conflicts(cards)
    assert len(out) == 1
    f = out[0]
    assert f["category"] == "contradiction" and f["needs_adjudication"] and f["chapters"] == [1, 3]
    assert {e["quote"] for e in f["evidence"]} == {"他左手握剑", "左手已经断了"}
    assert RK.check_fact_conflicts({1: card(1, facts_stated=[fact("a", "b", "x"), fact("a", "b", "y")])}) == []   # 同章不算


def test_departed_reappear_and_negation_and_revival():
    cards = {1: card(1, characters=chars(("甲", "战死沙场"), ("乙", "险些死亡"), ("丙", "被杀"))),
             2: card(2, characters=chars(("乙", "受伤"))),
             3: card(3, characters=chars(("甲", "在场"), ("丙", "重生归来"))),
             4: card(4, characters=chars(("甲", "在场")))}
    out = RK.check_departed_reappear(cards)
    assert [f["title"] for f in out] == ["「甲」第1章已死亡，之后仍出现"]     # 乙有否定词、丙复活均不报；甲只报一次
    assert out[0]["severity"] == "严重" and out[0]["chapters"] == [1, 3, 4]


def test_timeline_only_explicit_day_numbers():
    cards = {1: card(1, time_marker="第三天清晨"), 2: card(2, time_marker="一个雨夜"),
             3: card(3, time_marker="第十二天"), 4: card(4, time_marker="第五日黄昏"), 5: card(5, time_marker="第6天")}
    out = RK.check_timeline(cards)
    assert [(f["chapters"]) for f in out] == [[3, 4]]                      # 3 → 12 → 5 倒退；5 → 6 正常
    assert RK._cn_num("十二") == 12 and RK._cn_num("二十") == 20 and RK._cn_num("一百零") is None


def test_foreshadow_order_checks_ids_only():
    q = lambda t: t  # noqa: E731
    cards = {1: card(1, foreshadow=[{"ref": "F1", "action": "resolve", "quote": "回收一"},
                                    {"ref": "F2", "action": "plant", "quote": "种二"},
                                    {"ref": "F4", "action": "resolve", "quote": "回收四"},
                                    {"ref": "玉佩的秘密", "action": "resolve", "quote": "自由文本"}]),
             2: card(2, foreshadow=[{"ref": "f1", "action": "plant", "quote": "种一"}])}
    out = {f["id"]: f for f in RK.check_foreshadow_order(cards)}
    titles = sorted(f["title"] for f in out.values())
    assert titles == ["伏笔 F1 先回收后种下", "伏笔 F2 种下后全书未见回收", "伏笔 F4 被回收但没有种下记录"]
    assert not any("玉佩" in t for t in titles)                             # 自由文本不检查
    assert all(e["verified"] for f in out.values() for e in f["evidence"])


def test_unregistered_needs_two_chapters():
    cards = {1: card(1, unregistered_characters=["路人乙", "新人丙"]), 2: card(2, unregistered_characters=["新人丙"])}
    out = RK.check_unregistered(cards)
    assert [f["title"] for f in out] == ["「新人丙」多章出现但未登记"] and out[0]["needs_adjudication"] is False


def test_handoff_break_only_adjacent_and_different():
    cards = {1: card(1, location_end="山门"), 2: card(2, location_start="山门外的石阶", location_end="京城"),
             3: card(3, location_start="海边"), 5: card(5, location_start="沙漠")}
    out = RK.check_handoff(cards)
    assert [f["chapters"] for f in out] == [[2, 3]]                        # 1→2 互相包含不报；3→5 不相邻不报
    assert out[0]["severity"] == "轻"


# ─────────────────────────── 问题池 ───────────────────────────

def _ready(tmp_path):
    ws = _novel(tmp_path)
    RC.generate_cards(ws, None, CardInvoker())
    return ws


def test_run_checks_requires_cards_and_baseline(tmp_path):
    ws = _novel(tmp_path)
    with pytest.raises(FlowError, match="事实卡"):
        RK.run_checks(ws)
    with pytest.raises(FlowError, match="完稿"):
        RK.run_checks(_novel(tmp_path / "x", baseline=False))


def _inject(ws, n, **kw):
    """覆盖版本当前某章的事实卡（构造需要的内容）。"""
    src = next(s for s in RC.list_chapter_sources(ws, "v001") if s["number"] == n)
    c = RC.load_card(ws, n, src["sha"])
    c.update(kw)
    save_json(RC.card_path(ws, n, src["sha"]), c)


def test_run_checks_end_to_end_report_and_dimension_switch(tmp_path):
    ws = _ready(tmp_path)
    _inject(ws, 1, characters=chars(("甲", "战死")))
    _inject(ws, 3, characters=chars(("甲", "在场")))
    res = RK.run_checks(ws)
    assert res["review_id"] == "rev_v001" and res["counts"] == {"严重": 1}
    rpt = (ws.root / "_revision" / "review" / "rev_v001" / "report.md").read_text(encoding="utf-8")
    assert "已死亡，之后仍出现" in rpt and "待仲裁" in rpt and "## 严重（1）" in rpt
    CFG.update(ws, {"review_dimensions": ["foreshadow"]})                    # 关掉「前后矛盾」维度
    assert RK.run_checks(ws)["findings"] == []
    assert "未发现问题" in (ws.root / "_revision" / "review" / "rev_v001" / "report.md").read_text(encoding="utf-8")


def test_missing_cards_reported_regardless_of_dimensions(tmp_path):
    ws = _novel(tmp_path)
    RC.generate_cards(ws, None, CardInvoker(raise_for={2}))
    CFG.update(ws, {"review_dimensions": ["style_drift"]})
    res = RK.run_checks(ws)
    assert res["missing"] == [2]
    assert [f["check"] for f in res["findings"]] == ["coverage_missing"] and res["findings"][0]["chapters"] == [2]


def test_rerun_keeps_ids_status_and_foreign_findings(tmp_path):
    ws = _ready(tmp_path)
    _inject(ws, 1, characters=chars(("甲", "战死")))
    _inject(ws, 2, characters=chars(("甲", "在场")))
    first = RK.run_checks(ws)["findings"][0]
    RK.set_status(ws, "v001", first["id"], "rejected", notes="确认是回忆")
    data = RK.load_findings(ws, "v001")                                        # 模拟 R2 写入的 LLM 来源问题
    data["findings"].append({**first, "id": "f_llm0000001", "source": "llm", "check": "x", "status": "adopted"})
    save_json(RK.findings_path(ws, "v001"), data)
    again = RK.run_checks(ws)["findings"]
    by_id = {f["id"]: f for f in again}
    assert by_id[first["id"]]["status"] == "rejected" and by_id[first["id"]]["notes"] == "确认是回忆"
    assert by_id["f_llm0000001"]["status"] == "adopted"                        # 非确定性来源原样保留
    assert len(again) == 2


def test_stale_deterministic_finding_disappears_when_cause_is_gone(tmp_path):
    ws = _ready(tmp_path)
    _inject(ws, 1, characters=chars(("甲", "战死")))
    _inject(ws, 2, characters=chars(("甲", "在场")))
    assert len(RK.run_checks(ws)["findings"]) == 1
    _inject(ws, 2, characters=chars(("乙", "在场")))
    assert RK.run_checks(ws)["findings"] == []


def test_set_status_validation_and_unknown_id(tmp_path):
    ws = _ready(tmp_path)
    _inject(ws, 1, characters=chars(("甲", "战死")))
    _inject(ws, 2, characters=chars(("甲", "在场")))
    fid = RK.run_checks(ws)["findings"][0]["id"]
    with pytest.raises(FlowError):
        RK.set_status(ws, "v001", fid, "bogus")
    with pytest.raises(FlowError, match="不存在"):
        RK.set_status(ws, "v001", "f_nope", "rejected")


def test_checks_use_no_llm_and_do_not_touch_versions(tmp_path):
    ws = _ready(tmp_path)
    before = V.manifest(ws, "v001")
    RK.run_checks(ws)                    # run_checks 没有 invoker 参数：结构上不可能调用 LLM
    assert V.verify(ws, "v001") == [] and V.manifest(ws, "v001") == before
