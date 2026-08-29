"""
tests/test_daemon_crash_recovery.py

覆盖 next_doc/daemon_crash_recovery_and_alert_plan.md 全部四个阶段：
  - daemon_run_state.json 的写入/读取/"预期停止"标记
  - record_daemon_crash()：诊断信息收集（日志尾部/关联全局异常/摘要文案）
    + 写入 daemon_crash_history.jsonl + 独立告警通道落盘
  - notification/daemon_crash_store.py：崩溃告警的独立存储（append/list/ack）
  - cli/daemon_supervisor.py：后台 run_supervisor() 与前台
    run_foreground_supervisor() 的崩溃 vs 预期停止判定、自动重启预算/退避
  - 阶段四关键用例：模拟崩溃触发告警+自动重启、daemon stop 全程不触发
    误报/重启、重启预算耗尽后正确 giveup、前台 Ctrl-C 视为用户停止不重启、
    崩溃告警即使重启逻辑本身抛异常也已经先发出
"""

from __future__ import annotations

import json
import os
import sys
import time
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional

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


# ── 阶段二：自动重启（预算 + 退避）─────────────────────────────────────────

_CHILD_ALWAYS_CRASH_SCRIPT = """
import sys
sys.exit(1)
"""

_CHILD_RESTART_MARKER_SCRIPT = """
import sys
from pathlib import Path

marker = Path({marker!r})
count = int(marker.read_text()) if marker.exists() else 0
count += 1
marker.write_text(str(count))
if count < {succeed_on}:
    sys.exit(1)
# 第 succeed_on 次成功：模拟"恢复后正常运行到被 daemon stop"——直接标记
# stopped_by_user 然后正常退出，验证重启预算生效后不再是"崩溃"。
sys.path.insert(0, {src!r})
from mini_agent.cli.daemon import mark_stopped_by_user
mark_stopped_by_user(Path({project_root!r}), 1)
sys.exit(0)
"""


class TestSupervisorAutoRestart(_IsolatedHomeTestCase):
    def _src_dir(self) -> str:
        return str(Path(__file__).resolve().parents[1] / "src")

    def test_auto_restart_retries_and_eventually_succeeds(self):
        marker = self.project_root / "restart_count.txt"
        script_path = self.project_root / "child_restart.py"
        script_path.write_text(
            _CHILD_RESTART_MARKER_SCRIPT.format(
                marker=str(marker), succeed_on=3,
                src=self._src_dir(), project_root=str(self.project_root),
            ),
            encoding="utf-8",
        )

        daemon_supervisor.run_supervisor(
            self.project_root,
            [sys.executable, str(script_path)],
            auto_restart=True,
            max_attempts=5,
            window_seconds=600.0,
            backoff_seconds=[0.01, 0.01, 0.01, 0.01, 0.01],
        )

        # 崩溃了 2 次（第 1、2 次退出码非 0），第 3 次成功并优雅停止
        self.assertEqual(int(marker.read_text()), 3)
        hist_path = daemon_mod._crash_history_file(self.project_root)
        lines = hist_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        decisions = [json.loads(l)["restart_decision"] for l in lines]
        self.assertEqual(decisions, ["restarted", "restarted"])

        # 最终优雅停止后 supervisor 不再重启，run_state 停在 stopped_by_user
        state = daemon_mod._read_run_state(self.project_root)
        self.assertEqual(state["status"], daemon_mod._STATUS_STOPPED_BY_USER)

    def test_auto_restart_gives_up_after_budget_exhausted(self):
        script_path = self.project_root / "child_always_crash.py"
        script_path.write_text(_CHILD_ALWAYS_CRASH_SCRIPT, encoding="utf-8")

        daemon_supervisor.run_supervisor(
            self.project_root,
            [sys.executable, str(script_path)],
            auto_restart=True,
            max_attempts=2,
            window_seconds=600.0,
            backoff_seconds=[0.01, 0.01, 0.01],
        )

        hist_path = daemon_mod._crash_history_file(self.project_root)
        lines = hist_path.read_text(encoding="utf-8").splitlines()
        decisions = [json.loads(l)["restart_decision"] for l in lines]
        # 预算内的先 restarted，最后一次超出预算 giveup，不会无限重启
        self.assertEqual(len(decisions), 3)  # max_attempts=2 → 2 次 restarted + 1 次 giveup
        self.assertEqual(decisions, ["restarted", "restarted", "giveup"])

    def test_auto_restart_disabled_never_restarts_regardless_of_budget(self):
        script_path = self.project_root / "child_always_crash.py"
        script_path.write_text(_CHILD_ALWAYS_CRASH_SCRIPT, encoding="utf-8")

        daemon_supervisor.run_supervisor(
            self.project_root,
            [sys.executable, str(script_path)],
            auto_restart=False,
            max_attempts=99,
        )

        hist_path = daemon_mod._crash_history_file(self.project_root)
        lines = hist_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["restart_decision"], "no_restart")


