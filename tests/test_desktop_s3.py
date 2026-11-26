"""tests/test_desktop_s3.py

覆盖 next_doc/windows_desktop_resident_plan.md S3（Toast 与审批）：

后端（src/mini_agent）：
- 权限审批 / 交互提问登记时发布 attention（之前只有汇报与 cron 提问会发）
- 仅用户发起的 turn 完成/失败发布 attention；cron 发起的静默
- 无监听者时发布是空操作

桌面端（apps/mini_agent_desktop）：
- QuietHours 解析与跨午夜判断
- ToastPolicy：范围、去重、免打扰（阻塞类不受影响）、快速面板 session 静默
- collect_pending / answer_item：请求形态与各类型的回答体
- DesktopController：启动后已有崩溃告警不提醒，之后增长才提醒
- PendingDialog 离屏渲染冒烟（PySide6 不可用则跳过）

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_desktop_s3.py -q
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "mini_agent_desktop"))

from mini_agent.api import bridge as bridge_mod  # noqa: E402
from mini_agent.notification import attention_hub  # noqa: E402

from mini_agent_desktop.api import DaemonApi  # noqa: E402
from mini_agent_desktop.attention import (  # noqa: E402
    PendingItem, QuietHours, ToastPolicy, answer_item, collect_pending,
)
from mini_agent_desktop.controller import DesktopController  # noqa: E402
from mini_agent_desktop.quick import QuickSession  # noqa: E402
from mini_agent_desktop.state import REASON_LOST, TrayModel  # noqa: E402


# ════════════════════════════════════════════════════════════════════
# 后端：attention 发布
# ════════════════════════════════════════════════════════════════════

class RecordingBroadcaster:
    def __init__(self):
        self.events = []

    def push(self, ev):
        self.events.append(ev)


@pytest.fixture
def attention_listener():
    got = []
    attention_hub.register_listener(got.append)
    yield got
    attention_hub.unregister_listener(got.append)


def test_permission_registration_publishes_blocking_attention(attention_listener):
    gate = bridge_mod.HttpPermissionGate(RecordingBroadcaster())
    gate.register_pending("req-1", "bash", {"command": "ls"}, "turn-1")
    assert len(attention_listener) == 1
    ev = attention_listener[0]
    assert ev["kind"] == attention_hub.KIND_PERMISSION
    assert ev["ref_id"] == "req-1"
    assert ev["level"] == attention_hub.LEVEL_BLOCKING
    assert ev["extra"]["tool_name"] == "bash"
    assert "bash" in ev["title"]


def test_interaction_registration_publishes_question_as_title(attention_listener):
    gate = bridge_mod.HttpInteractionGate(RecordingBroadcaster())
    gate.register_pending("int-1", "ask_user", {"question": "选哪个分支？", "hint": ""}, "turn-2")
    ev = attention_listener[-1]
    assert ev["kind"] == attention_hub.KIND_INTERACTION
    assert ev["ref_id"] == "int-1"
    assert ev["title"] == "选哪个分支？"
    assert ev["extra"]["interaction_kind"] == "ask_user"


def test_no_listener_is_noop_and_registration_still_works():
    assert attention_hub.listener_count() == 0
    gate = bridge_mod.HttpPermissionGate(RecordingBroadcaster())
    pending = gate.register_pending("req-x", "bash", {}, "t")
    assert pending is not None
    assert gate.list_pending()[0]["req_id"] == "req-x"


def test_broken_listener_does_not_break_registration(attention_listener):
    def boom(_ev):
        raise RuntimeError("listener exploded")

    attention_hub.register_listener(boom)
    try:
        gate = bridge_mod.HttpPermissionGate(RecordingBroadcaster())
        gate.register_pending("req-b", "bash", {}, "t")
        assert gate.list_pending()[0]["req_id"] == "req-b"
    finally:
        attention_hub.unregister_listener(boom)


class _FakeBridgeForOutcome:
    """只提供 _publish_user_turn_outcome 需要的属性。"""

    def __init__(self, turns, session_id="main"):
        self.input_queue = SimpleNamespace(get_turn=lambda tid: turns.get(tid))
        self._sid = session_id

    def _current_session_id(self):
        return self._sid


def _turn(input_text, initiator):
    return SimpleNamespace(input=input_text, initiator=initiator)


def test_user_turn_done_publishes_turn_done(attention_listener):
    fake = _FakeBridgeForOutcome({"t1": _turn("整理报告", "user")}, session_id="s-main")
    bridge_mod.AgentBridge._publish_user_turn_outcome(fake, "t1", failed=False)
    ev = attention_listener[-1]
    assert ev["kind"] == attention_hub.KIND_TURN_DONE
    assert ev["title"] == "任务完成：整理报告"
    assert ev["extra"]["session_id"] == "s-main"


def test_user_turn_failed_publishes_turn_failed(attention_listener):
    fake = _FakeBridgeForOutcome({"t2": _turn("跑测试", "user")})
    bridge_mod.AgentBridge._publish_user_turn_outcome(fake, "t2", failed=True)
    assert attention_listener[-1]["kind"] == attention_hub.KIND_TURN_FAILED


def test_cron_and_autonomous_turns_are_silent(attention_listener):
    fake = _FakeBridgeForOutcome({
        "c1": _turn("定时巡检", "cron"),
        "a1": _turn("自主探索", "autonomous"),
    })
    bridge_mod.AgentBridge._publish_user_turn_outcome(fake, "c1", failed=False)
    bridge_mod.AgentBridge._publish_user_turn_outcome(fake, "a1", failed=True)
    assert attention_listener == []


def test_unknown_turn_or_empty_id_is_silent(attention_listener):
    fake = _FakeBridgeForOutcome({})
    bridge_mod.AgentBridge._publish_user_turn_outcome(fake, "nope", failed=False)
    bridge_mod.AgentBridge._publish_user_turn_outcome(fake, "", failed=False)
    assert attention_listener == []


# ════════════════════════════════════════════════════════════════════
# 免打扰
# ════════════════════════════════════════════════════════════════════

def test_quiet_hours_empty_means_none():
    assert QuietHours.parse("") is None
    assert QuietHours.parse("   ") is None


def test_quiet_hours_overnight_window():
    q = QuietHours.parse("23:00-08:00")
    assert q.contains(datetime(2026, 1, 1, 23, 30))
    assert q.contains(datetime(2026, 1, 1, 0, 5))
    assert q.contains(datetime(2026, 1, 1, 7, 59))
    assert not q.contains(datetime(2026, 1, 1, 8, 0))
    assert not q.contains(datetime(2026, 1, 1, 12, 0))


def test_quiet_hours_same_day_window():
    q = QuietHours.parse("09:00-12:30")
    assert q.contains(datetime(2026, 1, 1, 9, 0))
    assert not q.contains(datetime(2026, 1, 1, 12, 30))
    assert not q.contains(datetime(2026, 1, 1, 8, 59))


@pytest.mark.parametrize("bad", ["2300", "25:00-01:00", "abc-def", "9:60-10:00", "12:00-"])
def test_quiet_hours_rejects_bad_format(bad):
    with pytest.raises(ValueError):
        QuietHours.parse(bad)


# ════════════════════════════════════════════════════════════════════
# ToastPolicy
# ════════════════════════════════════════════════════════════════════

def _ev(kind, ref, title="t", level="normal", extra=None):
    return {"kind": kind, "ref_id": ref, "title": title, "level": level, "extra": extra or {}}


def test_policy_disabled_by_default_shows_nothing():
    assert ToastPolicy().decide(_ev("permission", "r1")) is None


def test_policy_permission_and_interaction_are_blocking_toasts():
    p = ToastPolicy(enabled=True)
    t = p.decide(_ev("permission", "r1", title="审批工具：bash"))
    assert t is not None and t.title == "需要审批" and t.blocking is True
    t2 = p.decide(_ev("interaction", "i1", title="选哪个？"))
    assert t2 is not None and t2.title == "需要你回答" and t2.blocking is True


def test_policy_dedupes_same_reference():
    p = ToastPolicy(enabled=True)
    assert p.decide(_ev("permission", "r1")) is not None
    assert p.decide(_ev("permission", "r1")) is None


def test_policy_report_and_unknown_kinds_are_silent():
    p = ToastPolicy(enabled=True)
    assert p.decide(_ev("report", "rep1")) is None
    assert p.decide(_ev("autonomous_tick", "x")) is None


def test_policy_skips_quick_panel_session_turns():
    p = ToastPolicy(enabled=True)
    quick = _ev("turn_done", "t1", extra={"session_id": "desktop-quick"})
    assert p.decide(quick) is None
    main = _ev("turn_done", "t2", title="任务完成：x", extra={"session_id": "main"})
    t = p.decide(main)
    assert t is not None and t.title == "任务完成"


def test_policy_turn_failed_toast_text():
    p = ToastPolicy(enabled=True)
    t = p.decide(_ev("turn_failed", "t9", title="任务失败：跑测试", extra={"session_id": "main"}))
    assert t.title == "任务失败" and t.blocking is False


def test_policy_quiet_hours_suppress_non_blocking_only():
    fixed = datetime(2026, 1, 1, 23, 30)
    p = ToastPolicy(enabled=True, quiet=QuietHours.parse("23:00-08:00"), clock=lambda: fixed)
    assert p.decide(_ev("turn_done", "t1", extra={"session_id": "main"})) is None
    assert p.decide(_ev("cron_question", "q1", level="normal")) is None
    # 阻塞类不受免打扰影响：没有答案，任务就停在那里
    assert p.decide(_ev("permission", "r1")) is not None
    assert p.decide(_ev("daemon_crash", "crash-1", level="blocking")) is not None


def test_policy_quiet_suppressed_item_is_not_reshown_later():
    now = {"t": datetime(2026, 1, 1, 23, 30)}
    p = ToastPolicy(enabled=True, quiet=QuietHours.parse("23:00-08:00"), clock=lambda: now["t"])
    assert p.decide(_ev("cron_question", "q1")) is None
    now["t"] = datetime(2026, 1, 1, 12, 0)
    assert p.decide(_ev("cron_question", "q1")) is None


def test_policy_outside_quiet_hours_shows_cron_question():
    p = ToastPolicy(enabled=True, quiet=QuietHours.parse("23:00-08:00"),
                    clock=lambda: datetime(2026, 1, 1, 12, 0))
    t = p.decide(_ev("cron_question", "q2", title="要不要继续？"))
    assert t is not None and t.title == "后台任务有问题" and t.body == "要不要继续？"


def test_policy_tolerates_garbage_events():
    p = ToastPolicy(enabled=True)
    assert p.decide("not a dict") is None
    assert p.decide({}) is None


# ════════════════════════════════════════════════════════════════════
# pending：收集与回答（MockTransport）
# ════════════════════════════════════════════════════════════════════

class FakeDaemon:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        key = (request.method, request.url.path)
        if key not in self.routes:
            return httpx.Response(404, json={"detail": "not found"})
        value = self.routes[key]
        if isinstance(value, httpx.Response):
            return value
        return httpx.Response(200, json=value)


def _api(fake):
    return DaemonApi("http://127.0.0.1:8765", "tok", transport=httpx.MockTransport(fake))


def test_collect_pending_gathers_three_kinds():
    fake = FakeDaemon({
        ("GET", "/v1/permissions/pending"): {"permissions": [
            {"req_id": "p1", "tool_name": "bash", "tool_input": {"command": "rm -rf x"}, "turn_id": "t"}]},
        ("GET", "/v1/interactions/pending"): {"interactions": [
            {"req_id": "i1", "kind": "ask_user_choice", "turn_id": "t",
             "data": {"question": "选哪个？", "options": ["A", "B"]}}]},
        ("GET", "/v1/cron_questions/pending"): {"questions": [
            {"question_id": "q1", "job_id": "job-a", "question": "继续吗？",
             "hint": "", "options": ["是", "否"]}]},
    })
    items = collect_pending(_api(fake))
    assert [i.kind for i in items] == ["permission", "interaction", "cron"]
    assert items[0].title == "审批工具：bash"
    assert "rm -rf" in items[0].detail
    assert items[1].options == ("A", "B")
    assert items[2].ref_id == "q1" and items[2].options == ("是", "否")


def test_collect_pending_one_failing_endpoint_does_not_hide_others():
    fake = FakeDaemon({
        ("GET", "/v1/permissions/pending"): httpx.Response(500, json={"detail": "boom"}),
        ("GET", "/v1/cron_questions/pending"): {"questions": [
            {"question_id": "q9", "question": "ok?", "job_id": "j"}]},
    })
    items = collect_pending(_api(fake))
    assert [i.kind for i in items] == ["cron"]


def test_answer_permission_approve_and_deny():
    fake = FakeDaemon({("POST", "/v1/permissions/p1"): {"ok": True}})
    api = _api(fake)
    item = PendingItem(kind="permission", ref_id="p1", title="x")
    answer_item(api, item, approve=True)
    body = json.loads(fake.calls[-1].content)
    assert body == {"approve": True, "mode": "once"}
    answer_item(api, item, approve=False)
    assert json.loads(fake.calls[-1].content)["approve"] is False


def test_answer_interaction_shapes_follow_kind():
    fake = FakeDaemon({("POST", "/v1/interactions/i1"): {"ok": True}})
    api = _api(fake)
    confirm = PendingItem(kind="interaction", ref_id="i1", title="确定？", interaction_kind="ask_user_confirm")
    answer_item(api, confirm, confirmed=True)
    assert json.loads(fake.calls[-1].content) == {"confirmed": True}

    choice = PendingItem(kind="interaction", ref_id="i1", title="选", interaction_kind="ask_user_choice",
                         options=("A", "B"))
    answer_item(api, choice, choice_index=1)
    assert json.loads(fake.calls[-1].content) == {"choice_index": 1}

    text = PendingItem(kind="interaction", ref_id="i1", title="说", interaction_kind="ask_user")
    answer_item(api, text, text="  分支 main  ")
    assert json.loads(fake.calls[-1].content) == {"answer": "分支 main"}


def test_answer_cron_question_posts_text_to_question_path():
    fake = FakeDaemon({("POST", "/v1/cron_questions/q1/answer"): {"ok": True}})
    answer_item(_api(fake), PendingItem(kind="cron", ref_id="q1", title="?"), text="是")
    assert fake.calls[-1].url.path == "/v1/cron_questions/q1/answer"
    assert json.loads(fake.calls[-1].content) == {"answer": "是"}


def test_empty_text_answer_is_rejected_without_request():
    fake = FakeDaemon({})
    with pytest.raises(ValueError):
        answer_item(_api(fake), PendingItem(kind="cron", ref_id="q1", title="?"), text="   ")
    assert fake.calls == []


def test_answer_404_surfaces_as_status_error():
    fake = FakeDaemon({})   # 任何回答都 404（已被别处处理）
    with pytest.raises(httpx.HTTPStatusError) as ei:
        answer_item(_api(fake), PendingItem(kind="permission", ref_id="gone", title="x"), approve=True)
    assert ei.value.response.status_code == 404


# ════════════════════════════════════════════════════════════════════
# 控制器：崩溃提醒只在增长时触发
# ════════════════════════════════════════════════════════════════════

def _controller(counter, sink):
    def factory():
        raise RuntimeError("no daemon in unit test")

    return DesktopController(
        TrayModel(), factory,
        offline_crashes=lambda: counter["n"],
        on_attention=sink.append,
    )


def test_preexisting_crash_alerts_do_not_toast_on_offline():
    counter = {"n": 2}
    sink = []
    ctrl = _controller(counter, sink)
    ctrl._crash_baseline = 2        # 等价于 start() 时读到的基线
    ctrl._go_offline(REASON_LOST)
    assert sink == []


def test_new_crash_alert_triggers_blocking_daemon_crash_event():
    counter = {"n": 2}
    sink = []
    ctrl = _controller(counter, sink)
    ctrl._crash_baseline = 2
    counter["n"] = 3
    ctrl._go_offline(REASON_LOST)
    assert len(sink) == 1
    ev = sink[0]
    assert ev["kind"] == "daemon_crash" and ev["level"] == "blocking"
    assert ev["ref_id"] == "crash-3"
    # 再次离线、数量未变：不重复提醒
    ctrl._go_offline(REASON_LOST)
    assert len(sink) == 1


# ════════════════════════════════════════════════════════════════════
# Qt 离屏冒烟
# ════════════════════════════════════════════════════════════════════

def test_pending_dialog_renders_rows_offscreen():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QPushButton

    from mini_agent_desktop.pending_dialog import PendingDialog

    QApplication.instance() or QApplication([])

    fake = FakeDaemon({})

    def factory():
        return QuickSession(_api(fake))

    dlg = PendingDialog(factory)
    items = [
        PendingItem(kind="permission", ref_id="p1", title="审批工具：bash", detail="{}"),
        PendingItem(kind="interaction", ref_id="i1", title="选哪个？",
                    interaction_kind="ask_user_choice", options=("A", "B")),
        PendingItem(kind="cron", ref_id="q1", title="继续吗？", options=()),
    ]
    dlg._render(items)
    assert "共 3 项" in dlg.hint.text()
    buttons = [b.text() for b in dlg.list_host.findChildren(QPushButton)]
    assert "批准" in buttons and "拒绝" in buttons
    assert "A" in buttons and "B" in buttons
    assert "提交" in buttons          # 文字回答的 cron 提问
    dlg._render([])
    assert "没有待处理" in dlg.hint.text()
    dlg.hide()
