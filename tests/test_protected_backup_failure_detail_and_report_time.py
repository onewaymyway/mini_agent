"""
tests/test_protected_backup_failure_detail_and_report_time.py

覆盖 next_doc/protected_backup_failure_visibility_and_report_time_plan.md：

1. 备份失败时错误带异常类型、能拼成可展示的失败详情（failure_detail）。
2. 失败的运行回滚自己新建的残缺快照，且不做保留清理——历史完整快照不会被
   "每分钟重试"挤掉（此前 5 次重试就会清光最后一份成功快照）。
3. handler 把失败详情写进 job.last_skip_detail，原因码 protected_backup_failed 归
   retry_backoff（指数退避，不再每分钟重试）；告警正文含"补充"和汇报时间。
4. Windows 只读文件不再让旧快照永远清理不掉（_rmtree_force）。
5. 待处理汇报读取时附带只读的 created_at_text / occurred_at_text。

运行：PYTHONPATH=src python3 -m pytest tests/test_protected_backup_failure_detail_and_report_time.py -q
"""

from __future__ import annotations

import os
import shutil
import stat
import time
from pathlib import Path

import pytest

from mini_agent.evolution import protected_files_backup as pb
from mini_agent.evolution.cron_scheduler import CronScheduler
from mini_agent.evolution.cron_skip_reasons import (
    CATEGORY_RETRY_BACKOFF,
    SKIP_REASONS,
    skip_category,
)
from mini_agent.notification import reports_store
from mini_agent.storage.paths import AgentPaths

T0 = 1_800_000_000


@pytest.fixture()
def project(tmp_path):
    (tmp_path / "protected_files.txt").write_text("a.txt\nb.txt\n", encoding="utf-8")
    (tmp_path / "a.txt").write_text("A", encoding="utf-8")
    (tmp_path / "b.txt").write_text("B", encoding="utf-8")
    return tmp_path


def _gens(project):
    return [p.name for p in pb._list_generations(pb._backup_root(project))]


@pytest.fixture()
def fail_b(monkeypatch):
    """让 b.txt 的复制失败（模拟被占用/权限问题），返回开关。"""
    real = shutil.copy2
    state = {"on": True}

    def flaky(src, dst, *a, **k):
        if state["on"] and str(src).endswith("b.txt"):
            raise PermissionError(13, "Permission denied", str(src))
        return real(src, dst, *a, **k)

    monkeypatch.setattr(shutil, "copy2", flaky)
    return state


# ── 1. 失败详情 ─────────────────────────────────────────────────────────────

class TestFailureDetail:
    def test_no_errors_gives_empty_detail(self, project):
        s = pb.run_backup_once(project, now=T0)
        assert s.ok and s.failure_detail() == ""

    def test_error_names_path_and_exception_type(self, project, fail_b):
        s = pb.run_backup_once(project, now=T0)
        assert not s.ok
        assert len(s.errors) == 1
        assert "backup_failed" in s.errors[0]
        assert "b.txt" in s.errors[0]
        assert "PermissionError" in s.errors[0]
        detail = s.failure_detail()
        assert detail.startswith("共 1 处失败：")
        assert "b.txt" in detail and "PermissionError" in detail

    def test_detail_truncates_long_error_lists(self):
        s = pb.BackupSummary(errors=[f"backup_failed(/p/{i}): OSError: x" for i in range(9)])
        detail = s.failure_detail(max_errors=3)
        assert "共 9 处失败" in detail and "另有 6 处未列出" in detail
        assert "/p/2" in detail and "/p/3" not in detail

    def test_rolled_back_note_in_detail(self):
        s = pb.BackupSummary(errors=["backup_failed(/p): OSError: x"], rolled_back=True)
        assert "已回滚本次残缺快照" in s.failure_detail()


# ── 2. 回滚 + 不清理旧快照 ──────────────────────────────────────────────────