# ── 阶段三：前台 supervisor（run_foreground_supervisor）──────────────────

class TestForegroundSupervisor(_IsolatedHomeTestCase):
    """覆盖 next_doc/daemon_crash_recovery_and_alert_plan.md 阶段三：
    POSIX/Windows 前台模式统一收敛为 Popen+wait 的 supervisor 模型，
    与后台 run_supervisor 共用同一套崩溃判定/记录/重启预算逻辑。"""

    def _src_dir(self) -> str:
        return str(Path(__file__).resolve().parents[1] / "src")

    def test_crash_is_recorded_and_restarted_until_success(self):
        marker = self.project_root / "fg_restart_count.txt"
        script_path = self.project_root / "child_fg_restart.py"
        script_path.write_text(
            _CHILD_RESTART_MARKER_SCRIPT.format(
                marker=str(marker), succeed_on=2,
                src=self._src_dir(), project_root=str(self.project_root),
            ),
            encoding="utf-8",
        )

        returncode = daemon_supervisor.run_foreground_supervisor(
            self.project_root,
            [sys.executable, str(script_path)],
            auto_restart=True,
            max_attempts=5,
            window_seconds=600.0,
            backoff_seconds=[0.01, 0.01, 0.01],
        )

        self.assertEqual(returncode, 0)
        self.assertEqual(int(marker.read_text()), 2)
        hist_path = daemon_mod._crash_history_file(self.project_root)
        lines = hist_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["restart_decision"], "restarted")
        state = daemon_mod._read_run_state(self.project_root)
        self.assertEqual(state["status"], daemon_mod._STATUS_STOPPED_BY_USER)

    def test_gives_up_after_budget_exhausted(self):
        script_path = self.project_root / "child_fg_always_crash.py"
        script_path.write_text(_CHILD_ALWAYS_CRASH_SCRIPT, encoding="utf-8")

        returncode = daemon_supervisor.run_foreground_supervisor(
            self.project_root,
            [sys.executable, str(script_path)],
            auto_restart=True,
            max_attempts=1,
            window_seconds=600.0,
            backoff_seconds=[0.01, 0.01],
        )

        self.assertEqual(returncode, 1)
        hist_path = daemon_mod._crash_history_file(self.project_root)
        decisions = [
            json.loads(l)["restart_decision"]
            for l in hist_path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(decisions, ["restarted", "giveup"])

    def test_graceful_stop_is_not_recorded_as_crash(self):
        script_path = self.project_root / "child_fg_graceful.py"
        script_path.write_text(
            _CHILD_GRACEFUL_SCRIPT.format(
                src=self._src_dir(), project_root=str(self.project_root)
            ),
            encoding="utf-8",
        )

        returncode = daemon_supervisor.run_foreground_supervisor(
            self.project_root,
            [sys.executable, str(script_path)],
            auto_restart=True,
            max_attempts=5,
        )

        self.assertEqual(returncode, 0)
        hist_path = daemon_mod._crash_history_file(self.project_root)
        self.assertFalse(hist_path.exists())

    def test_writes_and_cleans_its_own_pid_file(self):
        script_path = self.project_root / "child_fg_graceful2.py"
        script_path.write_text(
            _CHILD_GRACEFUL_SCRIPT.format(
                src=self._src_dir(), project_root=str(self.project_root)
            ),
            encoding="utf-8",
        )
        pid_path = daemon_mod._supervisor_pid_file(self.project_root)
        self.assertFalse(pid_path.exists())

        daemon_supervisor.run_foreground_supervisor(
            self.project_root,
            [sys.executable, str(script_path)],
            auto_restart=False,
        )

        # 循环结束后（finally 块）应清理掉自己的 PID 文件，不残留。
        self.assertFalse(pid_path.exists())

    def test_keyboard_interrupt_marks_stopped_by_user_and_does_not_restart(self):
        """阶段四关键用例 4：前台模式下 Ctrl-C 视为用户停止，不触发重启。
        通过 monkeypatch `Popen.wait` 在第一次调用时抛 KeyboardInterrupt
        模拟用户按下 Ctrl-C，验证：停止意图被兜底标记、信号被转发给子
        进程、不会记录崩溃也不会重启。"""
        import subprocess
        import unittest.mock as mock

        script_path = self.project_root / "child_fg_sleep.py"
        script_path.write_text(
            "import time\ntime.sleep(30)\n", encoding="utf-8"
        )

        real_popen = subprocess.Popen
        call_count = {"n": 0}
        sent_signals = []

        class _FakeProc:
            def __init__(self, real_proc):
                self._real = real_proc
                self.pid = real_proc.pid
                self._wait_calls = 0

            def wait(self):
                self._wait_calls += 1
                if self._wait_calls == 1:
                    raise KeyboardInterrupt()
                return self._real.wait()

            def send_signal(self, sig):
                sent_signals.append(sig)
                self._real.send_signal(sig)

        def _fake_popen(argv, **kwargs):
            call_count["n"] += 1
            return _FakeProc(real_popen(argv, **kwargs))

        with mock.patch("subprocess.Popen", side_effect=_fake_popen):
            returncode = daemon_supervisor.run_foreground_supervisor(
                self.project_root,
                [sys.executable, str(script_path)],
                auto_restart=True,
                max_attempts=5,
            )

        # 只启动了一次子进程（没有因为"崩溃"而重启）
        self.assertEqual(call_count["n"], 1)
        self.assertTrue(len(sent_signals) >= 1)
        state = daemon_mod._read_run_state(self.project_root)
        self.assertEqual(state["status"], daemon_mod._STATUS_STOPPED_BY_USER)
        hist_path = daemon_mod._crash_history_file(self.project_root)
        self.assertFalse(hist_path.exists())


# ── 阶段四关键用例 2：cmd_daemon_stop 全程不触发任何重启/误报崩溃 ──────────

class TestDaemonStopDoesNotTriggerRestart(_IsolatedHomeTestCase):
    """验证 `daemon stop` 的兜底标记先于一切停止动作发生：即使子进程被
    直接强杀（来不及自己处理信号走 graceful path），supervisor 读到的
    run_state 也已经是 stopped_by_user，不会误判为崩溃、不会重启。"""

    def test_mark_stopped_by_user_prevents_supervisor_restart_after_hard_kill(self):
        import subprocess

        script_path = self.project_root / "child_ignore_sigterm.py"
        # 子进程完全不处理任何信号、也不自己写 stopped_by_user——模拟
        # "daemon stop 降级到强杀"的最坏情况，唯一能阻止误判的只有
        # cmd_daemon_stop 自己提前写好的兜底标记。
        script_path.write_text(
            "import time\ntime.sleep(30)\n", encoding="utf-8"
        )

        proc = subprocess.Popen([sys.executable, str(script_path)])
        try:
            # 模拟 cmd_daemon_stop 的第一步：动手停止之前先兜底标记。
            daemon_mod.mark_stopped_by_user(self.project_root, proc.pid)
            # 模拟第 3 级强杀（子进程根本来不及自己写任何东西）。
            proc.kill()
            returncode = proc.wait()

            # supervisor 侧的判定逻辑：读到 stopped_by_user 就不算崩溃。
            state = daemon_mod._read_run_state(self.project_root)
            self.assertEqual(state["status"], daemon_mod._STATUS_STOPPED_BY_USER)
            hist_path = daemon_mod._crash_history_file(self.project_root)
            self.assertFalse(hist_path.exists())
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_run_supervisor_end_to_end_with_stop_marker_written_before_kill(self):
        """更贴近真实链路：直接跑 run_supervisor，在子进程存活期间由
        测试线程模拟"daemon stop"提前写标记 + 强杀，验证 supervisor 循环
        自然结束、不重启、不记录崩溃。"""
        import subprocess
        import threading

        script_path = self.project_root / "child_stop_race.py"
        script_path.write_text(
            "import time\ntime.sleep(30)\n", encoding="utf-8"
        )

        stopper_started = threading.Event()

        def _stopper():
            # 等 supervisor 把子进程真正拉起来后再动手，模拟用户在另一个
            # 终端执行 `daemon stop`。
            for _ in range(100):
                state = daemon_mod._read_run_state(self.project_root)
                if state and state.get("status") == daemon_mod._STATUS_RUNNING:
                    break
                time.sleep(0.05)
            pid = state["pid"] if state else None
            daemon_mod.mark_stopped_by_user(self.project_root, pid)
            if pid:
                try:
                    import signal as _signal
                    os.kill(pid, _signal.SIGKILL)
                except ProcessLookupError:
                    pass
            stopper_started.set()

        # 子进程启动后自己写一次 "running"（真实 daemon 由 app.py 完成，
        # 这里用一个小脚本模拟）。
        script_path.write_text(
            "import sys, time\n"
            f"sys.path.insert(0, {self._src_dir()!r})\n"
            "from pathlib import Path\n"
            "from mini_agent.cli.daemon import _write_run_state, _STATUS_RUNNING\n"
            "import os\n"
            f"_write_run_state(Path({str(self.project_root)!r}), os.getpid(), _STATUS_RUNNING)\n"
            "time.sleep(30)\n",
            encoding="utf-8",
        )

        t = threading.Thread(target=_stopper, daemon=True)
        t.start()

        daemon_supervisor.run_supervisor(
            self.project_root,
            [sys.executable, str(script_path)],
            auto_restart=True,
            max_attempts=5,
            window_seconds=600.0,
        )
        t.join(timeout=5)

        state = daemon_mod._read_run_state(self.project_root)
        self.assertEqual(state["status"], daemon_mod._STATUS_STOPPED_BY_USER)
        hist_path = daemon_mod._crash_history_file(self.project_root)
        self.assertFalse(hist_path.exists())

    def _src_dir(self) -> str:
        return str(Path(__file__).resolve().parents[1] / "src")


# ── 阶段四关键用例 5：崩溃告警即使重启逻辑本身抛异常也已经发出 ────────────

class TestAlertSentBeforeRestartLogicFails(_IsolatedHomeTestCase):
    """验证"先感知、后恢复"的顺序保证：即便重启预算判断/退避逻辑之后
    抛出异常，崩溃记录 + 告警也已经在那之前落盘完成。"""

    def test_crash_recorded_and_alerted_even_if_sleep_raises(self):
        import unittest.mock as mock

        script_path = self.project_root / "child_crash_then_sleep_fails.py"
        script_path.write_text(_CHILD_ALWAYS_CRASH_SCRIPT, encoding="utf-8")

        with mock.patch(
            "mini_agent.cli.daemon_supervisor.time.sleep",
            side_effect=RuntimeError("boom: 模拟重启退避逻辑本身抛异常"),
        ):
            with self.assertRaises(RuntimeError):
                daemon_supervisor.run_supervisor(
                    self.project_root,
                    [sys.executable, str(script_path)],
                    auto_restart=True,
                    max_attempts=5,
                    window_seconds=600.0,
                    backoff_seconds=[0.01],
                )

        # 尽管重启逻辑（time.sleep 之后的重启步骤）整个异常向上抛出，
        # 崩溃记录和告警在那之前就已经写入完成。
        hist_path = daemon_mod._crash_history_file(self.project_root)
        self.assertTrue(hist_path.exists())
        lines = hist_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["restart_decision"], "restarted")

        paths = AgentPaths(self.project_root)
        alerts = daemon_crash_store.list_crash_alerts(paths)
        self.assertEqual(len(alerts), 1)


