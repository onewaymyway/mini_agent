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


def test_upstream_repair_reruns_prerequisite_then_original(monkeypatch):
    """根因在上游：上游设为待重跑并请求运行，本步骤设回待执行（不停下来）。"""
    up_step, down = SimpleNamespace(id="build_characters"), SimpleNamespace(id="brief_draft:1")
    states = {"brief_draft:1": {"no_retry": True, "upstream_step": "build_characters"},
              "build_characters": {}}
    updates, controls = [], []
    monkeypatch.setattr(W.st, "get_step_state", lambda ws, sid: dict(states.get(sid, {})))
    monkeypatch.setattr(W.st, "update_step", lambda ws, sid, **kw: updates.append((sid, kw)))
    monkeypatch.setattr(W.st, "write_control", lambda ws, **kw: controls.append(kw))
    monkeypatch.setattr(W.st, "log_line", lambda ws, text: None)
    index = {"build_characters": up_step, "brief_draft:1": down}
    assert W._schedule_upstream_repair(None, down, index) is True
    assert controls == [{"run_step": "build_characters"}]
    assert any(sid == "build_characters" and kw.get("status") == W.st.ST_PENDING for sid, kw in updates)
    assert any(sid == "brief_draft:1" and kw.get("no_retry") is None for sid, kw in updates)


def test_upstream_repair_is_capped(monkeypatch):
    down = SimpleNamespace(id="brief_draft:1")
    states = {"brief_draft:1": {"upstream_step": "build_characters"},
              "build_characters": {"upstream_repairs": W.MAX_UPSTREAM_REPAIRS}}
    monkeypatch.setattr(W.st, "get_step_state", lambda ws, sid: dict(states.get(sid, {})))
    monkeypatch.setattr(W.st, "log_line", lambda ws, text: None)
    index = {"build_characters": SimpleNamespace(id="build_characters"), "brief_draft:1": down}
    assert W._schedule_upstream_repair(None, down, index) is False
