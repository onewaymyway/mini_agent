"""R5 以大纲为准的偏离检查测试：全部使用桩执行器，不调用真实 LLM。

大纲（蓝图）为权威；确定性比对（多写章节、少写章节、字数、人物）与 LLM 偏离判断分开；
只产出「以大纲为准修改正文」的选项；坏输出重试；批次缓存；作者处理状态跨重算保留；
与 R2 的复核、R3 的采纳互不干扰。
"""

import json
import re
from pathlib import Path

import pytest

from novel_writer import blueprint_store as BS
from novel_writer.revision_flow import outline_check as OC
from novel_writer.revision_flow import review_adopt as RA
from novel_writer.revision_flow import review_cards as RC
from novel_writer.revision_flow import review_checks as RK
from novel_writer.revision_flow import review_llm as RL
from novel_writer.revision_flow import versions as V
from novel_writer.revision_flow.common import FlowError

from test_revision_review_cards import CardInvoker, _novel, isolated  # noqa: F401

_OUT = re.compile(r"写入文件：(.+)")
_NUMS = re.compile(r"第(\d+)章《")


class CheckStub:
    """偏离判断的桩。bad_first：第一次输出不合法；fake：给出一条伪造引文的走样偏离。"""

    def __init__(self, *, bad_first=False, fake=False):
        self.cards = CardInvoker()
        self.calls = []
        self.bad_first = bad_first
        self.fake = fake

    def __call__(self, *, skill, prompt, export_dir, step_id):
        if "提取一张结构化的" in prompt:
            return self.cards(skill=skill, prompt=prompt, export_dir=export_dir, step_id=step_id)
        out = Path(_OUT.search(prompt).group(1).strip())
        nums = [int(x) for x in _NUMS.findall(prompt)]
        self.calls.append(nums)
        if self.bad_first and len(self.calls) == 1:
            out.write_text("不是 JSON", encoding="utf-8")
            return
        items = []
        if 1 in nums:
            items.append({"chapter": 1, "type": "多写", "severity": "中", "title": "多出一场山门冲突",
                          "outline_ref": "功能1", "description": "正文多写了山门冲突",
                          "evidence": [{"chapter": 1, "quote": "李凌云握着玉佩走进山门"}]})
        if 2 in nums:
            items.append({"chapter": 2, "type": "少写", "severity": "中", "title": "缺少玉佩的交代",
                          "outline_ref": "大纲要求交代玉佩来历", "description": "正文没有写出玉佩来历",
                          "evidence": []})
            if self.fake:
                items.append({"chapter": 2, "type": "走样", "severity": "轻", "title": "伪造引文的走样",
                              "outline_ref": "功能2", "description": "桩", "evidence": [
                                  {"chapter": 2, "quote": "这句话不在原文里"}]})
        out.write_text(json.dumps({"deviations": items}, ensure_ascii=False), encoding="utf-8")


def _blueprint(ids):
    """大纲条目：ids 为大纲中的章号（可以与正文不完全一致，用来制造多写 / 少写）。"""
    chapters = []
    for n in ids:
        chapters.append({"id": n, "title": f"第{n}章 标题{n}", "function": f"功能{n}", "chapter_type": "冲突",
                         "pov": "李凌云", "tension_level": 5,
                         "word_budget": 100 if n == 1 else 9999,           # 第 1 章预算极小 → 字数偏离
                         "characters_present": ["李凌云", "王道长"] if n == 1 else ["李凌云"]})
    return {"meta": {}, "structure": {"total_chapters": len(ids), "acts": [
        {"name": "唯一幕", "chapter_range": [1, max(ids)]}]}, "chapters": chapters}


def _ready(tmp_path, outline_ids=(1, 2, 4)):
    """正文 1–3 章；大纲为 outline_ids。所以：3 是多写（正文有、大纲无）；4 是少写（大纲有、正文无）。"""
    ws = _novel(tmp_path, total=3, baseline=False)
    BS.write_store(ws.path("blueprint_dir"), ws.path("blueprint"), _blueprint(list(outline_ids)))
    V.init_baseline(ws)
    RC.generate_cards(ws, None, CardInvoker())
    return ws


def _pool(ws):
    return RK.load_findings(ws, "v001")


def _outline(pool):
    return [f for f in pool["findings"] if f["source"] == "outline"]


# ─────────────────────────── 确定性部分 ───────────────────────────

def test_deterministic_only_without_model(tmp_path):
    ws = _ready(tmp_path)
    res = OC.run_check(ws, None, None)
    assert res["llm"] is False and res["calls"] == 0
    checks = {f["check"] for f in _outline(_pool(ws))}
    assert {"outline_extra_chapter", "outline_missing_chapter", "outline_word"} <= checks
    extra = next(f for f in _outline(_pool(ws)) if f["check"] == "outline_extra_chapter")
    assert extra["chapters"] == [3] and extra["severity"] == "中"
    assert all(f["origin_kind"] == "deterministic" for f in _outline(_pool(ws)))


def test_character_mismatch_flagged_both_ways(tmp_path):
    ws = _ready(tmp_path)
    OC.run_check(ws, None, None)
    pool = _pool(ws)
    miss = next(f for f in _outline(pool) if f["check"] == "outline_character_missing")
    assert miss["chapters"] == [1] and "王道长" in miss["description"]               # 大纲要求，正文事实卡没有
    extra = next(f for f in _outline(pool) if f["check"] == "outline_character_extra" and f["chapters"] == [1])
    assert "路人乙" in extra["description"]                                           # 正文多出的人物也要提示


