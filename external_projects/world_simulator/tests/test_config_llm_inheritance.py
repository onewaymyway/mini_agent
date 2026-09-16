"""tests/test_config_llm_inheritance.py — `world_simulator/config.py` 里
"自动继承主项目 LLM 配置"逻辑的单元测试。

背景：`spec_generator.generate_scenario`/`engine.advance` 之类的 LLM
调用统一走 `world_simulator.config.load_llm_cfg()` 获取 `AppConfig`，
而不是各自直接 `from mini_agent.config import load_config`——目的是让
本项目在**未注册进 daemon**、也**没有显式设置**
`MINI_AGENT_MAIN_PROJECT_ROOT` 环境变量的情况下，只要还"原地"躺在某个
mini_agent 主仓库的 `external_projects/` 下，也能自动继承主项目的
`agent_config.json`/`providers.json`（含 API key），不需要用户手动配置
一遍。本文件只测这条"探测 + 设置环境变量"的纯逻辑，不依赖真实
`mini_agent.config.load_config()` 能否成功（那部分是宿主职责，见
`docs/testing_guide.md`）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from world_simulator import config as wsc


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """每个用例前确保环境变量是干净的，不被本机真实环境（比如就在
    mini_agent 仓库里跑测试）污染。"""
    monkeypatch.delenv("MINI_AGENT_MAIN_PROJECT_ROOT", raising=False)
    yield


def _fake_layout(tmp_path: Path, *, with_src: bool, with_agent_config: bool) -> Path:
    """在 tmp_path 下搭一个 `<main_root>/external_projects/world_simulator/`
    的假布局，返回 `world_simulator` 子目录路径（供 monkeypatch
    `PROJECT_ROOT` 用）。"""
    main_root = tmp_path / "fake_main_project"
    ws_root = main_root / "external_projects" / "world_simulator"
    ws_root.mkdir(parents=True)
    if with_src:
        (main_root / "src" / "mini_agent").mkdir(parents=True)
    if with_agent_config:
        (main_root / "agent_config.json").write_text("{}", encoding="utf-8")
    return ws_root


def test_detects_in_place_layout_via_src_mini_agent(tmp_path, monkeypatch):
    ws_root = _fake_layout(tmp_path, with_src=True, with_agent_config=False)
    monkeypatch.setattr(wsc, "PROJECT_ROOT", ws_root)

    detected = wsc._detect_in_place_main_project_root()

    assert detected == ws_root.parent.parent


def test_detects_in_place_layout_via_agent_config_json(tmp_path, monkeypatch):
    ws_root = _fake_layout(tmp_path, with_src=False, with_agent_config=True)
    monkeypatch.setattr(wsc, "PROJECT_ROOT", ws_root)

    detected = wsc._detect_in_place_main_project_root()

    assert detected == ws_root.parent.parent


def test_no_signal_returns_none(tmp_path, monkeypatch):
    ws_root = _fake_layout(tmp_path, with_src=False, with_agent_config=False)
    monkeypatch.setattr(wsc, "PROJECT_ROOT", ws_root)

    assert wsc._detect_in_place_main_project_root() is None


def test_not_under_external_projects_returns_none(tmp_path, monkeypatch):
    # world_simulator 没有挂在 `external_projects/` 下 —— 比如已经被
    # 单独搬走、放进自己的 git 仓库 —— 不应该被误判。
    standalone_root = tmp_path / "world_simulator_standalone"
    standalone_root.mkdir()
    (standalone_root.parent / "src" / "mini_agent").mkdir(parents=True)
    monkeypatch.setattr(wsc, "PROJECT_ROOT", standalone_root)

    assert wsc._detect_in_place_main_project_root() is None


def test_ensure_main_project_root_env_sets_when_detected(tmp_path, monkeypatch):
    ws_root = _fake_layout(tmp_path, with_src=True, with_agent_config=False)
    monkeypatch.setattr(wsc, "PROJECT_ROOT", ws_root)

    wsc.ensure_main_project_root_env()

    assert os.environ.get("MINI_AGENT_MAIN_PROJECT_ROOT") == str(ws_root.parent.parent)


def test_ensure_main_project_root_env_noop_when_not_detected(tmp_path, monkeypatch):
    ws_root = _fake_layout(tmp_path, with_src=False, with_agent_config=False)
    monkeypatch.setattr(wsc, "PROJECT_ROOT", ws_root)

    wsc.ensure_main_project_root_env()

    assert "MINI_AGENT_MAIN_PROJECT_ROOT" not in os.environ


def test_ensure_main_project_root_env_does_not_override_existing(tmp_path, monkeypatch):
    # 已经显式设置过环境变量时（比如 daemon 拉起子进程时设置的），不能
    # 被这里的"原地布局探测"覆盖——环境变量的优先级必须高于自动探测。
    ws_root = _fake_layout(tmp_path, with_src=True, with_agent_config=False)
    monkeypatch.setattr(wsc, "PROJECT_ROOT", ws_root)
    monkeypatch.setenv("MINI_AGENT_MAIN_PROJECT_ROOT", "/some/explicit/path")

    wsc.ensure_main_project_root_env()

    assert os.environ["MINI_AGENT_MAIN_PROJECT_ROOT"] == "/some/explicit/path"
