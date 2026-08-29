"""
tests/test_daemon_crash_recovery.py

覆盖 next_doc/daemon_crash_recovery_and_alert_plan.md 阶段一：
  - daemon_run_state.json 的写入/读取/"预期停止"标记
  - record_daemon_crash()：诊断信息收集（日志尾部/关联全局异常/摘要文案）
    + 写入 daemon_crash_history.jsonl + 独立告警通道落盘
  - notification/daemon_crash_store.py：崩溃告警的独立存储（append/list/ack）
  - cli/daemon_supervisor.py：崩溃 vs 预期停止的判定 + （阶段一）不自动重启
"""

from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from mini_agent.cli import daemon as daemon_mod
from mini_agent.cli import daemon_supervisor
from mini_agent.notification import daemon_crash_store
from mini_agent.storage.paths import AgentPaths


class _IsolatedHomeTestCase(unittest.TestCase):
    """把 ~/.agent（全局异常日志所在目录）也隔离到临时目录，避免测试
    读写到真实的用户主目录。"""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.project_root = Path(self._tmp.name) / "project"
        self.project_root.mkdir(parents=True)
        self._home = Path(self._tmp.name) / "home"
        self._home.mkdir(parents=True)
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self._home)

    def tearDown(self):
        if self._old_home is not None:
            os.environ["HOME"] = self._old_home
        else:
            os.environ.pop("HOME", None)
        self._tmp.cleanup()


# ── run_state 标记 ───────────────────────────────────────────────────────

class TestRunState(_IsolatedHomeTestCase):
    def test_default_is_none_until_written(self):
        self.assertIsNone(daemon_mod._read_run_state(self.project_root))

    def test_write_and_read_running(self):
        daemon_mod._write_run_state(self.project_root, 4321, daemon_mod._STATUS_RUNNING)
        state = daemon_mod._read_run_state(self.project_root)
        self.assertEqual(state["pid"], 4321)
        self.assertEqual(state["status"], daemon_mod._STATUS_RUNNING)

    def test_mark_stopped_by_user_overwrites_running(self):
        daemon_mod._write_run_state(self.project_root, 4321, daemon_mod._STATUS_RUNNING)
        daemon_mod.mark_stopped_by_user(self.project_root, 4321)
        state = daemon_mod._read_run_state(self.project_root)
        self.assertEqual(state["status"], daemon_mod._STATUS_STOPPED_BY_USER)

    def test_mark_stopped_by_user_without_explicit_pid_reuses_existing(self):
        daemon_mod._write_run_state(self.project_root, 999, daemon_mod._STATUS_RUNNING)
        daemon_mod.mark_stopped_by_user(self.project_root)  # 不传 pid，从已有状态里取
        state = daemon_mod._read_run_state(self.project_root)
        self.assertEqual(state["pid"], 999)
        self.assertEqual(state["status"], daemon_mod._STATUS_STOPPED_BY_USER)

    def test_write_run_state_never_raises_on_bad_parent(self):
        # 父目录路径被一个同名文件占用，写入应该静默失败而不是抛异常
        bad_root = self.project_root / "not_a_dir"
        bad_root.write_text("occupied")
        # 不应该抛异常
        daemon_mod._write_run_state(bad_root, 1, daemon_mod._STATUS_RUNNING)


# ── record_daemon_crash：诊断信息收集 + 崩溃历史 + 告警 ──────────────────────

