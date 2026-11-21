"""R2 LLM 复核的测试：全部使用桩执行器，不调用真实 LLM。

桩按提示词里的任务标记分流（仲裁 / 衔接审阅 / 全书维度审阅），输出的引文取自真实章节原文，
需要时故意给出伪造引文或坏 JSON，验证校验、折叠、重试、调用上限与续跑、缓存失效。
"""

import json
import re
from pathlib import Path

import pytest

from novel_writer.revision_flow import config as CFG
from novel_writer.revision_flow import review_cards as RC
from novel_writer.revision_flow import review_checks as RK
from novel_writer.revision_flow import review_llm as RL
from novel_writer.revision_flow import versions as V
from novel_writer.revision_flow.common import FlowError, load_json

from test_revision_review_cards import TEXTS, CardInvoker, _novel, isolated  # noqa: F401  (复用夹具)
from test_revision_review_checks import chars, _inject  # noqa: F401

_OUT = re.compile(r"写入文件：(.+)")
_TITLE = re.compile(r"【候选问题】(.+?)\n")
_WIN_CH = re.compile(r"第(\d+)章《")


class ReviewStub:
    """按任务标记分流的桩执行器。

    verdicts: 候选标题子串 → verdict（默认 confirmed）
    fake_quote_windows: 窗口审阅时输出伪造引文
    bad_json_first: 这些 kind 的第一次输出为坏 JSON
    raise_for: 这些 kind 直接抛异常
    """

    def __init__(self, *, verdicts=None, fake_quote=False, bad_json_first=(), raise_for=(), whole_issue=True,
                 profile_issues=()):
        self.profile_issues = list(profile_issues)
        self.calls = []
        self.verdicts = verdicts or {}
        self.fake_quote = fake_quote
        self.bad_json_first = set(bad_json_first)
        self.raise_for = set(raise_for)
        self.whole_issue = whole_issue

    def kind_of(self, prompt):
        if "【任务】仲裁候选问题" in prompt:
            return "adj"
        if "【任务】衔接审阅" in prompt:
            return "win"
        if "【任务】全书维度审阅" in prompt:
            return "whole"
        if "【任务】人设与逻辑审阅" in prompt:
            return "profile"
        raise AssertionError("未知任务")

    def __call__(self, *, skill, prompt, export_dir, step_id):
        kind = self.kind_of(prompt)
        self.calls.append((kind, skill, step_id))
        if kind in self.raise_for:
            raise RuntimeError(f"模拟 {kind} 执行器异常")
        out = Path(_OUT.search(prompt).group(1).strip())
        if kind in self.bad_json_first and sum(1 for c in self.calls if c[0] == kind) == 1:
            out.write_text("不是 JSON", encoding="utf-8")
            return
        if kind == "adj":
            title = _TITLE.search(prompt).group(1)
            verdict = next((v for k, v in self.verdicts.items() if k in title), "confirmed")
            data = {"verdict": verdict, "reason": f"桩判定：{verdict}"}
        elif kind == "win":
            chs = sorted({int(x) for x in _WIN_CH.findall(prompt)})
            issues = []
            if len(chs) >= 2:
                c = chs[1]
                quote = "把玉佩放在桌上" if c == 2 else TEXTS[c][:6]
                if self.fake_quote:
                    quote = "这句话在原文中并不存在"
                issues.append({"category": "continuity", "severity": "中", "chapters": [chs[0], c],
                               "title": f"第{c}章开头承接不畅", "description": "桩：衔接存在疑点",
                               "evidence": [{"chapter": c, "quote": quote}]})
            data = {"issues": issues}
        elif kind == "profile":
            data = {"issues": list(self.profile_issues)}
        else:
            issues = []
            if self.whole_issue:
                issues.append({"category": "character_logic", "severity": "轻", "chapters": [1],
                               "title": "李凌云的动机变化缺少铺垫", "description": "桩：全书人物线",
                               "evidence": [{"chapter": 1, "quote": "李凌云握着玉佩走进山门"}]})
            data = {"issues": issues}
        out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def count(self, kind):
        return sum(1 for c in self.calls if c[0] == kind)


