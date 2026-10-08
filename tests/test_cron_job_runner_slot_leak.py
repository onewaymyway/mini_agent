"""排队期间被 watchdog 回收的 job：不能偷偷再执行、不能泄漏/误还槽位；槽位账实核对能自愈。"""
from __future__ import annotations

import threading
import time

from mini_agent.evolution.cron_job_runner import CronJobRunner
from test_cron_job_runner import (  # noqa: F401  (autouse fixture 需要一并导入)
    _FakeBaseCfg, _FakePaths, _make_job, _patch_agent_bridge,
)


def _wait(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def _runner(tmp_path, n=1):
    return CronJobRunner(_FakeBaseCfg(), _FakePaths(tmp_path), max_concurrent=n)


def test_reaped_queued_job_does_not_release_a_slot_it_never_held(tmp_path):
    r = _runner(tmp_path)
    r._acquire_slot()                       # 另一个 job 占着唯一的槽位
    assert r.submit(_make_job()) is True
    assert _wait(lambda: r.execution_phase("user:job1") == "queued")
    assert r.reap_stale_jobs(now=time.time() + 10_000) == ["user:job1"]
    assert r._held_slots == 1               # 以前这里会被误还成 0
    r._release_slot()


def test_reaped_queued_orphan_never_executes_and_gives_slot_back(tmp_path, monkeypatch):
    import mini_agent.evolution.cron_job_executor as executor_mod
    ran = threading.Event()

    class _Exec:
        def __init__(self, paths):
            pass

        def run_job(self, *a, **k):
            ran.set()
            raise AssertionError("孤儿不应执行")

    monkeypatch.setattr(executor_mod, "CronJobExecutor", _Exec)
    r = _runner(tmp_path)
    r._acquire_slot()
    r.submit(_make_job())
    assert _wait(lambda: r.execution_phase("user:job1") == "queued")
    r.reap_stale_jobs(now=time.time() + 10_000)
    r._release_slot()                       # 槽位空出 → 孤儿线程拿到槽位
    assert _wait(lambda: r._held_slots == 0)  # 孤儿立刻还回槽位，没有永久泄漏
    assert not ran.is_set()
    assert r.submit(_make_job()) is True    # 同一个 job 可以重新提交
    r.reap_stale_jobs(now=time.time() + 20_000)


def test_slot_leak_is_reconciled_after_grace(tmp_path):
    r = _runner(tmp_path, 2)
    r._held_slots = 2                       # 模拟泄漏：占着槽位但没有任何在跑的 job
    now = time.time()
    r._reconcile_slots(now)
    assert r._held_slots == 2               # 第一次只记下开始时间（排除瞬间窗口）
    r._reconcile_slots(now + 30)
    assert r._held_slots == 2
    r._reconcile_slots(now + r.SLOT_MISMATCH_GRACE_SECONDS + 1)
    assert r._held_slots == 0 and r._slot_reconciled_count == 1


def test_no_false_reconcile_when_slots_match_running_jobs(tmp_path):
    r = _runner(tmp_path, 2)
    r._held_slots = 1
    r._sem_acquired.add("user:job1")
    now = time.time()
    r._reconcile_slots(now)
    r._reconcile_slots(now + 1000)
    assert r._held_slots == 1 and r._slot_reconciled_count == 0


def test_external_entrypoint_timeout_sec_drives_watchdog_threshold(tmp_path):
    r = _runner(tmp_path)
    base = r._effective_timeout_seconds("ext:p:k")          # 全局默认 + grace
    r._timeout_override["ext:p:k"] = 3600.0                  # project.yaml 里声明 timeout_sec: 3600
    grace = base - 20 * 60
    assert r._effective_timeout_seconds("ext:p:k") == 3600.0 + grace
    assert r._effective_timeout_seconds("ext:p:other") == base   # 只影响这一个 job


def test_external_job_declares_timeout_and_clears_it_afterwards(tmp_path, monkeypatch):
    import types
    import mini_agent.external_projects.registry as reg_mod
    import mini_agent.external_projects.scheduler as sch_mod
    seen = {}
    entry = types.SimpleNamespace(timeout_sec=3600)
    manifest = types.SimpleNamespace(entrypoint=lambda key: entry)

    class _Reg:
        def get(self, name):
            return types.SimpleNamespace(enabled=True)

        def load_manifest_for(self, name):
            return manifest

    monkeypatch.setattr(reg_mod, "ExternalProjectRegistry", _Reg)
    r = _runner(tmp_path)

    def _fake_run(m, e, trigger):
        seen["during"] = r._effective_timeout_seconds("ext:p:k")

    monkeypatch.setattr(sch_mod, "_run_entrypoint", _fake_run)
    job = _make_job("ext:p:k")
    job.run_mode, job.external_project, job.external_entrypoint = "external_entrypoint", "p", "k"
    assert r.submit(job) is True
    assert _wait(lambda: "during" in seen and not r.is_running("ext:p:k"))
    assert seen["during"] >= 3600.0
    assert "ext:p:k" not in r._timeout_override
