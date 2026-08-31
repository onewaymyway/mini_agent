"""tests/test_protected_files_backup.py — 阶段 3 定期备份 + 缺失告警测试。

对应 next_doc/protected_files_manifest_and_delete_guard_plan.md 阶段 3。

覆盖：
  1. 没有受保护路径时直接返回空摘要，不产生快照目录
  2. 有受保护路径时正确打包快照（文件 + 目录两种条目形式）
  3. 保留策略：超过 keep_count 份时清理最旧的
  4. 缺失核对：上一份快照有、这一份没有的路径进入 summary.missing
  5. 缺失核对命中时写入 activity_digest.jsonl 告警，且不做任何自动恢复
  6. ensure_protected_files_backup_job() 正确注册 job 与本地回调 handler，
     且 handler 触发后能实际执行一次备份
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.protected_files import MANIFEST_FILENAME  # noqa: E402
from mini_agent.evolution.protected_files_backup import (  # noqa: E402
    JOB_ID,
    run_backup_once,
    ensure_protected_files_backup_job,
    _backup_root,
)
from mini_agent.evolution.cron_scheduler import CronScheduler  # noqa: E402
from mini_agent.evolution.resource_arbiter import read_activity_digest  # noqa: E402
from mini_agent.storage.paths import AgentPaths  # noqa: E402


class TestProtectedFilesBackup(unittest.TestCase):

    def test_no_protected_paths_no_snapshot(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary = run_backup_once(root)
            self.assertEqual(summary.backed_up, [])
            self.assertEqual(summary.missing, [])
            self.assertFalse(_backup_root(root).exists())

    def test_backs_up_file_and_directory_entries(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / MANIFEST_FILENAME).write_text(
                "a.txt\nimportant_notes/\n", encoding="utf-8"
            )
            (root / "a.txt").write_text("hello", encoding="utf-8")
            notes_dir = root / "important_notes"
            notes_dir.mkdir()
            (notes_dir / "n1.md").write_text("note", encoding="utf-8")

            summary = run_backup_once(root)
            self.assertTrue(summary.ok)
            self.assertEqual(len(summary.backed_up), 3)  # 清单自身 + a.txt + important_notes/

            gen_dir = _backup_root(root) / summary.generation_id
            self.assertTrue(gen_dir.is_dir())
            self.assertTrue((gen_dir / "manifest.txt").is_file())
            manifest_content = (gen_dir / "manifest.txt").read_text(encoding="utf-8")
            self.assertIn("a.txt", manifest_content)

    def test_keep_count_prunes_oldest(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / MANIFEST_FILENAME).write_text("a.txt\n", encoding="utf-8")
            (root / "a.txt").write_text("hello", encoding="utf-8")

            base_t = time.time()
            for i in range(4):
                run_backup_once(root, keep_count=2, now=base_t + i)

            generations = sorted(p.name for p in _backup_root(root).iterdir())
            self.assertEqual(len(generations), 2)  # 只保留最近 2 份

    def test_missing_detected_across_generations(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / MANIFEST_FILENAME).write_text("a.txt\nb.txt\n", encoding="utf-8")
            (root / "a.txt").write_text("hello", encoding="utf-8")
            (root / "b.txt").write_text("world", encoding="utf-8")

            first = run_backup_once(root, now=time.time())
            self.assertEqual(first.missing, [])

            # 从清单里移除 b.txt 并删掉文件本身，模拟"用户确实故意删除"
            (root / MANIFEST_FILENAME).write_text("a.txt\n", encoding="utf-8")
            (root / "b.txt").unlink()

            second = run_backup_once(root, now=time.time() + 1)
            missing_names = [Path(p).name for p in second.missing]
            self.assertIn("b.txt", missing_names)
            # a.txt 仍然存在，不应出现在 missing 里
            self.assertNotIn("a.txt", missing_names)

    def test_missing_alert_written_to_activity_digest(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = AgentPaths(project_root=root)
            (root / MANIFEST_FILENAME).write_text("a.txt\n", encoding="utf-8")
            (root / "a.txt").write_text("hello", encoding="utf-8")
            run_backup_once(root, now=time.time())

            (root / MANIFEST_FILENAME).write_text("", encoding="utf-8")
            (root / "a.txt").unlink()

            from mini_agent.evolution.protected_files_backup import _write_missing_alert
            summary = run_backup_once(root, now=time.time() + 1)
            _write_missing_alert(paths, summary)

            records = read_activity_digest(paths)
            missing_records = [r for r in records if r.get("type") == "protected_files_missing"]
            self.assertEqual(len(missing_records), 1)
            self.assertIn("a.txt", "".join(missing_records[0]["missing_paths"]))
            # 文件确实没有被自动恢复
            self.assertFalse((root / "a.txt").exists())

    def test_ensure_job_registers_and_handler_runs(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = AgentPaths(project_root=root)
            (root / MANIFEST_FILENAME).write_text("a.txt\n", encoding="utf-8")
            (root / "a.txt").write_text("hello", encoding="utf-8")

            scheduler = CronScheduler(paths)
            newly_added = ensure_protected_files_backup_job(paths, scheduler, keep_count=3)
            self.assertTrue(newly_added)
            job = next(j for j in scheduler.list_jobs() if j.id == JOB_ID)
            self.assertTrue(job.enabled)
            self.assertEqual(job.schedule, "interval:86400")

            ok = scheduler.run_now(JOB_ID)
            self.assertTrue(ok)
            self.assertTrue(_backup_root(root).exists())
            self.assertEqual(len(list(_backup_root(root).iterdir())), 1)

            newly_added_again = ensure_protected_files_backup_job(paths, scheduler, keep_count=3)
            self.assertFalse(newly_added_again)


if __name__ == "__main__":
    unittest.main()
