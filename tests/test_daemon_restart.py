"""看板“重启 daemon”：daemon 侧 restart 流程 + 看板侧权限/分离启动/审计。"""
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

import mini_agent.cli.daemon as d

KANBAN = Path(__file__).resolve().parents[1] / "apps" / "mini_agent_kanban"
sys.path.insert(0, str(KANBAN))
import daemon_restart as dr  # noqa: E402


# ── daemon 侧 ───────────────────────────────────────────────────────────────

def test_save_and_load_launch_config_roundtrip(tmp_path):
    d._save_launch_config(tmp_path, 19100, ["--http-multi-user"])
    assert d._load_launch_config(tmp_path) == {"http_port": 19100, "extra_argv": ["--http-multi-user"]}


def test_load_launch_config_missing_returns_none(tmp_path):
    assert d._load_launch_config(tmp_path) is None


def test_restart_stops_waits_then_starts_with_recorded_args(tmp_path, monkeypatch):
    d._save_launch_config(tmp_path, 19100, ["--http-multi-user"])
    order = []
    monkeypatch.setattr(d, "_read_daemon_info", lambda root: None)
    monkeypatch.setattr(d, "cmd_daemon_stop", lambda root: order.append("stop") or 0)
    monkeypatch.setattr(d, "_daemon_fully_gone", lambda root: order.append("wait") or True)

    def fake_start(root, http_port, detach, extra_argv):
        order.append(("start", http_port, detach, list(extra_argv)))
        return 0
    monkeypatch.setattr(d, "cmd_daemon_start", fake_start)

    assert d.cmd_daemon_restart(tmp_path) == 0
    assert order == ["stop", "wait", ("start", 19100, True, ["--http-multi-user"])]
    st = json.loads((tmp_path / ".agent" / "daemon_restart_status.json").read_text(encoding="utf-8"))
    assert st["stage"] == "done"


def test_restart_fails_without_starting_when_old_daemon_never_exits(tmp_path, monkeypatch):
    monkeypatch.setattr(d, "_read_daemon_info", lambda root: {"pid": 1, "http_port": 8765})
    monkeypatch.setattr(d, "cmd_daemon_stop", lambda root: 0)
    monkeypatch.setattr(d, "_daemon_fully_gone", lambda root: False)
    started = []
    monkeypatch.setattr(d, "cmd_daemon_start", lambda *a, **k: started.append(1) or 0)
    monkeypatch.setattr(d.time, "sleep", lambda s: None)
    assert d.cmd_daemon_restart(tmp_path, wait_timeout_seconds=0) == 1
    assert started == []
    st = json.loads((tmp_path / ".agent" / "daemon_restart_status.json").read_text(encoding="utf-8"))
    assert st["stage"] == "failed"


def test_restart_refuses_when_another_restart_is_running(tmp_path, monkeypatch):
    (tmp_path / ".agent").mkdir()
    (tmp_path / ".agent" / "daemon_restart_status.json").write_text(json.dumps(
        {"stage": "waiting", "updated_at": __import__("time").time(), "pid": 999999}), encoding="utf-8")
    monkeypatch.setattr(d, "_is_process_alive", lambda pid: True)
    stopped = []
    monkeypatch.setattr(d, "cmd_daemon_stop", lambda root: stopped.append(1) or 0)
    assert d.cmd_daemon_restart(tmp_path) == 1
    assert stopped == []


def test_restart_without_launch_record_uses_previous_port(tmp_path, monkeypatch):
    monkeypatch.setattr(d, "_read_daemon_info", lambda root: {"pid": 1, "http_port": 19222})
    monkeypatch.setattr(d, "cmd_daemon_stop", lambda root: 0)
    monkeypatch.setattr(d, "_daemon_fully_gone", lambda root: True)
    seen = {}
    monkeypatch.setattr(d, "cmd_daemon_start",
                        lambda root, http_port, detach, extra_argv: seen.update(port=http_port, extra=extra_argv) or 0)
    assert d.cmd_daemon_restart(tmp_path) == 0
    assert seen == {"port": 19222, "extra": []}


def test_restart_subcommand_is_dispatched(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(d, "cmd_daemon_restart", lambda root: called.append(root) or 0)
    assert d.run_daemon_cli(["restart"], tmp_path) == 0
    assert called == [tmp_path]


# ── 看板侧 ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("require_login,user,admin,expected", [
    (True, "alice", True, True),
    (True, "bob", False, False),       # 非管理员不可用
    (True, "", True, False),           # 未登录不可用
    (False, "alice", True, False),     # 未开启登录门禁时一律不可用
])
def test_can_restart_only_for_logged_in_admins(require_login, user, admin, expected):
    assert dr.can_restart(require_login, user, admin) is expected


def test_launch_fails_cleanly_without_main_py(tmp_path):
    ok, msg = dr.launch_restart(tmp_path, "alice")
    assert ok is False and "main.py" in msg
    audit = (tmp_path / ".agent" / "daemon_restart_audit.log").read_text(encoding="utf-8")
    assert "alice" in audit and "failed" in audit


def test_launch_spawns_detached_restart_command_and_audits(tmp_path, monkeypatch):
    (tmp_path / "main.py").write_text("print('x')", encoding="utf-8")
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env", {})
        return mock.Mock()
    monkeypatch.setattr(dr.subprocess, "Popen", fake_popen)

    ok, _ = dr.launch_restart(tmp_path, "alice", python_exe="/usr/bin/python3")
    assert ok is True
    assert captured["cmd"][:3] == ["/usr/bin/python3", str(tmp_path / "main.py"), "daemon"]
    assert captured["cmd"][3:] == ["restart", "--project", str(tmp_path)]
    assert captured["env"]["MINI_AGENT_RESTART_REQUESTED_BY"] == "alice"
    audit = (tmp_path / ".agent" / "daemon_restart_audit.log").read_text(encoding="utf-8")
    assert "submitted" in audit and "alice" in audit


def test_launch_refused_while_busy(tmp_path, monkeypatch):
    (tmp_path / "main.py").write_text("", encoding="utf-8")
    (tmp_path / ".agent").mkdir()
    (tmp_path / ".agent" / "daemon_restart_status.json").write_text(json.dumps(
        {"stage": "starting", "updated_at": __import__("time").time()}), encoding="utf-8")
    popen = mock.Mock()
    monkeypatch.setattr(dr.subprocess, "Popen", popen)
    ok, _ = dr.launch_restart(tmp_path, "alice")
    assert ok is False
    popen.assert_not_called()


def test_stale_status_does_not_lock_the_button(tmp_path):
    status = {"stage": "waiting", "updated_at": __import__("time").time() - 3600}
    assert dr.is_busy(status) is False
