"""tests/test_notification_routes_p7.py

覆盖 next_doc/watchlist_notification_goal_design.md §6/P7 新增的三个只读
REST 端点（看板"关注对象/tier 配置/通知发送记录"面板的数据来源）：

- GET /v1/notification/watchlist       watchlist.yaml 关注对象列表
- GET /v1/notification/report_tiers    report_tiers.yaml + job 运行时状态
- GET /v1/notification/dispatch_log    NotificationDispatcher 最近发送记录

风格对齐 tests/test_external_input_routes_p6.py：不拉起完整 HttpServer，
只挂载 router 到一个最小 FastAPI app，把 app.state.http_server 设成一个
满足 routes.py 实际读取路径的轻量 duck-typed 对象。

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_notification_routes_p7.py -q
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.api.routes import router
from mini_agent.evolution.cron_scheduler import CronScheduler
from mini_agent.notification.config import NotificationConfig
from mini_agent.notification.dispatcher import NotificationDispatcher, NotificationMessage
from mini_agent.storage.paths import AgentPaths


def _make_app(project_root: Path, cron_scheduler=None) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    bridge = SimpleNamespace(
        agent=SimpleNamespace(cfg=SimpleNamespace(project_root=project_root)),
        _cron_scheduler=cron_scheduler,
    )
    app.state.http_server = SimpleNamespace(bridge=bridge, autonomous_loop=None)
    return TestClient(app)


class TestNotificationWatchlistRoute(unittest.TestCase):
    def test_missing_config_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = _make_app(Path(tmp))
            resp = client.get("/v1/notification/watchlist")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), {"items": []})

    def test_returns_configured_items_including_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = AgentPaths(Path(tmp))
            p = paths.external_input_watchlist_config
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                "watchlist:\n"
                "  - id: competitor_x\n"
                "    keywords: [\"竞品X\"]\n"
                "    report_tier: daily\n"
                "  - id: paused_item\n"
                "    keywords: [\"暂停关键词\"]\n"
                "    report_tier: daily\n"
                "    enabled: false\n",
                encoding="utf-8",
            )
            client = _make_app(Path(tmp))
            resp = client.get("/v1/notification/watchlist")
            self.assertEqual(resp.status_code, 200)
            items = resp.json()["items"]
            self.assertEqual(len(items), 2)
            ids = {i["id"] for i in items}
            self.assertEqual(ids, {"competitor_x", "paused_item"})
            paused = next(i for i in items if i["id"] == "paused_item")
            self.assertFalse(paused["enabled"])


class TestNotificationReportTiersRoute(unittest.TestCase):
    def test_missing_config_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = _make_app(Path(tmp))
            resp = client.get("/v1/notification/report_tiers")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), {"tiers": []})

    def test_returns_tiers_with_job_runtime_info(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = AgentPaths(Path(tmp))
            p = paths.notification_report_tiers_config
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                "tiers:\n"
                "  - id: daily\n"
                "    schedule: \"interval:86400\"\n"
                "    notify_channels: [kanban]\n",
                encoding="utf-8",
            )
            scheduler = CronScheduler(paths, submit_fn=None)
            scheduler.load()
            scheduler.ensure_job(
                job_id="sys:watchlist_report_daily", name="daily 分级汇报",
                schedule="interval:86400",
            )
            client = _make_app(Path(tmp), cron_scheduler=scheduler)
            resp = client.get("/v1/notification/report_tiers")
            self.assertEqual(resp.status_code, 200)
            tiers = resp.json()["tiers"]
            self.assertEqual(len(tiers), 1)
            tier = tiers[0]
            self.assertEqual(tier["id"], "daily")
            self.assertEqual(tier["job_id"], "sys:watchlist_report_daily")
            self.assertTrue(tier["job_enabled"])
            self.assertIn("idle_streak", tier)

    def test_no_cron_scheduler_degrades_gracefully(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = AgentPaths(Path(tmp))
            p = paths.notification_report_tiers_config
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                "tiers:\n  - id: daily\n    schedule: \"interval:86400\"\n",
                encoding="utf-8",
            )
            client = _make_app(Path(tmp), cron_scheduler=None)
            resp = client.get("/v1/notification/report_tiers")
            self.assertEqual(resp.status_code, 200)
            tier = resp.json()["tiers"][0]
            self.assertIsNone(tier["job_enabled"])
            self.assertIsNone(tier["next_run_str"])


class TestNotificationDispatchLogRoute(unittest.TestCase):
    def test_missing_log_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = _make_app(Path(tmp))
            resp = client.get("/v1/notification/dispatch_log")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), {"entries": [], "has_more": False})

    def test_dispatch_writes_log_and_route_returns_it_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = AgentPaths(Path(tmp))
            cfg = NotificationConfig(default_channels=["kanban"])
            dispatcher = NotificationDispatcher(paths, config=cfg)
            dispatcher.dispatch(NotificationMessage(title="第一条", body="b1", source="test"))
            dispatcher.dispatch(NotificationMessage(title="第二条", body="b2", source="test"))

            client = _make_app(Path(tmp))
            resp = client.get("/v1/notification/dispatch_log")
            self.assertEqual(resp.status_code, 200)
            entries = resp.json()["entries"]
            self.assertEqual(len(entries), 2)
            # 倒序：最新的在前面
            self.assertEqual(entries[0]["title"], "第二条")
            self.assertEqual(entries[1]["title"], "第一条")
            self.assertIn("kanban", entries[0]["results"])

    def test_limit_query_param_is_respected(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = AgentPaths(Path(tmp))
            cfg = NotificationConfig(default_channels=["kanban"])
            dispatcher = NotificationDispatcher(paths, config=cfg)
            for i in range(5):
                dispatcher.dispatch(NotificationMessage(title=f"第{i}条", body="b", source="test"))

            client = _make_app(Path(tmp))
            resp = client.get("/v1/notification/dispatch_log?limit=2")
            entries = resp.json()["entries"]
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0]["title"], "第4条")


if __name__ == "__main__":
    unittest.main()
