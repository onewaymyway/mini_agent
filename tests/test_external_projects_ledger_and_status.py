"""
tests/test_external_projects_ledger_and_status.py — 阶段 4（状态账本约定
+ daemon 侧状态聚合）验收测试

对应 next_doc/external_projects_workspace_plan.md 阶段 4。独立成文件
（而不是塞进 test_external_projects.py），因为阶段 3/4 各自的关注点
足够独立，分开更容易定位失败原因。
"""

from __future__ import annotations

import sys

import pytest

from mini_agent.external_projects.ledger import (
    RunRecord,
    last_record,
    read_ledger,
    record_run,
    track_run,
)
from mini_agent.external_projects.manifest import ProjectManifestError
from mini_agent.external_projects.registry import ExternalProjectRegistry
from mini_agent.external_projects.status import (
    aggregate_status,
    probe_health,
    project_status_snapshot,
)

VALID_YAML = """
name: {name}
entrypoints:
  work:
    cmd: "{python} -c \\"pass\\""
"""


def _register(tmp_path, name="proj", *, health_cmd=None):
    root = tmp_path / name
    root.mkdir()
    yaml_text = VALID_YAML.format(name=name, python=sys.executable)
    if health_cmd:
        yaml_text += f'health_check:\n  cmd: "{health_cmd}"\n'
    (root / "project.yaml").write_text(yaml_text, encoding="utf-8")
    registry = ExternalProjectRegistry(store_path=tmp_path / "registry.json")
    registry.register(name, root)
    return registry, root


# ── ledger.py ────────────────────────────────────────────────────────────


def test_record_run_writes_jsonl_line(tmp_path):
    record_run(tmp_path, "hotlist_scan", 0, "manual")
    ledger_file = tmp_path / ".agent" / "run_status.jsonl"
    assert ledger_file.exists()
    lines = ledger_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_record_run_rejects_invalid_trigger(tmp_path):
    with pytest.raises(ValueError):
        record_run(tmp_path, "x", 0, "not_a_valid_trigger")


def test_read_ledger_empty_when_no_file(tmp_path):
    assert read_ledger(tmp_path) == []
    assert last_record(tmp_path) is None


def test_read_ledger_order_and_limit(tmp_path):
    for i in range(5):
        record_run(tmp_path, f"ep{i}", 0, "manual")
    all_records = read_ledger(tmp_path)
    assert [r.entrypoint for r in all_records] == [f"ep{i}" for i in range(5)]

    limited = read_ledger(tmp_path, limit=2)
    assert [r.entrypoint for r in limited] == ["ep3", "ep4"]

    assert last_record(tmp_path).entrypoint == "ep4"


def test_read_ledger_skips_corrupted_lines(tmp_path):
    ledger_file = tmp_path / ".agent" / "run_status.jsonl"
    ledger_file.parent.mkdir(parents=True)
    ledger_file.write_text(
        '{"entrypoint": "good", "started_at": "a", "finished_at": "b", '
        '"exit_code": 0, "trigger": "manual"}\n'
        "not even json\n",
        encoding="utf-8",
    )
    records = read_ledger(tmp_path)
    assert len(records) == 1
    assert records[0].entrypoint == "good"


def test_track_run_success_records_zero_exit(tmp_path):
    with track_run(tmp_path, "hotlist_scan", trigger="external_cron"):
        pass  # 正常跑完
    rec = last_record(tmp_path)
    assert rec.exit_code == 0
    assert rec.success is True
    assert rec.trigger == "external_cron"
    assert rec.error_summary is None


def test_track_run_exception_records_failure_and_reraises(tmp_path):
    with pytest.raises(RuntimeError):
        with track_run(tmp_path, "hotlist_scan", trigger="manual"):
            raise RuntimeError("boom")
    rec = last_record(tmp_path)
    assert rec.exit_code == 1
    assert rec.success is False
    assert "boom" in rec.error_summary


# ── status.py ────────────────────────────────────────────────────────────


def test_probe_health_none_when_not_declared(tmp_path):
    _, root = _register(tmp_path)
    from mini_agent.external_projects.manifest import load_manifest

    manifest = load_manifest(root)
    assert probe_health(manifest) is None


def test_probe_health_true_and_false(tmp_path):
    ok_cmd = f"{sys.executable} -c 'import sys; sys.exit(0)'"
    _, root_ok = _register(tmp_path, "ok", health_cmd=ok_cmd)
    from mini_agent.external_projects.manifest import load_manifest

    manifest_ok = load_manifest(root_ok)
    assert probe_health(manifest_ok) is True

    bad_cmd = f"{sys.executable} -c 'import sys; sys.exit(1)'"
    _, root_bad = _register(tmp_path, "bad", health_cmd=bad_cmd)
    manifest_bad = load_manifest(root_bad)
    assert probe_health(manifest_bad) is False


