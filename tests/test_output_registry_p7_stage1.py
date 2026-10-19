"""P7 阶段一测试：两档接入（只登记 / 受管）、配置化阈值（默认 3 天）、巡检通知去重。

对应 next_doc/management_output_project_model_impl_p7.md。

保证：
  1. "只登记"档：目录内不产生任何文件，项目 managed=False，无 PROJECT.json；
  2. "只登记"可升级为"受管"：升级写入 PROJECT.json，原有文件逐字节不变；
  3. 默认阈值为 3 天；agent_config.json 的 output_registry 块可覆盖；
  4. 巡检通知只针对新出现的建议推送，重复的不再推送；notify_enabled=False 时不推送；
  5. 巡检仍不修改产出项目目录、_registry/ 与 goals.json。
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mini_agent.output_registry import adopt_output_project, list_unregistered
from mini_agent.output_registry.patrol import DEFAULT_STALE_DAYS, DEFAULT_VERSION_GAP_DAYS, run_report
from mini_agent.output_registry.registry import load_record, registry_dir

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)


def _tree_hash(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for dirpath, _dn, fns in os.walk(root):
        for fn in fns:
            full = os.path.join(dirpath, fn)
            out[os.path.relpath(full, root)] = hashlib.sha256(Path(full).read_bytes()).hexdigest()
    return out


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    root = p / "output_projects"
    (root / "legacy" / "output").mkdir(parents=True)
    (root / "legacy" / "output" / "a.md").write_text("旧内容", encoding="utf-8")
    (p / ".agent").mkdir()
    (p / ".agent" / "goals.json").write_text(json.dumps({"goals": []}), encoding="utf-8")
    return p


# ── 1. 两档接入 ─────────────────────────────────────────────────────────────

def test_registered_tier_writes_nothing_into_project_dir(proj: Path):
    root = proj / "output_projects" / "legacy"
    before = _tree_hash(root)
    result = adopt_output_project(proj, "legacy", confirm=True, mode="registered", intro="应被忽略")
    assert result["mode"] == "registered"
    assert result["managed"] is False
    assert result["project_json_written"] is False
    assert _tree_hash(root) == before  # 目录内一个文件都不新增


def test_registered_tier_record_is_unmanaged(proj: Path):
    adopt_output_project(proj, "legacy", confirm=True, mode="registered")
    record = load_record(proj, "legacy")
    assert record["managed"] is False
    assert any(e.get("event") == "registered" for e in record["status_history"])


def test_registered_project_no_longer_listed_as_candidate(proj: Path):
    assert [r["slug"] for r in list_unregistered(proj)] == ["legacy"]
    adopt_output_project(proj, "legacy", confirm=True, mode="registered")
    assert list_unregistered(proj) == []


def test_upgrade_registered_to_managed_writes_project_json_and_keeps_files(proj: Path):
    root = proj / "output_projects" / "legacy"
    adopt_output_project(proj, "legacy", confirm=True, mode="registered")
    before = _tree_hash(root)
    result = adopt_output_project(proj, "legacy", confirm=True, mode="managed")
    assert result["upgraded"] is True
    assert result["managed"] is True
    assert result["project_json_written"] is True
    after = _tree_hash(root)
    for rel, digest in before.items():
        assert after[rel] == digest, rel
    assert set(after) - set(before) == {"PROJECT.json"}
    assert load_record(proj, "legacy")["managed"] is True


def test_registered_again_is_idempotent_and_does_not_downgrade(proj: Path):
    adopt_output_project(proj, "legacy", confirm=True, mode="managed")
    before = _tree_hash(proj)
    result = adopt_output_project(proj, "legacy", confirm=True, mode="registered")
    assert result["already_registered"] is True
    assert result["upgraded"] is False
    assert load_record(proj, "legacy")["managed"] is True  # 不会被降级
    assert _tree_hash(proj) == before


def test_invalid_mode_rejected(proj: Path):
    from mini_agent.output_registry import RegistryError
    with pytest.raises(RegistryError):
        adopt_output_project(proj, "legacy", confirm=True, mode="whatever")


def test_registered_mode_still_requires_confirm(proj: Path):
    from mini_agent.output_registry import RegistryError
    with pytest.raises(RegistryError):
        adopt_output_project(proj, "legacy", confirm=False, mode="registered")
    assert not registry_dir(proj).exists()


# ── 2. 配置化阈值 ───────────────────────────────────────────────────────────

def test_default_thresholds_are_three_days(proj: Path):
    assert DEFAULT_STALE_DAYS == 3.0
    assert DEFAULT_VERSION_GAP_DAYS == 3.0
    report = run_report(proj, now=NOW, notify=False)
    assert report["thresholds"]["stale_days"] == 3.0
    assert report["thresholds"]["version_gap_days"] == 3.0


def test_thresholds_read_from_agent_config(proj: Path):
    (proj / "agent_config.json").write_text(
        json.dumps({"output_registry": {"stale_days": 7, "version_gap_days": 10, "notify_enabled": False}}),
        encoding="utf-8",
    )
    report = run_report(proj, now=NOW)
    assert report["thresholds"]["stale_days"] == 7.0
    assert report["thresholds"]["version_gap_days"] == 10.0


def test_explicit_threshold_overrides_config(proj: Path):
    (proj / "agent_config.json").write_text(
        json.dumps({"output_registry": {"stale_days": 7}}), encoding="utf-8"
    )
    report = run_report(proj, thresholds={"stale_days": 2}, now=NOW, notify=False)
    assert report["thresholds"]["stale_days"] == 2.0


# ── 3. 通知与去重 ───────────────────────────────────────────────────────────

def _output_patrol_reports(proj: Path) -> list[dict]:
    from mini_agent.storage.paths import AgentPaths
    path = AgentPaths(project_root=proj).notification_reports
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if r.get("source") == "output_patrol"]


def test_new_findings_are_notified_once(proj: Path):
    # legacy 目录未登记 → 产生 unregistered / floating 建议
    first = run_report(proj, now=NOW)
    assert first["new_findings_total"] > 0
    assert first["notified"] is True
    assert len(_output_patrol_reports(proj)) == 1

    # 同样的问题再巡检一次：没有新建议，不再推送
    second = run_report(proj, now=NOW + timedelta(hours=1))
    assert second["new_findings_total"] == 0
    assert second["notified"] is False
    assert len(_output_patrol_reports(proj)) == 1


def test_notify_disabled_by_config_does_not_push(proj: Path):
    (proj / "agent_config.json").write_text(
        json.dumps({"output_registry": {"notify_enabled": False}}), encoding="utf-8"
    )
    report = run_report(proj, now=NOW)
    assert report["findings_total"] > 0
    assert report["notified"] is False
    assert _output_patrol_reports(proj) == []


def test_notification_body_lists_findings_and_is_suggestion_only(proj: Path):
    run_report(proj, now=NOW)
    body = _output_patrol_reports(proj)[0]["detail"]
    assert "legacy" in body
    assert "不会自动执行" in body