# ── daemon_hang_detection_and_alert_escalation_plan.md 阶段一：卡死检测 ──

_CHILD_SLEEP_FOREVER_SCRIPT = """
import os
import time
from pathlib import Path

Path({pid_marker!r}).write_text(str(os.getpid()))
time.sleep(3600)
"""


class TestHangDetection(_IsolatedHomeTestCase):
    """子进程存活但对健康检查完全无响应，应被判定为卡死、强杀、记录为
    `restart_decision == "hang_killed"`，且不能和"进程自己退出"的崩溃场景
    混为一谈（见计划 §1 用例 1）。"""

    def _src_dir(self) -> str:
        return str(Path(__file__).resolve().parents[1] / "src")

    def _write_sleep_forever_script(self, pid_marker: Optional[Path] = None) -> Path:
        script_path = self.project_root / "child_sleep_forever.py"
        script_path.write_text(
            _CHILD_SLEEP_FOREVER_SCRIPT.format(
                pid_marker=str(pid_marker or (self.project_root / "child.pid"))
            ),
            encoding="utf-8",
        )
        return script_path

    def test_unresponsive_process_is_detected_killed_and_recorded(self):
        script_path = self._write_sleep_forever_script()

        with unittest.mock.patch.object(
            daemon_mod.DaemonClient, "health_check", return_value=False
        ):
            daemon_supervisor.run_supervisor(
                self.project_root,
                [sys.executable, str(script_path)],
                auto_restart=False,
                http_port=1,  # 端口本身不重要，health_check 已被 mock
                hang_detection_enabled=True,
                hang_check_interval_seconds=0.05,
                hang_check_timeout_seconds=0.05,
                hang_consecutive_failures=2,
            )

        hist_path = daemon_mod._crash_history_file(self.project_root)
        lines = hist_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertEqual(record["restart_decision"], "hang_killed")
        self.assertIsNone(record["exit_code"])
        self.assertIsNone(record["last_exception"])
        self.assertIn("卡死", record["summary"])
        self.assertIn("健康检查无响应", record["summary"])

    def test_intermittent_health_check_success_resets_failure_counter(self):
        """健康检查偶尔成功一次不应该被判定为卡死——连续失败计数需要在
        探测成功后清零，避免正常场景下的一次慢响应被误判。"""
        pid_marker = self.project_root / "child.pid"
        script_path = self._write_sleep_forever_script(pid_marker)

        call_count = {"n": 0}

        def _flaky_health_check(self_client, timeout=2.0):
            call_count["n"] += 1
            # 每 3 次里成功 1 次，连续失败次数永远达不到阈值 2。
            return call_count["n"] % 3 == 0

        with unittest.mock.patch.object(
            daemon_mod.DaemonClient, "health_check", _flaky_health_check
        ):
            proc_result = {}

            def _run():
                proc_result["rc"] = daemon_supervisor.run_supervisor(
                    self.project_root,
                    [sys.executable, str(script_path)],
                    auto_restart=False,
                    http_port=1,
                    hang_detection_enabled=True,
                    hang_check_interval_seconds=0.02,
                    hang_check_timeout_seconds=0.02,
                    hang_consecutive_failures=2,
                )

            import threading
            t = threading.Thread(target=_run, daemon=True)
            t.start()
            # 跑一小段时间后主动标记 stopped_by_user 并结束子进程，
            # 验证这段时间内没有被误判为卡死（不会产生任何崩溃记录）。
            time.sleep(0.3)
            child_pid = None
            if pid_marker.exists():
                try:
                    child_pid = int(pid_marker.read_text().strip())
                except (ValueError, OSError):
                    child_pid = None
            daemon_mod.mark_stopped_by_user(self.project_root, child_pid or 0)
            if child_pid:
                daemon_mod._force_kill_process(child_pid)
            t.join(timeout=5)

        hist_path = daemon_mod._crash_history_file(self.project_root)
        self.assertFalse(hist_path.exists())

    def test_hang_detection_disabled_falls_back_to_plain_wait(self):
        """`hang_detection_enabled=False` 时完全不做探活，行为与阶段一
        之前完全一致（不会因为 http_port 缺失/health_check 失败而被
        误杀）。用一个正常快速退出的子进程验证不受影响。"""
        marker = self.project_root / "ran.txt"
        script_path = self.project_root / "child_quick_exit.py"
        script_path.write_text(
            f"from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('1')\n",
            encoding="utf-8",
        )

        with unittest.mock.patch.object(
            daemon_mod.DaemonClient, "health_check", return_value=False
        ):
            daemon_supervisor.run_supervisor(
                self.project_root,
                [sys.executable, str(script_path)],
                auto_restart=False,
                http_port=1,
                hang_detection_enabled=False,
            )

        self.assertTrue(marker.exists())
        hist_path = daemon_mod._crash_history_file(self.project_root)
        # 脚本正常退出码为 0，但没有走 stopped_by_user 标记，所以仍会被
        # 判定为"崩溃"（这是既有行为，与卡死检测无关）——这里只关心
        # restart_decision 不是 "hang_killed"，证明探活分支完全没有介入。
        lines = hist_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(lines[0])["restart_decision"], "no_restart")

    def test_no_http_port_falls_back_to_plain_wait(self):
        """`http_port=None`（比如调用方没能读到端口配置）时同样退化为
        不探活，不应该因为拿不到端口就报错或误杀。"""
        marker = self.project_root / "ran2.txt"
        script_path = self.project_root / "child_quick_exit2.py"
        script_path.write_text(
            f"from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('1')\n",
            encoding="utf-8",
        )

        daemon_supervisor.run_supervisor(
            self.project_root,
            [sys.executable, str(script_path)],
            auto_restart=False,
            http_port=None,
            hang_detection_enabled=True,
        )

        self.assertTrue(marker.exists())


