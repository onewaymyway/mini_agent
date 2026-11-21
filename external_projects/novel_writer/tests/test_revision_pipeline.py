"""评估全流程的自动化测试：步骤自动衔接、失败自动重试、单步失败不阻断后续、事实卡失败停止、
自动修改（采纳 → 预览 → 改写 → 增量复评，直到没有可处理的问题）、矛盾类永不自动采纳。全部使用桩执行器。"""

import re
from pathlib import Path

import pytest

from novel_writer.revision_flow import config as CFG
from novel_writer.revision_flow import review_llm as RL
from novel_writer.revision_flow import review_pipeline as PL
from novel_writer.revision_flow import review_checks as RK
from novel_writer.revision_flow import versions as V
from novel_writer.revision_flow import changes as CH

from test_revision_review_adopt import _ready, Router, REPLACE_FROM, REPLACE_TO  # noqa: F401
from test_revision_review_llm import ReviewStub, _OUT
from test_revision_review_cards import _novel, isolated  # noqa: F401


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(RL, "RETRY_BACKOFF_SECONDS", 0)


class Chaos(ReviewStub):
    """在 ReviewStub 之上，对指定任务的前 n 次调用抛异常（或永远抛）。"""

    def __init__(self, *, fail_kind=None, fail_times=None, fail_cards=False, **kw):
        super().__init__(**kw)
        self.fail_kind = fail_kind
        self.fail_times = fail_times
        self.fail_cards = fail_cards
        self.seen = {}

    def __call__(self, *, skill, prompt, export_dir, step_id):
        if self.fail_cards and "提取一张结构化的" in prompt:
            raise RuntimeError("模拟事实卡执行器故障")
        kind = self.kind_of(prompt)
        if kind == self.fail_kind:
            self.seen[kind] = self.seen.get(kind, 0) + 1
            if self.fail_times is None or self.seen[kind] <= self.fail_times:
                raise RuntimeError(f"模拟 {kind} 故障")
        return super().__call__(skill=skill, prompt=prompt, export_dir=export_dir, step_id=step_id)


class ReviewAndPreview(Router):
    """Router 加上预览的语义判定：所有章节都判定为涉及。"""

    def __call__(self, *, skill, prompt, export_dir, step_id):
        if "判断它是否真的涉及下列变更" in prompt:
            out = Path(_OUT.search(prompt).group(1).strip())
            out.write_text('{"affected": true, "reason": "桩：涉及"}', encoding="utf-8")
            return
        return super().__call__(skill=skill, prompt=prompt, export_dir=export_dir, step_id=step_id)


def _settings(ws, **patch):
    CFG.update(ws, {"auto_retry_backoff_seconds": 0, **patch})


def test_all_steps_chain_without_user_and_pending_is_reported(tmp_path):
    ws = _ready(tmp_path)
    state = PL.run(ws, ReviewStub(whole_issue=False), auto_fix=False)
    assert list(state["steps"]) == list(PL.STEPS)
    assert all(v["ok"] for v in state["steps"].values())
    assert state["pending_adoptable"] == 1                                # 窗口问题可采纳，但未开启自动修改
    assert "自动修改关闭" in state["stopped_reason"]
    assert CH.list_changes(ws) == []                                      # 没有擅自采纳
    assert V.load_index(ws)["head"] == "v001"
    assert PL.load_state(ws)["stopped_reason"] == state["stopped_reason"]  # 状态已落盘


def test_step_retries_after_unit_failures_and_recovers(tmp_path):
    ws = _ready(tmp_path)
    # 前 3 次「复核-衔接」调用全部失败：单元内部用完 3 次 → 步骤失败 → 步骤自动重试 → 第二次成功
    stub = Chaos(fail_kind="win", fail_times=3, whole_issue=False)
    state = PL.run(ws, stub, auto_fix=False)
    assert state["steps"]["review"]["ok"] is True
    assert stub.seen["win"] == 4                                          # 第 4 次起成功


