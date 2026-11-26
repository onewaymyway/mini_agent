"""tests/test_desktop_attention_s0.py

覆盖 next_doc/windows_desktop_resident_plan.md S0 的三件事：

1. notification/attention_hub：无监听者空操作、监听者异常不外溢、幂等注册；
2. 两个 store（reports / cron_questions）落盘后发布 attention 事件，
   且发布失败不影响落盘结果；崩溃告警【不】发布（由 supervisor 写入）；
3. GET /v1/desktop/summary 的计数聚合与 attention_events_enabled 标志；
4. HttpConfig 默认 desktop_attention_events_enabled=False（保守 opt-in）；
5. AgentBridge.emit_attention 产出 type=attention、不带 session_id 的事件。

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_desktop_attention_s0.py -q
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.api.models import AgentEvent, EventType
from mini_agent.api.routes import router
from mini_agent.config.models import HttpConfig
from mini_agent.notification import attention_hub, questions_store
from mini_agent.notification.daemon_crash_store import append_crash_alert
from mini_agent.notification.reports_store import append_report
from mini_agent.storage.paths import AgentPaths


class _Collector:
    """测试用监听者：收集收到的事件。"""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, ev: dict) -> None:
        self.events.append(ev)


class TestAttentionHub(unittest.TestCase):
    def tearDown(self) -> None:
        # 清理，避免污染其它用例
        for fn in list(attention_hub._listeners):
            attention_hub.unregister_listener(fn)

    def test_no_listener_is_noop(self):
        self.assertEqual(attention_hub.listener_count(), 0)
        # 不应抛异常
        attention_hub.publish_attention(attention_hub.KIND_REPORT, "x", ref_id="r1")

    def test_listener_receives_event_and_title_truncated(self):
        c = _Collector()
        attention_hub.register_listener(c)
        attention_hub.publish_attention(
            attention_hub.KIND_REPORT, "t" * 500, ref_id="r1",
            level=attention_hub.LEVEL_BLOCKING, extra={"job_id": "j"},
        )
        self.assertEqual(len(c.events), 1)
        ev = c.events[0]
        self.assertEqual(ev["kind"], "report")
        self.assertEqual(ev["ref_id"], "r1")
        self.assertEqual(ev["level"], "blocking")
        self.assertEqual(ev["extra"], {"job_id": "j"})
        self.assertEqual(len(ev["title"]), 200)

    def test_register_is_idempotent(self):
        c = _Collector()
        attention_hub.register_listener(c)
        attention_hub.register_listener(c)
        self.assertEqual(attention_hub.listener_count(), 1)
        attention_hub.publish_attention("report", "x")
        self.assertEqual(len(c.events), 1)

    def test_listener_exception_is_swallowed_and_others_still_called(self):
        def bad(_ev):
            raise RuntimeError("boom")

        c = _Collector()
        attention_hub.register_listener(bad)
        attention_hub.register_listener(c)
        attention_hub.publish_attention("report", "x")  # 不应抛
        self.assertEqual(len(c.events), 1)


class TestStoresPublish(unittest.TestCase):
    def tearDown(self) -> None:
        for fn in list(attention_hub._listeners):
            attention_hub.unregister_listener(fn)

    def test_append_report_publishes(self):
        c = _Collector()
        attention_hub.register_listener(c)
        with tempfile.TemporaryDirectory() as tmp:
            paths = AgentPaths(Path(tmp))
            append_report(paths, {"report_id": "rep1", "title": "每日汇总", "detail": "正文"})
        self.assertEqual(len(c.events), 1)
        self.assertEqual(c.events[0]["kind"], attention_hub.KIND_REPORT)
        self.assertEqual(c.events[0]["ref_id"], "rep1")
        self.assertEqual(c.events[0]["title"], "每日汇总")
        # 正文不进事件（只放摘要）
        self.assertNotIn("正文", str(c.events[0]))

    def test_append_question_publishes_with_urgency_level(self):
        c = _Collector()
        attention_hub.register_listener(c)
        with tempfile.TemporaryDirectory() as tmp:
            paths = AgentPaths(Path(tmp))
            q1 = questions_store.append_question(paths, "job1", "选A还是B？",
                                                 urgency=questions_store.URGENCY_BLOCKING)
            q2 = questions_store.append_question(paths, "job1", "顺便问下？")
        self.assertEqual(len(c.events), 2)
        self.assertEqual(c.events[0]["level"], attention_hub.LEVEL_BLOCKING)
        self.assertEqual(c.events[0]["ref_id"], q1["question_id"])
        self.assertEqual(c.events[0]["extra"], {"job_id": "job1"})
        self.assertEqual(c.events[1]["level"], attention_hub.LEVEL_NORMAL)
        self.assertEqual(c.events[1]["ref_id"], q2["question_id"])

    def test_store_write_unaffected_when_listener_raises(self):
        def bad(_ev):
            raise RuntimeError("boom")

        attention_hub.register_listener(bad)
        with tempfile.TemporaryDirectory() as tmp:
            paths = AgentPaths(Path(tmp))
            q = questions_store.append_question(paths, "job1", "问题")
            self.assertEqual(
                questions_store.count_questions(paths, status=questions_store.STATUS_PENDING), 1)
            self.assertTrue(q["question_id"])

    def test_crash_alert_does_not_publish(self):
        c = _Collector()
        attention_hub.register_listener(c)
        with tempfile.TemporaryDirectory() as tmp:
            paths = AgentPaths(Path(tmp))
            rec = append_crash_alert(paths, {"exit_code": 1})
            self.assertTrue(rec["alert_id"])
        self.assertEqual(c.events, [])


class TestBridgeEmitAttention(unittest.TestCase):
    def test_emit_attention_event_shape(self):
        from mini_agent.api.bridge import AgentBridge

        pushed: list[AgentEvent] = []
        # 不构造完整 bridge，只验证 emit_attention 的事件形状
        fake = SimpleNamespace(broadcaster=SimpleNamespace(push=pushed.append))
        AgentBridge.emit_attention(fake, "report", "标题", ref_id="r1", level="normal")
        self.assertEqual(len(pushed), 1)
        ev = pushed[0]
        self.assertEqual(ev.type, EventType.ATTENTION)
        self.assertEqual(ev.session_id, "")
        self.assertEqual(ev.data["kind"], "report")
        self.assertEqual(ev.data["ref_id"], "r1")
        # SSE 序列化可用
        self.assertIn("event: attention", ev.sse_format())


class TestConfigDefault(unittest.TestCase):
    def test_default_is_opt_in_off(self):
        self.assertFalse(HttpConfig().desktop_attention_events_enabled)


def _make_app(project_root: Path, *, attention_enabled: bool, inbox_items=None) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    # /v1/inbox 在单用户模式下读 bridge.permission_gate / interaction_gate；
    # 用空 gate + 空 objective 执行器，必要时注入 pending 权限/交互。
    perms = list(inbox_items or [])
    gate = SimpleNamespace(list_pending=lambda: perms)
    igate = SimpleNamespace(list_pending=lambda: [])
    bridge = SimpleNamespace(
        agent=SimpleNamespace(cfg=SimpleNamespace(project_root=project_root)),
        permission_gate=gate,
        interaction_gate=igate,
        session_id=None,
        _objective_executor=None,
    )
    app.state.http_server = SimpleNamespace(
        bridge=bridge, autonomous_loop=None,
        desktop_attention_events_enabled=attention_enabled,
    )
    app.state.session_pool = None
    return TestClient(app)


class TestDesktopSummaryRoute(unittest.TestCase):
    def tearDown(self) -> None:
        for fn in list(attention_hub._listeners):
            attention_hub.unregister_listener(fn)

    def test_empty_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = _make_app(Path(tmp), attention_enabled=False)
            resp = client.get("/v1/desktop/summary")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["attention_total"], 0)
            self.assertFalse(data["attention_events_enabled"])
            self.assertEqual(set(data["counts"]), {
                "pending_permissions", "pending_interactions", "failed_objectives",
                "pending_reports", "cron_questions_pending", "crash_alerts_unacked",
            })
            self.assertIsInstance(data["server_time"], float)

    def test_counts_aggregate_all_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = AgentPaths(root)
            append_report(paths, {"report_id": "r1", "title": "a", "acknowledged": False,
                                  "created_at": time.time()})
            append_report(paths, {"report_id": "r2", "title": "b", "acknowledged": False,
                                  "created_at": time.time()})
            questions_store.append_question(paths, "job1", "问题1")
            append_crash_alert(paths, {"exit_code": 1})
            client = _make_app(
                root, attention_enabled=True,
                inbox_items=[{"req_id": "p1", "summary": "写文件", "created_at": 1.0}],
            )
            data = client.get("/v1/desktop/summary").json()
            c = data["counts"]
            self.assertEqual(c["pending_permissions"], 1)
            self.assertEqual(c["pending_reports"], 2)
            self.assertEqual(c["cron_questions_pending"], 1)
            self.assertEqual(c["crash_alerts_unacked"], 1)
            self.assertEqual(data["attention_total"], 5)
            self.assertTrue(data["attention_events_enabled"])

    def test_acknowledged_report_not_counted(self):
        from mini_agent.notification.reports_store import acknowledge_report
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = AgentPaths(root)
            append_report(paths, {"report_id": "r1", "title": "a", "acknowledged": False,
                                  "created_at": time.time()})
            acknowledge_report(paths, "r1")
            client = _make_app(root, attention_enabled=False)
            self.assertEqual(client.get("/v1/desktop/summary").json()["counts"]["pending_reports"], 0)


if __name__ == "__main__":
    unittest.main()
