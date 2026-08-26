"""
tests/test_external_projects.py — 阶段 3（project.yaml 契约 + 注册表 +
CLI + 调度器）验收测试

对应 next_doc/external_projects_workspace_plan.md 阶段 3。
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

from mini_agent.external_projects.manifest import (
    ProjectManifestError,
    load_manifest,
    parse_manifest,
)
from mini_agent.external_projects.registry import (
    ExternalProjectRegistry,
    ExternalProjectRegistryError,
)
from mini_agent.external_projects.scheduler import cron_matches, run_due_entrypoints, trigger_run

VALID_YAML = """
name: stock_watch
entrypoints:
  hotlist_scan:
    cmd: "python entrypoints/run_hotlist_scan.py"
    schedule: "cron: 0 9,13 * * 1-5"
    timeout_sec: 600
  kline_batch:
    cmd: "python entrypoints/run_kline_batch.py"
    schedule: "cron: 0 16 * * 1-5"
health_check:
  cmd: "python entrypoints/health.py"
resources:
  allowed_domains: ["xueqiu.com", "eastmoney.com"]
  max_concurrency: 1
"""


# ── manifest.py ──────────────────────────────────────────────────────────


def test_parse_manifest_happy_path():
    manifest = parse_manifest(VALID_YAML)
    assert manifest.name == "stock_watch"
    assert set(manifest.entrypoints) == {"hotlist_scan", "kline_batch"}
    assert manifest.entrypoints["hotlist_scan"].cron_expr == "0 9,13 * * 1-5"
    assert manifest.entrypoints["hotlist_scan"].timeout_sec == 600
    assert manifest.health_check.cmd == "python entrypoints/health.py"
    assert manifest.resources.allowed_domains == ["xueqiu.com", "eastmoney.com"]
    assert manifest.resources.max_concurrency == 1
    assert len(manifest.scheduled_entrypoints()) == 2


@pytest.mark.parametrize(
    "bad_yaml",
    [
        "entrypoints:\n  a:\n    cmd: x\n",  # 缺 name
        "name: foo\n",  # 缺 entrypoints
        "name: foo\nentrypoints: {}\n",  # entrypoints 空
        "name: foo\nentrypoints:\n  a:\n    cmd: x\n    schedule: 'cron: 0 0 * *'\n",  # cron 字段数不对
        "name: foo\nentrypoints:\n  a:\n    cmd: x\n    timeout_sec: -1\n",  # timeout 非正
        "name: foo\nentrypoints:\n  a:\n    cmd: x\nresources:\n  max_concurrency: 0\n",  # 并发 < 1
    ],
)
def test_parse_manifest_rejects_invalid(bad_yaml):
    with pytest.raises(ProjectManifestError):
        parse_manifest(bad_yaml)


def test_load_manifest_from_dir_and_file(tmp_path):
    root = tmp_path / "stock_watch"
    root.mkdir()
    (root / "project.yaml").write_text(VALID_YAML, encoding="utf-8")

    m1 = load_manifest(root)
    m2 = load_manifest(root / "project.yaml")
    assert m1.name == m2.name == "stock_watch"
    assert m1.source_dir == root.resolve()


def test_load_manifest_missing_file(tmp_path):
    with pytest.raises(ProjectManifestError):
        load_manifest(tmp_path / "does_not_exist")


# ── registry.py ──────────────────────────────────────────────────────────


def _make_project_dir(tmp_path: Path, name: str = "stock_watch") -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / "project.yaml").write_text(VALID_YAML.replace("stock_watch", name), encoding="utf-8")
    return root


def test_registry_register_list_get_unregister(tmp_path):
    store = tmp_path / "registry.json"
    project_dir = _make_project_dir(tmp_path)
    registry = ExternalProjectRegistry(store_path=store)

    record = registry.register("stock_watch", project_dir)
    assert record.enabled is True
    assert store.exists()

    listed = registry.list()
    assert len(listed) == 1 and listed[0].name == "stock_watch"

    fetched = registry.get("stock_watch")
    assert fetched.path == str(project_dir.resolve())

    registry.unregister("stock_watch")
    assert registry.list() == []


def test_registry_register_duplicate_raises(tmp_path):
    store = tmp_path / "registry.json"
    project_dir = _make_project_dir(tmp_path)
    registry = ExternalProjectRegistry(store_path=store)
    registry.register("stock_watch", project_dir)

    with pytest.raises(ExternalProjectRegistryError):
        registry.register("stock_watch", project_dir)


def test_registry_register_invalid_manifest_raises(tmp_path):
    root = tmp_path / "broken"
    root.mkdir()
    (root / "project.yaml").write_text("entrypoints: {}\n", encoding="utf-8")
    registry = ExternalProjectRegistry(store_path=tmp_path / "registry.json")

    with pytest.raises(ExternalProjectRegistryError):
        registry.register("broken", root)

    # validate=False 时允许先占位注册，不做 manifest 校验
    registry.register("broken", root, validate=False)
    assert registry.get("broken").path == str(root.resolve())


def test_registry_unregister_unknown_raises(tmp_path):
    registry = ExternalProjectRegistry(store_path=tmp_path / "registry.json")
    with pytest.raises(ExternalProjectRegistryError):
        registry.unregister("nope")


def test_registry_isolated_per_store_path(tmp_path):
    dir_a = _make_project_dir(tmp_path, "proj_a")
    dir_b = _make_project_dir(tmp_path, "proj_b")
    reg_a = ExternalProjectRegistry(store_path=tmp_path / "a.json")
    reg_b = ExternalProjectRegistry(store_path=tmp_path / "b.json")
    reg_a.register("proj_a", dir_a)
    reg_b.register("proj_b", dir_b)

    assert [p.name for p in reg_a.list()] == ["proj_a"]
    assert [p.name for p in reg_b.list()] == ["proj_b"]


def test_registry_set_enabled(tmp_path):
    project_dir = _make_project_dir(tmp_path)
    registry = ExternalProjectRegistry(store_path=tmp_path / "registry.json")
    registry.register("stock_watch", project_dir)

    registry.set_enabled("stock_watch", False)
    assert registry.get("stock_watch").enabled is False
    assert registry.list(enabled_only=True) == []

    registry.set_enabled("stock_watch", True)
    assert registry.get("stock_watch").enabled is True


def test_registry_corrupted_store_degrades_to_empty(tmp_path):
    store = tmp_path / "registry.json"
    store.write_text("{not valid json", encoding="utf-8")
    registry = ExternalProjectRegistry(store_path=store)
    assert registry.list() == []  # 原则三：注册表损坏不应炸掉调用方


# ── scheduler.py ─────────────────────────────────────────────────────────


def test_cron_matches_basic():
    # "0 9,13 * * 1-5" -> 周一至周五的 9:00 或 13:00
    monday_9am = dt.datetime(2026, 8, 24, 9, 0)  # 2026-08-24 是周一
    assert monday_9am.isoweekday() == 1
    assert cron_matches("0 9,13 * * 1-5", monday_9am) is True

    monday_9_01 = dt.datetime(2026, 8, 24, 9, 1)
    assert cron_matches("0 9,13 * * 1-5", monday_9_01) is False

    saturday_9am = dt.datetime(2026, 8, 29, 9, 0)  # 周六
    assert cron_matches("0 9,13 * * 1-5", saturday_9am) is False


def test_trigger_run_executes_entrypoint(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "out.txt").write_text("", encoding="utf-8")
    manifest_yaml = f"""