class TestRecordDaemonCrash(_IsolatedHomeTestCase):
    def test_crash_history_written_with_diagnostics(self):
        log_path = self.project_root / ".agent" / "daemon.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("line1\nline2\nTraceback...\n", encoding="utf-8")

        started_at = time.time() - 120
        record = daemon_mod.record_daemon_crash(
            self.project_root,
            pid=555,
            exit_code=-9,
            started_at=started_at,
            log_path=log_path,
            restart_attempt=0,
            restart_decision="no_restart",
        )

        self.assertEqual(record["pid"], 555)
        self.assertEqual(record["exit_code"], -9)
        self.assertIn("疑似 OOM", record["summary"])
        self.assertGreater(record["uptime_seconds"], 100)
        self.assertEqual(record["log_tail"], ["line1", "line2", "Traceback..."])

        # 崩溃历史文件确实落盘了这一条
        hist_path = daemon_mod._crash_history_file(self.project_root)
        self.assertTrue(hist_path.exists())
        lines = hist_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["pid"], 555)

    def test_crash_alert_written_to_dedicated_store_not_generic_reports(self):
        log_path = self.project_root / ".agent" / "daemon.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("", encoding="utf-8")

        daemon_mod.record_daemon_crash(
            self.project_root, pid=1, exit_code=1, started_at=time.time(),
            log_path=log_path,
        )

        paths = AgentPaths(self.project_root)
        alerts = daemon_crash_store.list_crash_alerts(paths, unacknowledged_only=True)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["pid"], 1)

        # 崩溃告警刻意不应该出现在通用的 watchlist_report 存储里
        self.assertFalse(paths.notification_reports.exists())

    def test_reason_without_captured_exception_is_explicit(self):
        # 没有 pid 匹配的全局异常记录、退出码也非负数信号时，摘要应该
        # 明确写"未捕获到 Python 异常"，不能瞎猜出一个具体原因
        log_path = self.project_root / ".agent" / "daemon.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("", encoding="utf-8")
        record = daemon_mod.record_daemon_crash(
            self.project_root, pid=2, exit_code=1, started_at=time.time(),
            log_path=log_path,
        )
        self.assertIsNone(record["last_exception"])
        self.assertIn("未捕获到 Python 异常", record["summary"])

    def test_matches_last_global_exception_by_pid(self):
        # 不走 log_exception()（它内部用了模块级单例 handler，一旦在同一
        # 测试进程里被更早的用例以旧 HOME 初始化过，后续改 HOME 也不会
        # 换文件，属于测试环境的单例缓存问题，不是 record_daemon_crash
        # 该覆盖的行为）——直接照 log_exception() 落盘的字段格式，往
        # record_daemon_crash 实际会去读的那个路径写一条，测的是"读取+
        # 匹配"这一段逻辑本身。
        paths = AgentPaths(self.project_root)
        paths.global_error_log.parent.mkdir(parents=True, exist_ok=True)
        fake_pid = os.getpid()
        with open(paths.global_error_log, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": "2026-08-29 00:00:00", "pid": fake_pid, "thread": "MainThread",
                "where": "test.synthetic", "exc_type": "ValueError",
                "message": "boom", "traceback": "...",
            }, ensure_ascii=False) + "\n")

        log_path = self.project_root / ".agent" / "daemon.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("", encoding="utf-8")

        record = daemon_mod.record_daemon_crash(
            self.project_root, pid=fake_pid, exit_code=1,
            started_at=time.time(), log_path=log_path,
        )
        self.assertIsNotNone(record["last_exception"])
        self.assertEqual(record["last_exception"]["message"], "boom")
        self.assertIn("ValueError", record["summary"])


# ── notification/daemon_crash_store.py ──────────────────────────────────

