"""R3 采纳与增量复评的测试：全部使用桩执行器，不调用真实 LLM。

端到端：评估 → 采纳（矛盾须选方向、证据未核实需明确允许）→ 现有预览 → 确认 → 执行改写（新版本）
→ 标记 applied → 增量复评（已解决 / 仍存在 / 新出现）。原稿始终不被触碰。
"""

import json
import re
from pathlib import Path

import pytest

from novel_writer.revision_flow import changes as CH
from novel_writer.revision_flow import config as CFG
from novel_writer.revision_flow import executor
from novel_writer.revision_flow import review_adopt as RA
from novel_writer.revision_flow import review_cards as RC
from novel_writer.revision_flow import review_checks as RK
from novel_writer.revision_flow import review_llm as RL
from novel_writer.revision_flow import versions as V
from novel_writer.revision_flow.common import FlowError, load_json

from test_revision_review_cards import CardInvoker, _novel, isolated  # noqa: F401
from test_revision_review_checks import chars, _inject  # noqa: F401
from test_revision_review_llm import ReviewStub

_ORIG = re.compile(r"【原文路径】(.+?)（")
REPLACE_FROM, REPLACE_TO = "玉佩", "银钥匙"


class Router:
    """按提示分流：改写 → 替换词；事实卡 → CardInvoker；其余（仲裁/衔接/全书）→ ReviewStub。"""

    def __init__(self, review: ReviewStub):
        self.review = review
        self.cards = CardInvoker()
        self.rewrites = []          # (文件名, 提示全文)

    def __call__(self, *, skill, prompt, export_dir, step_id):
        if "【原文路径】" in prompt:
            src = Path(_ORIG.search(prompt).group(1).strip())
            self.rewrites.append((src.name, prompt))
            text = src.read_text(encoding="utf-8").replace(REPLACE_FROM, REPLACE_TO)
            Path(export_dir).mkdir(parents=True, exist_ok=True)
            (Path(export_dir) / src.name).write_text(text, encoding="utf-8")
            return
        if "提取一张结构化的" in prompt:                     # 事实卡提取（窗口/全书提示里也会出现“事实卡”一词，不能用它分流）
            return self.cards(skill=skill, prompt=prompt, export_dir=export_dir, step_id=step_id)
        return self.review(skill=skill, prompt=prompt, export_dir=export_dir, step_id=step_id)


def _ready(tmp_path):
    """三章完稿；甲在第 1 章战死、第 3 章又在场 → 一条矛盾候选；第 2 章与第 1 章的窗口审阅会给出一条衔接问题。"""
    ws = _novel(tmp_path)
    CFG.update(ws, {"review_dimensions": list(CFG.REVIEW_DEFAULT_DIMENSIONS) + ["rule_legacy"]})   # 死亡/地点规则默认关闭，这些用例需要它
    RC.generate_cards(ws, None, CardInvoker())
    _inject(ws, 1, characters=chars(("甲", "战死沙场")))
    _inject(ws, 3, characters=chars(("甲", "在场")))
    return ws


def _review(ws, **stub_kw):
    stub = ReviewStub(whole_issue=stub_kw.pop("whole_issue", False), **stub_kw)
    RL.run_review(ws, None, stub)
    return stub


def _pool(ws, vid="v001"):
    return RK.load_findings(ws, vid)


def _by(pool, check):
    return next(f for f in pool["findings"] if f["check"] == check)


def _text(ws, vid, n):
    return next(c for c in RC.list_chapter_sources(ws, vid) if c["number"] == n)["path"].read_text(encoding="utf-8")


# ─────────────────────────── 选项与采纳门禁 ───────────────────────────

def test_contradiction_offers_two_directions_and_requires_choice(tmp_path):
    ws = _ready(tmp_path)
    _review(ws)
    pool = _pool(ws)
    dep = _by(pool, "departed_reappear")
    opts = RA.options(dep)
    assert len(opts) == 2
    assert opts[0]["target_chapters"] == [3] and opts[1]["target_chapters"] == [1]   # 以 1 为准改 3；以 3 为准改 1
    with pytest.raises(FlowError, match="选定"):
        RA.adopt(ws, None, dep["id"])
    assert _pool(ws)["findings"] and _by(_pool(ws), "departed_reappear")["status"] == "open"   # 失败不改状态