def _ready_with_candidates(tmp_path, n_candidates=1):
    """三章完稿，并注入 n 个「已死亡又出现」候选（R1 确定性检查产生，needs_adjudication=True）。"""
    ws = _novel(tmp_path)
    CFG.update(ws, {"review_dimensions": list(CFG.REVIEW_DEFAULT_DIMENSIONS) + ["rule_legacy"]})   # 死亡/地点规则默认关闭，这些用例需要它
    RC.generate_cards(ws, None, CardInvoker())
    names = ["甲", "乙", "丙"][:n_candidates]
    _inject(ws, 1, characters=chars(*[(nm, "战死沙场") for nm in names]))
    _inject(ws, 3, characters=chars(*[(nm, "在场") for nm in names]))
    return ws


# ─────────────────────────── 纯函数 ───────────────────────────

def test_windows_split_with_one_chapter_overlap():
    assert RL._windows([1, 2, 3, 4, 5], 5) == [[1, 2, 3, 4, 5]]
    assert RL._windows([1, 2, 3, 4, 5, 6, 7], 5) == [[1, 2, 3, 4, 5], [5, 6, 7]]
    assert RL._windows([1, 2, 3, 4], 2) == [[1, 2], [2, 3], [3, 4]]
    assert RL._windows([1], 5) == [] and RL._windows([], 5) == []


def test_parse_issues_strict_and_empty_allowed():
    ok = json.dumps({"issues": []})
    assert RL.parse_issues(ok, ("continuity",)) == []
    good = {"issues": [{"category": "continuity", "severity": "中", "chapters": [2], "title": "t", "description": "d",
                        "evidence": [{"chapter": 2, "quote": "q"}]}]}
    assert len(RL.parse_issues(json.dumps(good, ensure_ascii=False), ("continuity",))) == 1
    bad_cases = [
        {"issues": [{**good["issues"][0], "category": "pacing"}]},                # 不在允许范围
        {"issues": [{**good["issues"][0], "severity": "致命"}]},
        {"issues": [{**good["issues"][0], "title": ""}]},
        {"issues": [{**good["issues"][0], "evidence": []}]},                    # 没有证据
        {"issues": [{**good["issues"][0], "evidence": [{"chapter": "2", "quote": "q"}]}]},
        {"issues": "不是列表"},
        {"nothing": 1},
    ]
    for bad in bad_cases:
        with pytest.raises(FlowError):
            RL.parse_issues(json.dumps(bad, ensure_ascii=False), ("continuity",))
    with pytest.raises(FlowError):
        RL.parse_issues("不是json", ("continuity",))


def test_parse_verdict_requires_known_value_and_reason():
    assert RL.parse_verdict(json.dumps({"verdict": "false_positive", "reason": "回忆"}, ensure_ascii=False)) == \
        {"verdict": "false_positive", "reason": "回忆"}
    for bad in ({"verdict": "maybe", "reason": "x"}, {"verdict": "confirmed", "reason": "  "}, {"verdict": "confirmed"}):
        with pytest.raises(FlowError):
            RL.parse_verdict(json.dumps(bad, ensure_ascii=False))


def test_evidence_context_prefers_quote_then_term_then_head():
    text = "李凌云握着玉佩，走进山门。玉佩发烫。"
    assert RC.context_for({"quote": "玉佩发烫"}, text, 3) == "山门。玉佩发烫。"
    assert RC.context_for({"term": "山门"}, text, 2) == "走进山门。玉"
    assert RC.context_for({}, text, 2) == "李凌云握"


# ─────────────────────────── 端到端 ───────────────────────────