def test_no_adoption_option_for_deterministic_findings(tmp_path):
    ws = _ready(tmp_path)
    OC.run_check(ws, None, None)
    for f in _outline(_pool(ws)):
        assert RA.options(f) == []                                                   # 规则偏离只是提示


# ─────────────────────────── LLM 部分 ───────────────────────────

def test_llm_deviations_verified_and_scoped_to_batch(tmp_path):
    ws = _ready(tmp_path)
    stub = CheckStub()
    res = OC.run_check(ws, None, stub)
    assert res["llm"] is True and res["calls"] == 1 and stub.calls == [[1, 2]]     # 只对照两章：大纲与正文都有的章
    llm = [f for f in _outline(_pool(ws)) if f["origin_kind"] == "llm"]
    multi = next(f for f in llm if f["check"] == "outline_extra")
    assert multi["evidence_ok"] is True and multi["evidence"][0]["verified"] is True
    missing = next(f for f in llm if f["check"] == "outline_missing")
    assert missing["evidence_ok"] is True and missing["evidence"] == []              # 少写可以没有引文
    assert "大纲要求：大纲要求交代玉佩来历" in missing["description"]


def test_fake_quote_marks_finding_unverified(tmp_path):
    ws = _ready(tmp_path)
    OC.run_check(ws, None, CheckStub(fake=True))
    drift = next(f for f in _outline(_pool(ws)) if f["check"] == "outline_drift")
    assert drift["evidence_ok"] is False and drift["evidence"][0]["verified"] is False


def test_llm_option_is_fix_body_to_match_outline(tmp_path):
    ws = _ready(tmp_path)
    OC.run_check(ws, None, CheckStub())
    f = next(x for x in _outline(_pool(ws)) if x["check"] == "outline_extra")
    opts = RA.options(f)
    assert len(opts) == 1 and opts[0]["target_chapters"] == [1]
    assert opts[0]["instruction"].startswith("以大纲为准修改正文")
    assert "只修改正文中与大纲偏离有关的部分" in opts[0]["instruction"]
    missing_chapter = next(x for x in _outline(_pool(ws)) if x["check"] == "outline_missing_chapter")
    assert RA.options(missing_chapter) == []                                         # 未写出的章没有正文可改


def test_bad_output_retried_then_accepted(tmp_path):
    ws = _ready(tmp_path)
    stub = CheckStub(bad_first=True)
    res = OC.run_check(ws, None, stub)
    assert len(stub.calls) == 2 and res["failed"] == []


def test_batch_cache_avoids_repeat_calls_and_invalidates_on_change(tmp_path):
    ws = _ready(tmp_path)
    stub = CheckStub()
    OC.run_check(ws, None, stub)
    again = OC.run_check(ws, None, stub)
    assert again["calls"] == 0 and len(stub.calls) == 1
    # 大纲变化（例如采纳了新大纲）→ 批次键变化 → 重新判断
    state_path = RK.review_dir(ws, "v001") / OC.STATE
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert len(state["batches"]) == 1


def test_status_survives_rerun(tmp_path):
    ws = _ready(tmp_path)
    OC.run_check(ws, None, CheckStub())
    f = next(x for x in _outline(_pool(ws)) if x["check"] == "outline_missing")
    RK.set_status(ws, "v001", f["id"], "rejected", "作者：后续章节会补上")
    OC.run_check(ws, None, CheckStub())
    kept = next(x for x in _outline(_pool(ws)) if x["id"] == f["id"])
    assert kept["status"] == "rejected" and kept["notes"] == "作者：后续章节会补上"


def test_outline_findings_survive_review_and_checks_reruns(tmp_path):
    """R1/R2 重算不得误删大纲偏离（它们用不同的 source）。"""
    ws = _ready(tmp_path)
    OC.run_check(ws, None, CheckStub())
    before = {f["id"] for f in _outline(_pool(ws))}
    RL.run_review(ws, None, _NoopReview())
    RK.run_checks(ws, "v001")
    assert {f["id"] for f in _outline(_pool(ws))} == before


class _NoopReview:
    def __call__(self, *, skill, prompt, export_dir, step_id):
        out = Path(_OUT.search(prompt).group(1).strip())
        if "【任务】衔接审阅" in prompt or "【任务】全书维度审阅" in prompt:
            out.write_text(json.dumps({"issues": []}), encoding="utf-8")
        else:
            out.write_text(json.dumps({"verdict": "confirmed", "reason": "x"}), encoding="utf-8")


def test_adoption_creates_outline_fix_change_and_missing_cards_refused(tmp_path):
    ws = _ready(tmp_path)
    OC.run_check(ws, None, CheckStub())
    f = next(x for x in _outline(_pool(ws)) if x["check"] == "outline_extra")
    ch = RA.adopt(ws, None, f["id"], option_index=0)
    assert ch["chapters"] == [1] and "以大纲为准" in ch["instruction"]
    assert ch["origin"]["finding_id"] == f["id"]
    ws2 = _novel(tmp_path / "b", total=2, baseline=False)
    BS.write_store(ws2.path("blueprint_dir"), ws2.path("blueprint"), _blueprint([1, 2]))
    V.init_baseline(ws2)
    with pytest.raises(FlowError, match="事实卡"):
        OC.run_check(ws2, None, None)


def test_outline_tab_and_review_tab_show_outline_findings(tmp_path, monkeypatch):
    from test_revision_review_llm import _render
    ws = _ready(tmp_path)
    OC.run_check(ws, None, CheckStub())
    at = _render(ws, monkeypatch)
    assert not at.exception
    assert any("偏离检查（调用模型）" in b.label for b in at.button)
    assert any("大纲偏离" in str(m.value) or "问题池" in str(m.value) for m in at.markdown)