class TestRollbackAndRetention:
    def test_failed_run_leaves_no_partial_generation(self, project, fail_b):
        s = pb.run_backup_once(project, now=T0)
        assert not s.ok and s.rolled_back
        assert s.generation_id == ""
        assert _gens(project) == []

    def test_good_snapshot_survives_repeated_failed_retries(self, project, fail_b):
        """回归：此前每分钟重试 5 次就会把唯一的成功快照清掉。"""
        fail_b["on"] = False
        good = pb.run_backup_once(project, keep_count=5, now=T0)
        assert good.ok
        fail_b["on"] = True
        for i in range(1, 12):
            s = pb.run_backup_once(project, keep_count=5, now=T0 + 86400 + i * 60)
            assert not s.ok and s.pruned_generations == []
        assert _gens(project) == [good.generation_id]

    def test_retention_still_works_when_backups_succeed(self, project):
        for i in range(8):
            assert pb.run_backup_once(project, keep_count=3, now=T0 + i * 60).ok
        assert len(_gens(project)) == 3

    def test_missing_check_ignores_legacy_partial_generation(self, project):
        """历史上失败运行遗留的、没有 manifest 的目录，不能拿来做缺失核对。"""
        good = pb.run_backup_once(project, now=T0)
        legacy = pb._backup_root(project) / "29990101_000000"  # 时间戳最大
        legacy.mkdir()
        (project / "b.txt").unlink()
        (project / "protected_files.txt").write_text("a.txt\n", encoding="utf-8")
        s = pb.run_backup_once(project, now=T0 + 10)
        # 以 good 的 manifest 为准：b.txt 上次在、这次不在 → 报缺失
        assert any(p.endswith("b.txt") for p in s.missing)
        assert good.generation_id in _gens(project)

    def test_mkdir_failure_reports_path(self, project, monkeypatch):
        real_mkdir = Path.mkdir

        def bad_mkdir(self, *a, **k):
            if "protected_backup" in str(self):
                raise OSError(28, "No space left on device")
            return real_mkdir(self, *a, **k)

        monkeypatch.setattr(Path, "mkdir", bad_mkdir)
        s = pb.run_backup_once(project, now=T0)
        assert not s.ok and s.errors[0].startswith("mkdir_failed(")
        assert "OSError" in s.errors[0]


# ── 3. handler / 原因码 / 告警正文 ──────────────────────────────────────────

class _Runner:
    """只提供 scheduler 读取配置和 submit 所需的最小接口。"""
    def __init__(self):
        from types import SimpleNamespace
        self._base_cfg = SimpleNamespace(cron=SimpleNamespace(skip_alert_threshold=1))
        self.submit = lambda job: False


def _make_scheduler(project, monkeypatch):
    sent = []

    class _FakeDispatcher:
        def __init__(self, paths):
            pass

        def dispatch(self, message):
            if str((message.meta or {}).get("job_id", "")) == pb.JOB_ID:
                sent.append(message)

    import mini_agent.notification.dispatcher as dispatcher_mod
    monkeypatch.setattr(dispatcher_mod, "NotificationDispatcher", _FakeDispatcher)
    paths = AgentPaths(project)
    cs = CronScheduler(paths, submit_fn=None, job_runner=_Runner())
    cs.load()
    pb.ensure_protected_files_backup_job(paths, cs, keep_count=5)
    return paths, cs, sent


class TestHandlerAndAlert:
    def test_reason_code_registered_and_backoff_category(self):
        assert "protected_backup_failed" in SKIP_REASONS
        assert skip_category("protected_backup_failed") == CATEGORY_RETRY_BACKOFF

    def test_failure_detail_lands_in_job_and_alert(self, project, fail_b, monkeypatch):
        paths, cs, sent = _make_scheduler(project, monkeypatch)
        job = cs.get(pb.JOB_ID)
        job.next_run_at = time.time() - 1
        cs.tick()

        job = cs.get(pb.JOB_ID)
        assert job.last_skip_reason == "protected_backup_failed"
        assert "b.txt" in job.last_skip_detail and "PermissionError" in job.last_skip_detail
        assert job.consecutive_skip_count == 1

        assert len(sent) == 1
        body = sent[0].body
        assert "protected_backup_failed" in body        # 不再是 local_handler_returned_false
        assert "补充：共 1 处失败" in body and "b.txt" in body
        assert "本次汇报时间：" in body
        assert sent[0].meta["skip_detail"] == job.last_skip_detail

    def test_failure_backs_off_instead_of_retrying_every_minute(self, project, fail_b, monkeypatch):
        paths, cs, sent = _make_scheduler(project, monkeypatch)
        cs.get(pb.JOB_ID).next_run_at = time.time() - 1
        t0 = time.time()
        cs.tick()
        delay = cs.get(pb.JOB_ID).next_run_at - t0
        assert 55 <= delay <= 70          # 第 1 次失败：约 60s
        cs.get(pb.JOB_ID).next_run_at = time.time() - 1
        t1 = time.time()
        cs.tick()
        assert 115 <= cs.get(pb.JOB_ID).next_run_at - t1 <= 130   # 第 2 次：约 120s，指数增长
        assert _gens(project) == []       # 没有残缺快照堆积

    def test_success_clears_state(self, project, fail_b, monkeypatch):
        paths, cs, sent = _make_scheduler(project, monkeypatch)
        cs.get(pb.JOB_ID).next_run_at = time.time() - 1
        cs.tick()
        fail_b["on"] = False
        cs.get(pb.JOB_ID).next_run_at = time.time() - 1
        cs.tick()
        job = cs.get(pb.JOB_ID)
        assert job.consecutive_skip_count == 0
        assert job.last_run_at > 0
        assert len(_gens(project)) == 1


