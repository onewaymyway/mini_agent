"""
tests/test_external_input_routes_p6.py

覆盖 next_doc/external_input_gateway_design.md §6/P6 新增的三个只读
REST 端点（看板"🔌 外部输入"面板的数据来源）：

- GET /v1/external_input/sources    已配置 source 列表 + 运行时健康度
- GET /v1/external_input/policies   policies.yaml 路由规则（只读）
- GET /v1/external_input/events     最近 external.* 事件流水（不消费游标）

跟 test_evolution_proposal_routes_track_i_r8.py 一样，不拉起完整
HttpServer，只挂载 router 到一个最小 FastAPI app，把 app.state.http_server
设成一个满足 routes.py 里实际读取路径（`bridge.agent.cfg.project_root`、
`bridge._external_input_poller`）的轻量 duck-typed 对象。

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_external_input_routes_p6.py -q
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.api.routes import router
from mini_agent.external_input.gateway import publish_event
from mini_agent.external_input.source import ExternalInputEvent
from mini_agent.storage.paths import AgentPaths


def _make_app(project_root: Path, gateway_poller=None) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    bridge = SimpleNamespace(
        agent=SimpleNamespace(cfg=SimpleNamespace(project_root=project_root)),
        _external_input_poller=gateway_poller,
    )
    app.state.http_server = SimpleNamespace(bridge=bridge)
    return TestClient(app)


class _FakePoller:
    """GatewayPoller 的最小 duck-typed 替身：只实现 routes.py 会调用的
    is_running()/get_all_health()，不涉及真正的轮询线程。"""

    def __init__(self, health: dict):
        self._health = health

    def is_running(self, source_id: str) -> bool:
        return source_id in self._health

    def get_all_health(self) -> dict:
        return self._health


def _make_event(event_id="e1", signal="hot_signal", title="标题", detail="正文"):
    return ExternalInputEvent(
        id=event_id, source_id="src1", source_type="rss",
        signal=signal, title=title, detail=detail,
    )


class TestListExternalInputSources(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.paths = AgentPaths(self.root)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write_sources_yaml(self, text: str):
        p = self.paths.external_input_sources_config
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def test_no_sources_yaml_returns_empty_list(self):
        client = _make_app(self.root)
        resp = client.get("/v1/external_input/sources")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["sources"], [])

    def test_poller_unavailable_falls_back_to_static_config(self):
        self._write_sources_yaml(
            "sources:\n  - id: news1\n    type: rss\n    enabled: true\n    interval_seconds: 300\n"
        )
        client = _make_app(self.root, gateway_poller=None)
        resp = client.get("/v1/external_input/sources")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["poller_available"])
        self.assertEqual(len(body["sources"]), 1)
        src = body["sources"][0]
        self.assertEqual(src["id"], "news1")
        self.assertEqual(src["type"], "rss")
        self.assertIsNone(src["is_running"])
        self.assertIsNone(src["last_poll_ts"])

    def test_poller_available_reports_health(self):
        self._write_sources_yaml(
            "sources:\n  - id: news1\n    type: rss\n    enabled: true\n    interval_seconds: 300\n"
        )
        now = time.time()
        poller = _FakePoller({
            "news1": {
                "last_poll_ts": now,
                "consecutive_failures": 2,
                "circuit_open": False,
                "last_error": "timeout",
            }
        })
        client = _make_app(self.root, gateway_poller=poller)
        resp = client.get("/v1/external_input/sources")
        body = resp.json()
        self.assertTrue(body["poller_available"])
        src = body["sources"][0]
        self.assertTrue(src["is_running"])
        self.assertEqual(src["consecutive_failures"], 2)
        self.assertEqual(src["last_error"], "timeout")
        self.assertFalse(src["circuit_open"])


class TestListExternalInputPolicies(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.paths = AgentPaths(self.root)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_no_policies_yaml_returns_empty_rules(self):
        client = _make_app(self.root)
        resp = client.get("/v1/external_input/policies")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["rules"], [])

    def test_lists_rules_in_file_order(self):
        p = self.paths.external_input_policies_config
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            "- match: {signal: hot_signal}\n  action: goal_candidate\n"
            "- match: {signal: urgent_signal}\n"
            "  action: enqueue_turn\n"
            "  enqueue:\n    initiator: external\n",
            encoding="utf-8",
        )
        client = _make_app(self.root)
        resp = client.get("/v1/external_input/policies")
        rules = resp.json()["rules"]
        self.assertEqual(len(rules), 2)
        self.assertEqual(rules[0]["action"], "goal_candidate")
        self.assertEqual(rules[1]["action"], "enqueue_turn")
        self.assertEqual(rules[1]["enqueue"]["initiator"], "external")

    def test_invalid_action_in_one_rule_is_skipped_silently(self):
        """单条规则 action 非法：与 load_policies() 的既有容错策略一致——
        跳过该条、其余正常加载，不是 fatal error，所以这里不应该出现
        `_error` 字段（区别于顶层结构错误的那种 PoliciesConfigError）。"""
        p = self.paths.external_input_policies_config
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            "- match: {signal: x}\n  action: not_a_real_action\n"
            "- match: {signal: y}\n  action: notify_only\n",
            encoding="utf-8",
        )
        client = _make_app(self.root)
        resp = client.get("/v1/external_input/policies")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body["rules"]), 1)
        self.assertEqual(body["rules"][0]["action"], "notify_only")
        self.assertNotIn("_error", body)

    def test_top_level_structure_error_surfaces_error_field(self):
        p = self.paths.external_input_policies_config
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("not_a_list: true\n", encoding="utf-8")
        client = _make_app(self.root)
        resp = client.get("/v1/external_input/policies")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["rules"], [])
        self.assertIn("_error", body)


class TestListExternalInputEvents(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.paths = AgentPaths(self.root)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_no_events_file_returns_empty_list(self):
        client = _make_app(self.root)
        resp = client.get("/v1/external_input/events")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["events"], [])

    def test_filters_external_prefix_and_orders_newest_first(self):
        publish_event(self.paths, _make_event(event_id="e1", title="第一条"))
        publish_event(self.paths, _make_event(event_id="e2", title="第二条"))
        # 写一条非 external.* 的事件混进同一个 events.jsonl，验证过滤逻辑
        from mini_agent.perception import system_events
        system_events.publish(
            self.paths, source="test", event_type="workflow.step_started",
            tier="tick", payload={"foo": "bar"},
        )

        client = _make_app(self.root)
        resp = client.get("/v1/external_input/events?limit=10")
        self.assertEqual(resp.status_code, 200)
        events = resp.json()["events"]
        self.assertEqual(len(events), 2)
        # 从文件尾部往前扫，最新的排在前面
        self.assertEqual(events[0]["payload"]["title"], "第二条")
        self.assertEqual(events[1]["payload"]["title"], "第一条")
        for evt in events:
            self.assertTrue(evt["event_type"].startswith("external."))

    def test_limit_is_respected_and_capped(self):
        for i in range(5):
            publish_event(self.paths, _make_event(event_id=f"e{i}", title=f"标题{i}"))
        client = _make_app(self.root)
        resp = client.get("/v1/external_input/events?limit=2")
        self.assertEqual(len(resp.json()["events"]), 2)
        # limit 上限是 200，传一个超大值不应该报错
        resp2 = client.get("/v1/external_input/events?limit=99999")
        self.assertEqual(resp2.status_code, 200)


if __name__ == "__main__":
    unittest.main()