class TestDaemonCrashStore(_IsolatedHomeTestCase):
    def setUp(self):
        super().setUp()
        self.paths = AgentPaths(self.project_root)

    def test_append_and_list(self):
        rec = daemon_crash_store.append_crash_alert(self.paths, {"pid": 1, "summary": "x"})
        self.assertIn("alert_id", rec)
        self.assertFalse(rec["acknowledged"])
        alerts = daemon_crash_store.list_crash_alerts(self.paths)
        self.assertEqual(len(alerts), 1)

    def test_unacknowledged_only_filters_out_acked(self):
        rec = daemon_crash_store.append_crash_alert(self.paths, {"pid": 1})
        daemon_crash_store.acknowledge_crash_alert(self.paths, rec["alert_id"])
        self.assertEqual(daemon_crash_store.list_crash_alerts(self.paths, unacknowledged_only=True), [])
        # 全量历史仍然能看到（已确认）
        all_alerts = daemon_crash_store.list_crash_alerts(self.paths, unacknowledged_only=False)
        self.assertEqual(len(all_alerts), 1)
        self.assertTrue(all_alerts[0]["acknowledged"])

    def test_acknowledge_unknown_id_returns_false(self):
        self.assertFalse(daemon_crash_store.acknowledge_crash_alert(self.paths, "does-not-exist"))

    def test_count_unacknowledged(self):
        daemon_crash_store.append_crash_alert(self.paths, {"pid": 1})
        daemon_crash_store.append_crash_alert(self.paths, {"pid": 2})
        self.assertEqual(daemon_crash_store.count_unacknowledged_crash_alerts(self.paths), 2)


# ── supervisor：崩溃 vs 预期停止的判定（阶段一：只观测，不重启）──────────────

_CHILD_CRASH_SCRIPT = """
import sys
# 模拟崩溃：非零退出，且不写 stopped_by_user 标记
sys.exit(1)
"""

_CHILD_GRACEFUL_SCRIPT = """
import sys
sys.path.insert(0, {src!r})
from pathlib import Path
from mini_agent.cli.daemon import mark_stopped_by_user
mark_stopped_by_user(Path({project_root!r}), 12345)
sys.exit(0)
"""


class TestSupervisorCrashDetection(_IsolatedHomeTestCase):
    def _src_dir(self) -> str:
        # tests/ 目录下运行，src 布局固定为 <repo>/src
        return str(Path(__file__).resolve().parents[1] / "src")

    def test_crash_is_recorded_when_child_exits_without_stop_marker(self):
        script_path = self.project_root / "child_crash.py"
        script_path.write_text(_CHILD_CRASH_SCRIPT, encoding="utf-8")

        daemon_supervisor.run_supervisor(
            self.project_root,
            [sys.executable, str(script_path)],
            auto_restart=False,
        )

        hist_path = daemon_mod._crash_history_file(self.project_root)
        self.assertTrue(hist_path.exists())
        lines = hist_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertEqual(record["restart_decision"], "no_restart")

        # 阶段一：不重启，supervisor 循环只跑一轮就结束
        paths = AgentPaths(self.project_root)
        alerts = daemon_crash_store.list_crash_alerts(paths)
        self.assertEqual(len(alerts), 1)

    def test_graceful_stop_is_not_recorded_as_crash(self):
        script_path = self.project_root / "child_graceful.py"
        script_path.write_text(
            _CHILD_GRACEFUL_SCRIPT.format(
                src=self._src_dir(), project_root=str(self.project_root)
            ),
            encoding="utf-8",
        )

        daemon_supervisor.run_supervisor(
            self.project_root,
            [sys.executable, str(script_path)],
            auto_restart=False,
        )

        hist_path = daemon_mod._crash_history_file(self.project_root)
        self.assertFalse(hist_path.exists())
        paths = AgentPaths(self.project_root)
        self.assertEqual(daemon_crash_store.list_crash_alerts(paths), [])

    def test_supervisor_writes_and_cleans_its_own_pid_file(self):
        script_path = self.project_root / "child_crash.py"
        script_path.write_text(_CHILD_CRASH_SCRIPT, encoding="utf-8")

        sup_pid_path = daemon_mod._supervisor_pid_file(self.project_root)
        self.assertFalse(sup_pid_path.exists())

        daemon_supervisor.run_supervisor(
            self.project_root,
            [sys.executable, str(script_path)],
            auto_restart=False,
        )
        # 循环结束后 supervisor 会在 finally 里清理自己的 pid 文件
        self.assertFalse(sup_pid_path.exists())


if __name__ == "__main__":
    unittest.main()
