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
    EntrypointParamError,
    ProjectManifestError,
    build_cmd_with_params,
    load_manifest,
    parse_manifest,
)
from mini_agent.external_projects.registry import (
    ExternalProjectRegistry,
    ExternalProjectRegistryError,
)
from mini_agent.external_projects.scheduler import (
    cron_matches,
    ensure_external_project_cron_jobs,
    run_due_entrypoints,
    trigger_run,
)

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

    # external_projects_cron_dispatch_plan.md 待确认问题 2：新注册项目
    # 默认 enabled=False（opt-in），需要显式打开。
    record = registry.register("stock_watch", project_dir)
    assert record.enabled is False
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


def test_registry_from_dict_defaults_enabled_false_when_key_missing(tmp_path):
    """[2026-08-31 回归测试] `enabled` 字段是后加进 `RegisteredProject`
    的，旧版本写盘的记录可能没有这个 key。`from_dict()` 缺 key 时的
    回退默认值必须跟 `register()`/dataclass 字段默认值保持一致
    （`False` = 未显式开启就是关闭），否则会出现"用户在看板上关掉了
    某个项目的自动调度，daemon 重启后又自动重新打开"的问题——根因是
    旧记录反序列化时被错误地当成 `enabled=True`，daemon 启动时对所有
    已注册项目跑 `ensure_external_project_cron_jobs()` 会据此把
    `ext:*` cron job 重新建出来。"""
    import json

    store = tmp_path / "registry.json"
    # 手工构造一条"缺 enabled key"的记录，模拟老版本写盘的历史数据。
    store.write_text(
        json.dumps({
            "projects": {
                "legacy_project": {
                    "name": "legacy_project",
                    "path": str(tmp_path / "legacy_project"),
                    "main_project_root": str(tmp_path),
                    "registered_at": "2025-01-01T00:00:00+00:00",
                    # 有意不写 "enabled" key
                }
            }
        }),
        encoding="utf-8",
    )
    registry = ExternalProjectRegistry(store_path=store)
    record = registry.get("legacy_project")
    assert record.enabled is False
    assert registry.list(enabled_only=True) == []


def test_registry_migrates_from_legacy_mini_agent_path(tmp_path, monkeypatch):
    """[2026-08-31] 默认存储路径从 `~/.mini_agent/external_projects.json`
    改为 `~/.agent/external_projects.json`；旧路径存在且新路径不存在时，
    首次读取应自动一次性迁移，旧文件保留不删。"""
    import json

    from mini_agent.external_projects import registry as registry_module

    legacy_path = tmp_path / ".mini_agent" / "external_projects.json"
    new_path = tmp_path / ".agent" / "external_projects.json"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_payload = {
        "projects": {
            "old_project": {
                "name": "old_project",
                "path": str(tmp_path / "old_project"),
                "main_project_root": str(tmp_path),
                "enabled": True,
                "registered_at": "2025-01-01T00:00:00+00:00",
            }
        }
    }
    legacy_path.write_text(json.dumps(legacy_payload), encoding="utf-8")

    monkeypatch.setattr(registry_module, "DEFAULT_REGISTRY_PATH", new_path)
    monkeypatch.setattr(registry_module, "_LEGACY_REGISTRY_PATH", legacy_path)

    registry = ExternalProjectRegistry()  # 用默认路径，触发迁移逻辑
    assert registry.store_path == new_path
    record = registry.get("old_project")
    assert record.enabled is True
    assert new_path.exists()
    assert legacy_path.exists()  # 旧文件不删除


def test_registry_no_migration_when_custom_store_path(tmp_path, monkeypatch):
    """自定义 store_path（比如测试/隔离场景）不应触发旧路径迁移。"""
    import json

    from mini_agent.external_projects import registry as registry_module

    legacy_path = tmp_path / ".mini_agent" / "external_projects.json"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(json.dumps({"projects": {}}), encoding="utf-8")
    monkeypatch.setattr(registry_module, "_LEGACY_REGISTRY_PATH", legacy_path)

    custom_path = tmp_path / "custom_registry.json"
    registry = ExternalProjectRegistry(store_path=custom_path)
    assert registry.list() == []
    assert not custom_path.exists()  # 没写过任何内容，迁移也没有被触发


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