def test_full_run_adjudicates_reviews_windows_and_whole_book(tmp_path):
    ws = _ready_with_candidates(tmp_path, 1)
    stub = ReviewStub(verdicts={"甲": "false_positive"})
    idx_before = json.dumps(V.load_index(ws), ensure_ascii=False, sort_keys=True)
    res = RL.run_review(ws, None, stub)
    assert res["paused"] is False and res["failed"] == []
    assert stub.count("adj") == 1 and stub.count("win") == 1 and stub.count("whole") == 1
    assert res["calls"] == 3
    adj_f = next(f for f in res["findings"] if f["check"] == "departed_reappear")
    assert adj_f["adjudication"]["verdict"] == "false_positive"
    llm = [f for f in res["findings"] if f["source"] == "llm"]
    assert {f["check"] for f in llm} == {"window_review", "whole_review"}
    assert all(f["evidence_ok"] and f["status"] == "open" for f in llm)
    # 只读版本：版本索引与章节内容未变
    assert json.dumps(V.load_index(ws), ensure_ascii=False, sort_keys=True) == idx_before
    rpt = (ws.root / "_revision" / "review" / "rev_v001" / "report.md").read_text(encoding="utf-8")
    assert "模型仲裁：误报" in rpt and "待仲裁" in rpt


def test_unverifiable_quote_marks_finding_folded_not_dropped(tmp_path):
    ws = _ready_with_candidates(tmp_path, 1)
    res = RL.run_review(ws, None, ReviewStub(fake_quote=True, whole_issue=False))
    win = next(f for f in res["findings"] if f["check"] == "window_review")
    assert win["evidence_ok"] is False
    assert win["evidence"][0]["verified"] is False


def test_bad_json_is_retried_once_then_accepted(tmp_path):
    ws = _ready_with_candidates(tmp_path, 1)
    stub = ReviewStub(bad_json_first={"adj"})
    res = RL.run_review(ws, None, stub)
    assert stub.count("adj") == 2 and res["failed"] == []
    assert any(f.get("adjudication") for f in res["findings"])


def test_executor_exception_isolated_per_unit(tmp_path):
    ws = _ready_with_candidates(tmp_path, 1)
    stub = ReviewStub(raise_for={"win"})
    res = RL.run_review(ws, None, stub)
    assert [f["stage"] for f in res["failed"]] == ["window"]
    assert stub.count("whole") == 1                      # 窗口失败不影响全书审阅
    assert any(f["check"] == "whole_review" for f in res["findings"])


def test_call_limit_pauses_and_resume_continues_from_cache(tmp_path):
    ws = _ready_with_candidates(tmp_path, 2)              # 两个候选 → 两次仲裁
    CFG.update(ws, {"review_max_llm_calls": 2})
    stub = ReviewStub()
    first = RL.run_review(ws, None, stub)
    assert first["paused"] is True and first["calls"] == 2
    assert stub.count("adj") == 2 and stub.count("win") == 0 and stub.count("whole") == 0
    second = RL.run_review(ws, None, stub)               # 续跑：仲裁全部命中缓存，只做窗口与全书
    assert second["paused"] is False and second["calls"] == 2
    assert stub.count("adj") == 2                        # 没有重复仲裁
    assert stub.count("win") == 1 and stub.count("whole") == 1


def test_cache_is_reused_then_invalidated_when_chapter_changes(tmp_path):
    ws = _ready_with_candidates(tmp_path, 1)
    stub = ReviewStub()
    RL.run_review(ws, None, stub)
    again = RL.run_review(ws, None, stub)                # 什么都没变：零调用
    assert again["calls"] == 0 and stub.count("adj") == 1 and stub.count("win") == 1 and stub.count("whole") == 1
    # 模拟第 2 章正文改动（真实流程里是新版本）→ 其事实卡重生成 → 包含它的窗口与全书判定失效；与之无关的仲裁仍然有效
    ch2 = next(s for s in RC.list_chapter_sources(ws, "v001") if s["number"] == 2)
    ch2["path"].write_text("# 第2章 标题2\n\n新的第二章内容，玉佩换了地方。\n", encoding="utf-8")
    RC.generate_cards(ws, None, CardInvoker())
    _inject(ws, 1, characters=chars(("甲", "战死沙场")))
    _inject(ws, 3, characters=chars(("甲", "在场")))
    third = RL.run_review(ws, None, stub)
    assert third["calls"] == 2 and stub.count("win") == 2 and stub.count("whole") == 2
    assert stub.count("adj") == 1
    state = load_json(RK.review_dir(ws, "v001") / "llm_state.json")
    assert len(state["windows"]) == 1 and len(state["whole"]) == 1   # 旧键已清理，只留当前有效的