name: proj
entrypoints:
  touch:
    cmd: "{sys.executable} -c \\"open('out.txt','w').write('ran')\\""
"""
    (root / "project.yaml").write_text(manifest_yaml, encoding="utf-8")
    registry = ExternalProjectRegistry(store_path=tmp_path / "registry.json")
    registry.register("proj", root)

    result = trigger_run(registry, "proj", "touch")
    assert result.returncode == 0
    assert (root / "out.txt").read_text(encoding="utf-8") == "ran"


def test_trigger_run_unknown_entrypoint_raises(tmp_path):
    root = _make_project_dir(tmp_path)
    registry = ExternalProjectRegistry(store_path=tmp_path / "registry.json")
    registry.register("stock_watch", root)

    with pytest.raises(ProjectManifestError):
        trigger_run(registry, "stock_watch", "does_not_exist")


def test_run_due_entrypoints_only_triggers_matching_schedule(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    manifest_yaml = f"""
name: proj
entrypoints:
  due:
    cmd: "{sys.executable} -c \\"open('due.txt','w').write('x')\\""
    schedule: "cron: 0 9 * * 1-5"
  not_due:
    cmd: "{sys.executable} -c \\"open('not_due.txt','w').write('x')\\""
    schedule: "cron: 0 23 * * 1-5"
