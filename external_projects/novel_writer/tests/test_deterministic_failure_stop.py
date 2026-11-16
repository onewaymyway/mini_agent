"""不停机模式：确定性失败（no_retry）不再被反复重置；普通失败仍照常重置。"""
from types import SimpleNamespace

from novel_writer import steps as S
from novel_writer import worker as W


def test_no_retry_failure_is_deterministic(monkeypatch):
    target = SimpleNamespace(id="brief_draft:1")
    monkeypatch.setattr(W.st, "get_step_state", lambda ws, sid: {"no_retry": True, "error": "人物档案未同步"})
    assert W._deterministic_failure(None, target) is True


def test_ordinary_failure_still_resets(monkeypatch):
    target = SimpleNamespace(id="brief_draft:1")
    monkeypatch.setattr(W.st, "get_step_state", lambda ws, sid: {"error": "超时"})
    assert W._deterministic_failure(None, target) is False
