import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(script, *args, home):
    env = dict(os.environ, ARTICLE_GARDEN_HOME=str(home), PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, str(ROOT / "entrypoints" / script), *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=60)


def test_health_ok_on_empty_home(data_home):
    r = run("health.py", home=data_home)
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads(r.stdout)
    assert data["ok"] and {c["name"] for c in data["checks"]} >= {"python>=3.9", "PyYAML", "data_root_writable", "domains"}


def test_create_and_list_via_cli(data_home):
    r = run("create_domain.py", "ai", "人工智能", "ai", home=data_home)
    assert r.returncode == 0, r.stderr
    r = run("create_domain.py", "ai", "重复", home=data_home)
    assert r.returncode == 1 and "已存在" in r.stderr
    r = run("create_domain.py", "x1", "副本", "--copy-charter-from", "ai", home=data_home)
    assert r.returncode == 0, r.stderr
    rows = json.loads(run("list_domains.py", home=data_home).stdout)["domains"]
    assert [d["id"] for d in rows] == ["ai", "x1"] and rows[0]["articles"] == 0


def test_health_reports_broken_domain_without_failing(data_home):
    run("create_domain.py", "ai", "AI", home=data_home)
    (data_home / "domains" / "ai" / "charter.yaml").write_text("a: [x", encoding="utf-8")
    r = run("health.py", home=data_home)
    data = json.loads(r.stdout)
    d = next(c for c in data["checks"] if c["name"] == "domains")
    assert d["ok"] is False and r.returncode == 0   # domains 非必需项：只提示


def test_project_yaml_is_valid_for_mini_agent_manifest():
    mani = ROOT.parents[1] / "src" / "mini_agent" / "external_projects"
    if not mani.is_dir():
        import pytest
        pytest.skip("不在 mini_agent 仓库内")
    sys.path.insert(0, str(ROOT.parents[1] / "src"))
    try:
        from mini_agent.external_projects.manifest import load_manifest
    except ModuleNotFoundError as exc:   # 缺 fastapi 等 mini_agent 自身依赖：与本项目无关
        import pytest
        pytest.skip(f"mini_agent 依赖未装：{exc}")
    m = load_manifest(ROOT)
    assert m.name == "article_garden"
