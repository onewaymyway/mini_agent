"""tests/test_desktop_locator.py

覆盖桌面端的项目根目录自动查找（apps/mini_agent_desktop/mini_agent_desktop/project_locator.py）：

- 优先级：命令行 > desktop.json > 环境变量 > 当前目录向上 > 桌面端安装位置向上
- 显式来源不校验标记；自动查找必须命中 src/mini_agent/__init__.py
- 找不到时返回 None（不再静默使用当前目录）
- --check 输出会说明目录来源；找不到时退出码 4 并给出提示

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_desktop_locator.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "mini_agent_desktop"))

from mini_agent_desktop import project_locator as loc  # noqa: E402
from mini_agent_desktop.app import (  # noqa: E402
    NOT_FOUND_HINT, parse_args, resolve_project_root_info, run_check,
)
from mini_agent_desktop.config import DesktopConfig  # noqa: E402


def make_project(root: Path) -> Path:
    (root / "src" / "mini_agent").mkdir(parents=True)
    (root / "src" / "mini_agent" / "__init__.py").write_text("", encoding="utf-8")
    (root / "apps" / "mini_agent_desktop" / "mini_agent_desktop").mkdir(parents=True)
    return root


@pytest.fixture
def project(tmp_path):
    return make_project(tmp_path / "mini_agent")


def test_cli_wins_and_is_not_validated(tmp_path, project):
    explicit = tmp_path / "anywhere"
    r = loc.resolve_project_root(cli_value=str(explicit), config_value=str(project),
                                 env={}, cwd=project, app_location=project)
    assert r.path == explicit.resolve() and r.source == loc.SOURCE_CLI


def test_config_beats_env_and_search(project, tmp_path):
    cfg_root = tmp_path / "from_cfg"
    r = loc.resolve_project_root(config_value=str(cfg_root),
                                 env={loc.ENV_VAR: str(project)}, cwd=project)
    assert r.path == cfg_root.resolve() and r.source == loc.SOURCE_CONFIG


def test_env_beats_search(project, tmp_path):
    env_root = tmp_path / "from_env"
    r = loc.resolve_project_root(env={loc.ENV_VAR: str(env_root)}, cwd=project)
    assert r.path == env_root.resolve() and r.source.startswith("环境变量")


def test_search_from_cwd_finds_project_from_subdirectory(project):
    sub = project / "apps" / "mini_agent_desktop"
    r = loc.resolve_project_root(env={}, cwd=sub, app_location=Path("/nonexistent/x.py"))
    assert r.path == project.resolve()
    assert r.source == loc.SOURCE_CWD


def test_search_from_app_location_when_cwd_outside_project(project, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    app_file = project / "apps" / "mini_agent_desktop" / "mini_agent_desktop" / "project_locator.py"
    app_file.write_text("", encoding="utf-8")
    r = loc.resolve_project_root(env={}, cwd=outside, app_location=app_file)
    assert r.path == project.resolve()
    assert r.source == loc.SOURCE_APP


def test_returns_none_when_nothing_found(tmp_path):
    lonely = tmp_path / "lonely" / "deep"
    lonely.mkdir(parents=True)
    assert loc.resolve_project_root(env={}, cwd=lonely,
                                    app_location=lonely / "x.py") is None


def test_marker_required_for_search(tmp_path):
    fake = tmp_path / "not_a_project"
    (fake / "src").mkdir(parents=True)          # 有 src 但没有 mini_agent 包
    assert loc.find_upwards(fake) is None


def test_find_upwards_respects_max_levels(tmp_path):
    root = make_project(tmp_path / "p")
    deep = root
    for i in range(12):
        deep = deep / f"d{i}"
    deep.mkdir(parents=True)
    assert loc.find_upwards(deep, max_levels=3) is None
    assert loc.find_upwards(deep, max_levels=20) == root.resolve()


def test_app_level_resolution_uses_args_and_config(project, tmp_path, monkeypatch):
    monkeypatch.delenv(loc.ENV_VAR, raising=False)
    args = parse_args(["--project-root", str(tmp_path / "x")])
    r = resolve_project_root_info(args, DesktopConfig())
    assert r.source == loc.SOURCE_CLI


def test_check_reports_source(tmp_path, capsys):
    rc = run_check(tmp_path, 1, loc.SOURCE_CWD)
    out = capsys.readouterr().out
    assert "来源：当前目录向上查找" in out
    assert rc == 2  # 端口没人监听 → daemon 未运行


def test_check_exits_4_with_hint_when_project_not_found(monkeypatch, capsys):
    import mini_agent_desktop.app as app_mod
    from mini_agent_desktop.app import main

    monkeypatch.setattr(app_mod, "resolve_project_root_info", lambda args, cfg: None)
    assert main(["--check"]) == 4
    assert NOT_FOUND_HINT in capsys.readouterr().err