def test_adopt_creates_chapter_change_with_evidence_and_origin(tmp_path):
    ws = _ready(tmp_path)
    _review(ws)
    dep = _by(_pool(ws), "departed_reappear")
    ch = RA.adopt(ws, None, dep["id"], option_index=0)
    assert ch["kind"] == "chapter" and ch["chapters"] == [3] and ch["status"] == "draft"
    assert ch["origin"]["finding_id"] == dep["id"] and ch["origin"]["review_id"] == "rev_v001"
    assert "以第1章为准，改第3章" in ch["origin"]["option"]
    assert "以第1章的描述为准" in ch["instruction"]
    assert _by(_pool(ws), "departed_reappear")["status"] == "adopted"
    with pytest.raises(FlowError, match="不能采纳"):
        RA.adopt(ws, None, dep["id"], option_index=0)               # 已采纳的问题不能重复采纳


def test_unverified_llm_finding_refused_unless_explicitly_allowed(tmp_path):
    ws = _ready(tmp_path)
    _review(ws, fake_quote=True)
    win = _by(_pool(ws), "window_review")
    assert win["evidence_ok"] is False
    with pytest.raises(FlowError, match="证据未核实"):
        RA.adopt(ws, None, win["id"])
    ch = RA.adopt(ws, None, win["id"], allow_unverified=True, target_chapters=[2])
    assert ch["chapters"] == [2]


def test_batch_skips_contradiction_without_choice_and_unverified(tmp_path):
    ws = _ready(tmp_path)
    _review(ws, fake_quote=True, whole_issue=True)
    pool = _pool(ws)
    dep, win = _by(pool, "departed_reappear"), _by(pool, "window_review")
    whole = _by(pool, "whole_review")
    res = RA.adopt_batch(ws, None, [dep["id"], win["id"], whole["id"]])
    assert len(res["created"]) == 1                              # 只有证据核实的 whole 可直接采纳
    reasons = {s["id"]: s["reason"] for s in res["skipped"]}
    assert "选定" in reasons[dep["id"]] and "证据未核实" in reasons[win["id"]]
    res2 = RA.adopt_batch(ws, None, [dep["id"]], choices={dep["id"]: 1})
    assert len(res2["created"]) == 1


def test_adopt_refuses_false_positive_verdict(tmp_path):
    ws = _ready(tmp_path)
    _review(ws, verdicts={"甲": "false_positive"})
    dep = _by(_pool(ws), "departed_reappear")
    with pytest.raises(FlowError, match="误报"):
        RA.adopt(ws, None, dep["id"], option_index=0)


# ─────────────────────────── 端到端：采纳 → 预览 → 执行 → 增量复评 ───────────────────────────

def _adopt_and_apply(ws, invoker):
    dep = _by(_pool(ws), "departed_reappear")
    win = _by(_pool(ws), "window_review")
    c1 = RA.adopt(ws, None, dep["id"], option_index=0)                    # 改第 3 章
    c2 = RA.adopt(ws, None, win["id"], allow_unverified=not win["evidence_ok"], target_chapters=[2])
    pv = CH.build_preview(ws, "v001", [c1["id"], c2["id"]])
    assert pv["chapters"] and [r["number"] for r in pv["chapters"]] == [2, 3]
    CH.confirm_preview(ws, pv["id"])
    run = executor.start(ws, pv["id"], invoker)
    assert run["status"] == "done" and run["target_version"] == "v002"
    return c1, c2, pv


def test_adopted_changes_flow_through_existing_preview_and_run(tmp_path):
    ws = _ready(tmp_path)
    _review(ws, fake_quote=True)
    inv = Router(ReviewStub(whole_issue=False))
    c1, c2, pv = _adopt_and_apply(ws, inv)
    assert _text(ws, "v002", 3).count(REPLACE_TO) == 1 and REPLACE_FROM not in _text(ws, "v002", 3)
    assert REPLACE_FROM in _text(ws, "v002", 1)                          # 未命中的章节不改
    assert REPLACE_FROM in _text(ws, "v001", 3)                          # 原稿不被触碰
    assert V.verify(ws, "v002") == [] and V.load_index(ws)["head"] == "v002"
    # 证据定位与选定方向被写入改写提示
    prompt3 = next(p for n, p in inv.rewrites if n.startswith("第03章"))
    assert "以第1章的描述为准" in prompt3                                 # 选定的方向进入改写提示
    assert CH.load_change(ws, c1["id"])["status"] == "applied"