# ── entrypoint params（external_projects_kanban_integration_plan.md 阶段6）──


PARAMS_YAML = """
name: proj
entrypoints:
  analyze:
    cmd: "python run.py"
    params:
      - name: code
        required: true
        help: "股票代码"
      - name: label
        required: false
        default: "unnamed"
"""


def test_parse_manifest_params_happy_path():
    manifest = parse_manifest(PARAMS_YAML)
    ep = manifest.entrypoints["analyze"]
    assert [p.name for p in ep.params] == ["code", "label"]
    assert ep.params[0].required is True
    assert ep.params[1].required is False
    assert ep.params[1].default == "unnamed"


@pytest.mark.parametrize(
    "bad_yaml",
    [
        "name: p\nentrypoints:\n  a:\n    cmd: x\n    params: notalist\n",
        "name: p\nentrypoints:\n  a:\n    cmd: x\n    params:\n      - required: true\n",  # 缺 name
        "name: p\nentrypoints:\n  a:\n    cmd: x\n    params:\n      - name: c\n        required: notabool\n",
        "name: p\nentrypoints:\n  a:\n    cmd: x\n    params:\n      - name: c\n      - name: c\n",  # 重复
    ],
)
def test_parse_manifest_params_rejects_invalid(bad_yaml):
    with pytest.raises(ProjectManifestError):
        parse_manifest(bad_yaml)


def test_build_cmd_with_params_appends_in_order_and_quotes():
    manifest = parse_manifest(PARAMS_YAML)
    ep = manifest.entrypoints["analyze"]
    assert build_cmd_with_params(ep, {"code": "600519"}) == "python run.py 600519 unnamed"
    assert (
        build_cmd_with_params(ep, {"code": "600519", "label": "a b"})
        == "python run.py 600519 'a b'"
    )


def test_build_cmd_with_params_no_params_ignores_values():
    manifest = parse_manifest(VALID_YAML)
    ep = manifest.entrypoints["hotlist_scan"]
    assert build_cmd_with_params(ep, {"anything": "x"}) == ep.cmd
    assert build_cmd_with_params(ep, None) == ep.cmd


def test_build_cmd_with_params_missing_required_raises():
    manifest = parse_manifest(PARAMS_YAML)
    ep = manifest.entrypoints["analyze"]
    with pytest.raises(EntrypointParamError):
        build_cmd_with_params(ep, {})


def test_build_cmd_with_params_unknown_param_raises():
    manifest = parse_manifest(PARAMS_YAML)
    ep = manifest.entrypoints["analyze"]
    with pytest.raises(EntrypointParamError):
        build_cmd_with_params(ep, {"code": "600519", "bogus": "x"})


