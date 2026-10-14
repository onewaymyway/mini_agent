"""
看板 Cron 计数口径修复：`Cron 5/2` 这种"运行数超过并发上限"的假象。

对应 next_doc/kanban_cron_running_queued_display_fix.md。根因是
managed_running_count 的语义是"占槽位 + 排队等槽位"，被直接当成"运行中"展示。
这里覆盖 CronJobRunner.managed_job_ids_by_phase() 的拆分口径：
  - 已拿到槽位（_sem_acquired）的 managed job → running
  - 已提交但还没拿到槽位的 managed job → queued
  - unmanaged 不出现在任何一组（单独计数）
  - 与 execution_phase() / managed_running_count 口径互相一致
"""
from __future__ import annotations

from mini_agent.evolution.cron_job_runner import CronJobRunner


class _FakePaths:
    def __init__(self, root):
        self.project_root = str(root)


class _FakeBaseCfg:
    cron = None


def _runner(tmp_path, cap=2):
    return CronJobRunner(_FakeBaseCfg(), _FakePaths(tmp_path), max_concurrent=cap)


def test_empty_runner_has_no_running_or_queued(tmp_path):
    r = _runner(tmp_path)
    assert r.managed_job_ids_by_phase() == ([], [])


def test_one_running_four_queued_is_not_five_running(tmp_path):
    """复现现场：managed_running_count == 5，但真正在跑的只有 1 个。"""
    r = _runner(tmp_path, cap=2)
    ids = [f"job{i}" for i in range(5)]
    r._running_job_ids.update(ids)
    r._sem_acquired.add("job0")          # 只有 job0 拿到了槽位

    running, queued = r.managed_job_ids_by_phase()
    assert running == ["job0"]
    assert queued == ["job1", "job2", "job3", "job4"]
    # 旧口径（占槽位 + 排队）仍是 5，也就是看板上曾经显示的 5/2
    assert r.managed_running_count == 5
    assert len(running) + len(queued) == r.managed_running_count
    assert len(running) <= 2             # 新口径不会超过上限


def test_unmanaged_excluded_from_both_groups(tmp_path):
    r = _runner(tmp_path)
    r._running_job_ids.update({"m_run", "m_wait", "u1"})
    r._sem_acquired.add("m_run")
    r._unmanaged_running.add("u1")

    running, queued = r.managed_job_ids_by_phase()
    assert running == ["m_run"]
    assert queued == ["m_wait"]
    assert r.unmanaged_running_count == 1


def test_consistent_with_execution_phase(tmp_path):
    r = _runner(tmp_path)
    r._running_job_ids.update({"a", "b"})
    r._sem_acquired.add("a")
    running, queued = r.managed_job_ids_by_phase()
    assert all(r.execution_phase(j) == "running" for j in running)
    assert all(r.execution_phase(j) == "queued" for j in queued)
