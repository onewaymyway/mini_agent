"""
执行命令型 cron（run_mode="command"）。对应 next_doc/cron_command_job_plan.md。

覆盖：CronJob 序列化兼容；add/update 校验；runner（成功/失败/超时杀进程树/输出尾部截断/
cwd 与环境变量/unmanaged 不占槽位/managed 仍占）；agent 工具路径 deny；反馈与调优不能改写命令；CLI add-cmd 解析。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from mini_agent.evolution.cron_job_runner import CronJobRunner
from mini_agent.evolution.cron_job_workspace import CronJobWorkspace
from mini_agent.evolution.cron_scheduler import CronJob, CronScheduler
from mini_agent.storage.paths import AgentPaths


class _Cron:
    command_default_timeout_seconds = 600
    command_max_timeout_seconds = 3600
    command_output_tail_bytes = 200


class _Cfg:
    cron = _Cron()


def _wait(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def _py(code: str) -> str:
    # 用双引号包整段，内部只用单引号，cmd.exe 与 sh 都能解析
    return f'"{sys.executable}" -c "{code}"'


@pytest.fixture
def env(tmp_path):
    workdir = tmp_path / "wd"
    workdir.mkdir()
    paths = AgentPaths(project_root=workdir)
    runner = CronJobRunner(_Cfg(), paths, max_concurrent=1)
    cs = CronScheduler(paths, job_runner=runner)
    cs.load()
    return cs, runner, paths, workdir


def _run(env, job, wait=True):
    cs, runner, paths, _ = env
    assert runner.submit(job) is True
    if wait:
        assert _wait(lambda: not runner.is_running(job.id)), "job 未在时限内结束"
    return CronJobWorkspace(paths, job.id)


def _cmd_job(cs, command, **kw):
    return cs.add_command_job("t", "interval:3600", command, **kw)


# ── 数据层 ────────────────────────────────────────────────────────────────

def test_roundtrip_and_legacy_defaults():
    j = CronJob(id="user:x", name="n", schedule="interval:60", task_template="",
                run_mode="command", command="echo hi", cwd="/tmp", timeout_sec=30)
    r = CronJob.from_dict(j.to_dict())
    assert (r.command, r.cwd, r.timeout_sec) == ("echo hi", "/tmp", 30)
    legacy = j.to_dict()
    for k in ("command", "cwd", "timeout_sec"):
        legacy.pop(k)
    r = CronJob.from_dict(legacy)
    assert (r.command, r.cwd, r.timeout_sec) == ("", "", None)


def test_add_command_job_defaults_and_validation(env):
    cs, _, _, workdir = env
    j = _cmd_job(cs, "echo hi")
    assert j.run_mode == "command" and j.concurrency == "managed"
    assert j.timeout_sec == 600 and j.cwd == "" and j.task_template == ""
    assert cs.get(j.id) is j

    with pytest.raises(ValueError):
        _cmd_job(cs, "   ")
    with pytest.raises(ValueError):
        _cmd_job(cs, "x", concurrency="fast")
    with pytest.raises(ValueError):
        _cmd_job(cs, "x", timeout_sec=0)
    with pytest.raises(ValueError):
        _cmd_job(cs, "x", timeout_sec=99999)
    with pytest.raises(ValueError):
        _cmd_job(cs, "x", timeout_sec=True)
    with pytest.raises(ValueError):
        _cmd_job(cs, "x", cwd=str(workdir / "nope"))
    ok = _cmd_job(cs, "x", cwd=str(workdir), timeout_sec=5, concurrency="UNMANAGED")
    assert ok.cwd == str(workdir.resolve()) or ok.cwd == str(workdir)
    assert ok.concurrency == "unmanaged"


def test_persisted_across_reload(env):
    cs, _, paths, _ = env
    j = _cmd_job(cs, "echo hi", timeout_sec=7)
    cs2 = CronScheduler(paths)
    cs2.load()
    got = cs2.get(j.id)
    assert got.run_mode == "command" and got.command == "echo hi" and got.timeout_sec == 7


def test_update_command_job(env):
    cs, _, _, _ = env
    j = _cmd_job(cs, "echo a")
    cs.update_command_job(j.id, command="echo b", timeout_sec=9)
    assert (j.command, j.timeout_sec) == ("echo b", 9)
    with pytest.raises(ValueError):
        cs.update_command_job(j.id, timeout_sec=10**6)
    assert j.timeout_sec == 9                       # 校验失败不产生部分修改
    with pytest.raises(ValueError):
        cs.update_command_job(j.id, task_template="x")
    assert cs.update_command_job("user:nope", command="x") is None
    msg = cs.add_job("m", "interval:60", "do it")
    with pytest.raises(ValueError):
        cs.update_command_job(msg.id, command="x")


def test_feedback_and_tuning_cannot_touch_command_job(env):
    cs, _, _, _ = env
    j = _cmd_job(cs, "echo a")
    assert cs.add_user_feedback(j.id, "改成 rm -rf") is False
    assert cs.update_task_template(j.id, "x") is False
    assert j.command == "echo a" and j.task_template == "" and j.user_feedback == []


# ── runner ────────────────────────────────────────────────────────────────

def test_success_records_state_and_output(env):
    cs, _, _, _ = env
    j = _cmd_job(cs, _py("print('hello-out')"), timeout_sec=30)
    ws = _run(env, j)
    st = ws.read_state()
    assert st.status == "idle" and st.last_error == "" and st.consecutive_failures == 0
    ev = ws.read_run_events(st.last_run_id)
    types = [e["type"] for e in ev]
    assert types[0] == "run_started" and types[-1] == "run_finished"
    out = next(e for e in ev if e["type"] == "command_output")
    assert out["returncode"] == 0 and "hello-out" in out["stdout_tail"]
    summ = ws.recent_runs_summary(limit=1)[0]
    assert summ["success"] is True and summ["status"] == "success"


def test_nonzero_exit_is_failure_with_tail(env):
    cs, _, _, _ = env
    j = _cmd_job(cs, _py("import sys; sys.stderr.write('boom'); sys.exit(3)"), timeout_sec=30)
    ws = _run(env, j)
    st = ws.read_state()
    assert st.status == "needs_human_review" and st.consecutive_failures == 1
    assert "退出码 3" in st.last_error and "boom" in st.last_error
    summ = ws.recent_runs_summary(limit=1)[0]
    assert summ["success"] is False and "退出码 3" in summ["error"]
    _run(env, j)                                    # 再失败一次，连续失败累加
    assert ws.read_state().consecutive_failures == 2


def test_timeout_kills_process_tree(env, tmp_path):
    cs, _, _, _ = env
    marker = tmp_path / "survivor.txt"
    # 孙进程：睡 4s 后写文件；如果进程树没被杀掉，文件会出现
    child = f"import time; time.sleep(4); open(r'{marker}','w').write('x')"
    parent = (f"import subprocess,sys,time; "
              f"subprocess.Popen([sys.executable,'-c',\\\"{child}\\\"]); time.sleep(60)")
    j = _cmd_job(cs, _py(parent), timeout_sec=1)
    t0 = time.time()
    ws = _run(env, j)
    assert time.time() - t0 < 15
    st = ws.read_state()
    assert st.status == "timed_out" and "超过 1s" in st.last_error
    assert ws.recent_runs_summary(limit=1)[0]["status"] == "timed_out"
    time.sleep(5)
    assert not marker.exists(), "超时后孙进程仍存活"


def test_output_tail_truncated(env):
    cs, _, _, _ = env
    j = _cmd_job(cs, _py("print('A'*5000); print('END-MARK')"), timeout_sec=30)
    ws = _run(env, j)
    ev = ws.read_run_events(ws.read_state().last_run_id)
    out = next(e for e in ev if e["type"] == "command_output")
    assert "END-MARK" in out["stdout_tail"] and "已截断" in out["stdout_tail"]
    assert len(out["stdout_tail"]) < 400
    assert not list(ws.runs_dir.glob("*.tmp"))      # 临时文件已清理


def test_cwd_and_env_injected(env, tmp_path):
    cs, _, _, _ = env
    d = tmp_path / "cwd_here"
    d.mkdir()
    j = _cmd_job(cs, _py("import os; print(os.getcwd()); print(os.environ['MINI_AGENT_CRON_JOB_ID'])"),
                 cwd=str(d), timeout_sec=30)
    ws = _run(env, j)
    out = next(e for e in ws.read_run_events(ws.read_state().last_run_id) if e["type"] == "command_output")
    assert Path(out["stdout_tail"].splitlines()[0]).resolve() == d.resolve()
    assert j.id in out["stdout_tail"]


def test_cwd_removed_after_creation_fails_cleanly(env, tmp_path):
    cs, _, _, _ = env
    d = tmp_path / "gone"
    d.mkdir()
    j = _cmd_job(cs, "echo x", cwd=str(d))
    d.rmdir()
    ws = _run(env, j)
    st = ws.read_state()
    assert st.status == "needs_human_review" and "cwd" in st.last_error


def test_unmanaged_does_not_hold_slot_and_managed_does(env):
    cs, runner, _, _ = env
    runner._acquire_slot()                          # 唯一槽位被占满
    slow = _py("import time; time.sleep(1.5)")
    u = _cmd_job(cs, slow, concurrency="unmanaged", timeout_sec=30)
    assert runner.submit(u) is True
    assert _wait(lambda: runner.execution_phase(u.id) == "running", 3)
    assert runner._held_slots == 1 and u.id not in runner._sem_acquired
    assert runner.unmanaged_running_count == 1
    assert _wait(lambda: not runner.is_running(u.id))
    assert runner._held_slots == 1                  # 没误还

    m = _cmd_job(cs, "echo x", timeout_sec=30)
    assert runner.submit(m) is True
    assert _wait(lambda: runner.execution_phase(m.id) == "queued", 3)
    runner._release_slot()
    assert _wait(lambda: not runner.is_running(m.id))


def test_timeout_sec_drives_watchdog_override(env):
    cs, runner, _, _ = env
    j = _cmd_job(cs, _py("import time; time.sleep(1)"), timeout_sec=42)
    assert runner.submit(j)
    assert runner._timeout_override.get(j.id) == 42.0
    assert _wait(lambda: not runner.is_running(j.id))
    assert j.id not in runner._timeout_override


def test_dedup_same_job(env):
    cs, runner, _, _ = env
    j = _cmd_job(cs, _py("import time; time.sleep(1)"), concurrency="unmanaged")
    assert runner.submit(j) is True
    assert runner.submit(j) is False
    assert _wait(lambda: not runner.is_running(j.id))


# ── agent 路径 deny / CLI ─────────────────────────────────────────────────

@pytest.mark.parametrize("cmd,denied", [
    ("cron add-cmd a interval:60 echo hi", True),
    ("CRON ADD-CMD a interval:60 echo hi", True),
    ("cron add a interval:60 do something", False),
    ("cron list", False),
    ("cron", False),
])
def test_slash_deny_only_add_cmd(cmd, denied):
    from mini_agent.tools.slash_command import _is_denied
    name = cmd.split()[0]
    assert _is_denied(name, cmd) is denied


@pytest.mark.asyncio
async def test_cli_add_cmd_end_to_end(env):
    from mini_agent.cli.commands.cron import handle_cron

    class Ctx:
        pass
    ctx = Ctx()
    ctx.cron_scheduler = env[0]
    out = await handle_cron(
        ["add-cmd", "etl", "cron:0", "6", "*", "*", "*", "python", "a.py", "--timeout", "90", "--unmanaged"], ctx)
    assert "✓" in out, out
    j = [x for x in env[0].list_jobs() if x.name == "etl"][0]
    assert j.schedule == "cron:0 6 * * *" and j.command == "python a.py"
    assert j.timeout_sec == 90 and j.concurrency == "unmanaged"

    bad = await handle_cron(["add-cmd", "x", "interval:60", "echo", "--timeout", "abc"], ctx)
    assert "✗" in bad
    bad = await handle_cron(["add-cmd", "x", "cron:0", "6", "*"], ctx)
    assert "5 段" in bad
    bad = await handle_cron(["add-cmd", "x"], ctx)
    assert "用法" in bad


# ── REST ──────────────────────────────────────────────────────────────────

def _client(env):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import mini_agent.api.routes as routes

    cs = env[0]

    class _Bridge:
        _cron_scheduler = cs

    class _Http:
        bridge = _Bridge()

    app = FastAPI()
    app.state.http_server = _Http()
    app.include_router(routes.router)
    return TestClient(app)


@pytest.fixture
def rest(env, monkeypatch):
    import mini_agent.api.routes as routes
    monkeypatch.setattr(routes, "_require_owner", lambda request: None)
    monkeypatch.setattr(routes, "_get_cron_scheduler", lambda http_server: env[0])
    return _client(env), env[0]


def test_rest_create_command_job(rest):
    c, cs = rest
    r = c.post("/v1/cron/jobs", json={"run_mode": "command", "name": "n", "schedule": "interval:60",
                                      "command": "echo hi", "timeout_sec": 12, "concurrency": "unmanaged"})
    assert r.status_code == 200, r.text
    job = r.json()["job"]
    assert job["run_mode"] == "command" and job["command"] == "echo hi"
    assert job["timeout_sec"] == 12 and job["concurrency"] == "unmanaged"


@pytest.mark.parametrize("body", [
    {"run_mode": "command", "name": "n", "schedule": "interval:60"},                                  # 缺 command
    {"run_mode": "command", "name": "n", "schedule": "interval:60", "command": "x", "timeout_sec": 10**7},
    {"run_mode": "command", "name": "n", "schedule": "interval:60", "command": "x", "concurrency": "zzz"},
    {"run_mode": "command", "name": "n", "schedule": "interval:60", "command": "x", "cwd": "/no/such/dir"},
    {"run_mode": "weird", "name": "n", "schedule": "interval:60", "task_template": "t"},
])
def test_rest_create_rejects_bad_input(rest, body):
    c, cs = rest
    assert c.post("/v1/cron/jobs", json=body).status_code == 400
    assert cs.list_jobs() == [] or all(j.is_system for j in cs.list_jobs())


def test_rest_message_job_still_works(rest):
    c, _ = rest
    r = c.post("/v1/cron/jobs", json={"name": "n", "schedule": "interval:60", "task_template": "do"})
    assert r.status_code == 200 and r.json()["job"]["run_mode"] == "message"


def test_rest_put_and_feedback_guards(rest):
    c, cs = rest
    j = cs.add_command_job("n", "interval:60", "echo a")
    assert c.put(f"/v1/cron/jobs/{j.id}", json={"command": "echo b", "timeout_sec": 20}).status_code == 200
    assert j.command == "echo b" and j.timeout_sec == 20
    assert c.put(f"/v1/cron/jobs/{j.id}", json={"timeout_sec": 10**7, "enabled": False}).status_code == 400
    assert j.enabled is True and j.timeout_sec == 20                  # 校验失败整体不生效
    assert c.post(f"/v1/cron/jobs/{j.id}/feedback", json={"text": "rm -rf /"}).status_code == 400
    assert j.command == "echo b"
    m = cs.add_job("m", "interval:60", "t")
    assert c.put(f"/v1/cron/jobs/{m.id}", json={"command": "x"}).status_code == 400
    assert c.put("/v1/cron/jobs/user:nope", json={"command": "x"}).status_code == 404
