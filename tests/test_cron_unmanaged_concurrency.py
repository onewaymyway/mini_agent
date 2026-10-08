"""
concurrency: unmanaged —— 不涉及 LLM 的外部项目 entrypoint 不占 cron 并发槽位、不过仲裁。

对应 next_doc/cron_unmanaged_concurrency_plan.md。覆盖：
  - manifest 解析/校验（缺省 managed、非法值报错）
  - CronJob 序列化兼容（旧数据缺省 managed）
  - ensure_external_project_cron_jobs() 把字段同步进 job（含 yaml 改动后的重新对齐）
  - CronJobRunner：槽位占满时 unmanaged 仍能立即执行；不碰槽位计数；
    跳过 ResourceArbiter；同 job 去重仍生效；watchdog 回收不误还槽位；
    run_mode 非 external_entrypoint 时字段被忽略（仍占槽位）
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

from mini_agent.evolution.cron_job_runner import CronJobRunner
from mini_agent.evolution.cron_scheduler import CronJob
from mini_agent.external_projects.manifest import ProjectManifestError, parse_manifest
from mini_agent.external_projects.registry import ExternalProjectRegistry
from mini_agent.external_projects.scheduler import ensure_external_project_cron_jobs


# ── manifest ─────────────────────────────────────────────────────────────


def test_manifest_concurrency_defaults_to_managed():
    m = parse_manifest("name: p\nentrypoints:\n  a:\n    cmd: x\n")
    assert m.entrypoints["a"].concurrency == "managed"


def test_manifest_concurrency_unmanaged_parsed():
    m = parse_manifest("name: p\nentrypoints:\n  a:\n    cmd: x\n    concurrency: unmanaged\n")
    assert m.entrypoints["a"].concurrency == "unmanaged"


@pytest.mark.parametrize("bad", ["fast", "", "123", "[unmanaged]"])
def test_manifest_concurrency_invalid_rejected(bad):
    with pytest.raises(ProjectManifestError):
        parse_manifest(f"name: p\nentrypoints:\n  a:\n    cmd: x\n    concurrency: {bad}\n")


# ── CronJob 序列化 ────────────────────────────────────────────────────────


def test_cronjob_roundtrip_and_legacy_default():
    job = CronJob(id="ext:p:a", name="n", schedule="interval:60", task_template="",
                  run_mode="external_entrypoint", concurrency="unmanaged")
    assert CronJob.from_dict(job.to_dict()).concurrency == "unmanaged"
    legacy = job.to_dict()
    legacy.pop("concurrency")
    assert CronJob.from_dict(legacy).concurrency == "managed"


# ── 对齐 ──────────────────────────────────────────────────────────────────


def _project(tmp_path: Path, concurrency_line: str) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "project.yaml").write_text(
        "name: proj\nentrypoints:\n  scan:\n"
        f"    cmd: \"{sys.executable} -c \\\"pass\\\"\"\n"
        "    schedule: \"cron: 0 9 * * 1-5\"\n" + concurrency_line,
        encoding="utf-8",
    )
    return root


def _cron_scheduler(tmp_path: Path):
    from mini_agent.evolution.cron_scheduler import CronScheduler
    from mini_agent.storage.paths import AgentPaths

    workdir = tmp_path / "agent_workdir"
    workdir.mkdir()
    cs = CronScheduler(AgentPaths(project_root=workdir))
    cs.load()
    return cs


def test_ensure_syncs_concurrency_and_realigns(tmp_path):
    root = _project(tmp_path, "    concurrency: unmanaged\n")
    registry = ExternalProjectRegistry(store_path=tmp_path / "registry.json")
    registry.register("proj", root, enabled=True)
    cs = _cron_scheduler(tmp_path)

    ensure_external_project_cron_jobs("proj", registry, cs)
    assert cs.get("ext:proj:scan").concurrency == "unmanaged"

    # yaml 去掉该字段 → 重新对齐后回到默认 managed（project.yaml 是唯一权威来源）
    _project_path = root / "project.yaml"
    text = _project_path.read_text(encoding="utf-8").replace("    concurrency: unmanaged\n", "")
    _project_path.write_text(text, encoding="utf-8")
    ensure_external_project_cron_jobs("proj", registry, cs)
    assert cs.get("ext:proj:scan").concurrency == "managed"


# ── runner ────────────────────────────────────────────────────────────────


class _FakePaths:
    def __init__(self, root):
        self.project_root = str(root)


class _FakeBaseCfg:
    cron = None


def _wait(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def _ext_job(job_id="ext:p:a", concurrency="unmanaged", run_mode="external_entrypoint"):
    return CronJob(id=job_id, name="n", schedule="interval:60", task_template="",
                   run_mode=run_mode, external_project="p", external_entrypoint="a",
                   concurrency=concurrency)


@pytest.fixture
def blocking_entrypoint(monkeypatch):
    """把外部项目 entrypoint 执行替换成可控的阻塞函数。"""
    started, release = threading.Event(), threading.Event()

    def _fake(self, job):
        started.set()
        release.wait(timeout=5.0)

    monkeypatch.setattr(CronJobRunner, "_run_external_entrypoint_job", _fake)
    yield started, release
    release.set()


def test_unmanaged_runs_even_when_all_slots_are_full(tmp_path, blocking_entrypoint):
    started, release = blocking_entrypoint
    r = CronJobRunner(_FakeBaseCfg(), _FakePaths(tmp_path), max_concurrent=1)
    r._acquire_slot()                                    # 唯一的槽位被占满
    assert r.submit(_ext_job()) is True
    assert started.wait(2.0), "unmanaged job 不应排队等槽位"
    assert r.execution_phase("ext:p:a") == "running"
    assert r._held_slots == 1                            # 没有多占
    assert "ext:p:a" not in r._sem_acquired
    release.set()
    assert _wait(lambda: not r.is_running("ext:p:a"))
    assert r._held_slots == 1                            # 结束时也没有误还
    r._release_slot()


def test_managed_external_job_still_queues_when_slots_full(tmp_path, blocking_entrypoint):
    started, release = blocking_entrypoint
    r = CronJobRunner(_FakeBaseCfg(), _FakePaths(tmp_path), max_concurrent=1)
    r._acquire_slot()
    assert r.submit(_ext_job(concurrency="managed")) is True
    assert _wait(lambda: r.execution_phase("ext:p:a") == "queued")
    assert not started.is_set()
    release.set()
    r._release_slot()
    assert _wait(lambda: not r.is_running("ext:p:a"))


def test_non_external_job_ignores_unmanaged_field(tmp_path, monkeypatch):
    """message 类 job 必然涉及 LLM：即使字段被误写成 unmanaged 也照常占槽位。"""
    r = CronJobRunner(_FakeBaseCfg(), _FakePaths(tmp_path), max_concurrent=1)
    assert r._is_unmanaged(_ext_job(run_mode="message")) is False
    assert r._is_unmanaged(_ext_job(run_mode="goal_cycle")) is False
    assert r._is_unmanaged(_ext_job()) is True


def test_unmanaged_skips_arbiter_but_managed_does_not(tmp_path, monkeypatch, blocking_entrypoint):
    import mini_agent.evolution.resource_arbiter as ra

    calls = []

    class _BlockedArbiter:
        def __init__(self, paths, cfg):
            pass

        def gating_state(self):
            calls.append(1)
            return {"state": "blocked", "reason": "user_present"}

    monkeypatch.setattr(ra, "ResourceArbiter", _BlockedArbiter)
    started, release = blocking_entrypoint
    r = CronJobRunner(_FakeBaseCfg(), _FakePaths(tmp_path), max_concurrent=2)

    assert r.submit(_ext_job(job_id="ext:p:m", concurrency="managed")) is False   # 被仲裁挡住
    assert len(calls) == 1 and r.arbiter_skipped_count == 1

    assert r.submit(_ext_job(job_id="ext:p:u")) is True                           # 不过仲裁
    assert len(calls) == 1 and r.arbiter_skipped_count == 1
    assert started.wait(2.0)
    release.set()


def test_unmanaged_dedup_still_applies(tmp_path, blocking_entrypoint):
    started, release = blocking_entrypoint
    r = CronJobRunner(_FakeBaseCfg(), _FakePaths(tmp_path), max_concurrent=1)
    assert r.submit(_ext_job()) is True
    assert started.wait(2.0)
    assert r.submit(_ext_job()) is False                 # 同一个 job 不并发跑两份
    assert r.running_count == 1
    release.set()
    assert _wait(lambda: not r.is_running("ext:p:a"))
    assert r.submit(_ext_job()) is True                  # 结束后可再次提交
    release.set()


def test_reap_unmanaged_does_not_release_any_slot(tmp_path, blocking_entrypoint):
    started, release = blocking_entrypoint
    r = CronJobRunner(_FakeBaseCfg(), _FakePaths(tmp_path), max_concurrent=1)
    r._acquire_slot()                                    # 别的 job 持有的槽位
    assert r.submit(_ext_job()) is True
    assert started.wait(2.0)
    assert r.reap_stale_jobs(now=time.time() + 10_000) == ["ext:p:a"]
    assert r._held_slots == 1                            # 不能把别人的槽位还掉
    assert not r.is_running("ext:p:a")
    assert "ext:p:a" not in r._unmanaged_running
    release.set()                                        # 孤儿线程迟到收尾
    time.sleep(0.2)
    assert r._held_slots == 1                            # 孤儿收尾同样不动槽位
    r._release_slot()


def test_unmanaged_not_counted_by_slot_reconcile(tmp_path, blocking_entrypoint):
    started, release = blocking_entrypoint
    r = CronJobRunner(_FakeBaseCfg(), _FakePaths(tmp_path), max_concurrent=1)
    r.submit(_ext_job())
    assert started.wait(2.0)
    now = time.time()
    r._reconcile_slots(now)
    r._reconcile_slots(now + 1000)
    assert r._held_slots == 0 and r._slot_reconciled_count == 0
    release.set()


def test_running_counts_split_managed_and_unmanaged(tmp_path, blocking_entrypoint):
    started, release = blocking_entrypoint
    r = CronJobRunner(_FakeBaseCfg(), _FakePaths(tmp_path), max_concurrent=2)
    r.submit(_ext_job(job_id="ext:p:u"))
    r.submit(_ext_job(job_id="ext:p:m", concurrency="managed"))
    assert started.wait(2.0)
    assert _wait(lambda: r.running_count == 2)
    assert r.unmanaged_running_count == 1
    assert r.managed_running_count == 1
    release.set()
    assert _wait(lambda: r.running_count == 0)
    assert r.unmanaged_running_count == 0
