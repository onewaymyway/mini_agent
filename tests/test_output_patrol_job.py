"""产出项目定期巡检 job 测试（P7 阶段三）。

对应 next_doc/management_output_project_model_impl_p7.md 阶段三。

保证：
  1. 默认注册 sys:output_patrol，间隔 3 天（259200 秒），enabled=True；
  2. 修改 patrol_interval_days 后，再次启动时 schedule 与 next_run_at 随之更新；
  3. patrol_schedule_enabled=false 时 job 保留但 disabled；
  4. 本地回调会真正运行巡检并写出 latest.json；
  5. 重复注册是幂等的，不产生重复 job。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mini_agent.evolution.cron_scheduler import CronScheduler
from mini_agent.output_registry.patrol_job import JOB_ID, ensure_output_patrol_job, interval_schedule
from mini_agent.storage.paths import AgentPaths


@pytest.fixture
def env(tmp_path: Path):
    root = tmp_path / "proj"
    (root / "output_projects" / "legacy" / "output").mkdir(parents=True)
    (root / "output_projects" / "legacy" / "output" / "a.md").write_text("x", encoding="utf-8")
    (root / ".agent").mkdir()
    paths = AgentPaths(project_root=root)
    scheduler = CronScheduler(paths)
    return root, paths, scheduler


def _job(scheduler):
    return next(j for j in scheduler.list_jobs() if j.id == JOB_ID)


def test_default_interval_is_three_days(env):
    root, paths, scheduler = env
    assert interval_schedule(3) == "interval:259200"
    result = ensure_output_patrol_job(paths, scheduler, interval_days=3, enabled=True)
    assert result["created"] is True
    job = _job(scheduler)
    assert job.schedule == "interval:259200"
    assert job.enabled is True
    assert "output_registry" in job.tags


def test_changed_interval_is_applied_on_next_registration(env):
    root, paths, scheduler = env
    ensure_output_patrol_job(paths, scheduler, interval_days=3, enabled=True)
    result = ensure_output_patrol_job(paths, scheduler, interval_days=1, enabled=True)
    assert result["created"] is False
    job = _job(scheduler)
    assert job.schedule == "interval:86400"


def test_disabled_by_config_keeps_job_but_disables_it(env):
    root, paths, scheduler = env
    ensure_output_patrol_job(paths, scheduler, interval_days=3, enabled=False)
    job = _job(scheduler)
    assert job.enabled is False
    # 再次以启用状态启动，应重新启用
    ensure_output_patrol_job(paths, scheduler, interval_days=3, enabled=True)
    assert _job(scheduler).enabled is True


def test_registration_is_idempotent(env):
    root, paths, scheduler = env
    ensure_output_patrol_job(paths, scheduler)
    ensure_output_patrol_job(paths, scheduler)
    assert len([j for j in scheduler.list_jobs() if j.id == JOB_ID]) == 1


def test_local_handler_runs_patrol_and_writes_latest(env):
    root, paths, scheduler = env
    ensure_output_patrol_job(paths, scheduler)
    handler = scheduler._local_handlers[JOB_ID]
    assert handler(_job(scheduler)) is True
    latest = root / ".agent" / "output_registry" / "patrol" / "latest.json"
    assert latest.is_file()
    report = json.loads(latest.read_text(encoding="utf-8"))
    assert report["thresholds"]["stale_days"] == 3.0
    assert "unregistered" in {f["kind"] for f in report["findings"]}


def test_interval_from_config_block_default(env):
    from mini_agent.config.models import OutputRegistryConfig
    cfg = OutputRegistryConfig()
    assert cfg.patrol_interval_days == 3.0
    assert cfg.patrol_schedule_enabled is True
