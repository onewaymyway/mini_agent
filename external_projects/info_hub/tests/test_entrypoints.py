"""entrypoints 脚本：用 INFO_HUB_ROOT 指向临时项目，以子进程方式真实运行（不碰项目自身数据）。"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from tests.helpers import make_project

PROJECT = Path(__file__).resolve().parents[1]
REPO_SRC = PROJECT.parent.parent / "src"
SRC = {"id": "n", "name": "N", "category": "news", "kind": "feed", "adapter": "rss",
       "request": {"url": "http://127.0.0.1:9/feed"}, "schedule": {"every": "1h"}}


def run_ep(root: Path, script: str, *args: str, with_framework: bool = False):
    env = {**os.environ, "INFO_HUB_ROOT": str(root)}
    env.pop("PYTHONPATH", None)
    if with_framework:
        env["PYTHONPATH"] = str(REPO_SRC)
    return subprocess.run([sys.executable, str(PROJECT / "entrypoints" / script), *args],
                          cwd=str(root), env=env, capture_output=True, text=True, timeout=120)


class EntrypointTests(unittest.TestCase):
    def test_health_ok_and_warns_on_bad_source(self):
        root = make_project({"n": SRC, "bad": {"id": "bad", "name": "B", "category": "nope",
                                               "kind": "feed", "adapter": "rss", "request": {"url": "x"}}})
        r = run_ep(root, "health.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ok", r.stdout)
        self.assertIn("信息源 bad 配置有误", r.stderr)      # 只告警，不影响退出码

    def test_health_fails_when_settings_unreadable(self):
        root = make_project({})
        (root / "config" / "settings.yaml").write_text("- not a mapping", encoding="utf-8")
        r = run_ep(root, "health.py")
        self.assertEqual(r.returncode, 1)
        self.assertIn("配置读取异常", r.stderr)

    def test_missing_param_exits_2(self):
        root = make_project({"n": SRC})
        for script in ("run_source.py", "report.py", "resume_source.py"):
            self.assertEqual(run_ep(root, script).returncode, 2, script)

    def test_run_source_failure_returns_nonzero_and_logs(self):
        root = make_project({"n": SRC})          # 地址不可达 → 抓取失败
        r = run_ep(root, "run_source.py", "n")
        self.assertNotEqual(r.returncode, 0)
        logs = list((root / "data" / "runs").glob("*.log.md"))
        self.assertTrue(logs and "失败" in logs[0].read_text(encoding="utf-8"))

    def test_resume_without_state_returns_1(self):
        root = make_project({"n": SRC})
        self.assertEqual(run_ep(root, "resume_source.py", "n").returncode, 1)

    def test_tick_runs_and_report_unknown_plan_fails(self):
        root = make_project({"n": SRC})
        self.assertEqual(run_ep(root, "tick.py").returncode, 0)
        self.assertNotEqual(run_ep(root, "report.py", "no_such_plan").returncode, 0)

    def test_ledger_written_when_framework_available(self):
        root = make_project({"n": SRC})
        r = run_ep(root, "tick.py", with_framework=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        ledger = root / ".agent" / "run_status.jsonl"
        self.assertTrue(ledger.exists())
        rec = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual((rec["entrypoint"], rec["exit_code"]), ("tick", 0))

    def test_failed_run_is_recorded_as_failure_in_ledger(self):
        root = make_project({"n": SRC})
        run_ep(root, "run_source.py", "n", with_framework=True)
        rec = json.loads((root / ".agent" / "run_status.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(rec["entrypoint"], "run_source")
        self.assertNotEqual(rec["exit_code"], 0)


if __name__ == "__main__":
    unittest.main()
