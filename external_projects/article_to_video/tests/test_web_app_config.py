"""看板配置回归：project.yaml 的 web_app 块（端口 8507、入口 app.py、本机绑定）。

若 mini_agent 可导入，则用其清单解析器校验；否则只做 YAML 层面的检查。
"""
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def _spec():
    return yaml.safe_load((ROOT / "project.yaml").read_text(encoding="utf-8"))["web_app"]


def test_web_app_port_and_entry_follow_the_convention():
    spec = _spec()
    assert spec["port"] == 8507                       # 8502–8506 已被其它项目占用
    assert (ROOT / spec["entry"]).is_file()
    assert spec.get("bind") == "127.0.0.1"            # 无登录保护，默认只监听本机


def test_port_is_not_claimed_by_another_project_in_this_repo():
    repo = ROOT.parent
    claims = {}
    for yml in repo.glob("*/project.yaml"):
        if yml.parent == ROOT:
            continue
        data = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
        port = (data.get("web_app") or {}).get("port")
        if port:
            claims[yml.parent.name] = port
    assert 8507 not in claims.values(), claims


def test_mini_agent_manifest_parser_accepts_the_project():
    try:
        from mini_agent.external_projects.manifest import load_manifest
    except Exception:  # noqa: BLE001 — mini_agent 不在本环境时跳过
        pytest.skip("mini_agent 不可导入，跳过清单解析校验")
    m = load_manifest(ROOT / "project.yaml")
    assert m.web_app is not None and m.web_app.port == 8507 and m.web_app.entry == "app.py"