# ── daemon_hang_detection_and_alert_escalation_plan.md 阶段二 ────────────

class TestRestartBudgetPersistence(_IsolatedHomeTestCase):
    """§2.1：重启预算判断不再只看内存计数，要与
    `daemon_crash_history.jsonl` 里最近 window_seconds 内的
    `restarted`/`hang_killed` 记录数取较大值，保证跨 supervisor 生命周期
    延续。"""

    def test_count_recent_restart_events_only_counts_restarted_and_hang_killed(self):
        hist_path = daemon_mod._crash_history_file(self.project_root)
        hist_path.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        records = [
            {"timestamp": now - 10, "restart_decision": "restarted"},
            {"timestamp": now - 20, "restart_decision": "hang_killed"},
            {"timestamp": now - 30, "restart_decision": "giveup"},
            {"timestamp": now - 40, "restart_decision": "no_restart"},
            {"timestamp": now - 9999, "restart_decision": "restarted"},  # 超出窗口
        ]
        hist_path.write_text(
            "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
        )
        count = daemon_mod.count_recent_restart_events(self.project_root, window_seconds=600)
        self.assertEqual(count, 2)  # 只有窗口内的 restarted + hang_killed

    def test_budget_persists_across_supervisor_lifecycles(self):
        """预置一批"历史崩溃记录"（模拟上一个 supervisor 生命周期已经
        用掉的重启次数），新的 supervisor 实例（内存计数从零开始）应该
        据此正确判断预算已经耗尽，直接 giveup，不再重启。"""
        hist_path = daemon_mod._crash_history_file(self.project_root)
        hist_path.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        # 预置 2 条 "restarted" 记录，配合 max_attempts=2，预算已经用满。
        records = [
            {"timestamp": now - 5, "restart_decision": "restarted"},
            {"timestamp": now - 3, "restart_decision": "restarted"},
        ]
        hist_path.write_text(
            "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
        )

        script_path = self.project_root / "child_always_crash.py"
        script_path.write_text(_CHILD_ALWAYS_CRASH_SCRIPT, encoding="utf-8")

        # 全新的 supervisor 实例（内存里的 restart_timestamps 从零开始），
        # 但磁盘历史里已经有 2 条 restarted——按 §2.1，本次崩溃应直接判定
        # 预算耗尽（giveup），不会先"restarted"一次再耗尽。
        daemon_supervisor.run_supervisor(
            self.project_root,
            [sys.executable, str(script_path)],
            auto_restart=True,
            max_attempts=2,
            window_seconds=600.0,
            backoff_seconds=[0.01],
        )

        lines = hist_path.read_text(encoding="utf-8").splitlines()
        # 预置的 2 条 + 这次新增的 1 条
        self.assertEqual(len(lines), 3)
        self.assertEqual(json.loads(lines[-1])["restart_decision"], "giveup")

    def test_memory_count_still_wins_when_history_file_missing(self):
        """历史文件缺失/读取失败时预算判断退化为只看内存计数，不阻断
        主流程（`count_recent_restart_events` 内部已经兜底返回 0）。"""
        script_path = self.project_root / "child_always_crash.py"
        script_path.write_text(_CHILD_ALWAYS_CRASH_SCRIPT, encoding="utf-8")

        daemon_supervisor.run_supervisor(
            self.project_root,
            [sys.executable, str(script_path)],
            auto_restart=True,
            max_attempts=2,
            window_seconds=600.0,
            backoff_seconds=[0.01, 0.01, 0.01],
        )

        hist_path = daemon_mod._crash_history_file(self.project_root)
        lines = hist_path.read_text(encoding="utf-8").splitlines()
        decisions = [json.loads(l)["restart_decision"] for l in lines]
        # 没有预置历史文件时，行为应与阶段一/纯内存计数完全一致。
        self.assertEqual(decisions, ["restarted", "restarted", "giveup"])


