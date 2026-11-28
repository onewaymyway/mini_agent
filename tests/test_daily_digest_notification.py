"""tests/test_daily_digest_notification.py

覆盖"每日融合日报通知"改造：
1. 日报生成后走 NotificationDispatcher（默认渠道 + 看板兜底）并发布 attention 事件；
2. 通知发送失败不影响日报落盘与返回；
3. reports 分类映射：daily_digest → 关注汇报；
4. sys:daily_digest 改为本地 handler（零 LLM），调度为 cron:0 22 * * *；
5. 桌面端：默认 toast 开启、日报弹窗非阻塞、按日期去重；
6. 桌面端补弹判定：未弹过才弹、已弹过不重复、过旧日报不补弹、弹后落盘。

运行方式：
    PYTHONPATH=src:apps/mini_agent_desktop python3 -m pytest tests/test_daily_digest_notification.py -q
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from mini_agent.notification import attention_hub
from mini_agent.notification import reports_store
from mini_agent.storage.paths import AgentPaths
from mini_agent.evolution import daily_digest as dd
from mini_agent.evolution import daily_digest_job as ddj


def _digest(day: str, generated_at: float | None = None) -> dict:
    return {
        "day": day,
        "generated_at": generated_at if generated_at is not None else time.time(),
        "behavior": {},
        "goal_deltas": [],
        "errors": {"total": 2, "top_types": [], "top_where": []},
        "sessions": [],
        "shown_at": None,
    }


class NotifyDigestTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmp.name))
        self.paths.daily_reports_dir.mkdir(parents=True, exist_ok=True)
        self.events: list[dict] = []
        self._listener = self.events.append
        attention_hub.register_listener(self._listener)

    def tearDown(self) -> None:
        attention_hub.unregister_listener(self._listener)
        self._tmp.cleanup()

    def test_notify_dispatches_and_publishes_attention(self) -> None:
        data = _digest("2026-11-27")
        self.paths.daily_report_path("2026-11-27").write_text("# 日报正文", encoding="utf-8")
        fake = mock.MagicMock()
        fake.dispatch.return_value = {"kanban": True}
        with mock.patch("mini_agent.notification.dispatcher.NotificationDispatcher", return_value=fake):
            results = dd.notify_digest_ready(self.paths, data)

        self.assertEqual(results, {"kanban": True})
        msg = fake.dispatch.call_args.args[0]
        self.assertEqual(msg.source, "daily_digest")
        self.assertEqual(msg.title, "2026-11-27 日报已生成")
        self.assertIn("日报正文", msg.body)
        # channels 参数不传 → 走 default_channels + 看板兜底（"通知渠道都走"）
        self.assertEqual(fake.dispatch.call_args.kwargs.get("channels"), None)

        digest_events = [e for e in self.events if e["kind"] == attention_hub.KIND_DAILY_DIGEST]
        self.assertEqual(len(digest_events), 1)
        self.assertEqual(digest_events[0]["ref_id"], "2026-11-27")

    def test_dispatch_failure_does_not_break_and_still_publishes(self) -> None:
        data = _digest("2026-11-27")
        with mock.patch(
            "mini_agent.notification.dispatcher.NotificationDispatcher",
            side_effect=RuntimeError("boom"),
        ):
            results = dd.notify_digest_ready(self.paths, data)
        self.assertEqual(results, {})
        self.assertTrue(any(e["kind"] == attention_hub.KIND_DAILY_DIGEST for e in self.events))

    def test_category_mapping_for_daily_digest(self) -> None:
        self.assertEqual(reports_store.categorize_report({"source": "daily_digest"}), "关注汇报")

    def test_generate_defaults_to_today(self) -> None:
        import datetime as _dt
        self.assertEqual(dd._today(), _dt.date.today().isoformat())


class _FakeJob:
    def __init__(self, job_id: str, schedule: str) -> None:
        self.id = job_id
        self.schedule = schedule
        self.enabled = True
        self.next_run_at = 0.0
        self.last_run_at = None


class _FakeScheduler:
    def __init__(self) -> None:
        self.jobs: dict[str, _FakeJob] = {}
        self.handlers: dict = {}
        self.saved = 0

    def list_jobs(self):
        return list(self.jobs.values())

    def ensure_job(self, job_id, name, schedule, description="", tags=None):
        if job_id not in self.jobs:
            self.jobs[job_id] = _FakeJob(job_id, schedule)
        return self.jobs[job_id]

    def save(self):
        self.saved += 1

    def register_local_handler(self, job_id, handler):
        self.handlers[job_id] = handler


class DailyDigestJobTests(unittest.TestCase):
    def test_registers_local_handler_and_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AgentPaths(Path(tmp))
            sched = _FakeScheduler()
            with mock.patch("mini_agent.evolution.daily_digest_job.compute_next_run", return_value=1.0):
                info = ddj.ensure_daily_digest_job(paths, sched, enabled=True)
            self.assertEqual(info["schedule"], "cron:0 22 * * *")
            self.assertIn(ddj.JOB_ID, sched.handlers)
            self.assertTrue(sched.jobs[ddj.JOB_ID].enabled)

    def test_disabled_keeps_job_but_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sched = _FakeScheduler()
            ddj.ensure_daily_digest_job(AgentPaths(Path(tmp)), sched, enabled=False)
            self.assertFalse(sched.jobs[ddj.JOB_ID].enabled)

    def test_handler_generates_then_notifies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = AgentPaths(Path(tmp))
            sched = _FakeScheduler()
            ddj.ensure_daily_digest_job(paths, sched, enabled=True)
            handler = sched.handlers[ddj.JOB_ID]
            with mock.patch.object(dd, "generate_daily_digest", return_value=_digest("2026-11-27")) as gen, \
                 mock.patch.object(dd, "notify_digest_ready", return_value={}) as notify:
                self.assertTrue(handler(None))
            gen.assert_called_once_with(paths)
            notify.assert_called_once()

    def test_handler_returns_false_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sched = _FakeScheduler()
            ddj.ensure_daily_digest_job(AgentPaths(Path(tmp)), sched, enabled=True)
            with mock.patch.object(dd, "generate_daily_digest", side_effect=RuntimeError("x")):
                self.assertFalse(sched.handlers[ddj.JOB_ID](None))


# ── 桌面端 ───────────────────────────────────────────────────────────

_DESKTOP = Path(__file__).resolve().parents[1] / "apps" / "mini_agent_desktop"
if str(_DESKTOP) not in sys.path:
    sys.path.insert(0, str(_DESKTOP))

from mini_agent_desktop import attention as att  # noqa: E402
from mini_agent_desktop import config as desk_cfg  # noqa: E402
from mini_agent_desktop import digest_catchup as dc  # noqa: E402


class DesktopPolicyTests(unittest.TestCase):
    def test_toast_enabled_by_default(self) -> None:
        self.assertTrue(desk_cfg.DesktopConfig().toast_enabled)

    def test_digest_event_pops_non_blocking(self) -> None:
        p = att.ToastPolicy(enabled=True)
        toast = p.decide({"kind": att.KIND_DAILY_DIGEST, "title": "2026-11-27 日报已生成",
                          "ref_id": "2026-11-27", "level": "normal"})
        self.assertIsNotNone(toast)
        self.assertEqual(toast.title, "今日日报已生成")
        self.assertFalse(toast.blocking)

    def test_digest_event_dedupes_same_day(self) -> None:
        p = att.ToastPolicy(enabled=True)
        ev = {"kind": att.KIND_DAILY_DIGEST, "title": "t", "ref_id": "2026-11-27"}
        self.assertIsNotNone(p.decide(ev))
        self.assertIsNone(p.decide(ev))
        self.assertEqual(p.last_reason, att.REASON_DUPLICATE)

    def test_digest_event_respects_quiet_hours(self) -> None:
        import datetime as _dt
        quiet = att.QuietHours.parse("20:00-08:00")
        p = att.ToastPolicy(enabled=True, quiet=quiet,
                            clock=lambda: _dt.datetime(2026, 11, 27, 22, 0))
        self.assertIsNone(p.decide({"kind": att.KIND_DAILY_DIGEST, "title": "t", "ref_id": "d"}))
        self.assertEqual(p.last_reason, att.REASON_QUIET)

    def test_digest_event_disabled_when_toast_off(self) -> None:
        p = att.ToastPolicy(enabled=False)
        self.assertIsNone(p.decide({"kind": att.KIND_DAILY_DIGEST, "title": "t", "ref_id": "d"}))
        self.assertEqual(p.last_reason, att.REASON_DISABLED)


class DigestCatchupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.mem = dc.DigestToastMemory(Path(self._tmp.name) / dc.STATE_FILENAME)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_pops_when_never_popped(self) -> None:
        now = time.time()
        ev = dc.pending_digest_event({"digest": _digest("2026-11-27", now), "markdown": "x"}, self.mem, now=now)
        self.assertIsNotNone(ev)
        self.assertEqual(ev["kind"], att.KIND_DAILY_DIGEST)
        self.assertEqual(ev["ref_id"], "2026-11-27")

    def test_no_repeat_after_marked(self) -> None:
        now = time.time()
        resp = {"digest": _digest("2026-11-27", now)}
        self.mem.mark("2026-11-27")
        self.assertIsNone(dc.pending_digest_event(resp, self.mem, now=now))

    def test_memory_survives_new_instance(self) -> None:
        self.mem.mark("2026-11-27")
        again = dc.DigestToastMemory(self.mem._path)
        self.assertEqual(again.last_day(), "2026-11-27")

    def test_old_digest_not_caught_up(self) -> None:
        now = time.time()
        old = _digest("2026-11-01", now - 10 * 86400)
        self.assertIsNone(dc.pending_digest_event({"digest": old}, self.mem, now=now))

    def test_no_digest_no_event(self) -> None:
        self.assertIsNone(dc.pending_digest_event({"digest": None}, self.mem))
        self.assertIsNone(dc.pending_digest_event(None, self.mem))

    def test_mark_is_atomic_file(self) -> None:
        self.mem.mark("2026-11-27")
        raw = json.loads(self.mem._path.read_text(encoding="utf-8"))
        self.assertEqual(raw["last_popped_day"], "2026-11-27")


if __name__ == "__main__":
    unittest.main()
