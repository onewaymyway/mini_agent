import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(*args):
    return subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8")


def test_health_entrypoint():
    p = run("entrypoints/health.py")
    assert p.returncode == 0 and p.stdout.strip() == "ok"


def test_import_entrypoint_dry_run(tmp_path, export_dir):
    p = run("entrypoints/import_novel.py", str(export_dir), "--projects-root", str(tmp_path / "o"),
            "--dry-run")
    assert p.returncode == 0, p.stderr
    data = json.loads(p.stdout)
    assert data["ok"] and data["data"]["dry_run"]
    assert not (tmp_path / "o").exists()


def test_import_entrypoint_creates_and_lists(tmp_path, export_dir):
    out = tmp_path / "o"
    p = run("entrypoints/import_novel.py", str(export_dir), "--projects-root", str(out), "--title", "书")
    assert p.returncode == 0, p.stderr
    p2 = run("entrypoints/list_projects.py", "--projects-root", str(out))
    assert p2.returncode == 0
    rows = json.loads(p2.stdout)["data"]
    assert rows[0]["title"] == "书"


def test_import_entrypoint_blocked_exit_code(tmp_path):
    p = run("entrypoints/import_novel.py", str(tmp_path / "nope"), "--projects-root", str(tmp_path / "o"))
    assert p.returncode == 1