# ── 4. 只读文件清理 ─────────────────────────────────────────────────────────

class TestRmtreeForce:
    def test_removes_readonly_files(self, tmp_path):
        d = tmp_path / "gen" / "sub"
        d.mkdir(parents=True)
        f = d / "ro.txt"
        f.write_text("x", encoding="utf-8")
        os.chmod(f, stat.S_IREAD)
        pb._rmtree_force(tmp_path / "gen")
        assert not (tmp_path / "gen").exists()

    def test_propagates_when_it_cannot_remove(self, tmp_path):
        with pytest.raises(Exception):
            pb._rmtree_force(tmp_path / "does_not_exist")


# ── 5. 待处理汇报的时间字段 ─────────────────────────────────────────────────

class TestReportTimeFields:
    def _paths(self, tmp_path):
        return AgentPaths(tmp_path)

    def test_format_report_time(self):
        assert reports_store.format_report_time(None) == ""
        assert reports_store.format_report_time(0) == ""
        assert reports_store.format_report_time("garbage") == ""
        text = reports_store.format_report_time(T0)
        assert len(text) == 19 and text[4] == "-" and text[10] == " "

    def test_pending_reports_carry_time_text(self, tmp_path):
        paths = self._paths(tmp_path)
        reports_store.append_report(paths, {
            "report_id": "notif:cron_skip_alert:1", "source": "cron_skip_alert",
            "title": "t", "detail": "d", "created_at": T0 + 100, "occurred_at": T0,
            "acknowledged": False,
        })
        reports_store.append_report(paths, {
            "report_id": "notif:cron_skip_alert:2", "source": "cron_skip_alert",
            "title": "old", "detail": "d", "acknowledged": False,   # 旧数据：无时间
        })
        got = {d["report_id"]: d for d in reports_store.list_pending_reports(paths)}
        a = got["notif:cron_skip_alert:1"]
        assert a["created_at_text"] == reports_store.format_report_time(T0 + 100)
        assert a["occurred_at_text"] == reports_store.format_report_time(T0)
        assert a["created_at_text"] != a["occurred_at_text"]
        b = got["notif:cron_skip_alert:2"]
        assert b["created_at_text"] == "" and b["occurred_at_text"] == ""

    def test_time_text_is_not_persisted(self, tmp_path):
        paths = self._paths(tmp_path)
        reports_store.append_report(paths, {
            "report_id": "r1", "source": "watchlist_report", "title": "t", "detail": "d",
            "created_at": T0, "acknowledged": False,
        })
        reports_store.list_pending_reports(paths)
        raw = paths.notification_reports.read_text(encoding="utf-8")
        assert "created_at_text" not in raw

    def test_order_and_category_unchanged(self, tmp_path):
        paths = self._paths(tmp_path)
        for i, ts in enumerate([T0, T0 + 50, T0 + 20]):
            reports_store.append_report(paths, {
                "report_id": f"r{i}", "source": "cron_skip_alert", "title": "t", "detail": "d",
                "created_at": ts, "acknowledged": False,
            })
        rows = reports_store.list_pending_reports(paths)
        assert [r["report_id"] for r in rows] == ["r1", "r2", "r0"]   # 新的在前
        assert all(r["category"] == "执行失败" for r in rows)