def test_sync_applied_marks_findings_applied(tmp_path):
    ws = _ready(tmp_path)
    _review(ws, fake_quote=True)
    _adopt_and_apply(ws, Router(ReviewStub(whole_issue=False)))
    updated = RA.sync_applied(ws)
    dep = _by(_pool(ws), "departed_reappear")
    assert dep["id"] in updated and dep["status"] == "applied" and dep["applied_version"] == "v002"


def test_increment_resolves_gone_keeps_still_and_marks_new(tmp_path):
    ws = _ready(tmp_path)
    _review(ws, fake_quote=True)
    _adopt_and_apply(ws, Router(ReviewStub(whole_issue=False)))
    RA.sync_applied(ws)
    inv = Router(ReviewStub(whole_issue=True))                           # 复评时全书审阅会新发现一条
    summary = RA.increment(ws, inv)
    assert summary["parent"] == "v001" and summary["version"] == "v002"
    dep_id = _by(_pool(ws, "v001"), "departed_reappear")["id"]
    assert dep_id in summary["resolved"]                                 # 第 3 章已不再出现甲：已解决
    assert _by(_pool(ws, "v001"), "departed_reappear")["status"] == "resolved"
    win_id = _by(_pool(ws, "v001"), "window_review")["id"]
    assert win_id in summary["still_open"]                               # 仍存在（同一编号，证据仍需核实）
    new_pool = _pool(ws, "v002")
    assert _by(new_pool, "whole_review")["id"] in summary["new"]
    assert _by(new_pool, "whole_review")["new_since"] == "v001"


def test_increment_reuses_adjudication_for_unchanged_chapters(tmp_path):
    """只改第 2 章（窗口问题），甲的候选涉及第 1、3 章，它们未改动 → 仲裁结论沿用，不重新调用模型。"""
    ws = _ready(tmp_path)
    _review(ws, verdicts={"甲": "uncertain"}, fake_quote=True)
    win = _by(_pool(ws), "window_review")
    c = RA.adopt(ws, None, win["id"], allow_unverified=True, target_chapters=[2])
    pv = CH.build_preview(ws, "v001", [c["id"]])
    CH.confirm_preview(ws, pv["id"])
    executor.start(ws, pv["id"], Router(ReviewStub()))
    RA.sync_applied(ws)
    stub = ReviewStub(whole_issue=False, verdicts={"甲": "uncertain"})
    summary = RA.increment(ws, Router(stub))
    assert stub.count("adj") == 0
    assert _by(_pool(ws, "v002"), "departed_reappear")["adjudication"]["verdict"] == "uncertain"


def test_rejected_status_carries_into_new_version(tmp_path):
    ws = _ready(tmp_path)
    _review(ws, whole_issue=True, fake_quote=True)
    whole = _by(_pool(ws), "whole_review")
    RK.set_status(ws, "v001", whole["id"], "rejected", "作者：这是有意的伏笔")
    dep = _by(_pool(ws), "departed_reappear")
    RA.adopt(ws, None, dep["id"], option_index=0)
    pv = CH.build_preview(ws, "v001", [CH.list_changes(ws)[0]["id"]])
    CH.confirm_preview(ws, pv["id"])
    executor.start(ws, pv["id"], Router(ReviewStub(whole_issue=True)))
    RA.sync_applied(ws)
    RA.increment(ws, Router(ReviewStub(whole_issue=True)))
    kept = next(f for f in _pool(ws, "v002")["findings"] if f["id"] == whole["id"])
    assert kept["status"] == "rejected" and kept["notes"] == "作者：这是有意的伏笔"


def test_increment_requires_parent_with_findings(tmp_path):
    ws = _ready(tmp_path)
    with pytest.raises(FlowError, match="没有评估记录|父版本"):
        RA.increment(ws, Router(ReviewStub()))
    _review(ws)
    _adopt_and_apply(ws, Router(ReviewStub(whole_issue=False)))
    RA.sync_applied(ws)
    # 旧版本的评估存在，增量复评可运行
    RA.increment(ws, Router(ReviewStub(whole_issue=False)))


# ─────────────────────────── 界面冒烟 ───────────────────────────

def test_review_tab_shows_adoption_and_increment_controls(tmp_path, monkeypatch):
    from test_revision_review_llm import _render
    ws = _ready(tmp_path)
    _review(ws)
    at = _render(ws, monkeypatch)
    assert not at.exception
    assert any("增量复评" in m.value for m in at.markdown)
    assert any("采纳方式" in s.label for s in at.selectbox)