class TestPostRestartHealthCheck(_IsolatedHomeTestCase):
    """§2.2：每次 Popen 新子进程后先给它一个固定窗口期证明自己真的把
    HTTP 服务起来了，超时未通过按卡死处理，不必等常规探活轮询的多轮
    判定。"""

    def test_process_never_becomes_healthy_is_killed_as_hang(self):
        script_path = self.project_root / "child_sleep_forever.py"
        script_path.write_text(
            _CHILD_SLEEP_FOREVER_SCRIPT.format(
                pid_marker=str(self.project_root / "child.pid")
            ),
            encoding="utf-8",
        )

        with unittest.mock.patch.object(
            daemon_mod.DaemonClient, "health_check", return_value=False
        ):
            daemon_supervisor.run_supervisor(
                self.project_root,
                [sys.executable, str(script_path)],
                auto_restart=False,
                http_port=1,
                hang_detection_enabled=True,
                hang_check_interval_seconds=0.05,
                hang_check_timeout_seconds=0.05,
                hang_consecutive_failures=3,
                post_restart_health_check_seconds=0.15,
            )

        hist_path = daemon_mod._crash_history_file(self.project_root)
        lines = hist_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertEqual(record["restart_decision"], "hang_killed")
        self.assertIn("重启后", record["summary"])
        self.assertIn("未通过健康检查", record["summary"])

    def test_process_becomes_healthy_within_window_is_not_killed(self):
        """启动阶段健康检查一次成功后，应该正常进入常规探活轮询（不会
        被判定为卡死），子进程随后正常退出时按普通"崩溃"记录。"""
        script_path = self.project_root / "child_always_crash.py"
        script_path.write_text(_CHILD_ALWAYS_CRASH_SCRIPT, encoding="utf-8")

        with unittest.mock.patch.object(
            daemon_mod.DaemonClient, "health_check", return_value=True
        ):
            daemon_supervisor.run_supervisor(
                self.project_root,
                [sys.executable, str(script_path)],
                auto_restart=False,
                http_port=1,
                hang_detection_enabled=True,
                hang_check_interval_seconds=0.05,
                hang_check_timeout_seconds=0.05,
                hang_consecutive_failures=2,
                post_restart_health_check_seconds=0.2,
            )

        hist_path = daemon_mod._crash_history_file(self.project_root)
        lines = hist_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        # 健康检查一直返回 True，走的是正常"进程退出"路径，不是卡死。
        self.assertEqual(record["restart_decision"], "no_restart")
        self.assertIsNotNone(record["exit_code"])

    def test_process_exits_during_health_check_window_is_treated_as_normal_exit(self):
        """启动健康检查窗口期内子进程自己就退出了（比如配置错误直接
        崩了），应该按正常"进程退出"处理，不算卡死——被强杀的语义只适用
        于"进程还活着但不响应"的场景。"""
        script_path = self.project_root / "child_quick_crash.py"
        script_path.write_text("import sys, time\ntime.sleep(0.05)\nsys.exit(7)\n", encoding="utf-8")

        with unittest.mock.patch.object(
            daemon_mod.DaemonClient, "health_check", return_value=False
        ):
            daemon_supervisor.run_supervisor(
                self.project_root,
                [sys.executable, str(script_path)],
                auto_restart=False,
                http_port=1,
                hang_detection_enabled=True,
                hang_check_interval_seconds=0.05,
                hang_check_timeout_seconds=0.05,
                hang_consecutive_failures=2,
                post_restart_health_check_seconds=5.0,
            )

        hist_path = daemon_mod._crash_history_file(self.project_root)
        lines = hist_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertEqual(record["restart_decision"], "no_restart")
        self.assertEqual(record["exit_code"], 7)

    def test_disabled_by_default_zero_does_not_delay_normal_flow(self):
        """`post_restart_health_check_seconds=0`（函数默认值）完全跳过这
        项检查，行为与阶段一完全一致——保证没有显式配置这个新参数的调用方
        （包括阶段一写的既有测试）不受影响。"""
        script_path = self.project_root / "child_always_crash.py"
        script_path.write_text(_CHILD_ALWAYS_CRASH_SCRIPT, encoding="utf-8")

        start = time.time()
        with unittest.mock.patch.object(
            daemon_mod.DaemonClient, "health_check", return_value=False
        ):
            daemon_supervisor.run_supervisor(
                self.project_root,
                [sys.executable, str(script_path)],
                auto_restart=False,
                http_port=1,
                hang_detection_enabled=True,
                hang_check_interval_seconds=0.05,
                hang_check_timeout_seconds=0.05,
                hang_consecutive_failures=2,
                # post_restart_health_check_seconds 不传，默认 0.0
            )
        elapsed = time.time() - start
        self.assertLess(elapsed, 2.0)

        hist_path = daemon_mod._crash_history_file(self.project_root)
        lines = hist_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(lines[0])["restart_decision"], "no_restart")


if __name__ == "__main__":
    unittest.main()
