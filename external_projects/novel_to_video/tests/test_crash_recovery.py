"""后台任务意外终结后的状态回收：运行状态不得永远停在 running；可以检测到已结束；可以重新开始。

场景：进程被强杀（断电、强退、Streamlit 重启）时，状态文件与运行锁留在磁盘上。
"""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import yaml

from novel_to_video import joblog, jobs, runlock, runtime


def _project(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    (p / "logs").mkdir(parents=True)
    return p


def _dead_pid() -> int:
    """返回一个已经退出的进程号。"""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _write_lock(p: Path, *, pid: int, host: str = None, heartbeat: float = None) -> None:
    body = {"pid": pid, "host": host or socket.gethostname(),
            "heartbeat": time.time() if heartbeat is None else heartbeat}
    (p / runlock.LOCK_FILE).write_text(json.dumps(body), encoding="utf-8")


def _write_state(p: Path, rel: str, **fields) -> None:
    path = p / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fields, ensure_ascii=False), encoding="utf-8")


def _wait_state(read, want, timeout=5.0):
    end = time.time() + timeout
    st = read()
    while time.time() < end and st.get("state") != want:
        time.sleep(0.05)
        st = read()
    return st


def test_running_state_without_any_lock_is_reconciled_to_interrupted(tmp_path):
    p = _project(tmp_path)
    _write_state(p, "logs/runtime.json", state="running", message="正在运行第 2 章", chapter=2)
    st = runtime.read_state(p)
    assert st["state"] == "interrupted"
    assert "已回收" in st["message"] and st.get("finished_at")
    assert any("意外中断" in r["message"] for r in joblog.tail(p, 50, ["system"]))


def test_dead_owner_lock_is_reconciled_and_job_status_reports_it(tmp_path):
    p = _project(tmp_path)
    _write_state(p, "logs/job_state.json", state="running", label="全书素材", scope="assets")
    _write_lock(p, pid=_dead_pid())
    st = jobs.status(p)
    assert st["state"] == "interrupted" and "持有进程已退出" in st["interrupted_reason"]
    assert not (p / runlock.LOCK_FILE).exists() or runlock.inspect(p)["held"] is False


def test_restart_after_crash_is_allowed_and_completes(tmp_path):
    p = _project(tmp_path)
    _write_state(p, "logs/job_state.json", state="running", label="旧任务", scope="assets")
    _write_lock(p, pid=_dead_pid())
    res = jobs.start(p, name="again", label="重新开始", scope="assets",
                     fn=lambda: {"done": ["a"], "failed": [], "skipped": []})
    assert res["ok"], res
    st = _wait_state(lambda: jobs.status(p), "done")
    assert st["state"] == "done" and st["summary"]["done"] == 1


def test_live_run_is_never_reconciled(tmp_path):
    p = _project(tmp_path)
    _write_state(p, "logs/runtime.json", state="running", message="运行中")
    _write_lock(p, pid=os.getpid())                  # 本进程持有：确实在运行
    assert runtime.read_state(p)["state"] == "running"
    assert runlock.reconcile(p) == []


def test_chapter_left_running_is_recovered_when_run_is_gone(tmp_path):
    p = _project(tmp_path)
    (p / "chapters" / "001").mkdir(parents=True)
    (p / "chapters" / "index.yaml").write_text(
        yaml.safe_dump({"chapters": [{"no": 1, "status": "running", "message": ""}]}, allow_unicode=True),
        encoding="utf-8")
    _write_state(p, "logs/runtime.json", state="running")
    runtime.read_state(p)                             # 触发校正
    idx = yaml.safe_load((p / "chapters" / "index.yaml").read_text(encoding="utf-8"))
    assert idx["chapters"][0]["status"] == "pending"
    assert "进程中断后回收" in idx["chapters"][0]["message"]


def test_heartbeat_loop_keeps_the_lock_fresh_during_a_long_run(tmp_path):
    p = _project(tmp_path)
    _write_lock(p, pid=os.getpid(), heartbeat=1.0)
    with runlock.HeartbeatLoop(p, interval=0.05):
        time.sleep(0.3)
    hb = json.loads((p / runlock.LOCK_FILE).read_text(encoding="utf-8"))["heartbeat"]
    assert hb > time.time() - 5                       # 运行期间被持续刷新


def test_fresh_heartbeat_from_another_host_is_still_held(tmp_path):
    p = _project(tmp_path)
    _write_lock(p, pid=_dead_pid(), host="another-machine")   # 异机：只能依据心跳判断
    assert runlock.inspect(p)["held"] is True


def test_stale_heartbeat_from_another_host_is_recovered(tmp_path):
    p = _project(tmp_path)
    _write_state(p, "logs/job_state.json", state="running")
    _write_lock(p, pid=_dead_pid(), host="another-machine", heartbeat=time.time() - 10_000)
    st = jobs.status(p)
    assert st["state"] == "interrupted" and st["interrupted_reason"] == "心跳超时"


def test_reconcile_is_idempotent(tmp_path):
    p = _project(tmp_path)
    _write_state(p, "logs/runtime.json", state="running")
    assert len(runlock.reconcile(p)) == 1
    assert runlock.reconcile(p) == []                 # 第二次不再改动