def test_adjudications_pruned_when_candidate_disappears(tmp_path):
    ws = _ready_with_candidates(tmp_path, 1)
    RL.run_review(ws, None, ReviewStub())
    state_file = RK.review_dir(ws, "v001") / "llm_state.json"
    assert load_json(state_file)["adjudications"]
    CFG.update(ws, {"review_dimensions": ["foreshadow"]})   # 关掉矛盾维度：候选消失，窗口与全书也不再运行
    res = RL.run_review(ws, None, ReviewStub())
    assert load_json(state_file)["adjudications"] == {}
    assert not any(f["source"] == "llm" for f in res["findings"])


def test_status_survives_rerun_for_llm_findings(tmp_path):
    ws = _ready_with_candidates(tmp_path, 1)
    res = RL.run_review(ws, None, ReviewStub(whole_issue=False))
    win = next(f for f in res["findings"] if f["check"] == "window_review")
    RK.set_status(ws, "v001", win["id"], "rejected", "作者判断：是有意的转场")
    again = RL.run_review(ws, None, ReviewStub(whole_issue=False))
    kept = next(f for f in again["findings"] if f["id"] == win["id"])
    assert kept["status"] == "rejected" and kept["notes"] == "作者判断：是有意的转场"


def test_dimension_switch_controls_stages(tmp_path):
    ws = _ready_with_candidates(tmp_path, 1)
    CFG.update(ws, {"review_dimensions": ["foreshadow"]})
    stub = ReviewStub()
    res = RL.run_review(ws, None, stub)
    assert stub.count("win") == 0 and stub.count("whole") == 0 and stub.count("adj") == 0
    assert res["calls"] == 0


def test_requires_cards_and_invoker(tmp_path):
    ws = _novel(tmp_path)
    with pytest.raises(FlowError, match="事实卡"):
        RL.run_review(ws, None, ReviewStub())
    ws2 = _ready_with_candidates(tmp_path / "b", 1)
    with pytest.raises(FlowError, match="执行器"):
        RL.run_review(ws2, None, None)


def test_estimate_llm_counts_without_calling_model(tmp_path):
    ws = _ready_with_candidates(tmp_path, 2)
    est = RL.estimate_llm(ws)
    assert est["checks_run"] is False                    # 还没运行 review-checks：候选数未知
    RK.run_checks(ws)
    est = RL.estimate_llm(ws)
    assert est["checks_run"] is True and est["adjudication_pending"] == 2
    assert est["windows"] == 1 and est["whole_book"] == 1 and est["calls_typical"] == 4
    assert est["max_llm_calls"] is None                  # 0 = 不限


# ─────────────────────────── 界面冒烟（AppTest，不调用 LLM） ───────────────────────────

APP = str(Path(__file__).parent / "apps" / "post_revision_app.py")


def _render(ws, monkeypatch):
    monkeypatch.setenv("RV_WS", str(ws.root))
    from streamlit.testing.v1 import AppTest
    return AppTest.from_file(APP, default_timeout=30).run()


def test_review_tab_renders_and_folds_unverified(tmp_path, monkeypatch):
    ws = _ready_with_candidates(tmp_path, 1)
    RL.run_review(ws, None, ReviewStub(fake_quote=True, whole_issue=False))
    at = _render(ws, monkeypatch)
    assert not at.exception
    assert len(at.tabs) == 7 and any("评估" in t.label for t in at.tabs)
    captions = [c.value for c in at.caption]
    assert any("折叠 1 条" in c for c in captions)            # 证据未核实的模型问题默认折叠
    assert any("问题池（版本 v001）" in m.value for m in at.markdown)


def test_review_tab_without_findings_shows_hint(tmp_path, monkeypatch):
    ws = _novel(tmp_path)
    at = _render(ws, monkeypatch)
    assert not at.exception
    assert any("还没有问题记录" in i.value for i in at.info)
