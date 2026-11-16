"""不停机 / 托管：确定性失败的上游自动修复。上游在本次运行中直接重跑；修复次数按运行计算；上游失败则停止并说明。"""
from types import SimpleNamespace

from novel_writer import worker as W


class States:
    """可替换的步骤状态存根。"""
    def __init__(self, data):
        self.data, self.updates, self.logs = data, [], []

    def get(self, ws, sid):
        return dict(self.data.get(sid, {}))

    def update(self, ws, sid, **kw):
        self.updates.append((sid, kw))
        self.data.setdefault(sid, {}).update(kw)

    def log(self, ws, text):
        self.logs.append(text)


def _patch(monkeypatch, states, run_status=W.st.ST_PASSED):
    ran = []
    monkeypatch.setattr(W.st, "get_step_state", states.get)
    monkeypatch.setattr(W.st, "update_step", states.update)
    monkeypatch.setattr(W.st, "log_line", states.log)
    monkeypatch.setattr(W.R, "run_step", lambda ws, step, **kw: ran.append(step.id) or run_status)
    return ran


def _call(down, index, token="run-1", executed=None):
    return W._schedule_upstream_repair(None, down, index, invoker=None, settings={}, handlers=None,
                                       executed=executed if executed is not None else [], run_token=token)


def _steps():
    return (SimpleNamespace(id="characters_complete", title="门禁"), SimpleNamespace(id="build_characters", title="建档"))


def test_no_retry_failure_is_deterministic(monkeypatch):
    monkeypatch.setattr(W.st, "get_step_state", lambda ws, sid: {"no_retry": True, "error": "x"})
    assert W._deterministic_failure(None, SimpleNamespace(id="b")) is True


def test_ordinary_failure_still_resets(monkeypatch):
    monkeypatch.setattr(W.st, "get_step_state", lambda ws, sid: {"error": "超时"})
    assert W._deterministic_failure(None, SimpleNamespace(id="b")) is False


def test_text_marker_alone_counts_as_upstream_blocked(monkeypatch):
    monkeypatch.setattr(W.st, "get_step_state", lambda ws, sid: {"error": "人物档案未同步：缺少：母亲"})
    assert W._deterministic_failure(None, SimpleNamespace(id="characters_complete")) is True


def test_upstream_is_rerun_directly_then_downstream_is_pending(monkeypatch):
    down, up = _steps()
    states = States({"characters_complete": {"no_retry": True, "upstream_step": "build_characters"}})
    ran = _patch(monkeypatch, states)
    executed = []
    assert _call(down, {"build_characters": up, "characters_complete": down}, executed=executed) is True
    assert ran == ["build_characters"]                                  # 直接执行上游，不靠控制指令
    assert executed and executed[0]["step"] == "build_characters"        # 计入本次执行记录
    assert any(sid == "characters_complete" and kw.get("status") == W.st.ST_PENDING for sid, kw in states.updates)


def test_failing_upstream_stops_with_explanation(monkeypatch):
    down, up = _steps()
    states = States({"characters_complete": {"upstream_step": "build_characters"}})
    _patch(monkeypatch, states, run_status=W.st.ST_FAILED)
    assert _call(down, {"build_characters": up, "characters_complete": down}) is False
    assert any("重跑后仍未通过" in t for t in states.logs)


def test_repair_budget_is_per_run_and_capped_within_a_run(monkeypatch):
    down, up = _steps()
    index = {"build_characters": up, "characters_complete": down}
    states = States({"characters_complete": {"upstream_step": "build_characters"},
                     "build_characters": {"upstream_repairs": W.MAX_UPSTREAM_REPAIRS, "upstream_run": "run-1"}})
    _patch(monkeypatch, states)
    assert _call(down, index, token="run-1") is False                   # 同一次运行：已用完
    assert _call(down, index, token="run-2") is True                    # 新的一次运行：重新计算


def test_text_fallback_triggers_direct_upstream_rerun(monkeypatch):
    down, up = _steps()
    states = States({"characters_complete": {"error": "人物档案未同步：缺少：母亲"}})
    ran = _patch(monkeypatch, states)
    assert _call(down, {"build_characters": up, "characters_complete": down}) is True
    assert ran == ["build_characters"]


def test_upstream_already_passed_after_downstream_failure_is_not_rerun(monkeypatch):
    """上游在下游失败之后已经通过：不重跑上游，只让下游重新执行（日志里出现的「多跑一次建档」）。"""
    up = SimpleNamespace(id="build_characters", title="建档")
    down = SimpleNamespace(id="brief_draft:1", title="简述")
    states = States({"brief_draft:1": {"error": "人物档案未同步", "updated_at": "2026-11-16T21:00:00"},
                     "build_characters": {"status": "passed", "updated_at": "2026-11-16T21:27:14"}})
    ran = _patch(monkeypatch, states)
    assert _call(down, {"build_characters": up, "brief_draft:1": down}) is True
    assert ran == []
    assert any(sid == "brief_draft:1" and kw.get("status") == W.st.ST_PENDING for sid, kw in states.updates)