def test_failed_step_does_not_block_later_steps(tmp_path):
    ws = _ready(tmp_path)
    state = PL.run(ws, Chaos(fail_kind="win", fail_times=None, whole_issue=False), auto_fix=False)
    assert state["steps"]["review"]["ok"] is False
    assert "outline" in state["steps"] and "export" in state["steps"]     # 后续步骤照常执行
    assert state["steps"]["export"]["ok"] is True
    assert "事实卡未齐全" not in state["stopped_reason"]


def test_cards_failure_stops_pipeline(tmp_path):
    ws = _novel(tmp_path, total=3, baseline=True)                          # 没有任何事实卡，事实卡步骤必须执行并失败
    state = PL.run(ws, Chaos(fail_cards=True), auto_fix=False)
    assert list(state["steps"]) == ["cards"] and state["steps"]["cards"]["ok"] is False
    assert "事实卡" in state["stopped_reason"]


def test_auto_fix_adopts_rewrites_and_increments_until_nothing_left(tmp_path):
    ws = _ready(tmp_path)
    inv = ReviewAndPreview(ReviewStub(whole_issue=False))
    state = PL.run(ws, inv, auto_fix=True)
    assert state["auto_fix"] is True
    assert len(state["rounds"]) == 1                                      # 第二轮没有可处理的问题，结束
    r = state["rounds"][0]
    assert r["run"]["status"] == "done" and r["increment"] is not None
    assert state["version_end"] == "v002" and V.load_index(ws)["head"] == "v002"
    t2 = next(c for c in RC_sources(ws, "v002") if c["number"] == 2)["path"].read_text(encoding="utf-8")
    t1 = next(c for c in RC_sources(ws, "v002") if c["number"] == 1)["path"].read_text(encoding="utf-8")
    assert REPLACE_TO in t2 and REPLACE_FROM not in t2                    # 被采纳的章节已改写
    assert REPLACE_FROM in t1                                             # 未涉及的章节不动
    orig = next(c for c in RC_sources(ws, "v001") if c["number"] == 2)["path"].read_text(encoding="utf-8")
    assert REPLACE_FROM in orig                                           # 原稿不动
    assert "没有可自动处理的问题" in state["stopped_reason"] or "轮数上限" in state["stopped_reason"]


def test_contradictions_are_never_auto_adopted(tmp_path):
    ws = _ready(tmp_path)
    PL.run(ws, ReviewAndPreview(ReviewStub(whole_issue=False)), auto_fix=True)
    dep = next(f for f in RK.load_findings(ws, "v001")["findings"] if f["check"] == "departed_reappear")
    assert dep["status"] == "open" and not dep.get("change_id")
    assert all(not f.get("change_id") for f in RK.load_findings(ws, "v001")["findings"] if f["category"] == "contradiction")


def test_auto_fix_respects_min_severity(tmp_path):
    ws = _ready(tmp_path)
    _settings(ws, auto_fix_min_severity="严重")                             # 窗口问题是「中」，低于门槛
    state = PL.run(ws, ReviewAndPreview(ReviewStub(whole_issue=False)), auto_fix=True)
    assert state["rounds"] == [] and V.load_index(ws)["head"] == "v001"


def test_retry_settings_are_bounded():
    from novel_writer.revision_flow.config import _BOUNDS
    assert _BOUNDS["auto_retry_attempts"][:2] == (1, 10)
    assert "auto_fix_enabled" in CFG.DEFAULTS and CFG.DEFAULTS["auto_fix_enabled"] is False


def RC_sources(ws, vid):
    from novel_writer.revision_flow import review_cards as RC
    return RC.list_chapter_sources(ws, vid)


def test_pipeline_button_and_status_render(tmp_path, monkeypatch):
    from test_revision_review_llm import _render
    ws = _ready(tmp_path)
    PL.run(ws, ReviewStub(whole_issue=False), auto_fix=False)
    at = _render(ws, monkeypatch)
    assert not at.exception
    assert any(b.label == "一键评估全流程" for b in at.button)
    assert any("最近一次全流程" in e.label for e in at.expander)
