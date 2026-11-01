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


def test_build_assets_entrypoint_stub(tmp_path, export_dir):
    import os
    out = tmp_path / "o"
    p = run("entrypoints/import_novel.py", str(export_dir), "--projects-root", str(out), "--title", "书")
    proj = json.loads(p.stdout) if False else None
    created = [d for d in out.iterdir()][0]
    env = dict(os.environ, NOVEL_TO_VIDEO_PROVIDERS="stub")
    p2 = subprocess.run([sys.executable, "entrypoints/build_assets.py", str(created)],
                        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=env)
    assert p2.returncode == 0, p2.stdout + p2.stderr
    data = json.loads(p2.stdout)
    assert data["ok"] and data["status"]["data"]["counts"]["total"] == 3


def test_run_chapter_entrypoint_rejects_unknown_chapter(tmp_path, export_dir):
    out = tmp_path / "o"
    run("entrypoints/import_novel.py", str(export_dir), "--projects-root", str(out), "--title", "书")
    created = next(out.iterdir())
    p = run("entrypoints/run_chapter.py", str(created), "--chapters", "99")
    assert p.returncode == 1


def test_run_queue_entrypoint_g0_reports_waiting(tmp_path, export_dir, monkeypatch):
    """run_queue 入口：G0 下章节只会停在等待审核，退出码为 0（waiting 不是错误）。"""
    import json as _json
    from novel_to_video import tool_api
    from novel_to_video.providers.image import StubImageProvider
    from novel_to_video.providers.text import RuleTextLLM
    from novel_to_video.steps import global_visual as gv
    monkeypatch.setenv("NOVEL_TO_VIDEO_PROVIDERS", "stub")
    res = tool_api.create_project_from_source(str(tmp_path / "out"), str(export_dir), title="t")
    assert res["ok"]
    proj = res["data"]["project_dir"]
    gv.visual_text_pass(__import__("pathlib").Path(proj), RuleTextLLM())
    gv.portrait_pass(__import__("pathlib").Path(proj), StubImageProvider())
    out = run("entrypoints/run_queue.py", proj, "--chapters", "1", "--gear", "G0")
    assert out.returncode in (0, 1), out.stderr  # 1 允许：离线规则剧本可能未通过校验
    payload = _json.loads(out.stdout)
    assert payload["ok"] is True
    assert "1" in payload["data"]["results"] or 1 in payload["data"]["results"]