def test_project_status_snapshot_falls_back_to_ledger_when_no_health_check(tmp_path):
    registry, root = _register(tmp_path)
    record_run(root, "work", 0, "manual")

    snap = project_status_snapshot(registry, "proj")
    assert snap.health == "healthy"
    assert snap.health_source == "ledger"
    assert snap.last_run is not None


def test_project_status_snapshot_unknown_when_no_ledger_no_health_check(tmp_path):
    registry, _root = _register(tmp_path)
    snap = project_status_snapshot(registry, "proj")
    assert snap.health == "unknown"
    assert snap.health_source == "none"
    assert snap.last_run is None


def test_project_status_snapshot_unhealthy_from_failed_ledger(tmp_path):
    registry, root = _register(tmp_path)
    record_run(root, "work", 1, "manual", error_summary="boom")
    snap = project_status_snapshot(registry, "proj")
    assert snap.health == "unhealthy"


def test_project_status_snapshot_handles_bad_manifest(tmp_path):
    root = tmp_path / "broken"
    root.mkdir()
    (root / "project.yaml").write_text("entrypoints: {}\n", encoding="utf-8")
    registry = ExternalProjectRegistry(store_path=tmp_path / "registry.json")
    registry.register("broken", root, validate=False)

    snap = project_status_snapshot(registry, "broken")
    assert snap.health == "unknown"
    assert snap.manifest_error is not None


def test_aggregate_status_multiple_projects(tmp_path):
    registry, root_a = _register(tmp_path, "proj_a")
    record_run(root_a, "work", 0, "manual")
    root_b = tmp_path / "proj_b"
    root_b.mkdir()
    (root_b / "project.yaml").write_text(
        VALID_YAML.format(name="proj_b", python=sys.executable), encoding="utf-8"
    )
    registry.register("proj_b", root_b)

    results = aggregate_status(registry)
    by_name = {r["name"]: r for r in results}
    assert by_name["proj_a"]["health"] == "healthy"
    assert by_name["proj_b"]["health"] == "unknown"
    assert by_name["proj_a"]["last_run"]["exit_code"] == 0
    assert by_name["proj_b"]["last_run"] is None


def test_aggregate_status_empty_registry(tmp_path):
    registry = ExternalProjectRegistry(store_path=tmp_path / "registry.json")
    assert aggregate_status(registry) == []


# ── scheduler.py 自动写账本（阶段 3 的 _run_entrypoint 现在会自动记账）───────


def test_trigger_run_writes_ledger_entry(tmp_path):
    from mini_agent.external_projects.scheduler import trigger_run

    root = tmp_path / "proj"
    root.mkdir()
    yaml_text = f"""
name: proj
entrypoints:
  touch:
    cmd: "{sys.executable} -c \\"open('out.txt','w').write('ran')\\""
"""
    (root / "project.yaml").write_text(yaml_text, encoding="utf-8")
    registry = ExternalProjectRegistry(store_path=tmp_path / "registry.json")
    registry.register("proj", root)

    trigger_run(registry, "proj", "touch", trigger="manual")

    rec = last_record(root)
    assert rec is not None
    assert rec.entrypoint == "touch"
    assert rec.exit_code == 0
    assert rec.trigger == "manual"


def test_trigger_run_failure_records_error_summary(tmp_path):
    from mini_agent.external_projects.scheduler import trigger_run

    root = tmp_path / "proj"
    root.mkdir()
    yaml_text = f"""
name: proj
entrypoints:
  fail:
    cmd: "{sys.executable} -c \\"import sys; sys.exit(3)\\""
"""
    (root / "project.yaml").write_text(yaml_text, encoding="utf-8")
    registry = ExternalProjectRegistry(store_path=tmp_path / "registry.json")
    registry.register("proj", root)

    result = trigger_run(registry, "proj", "fail", trigger="manual")
    assert result.returncode == 3

    rec = last_record(root)
    assert rec.exit_code == 3
    assert rec.success is False
    assert "3" in rec.error_summary


# ── CLI: `projects ledger` 子命令 ───────────────────────────────────────


def test_cli_ledger_subcommand(tmp_path, monkeypatch):
    from mini_agent.cli.commands.projects_cmd import run_projects_cli
    from mini_agent.external_projects import registry as registry_mod

    monkeypatch.setattr(registry_mod, "DEFAULT_REGISTRY_PATH", tmp_path / "registry.json")
    root = tmp_path / "proj"
    root.mkdir()
    yaml_text = f"""
name: proj
entrypoints:
  touch:
    cmd: "{sys.executable} -c \\"open('out.txt','w').write('ran')\\""
"""
    (root / "project.yaml").write_text(yaml_text, encoding="utf-8")

    assert run_projects_cli(["register", str(root)]) == 0
    assert run_projects_cli(["ledger", "proj"]) == 0  # 空账本也应正常退出
    assert run_projects_cli(["run", "proj", "touch"]) == 0
    assert run_projects_cli(["ledger", "proj"]) == 0
    assert run_projects_cli(["status", "proj"]) == 0
    assert run_projects_cli(["list"]) == 0