def test_trigger_run_passes_params_to_cmd(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    manifest_yaml = f"""
name: proj
entrypoints:
  echo_code:
    cmd: "{sys.executable} -c \\"import sys; open('out.txt','w').write(sys.argv[1])\\""
    params:
      - name: code
        required: true
"""
    (root / "project.yaml").write_text(manifest_yaml, encoding="utf-8")
    registry = ExternalProjectRegistry(store_path=tmp_path / "registry.json")
    registry.register("proj", root)

    result = trigger_run(registry, "proj", "echo_code", params={"code": "600519"})
    assert result.returncode == 0
    assert (root / "out.txt").read_text(encoding="utf-8") == "600519"


def test_trigger_run_missing_required_param_raises(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    manifest_yaml = """
name: proj
entrypoints:
  echo_code:
    cmd: "python -c \\"pass\\""
    params:
      - name: code
        required: true
"""
    (root / "project.yaml").write_text(manifest_yaml, encoding="utf-8")
    registry = ExternalProjectRegistry(store_path=tmp_path / "registry.json")
    registry.register("proj", root)

    with pytest.raises(EntrypointParamError):
        trigger_run(registry, "proj", "echo_code", params={})


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
    # register() 默认 enabled=False（opt-in），这个测试要验证"已启用项目"
    # 的扫描逻辑，显式打开。
    registry.register("proj", root, enabled=True)

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


# ── external_projects_cron_dispatch_plan.md 3.3 ── ensure_external_project_cron_jobs ──


def _make_scheduled_project_dir(tmp_path: Path, name: str = "proj") -> Path:
    root = tmp_path / name
    root.mkdir()
    manifest_yaml = f"""
name: {name}
entrypoints:
  scan:
    cmd: "{sys.executable} -c \\"pass\\""
    schedule: "cron: 0 9 * * 1-5"
  batch:
    cmd: "{sys.executable} -c \\"pass\\""
    schedule: "cron: 0 16 * * 1-5"
  unscheduled:
    cmd: "{sys.executable} -c \\"pass\\""
"""
    (root / "project.yaml").write_text(manifest_yaml, encoding="utf-8")
    return root


def _fresh_cron_scheduler(tmp_path: Path):
    from mini_agent.evolution.cron_scheduler import CronScheduler
    from mini_agent.storage.paths import AgentPaths

    workdir = tmp_path / "agent_workdir"
    workdir.mkdir()
    paths = AgentPaths(project_root=workdir)
    cs = CronScheduler(paths)
    cs.load()
    return cs


def test_ensure_external_project_cron_jobs_registers_scheduled_entrypoints(tmp_path):
    root = _make_scheduled_project_dir(tmp_path)
    registry = ExternalProjectRegistry(store_path=tmp_path / "registry.json")
    registry.register("proj", root, enabled=True)
    cs = _fresh_cron_scheduler(tmp_path)

    ensure_external_project_cron_jobs("proj", registry, cs)

    job_ids = {j.id for j in cs.list_jobs() if j.id.startswith("ext:proj:")}
    assert job_ids == {"ext:proj:scan", "ext:proj:batch"}
    scan_job = cs.get("ext:proj:scan")
    assert scan_job.run_mode == "external_entrypoint"
    assert scan_job.external_project == "proj"
    assert scan_job.external_entrypoint == "scan"
    assert scan_job.schedule == "cron: 0 9 * * 1-5"


def test_ensure_external_project_cron_jobs_disabled_project_clears_jobs(tmp_path):
    root = _make_scheduled_project_dir(tmp_path)
    registry = ExternalProjectRegistry(store_path=tmp_path / "registry.json")
    registry.register("proj", root, enabled=True)
    cs = _fresh_cron_scheduler(tmp_path)
    ensure_external_project_cron_jobs("proj", registry, cs)
    assert len([j for j in cs.list_jobs() if j.id.startswith("ext:proj:")]) == 2

    registry.set_enabled("proj", False)
    ensure_external_project_cron_jobs("proj", registry, cs)

    assert [j for j in cs.list_jobs() if j.id.startswith("ext:proj:")] == []


def test_ensure_external_project_cron_jobs_realigns_after_manifest_change(tmp_path):
    root = _make_scheduled_project_dir(tmp_path)
    registry = ExternalProjectRegistry(store_path=tmp_path / "registry.json")
    registry.register("proj", root, enabled=True)
    cs = _fresh_cron_scheduler(tmp_path)
    ensure_external_project_cron_jobs("proj", registry, cs)

    # 改写 project.yaml：删掉 batch，改 scan 的 schedule
    manifest_yaml = f"""
name: proj
entrypoints:
  scan:
    cmd: "{sys.executable} -c \\"pass\\""
    schedule: "cron: 30 10 * * 1-5"
"""
    (root / "project.yaml").write_text(manifest_yaml, encoding="utf-8")

    ensure_external_project_cron_jobs("proj", registry, cs)

    job_ids = {j.id for j in cs.list_jobs() if j.id.startswith("ext:proj:")}
    assert job_ids == {"ext:proj:scan"}
    assert cs.get("ext:proj:scan").schedule == "cron: 30 10 * * 1-5"
