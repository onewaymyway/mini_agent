"""所有 external_projects/* 的 project.yaml 都必须能通过框架 manifest 校验，
且 entrypoint 脚本存在、web_app 端口不冲突。

此前 info_hub 的 project.yaml 用了不被识别的 `command` 字段，注册时才报
“缺少必填字段 'cmd'”——这里在测试阶段就拦住同类问题。
"""
import shlex
from pathlib import Path

import pytest

from mini_agent.external_projects.manifest import load_manifest

ROOT = Path(__file__).resolve().parent.parent / "external_projects"
PROJECTS = sorted(p for p in ROOT.iterdir() if (p / "project.yaml").is_file()) if ROOT.is_dir() else []


@pytest.mark.parametrize("proj", PROJECTS, ids=[p.name for p in PROJECTS])
def test_manifest_loads_and_scripts_exist(proj):
    m = load_manifest(proj)
    assert m.entrypoints, "至少一个 entrypoint"
    cmds = [ep.cmd for ep in m.entrypoints.values()] + ([m.health_check.cmd] if m.health_check else [])
    for cmd in cmds:
        parts = shlex.split(cmd)
        scripts = [x for x in parts if x.endswith(".py")]
        for sc in scripts:
            assert (proj / sc).is_file(), f"{proj.name}: {cmd} 指向的脚本不存在: {sc}"
    if m.web_app:
        assert (proj / m.web_app.entry).is_file(), f"{proj.name}: web_app.entry 不存在"


def test_web_app_ports_are_unique():
    seen = {}
    for proj in PROJECTS:
        m = load_manifest(proj)
        if m.web_app:
            assert m.web_app.port not in seen, f"端口 {m.web_app.port} 被 {seen[m.web_app.port]} 与 {proj.name} 同时使用"
            seen[m.web_app.port] = proj.name


def test_info_hub_contract():
    m = load_manifest(ROOT / "info_hub")
    assert m.name == "info_hub"
    assert {"tick", "run_source", "report", "resume_source"} <= set(m.entrypoints)
    assert m.health_check and m.health_check.cmd == "python entrypoints/health.py"
    assert m.web_app and m.web_app.port == 8506
    # 保守默认：不声明任何 schedule（daemon 不会自动调度，避免与看板后台调度重叠）
    assert m.scheduled_entrypoints() == []
    for key in ("run_source", "resume_source"):
        assert [p.name for p in m.entrypoints[key].params] == ["source_id"]
        assert m.entrypoints[key].params[0].required
    assert [p.name for p in m.entrypoints["report"].params] == ["plan_id"]
    assert all(ep.concurrency == "unmanaged" and ep.timeout_sec for ep in m.entrypoints.values())


def test_info_hub_registrable_and_scan_no_longer_flags_it(tmp_path):
    from mini_agent.external_projects.registry import ExternalProjectRegistry, scan_unregistered

    reg = ExternalProjectRegistry(tmp_path / "reg.json")
    cands = {c["dir_name"]: c for c in scan_unregistered(ROOT, reg)}
    assert cands["info_hub"]["registrable"], cands["info_hub"]["manifest_error"]
    rec = reg.register("info_hub", ROOT / "info_hub")
    assert rec.enabled is False          # 默认不开自动调度