"""
    (root / "project.yaml").write_text(manifest_yaml, encoding="utf-8")
    registry = ExternalProjectRegistry(store_path=tmp_path / "registry.json")
    registry.register("proj", root)

    moment = dt.datetime(2026, 8, 24, 9, 0)  # 周一 9:00，命中 due，不命中 not_due
    results = run_due_entrypoints(registry, now=moment)

    assert len(results) == 1
    assert results[0].entrypoint_key == "due"
    assert (root / "due.txt").exists()
    assert not (root / "not_due.txt").exists()


def test_run_due_entrypoints_skips_disabled_projects(tmp_path):
    root = _make_project_dir(tmp_path)
    registry = ExternalProjectRegistry(store_path=tmp_path / "registry.json")
    registry.register("stock_watch", root)
    registry.set_enabled("stock_watch", False)

    moment = dt.datetime(2026, 8, 24, 9, 0)
    assert run_due_entrypoints(registry, now=moment) == []


# ── CLI (projects_cmd.py) ───────────────────────────────────────────────


def test_cli_register_list_status_unregister(tmp_path, monkeypatch):
    from mini_agent.cli.commands.projects_cmd import run_projects_cli
    from mini_agent.external_projects import registry as registry_mod

    monkeypatch.setattr(registry_mod, "DEFAULT_REGISTRY_PATH", tmp_path / "registry.json")
    project_dir = _make_project_dir(tmp_path)

    assert run_projects_cli(["register", str(project_dir), "--name", "stock_watch"]) == 0
    assert run_projects_cli(["list"]) == 0
    assert run_projects_cli(["status", "stock_watch"]) == 0
    assert run_projects_cli(["disable", "stock_watch"]) == 0
    assert run_projects_cli(["enable", "stock_watch"]) == 0
    assert run_projects_cli(["unregister", "stock_watch"]) == 0
    # 二次 unregister 应该失败（返回非 0），而不是静默成功
    assert run_projects_cli(["unregister", "stock_watch"]) != 0


def test_cli_run_triggers_entrypoint(tmp_path, monkeypatch):
    from mini_agent.cli.commands.projects_cmd import run_projects_cli
    from mini_agent.external_projects import registry as registry_mod

    monkeypatch.setattr(registry_mod, "DEFAULT_REGISTRY_PATH", tmp_path / "registry.json")
    root = tmp_path / "proj"
    root.mkdir()
    manifest_yaml = f"""
name: proj
entrypoints:
  touch:
    cmd: "{sys.executable} -c \\"open('out.txt','w').write('ran')\\""
"""
    (root / "project.yaml").write_text(manifest_yaml, encoding="utf-8")

    assert run_projects_cli(["register", str(root)]) == 0
    assert run_projects_cli(["run", "proj", "touch"]) == 0
    assert (root / "out.txt").read_text(encoding="utf-8") == "ran"
