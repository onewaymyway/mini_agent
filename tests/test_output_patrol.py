"""P4.5 / P5 巡检测试：数据采集、告警评估、报告、只读保证。

对应 next_doc/management_output_project_model_plan.md 第 8.6 节与第 9 章 P4.5、P5。

保证：
  1. collect 模式只采集，不产生任何告警，只写 metrics.jsonl；
  2. report 模式按阈值生成 findings，并标注阈值为未校准；
  3. 巡检前后，产出项目目录、_registry/、goals.json 逐文件哈希不变（只读）；
  4. 巡检只写入 .agent/output_registry/ 下；
  5. 各类 finding（停滞、版本缺口、孤儿、断链、游离、缺介绍）的判定正确。
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mini_agent.output_registry import link_management_output, mark_management_root, register_project
from mini_agent.output_registry.lifecycle import create_output_project
from mini_agent.output_registry.patrol import (
    UNCALIBRATED_THRESHOLDS,
    collect_metrics,
    evaluate,
    run_collect,
    run_report,
)
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)


def _tree_hash(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not root.exists():
        return out
    for dirpath, _dn, fns in os.walk(root):
        for fn in fns:
            full = os.path.join(dirpath, fn)
            out[os.path.relpath(full, root)] = hashlib.sha256(Path(full).read_bytes()).hexdigest()
    return out


def _age(path: Path, days: float) -> None:
    t = (NOW - timedelta(days=days)).timestamp()
    os.utime(path, (t, t))


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    (p / "output_projects").mkdir(parents=True)
    # 旧式项目：无介绍文件，文件很久没动，游离
    old = p / "output_projects" / "old_scratch"
    (old / "output").mkdir(parents=True)
    f = old / "output" / "a.md"
    f.write_text("旧内容", encoding="utf-8")
    _age(f, 200)
    _age(old, 200)
    register_project(p, "old_scratch")
    # 受管新项目：刚刚创建
    create_output_project(p, "novel", title="长篇")
    return p


# ── 1. 采集 ───────────────────────────────────────────────────────────────────

def test_collect_metrics_fields(proj: Path):
    rows = {r["slug"]: r for r in collect_metrics(proj, NOW)}
    assert set(rows) == {"old_scratch", "novel"}
    assert rows["old_scratch"]["has_project_info"] is False
    assert rows["old_scratch"]["days_since_update"] > 190
    assert rows["old_scratch"]["status"] == "floating"
    assert rows["novel"]["managed"] is True
    assert rows["novel"]["has_project_info"] is True


def test_collect_mode_writes_metrics_and_no_alerts(proj: Path):
    result = run_collect(proj, NOW)
    assert result["alerts"] == 0
    assert result["projects"] == 2
    metrics = Path(result["written_to"])
    lines = [json.loads(l) for l in metrics.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 2
    assert not (proj / ".agent" / "output_registry" / "patrol").exists(), "collect 模式不生成报告"


def test_collect_appends_across_runs(proj: Path):
    run_collect(proj, NOW)
    run_collect(proj, NOW + timedelta(days=1))
    lines = (proj / ".agent" / "output_registry" / "metrics.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4


def test_collect_without_output_root_returns_empty(tmp_path: Path):
    assert collect_metrics(tmp_path, NOW) == []


# ── 2. 报告 ───────────────────────────────────────────────────────────────────

def test_report_flags_stale_missing_intro_and_floating(proj: Path):
    report = run_report(proj, now=NOW)
    kinds = {(f["slug"], f["kind"]) for f in report["findings"]}
    assert ("old_scratch", "stale") in kinds
    assert ("old_scratch", "missing_intro") in kinds
    assert ("old_scratch", "floating") in kinds
    assert ("novel", "stale") not in kinds


def test_report_is_marked_uncalibrated(proj: Path):
    report = run_report(proj, now=NOW)
    assert report["thresholds"]["calibration"] == "uncalibrated"
    assert report["thresholds"]["stale_days"] == UNCALIBRATED_THRESHOLDS["stale_days"]


def test_report_written_to_patrol_dir_and_latest(proj: Path):
    run_report(proj, now=NOW)
    patrol = proj / ".agent" / "output_registry" / "patrol"
    assert (patrol / "latest.json").is_file()
    assert len(list(patrol.glob("*.json"))) == 2  # 时间戳文件 + latest.json


def test_thresholds_can_be_overridden(proj: Path):
    report = run_report(proj, thresholds={"stale_days": 500}, now=NOW)
    kinds = {f["kind"] for f in report["findings"]}
    assert "stale" not in kinds


def test_orphan_detected_after_goal_completes(proj: Path):
    paths = AgentPaths(project_root=proj)
    b = GoalBacklog(paths)
    top = b.get_root_node()
    domain = b.add_node("domain", "工作", parent_id=top.id)
    goal = b.add_node("goal", "写小说", parent_id=domain.id)
    mark_management_root(proj, goal.id, "写小说")
    link_management_output(proj, goal.id, "novel", "build")
    b.set_status(goal.id, "completed", actor="test", reason="done")

    report = run_report(proj, now=NOW)
    assert ("novel", "orphan") in {(f["slug"], f["kind"]) for f in report["findings"]}


# ── 3. 评估规则（直接测试，不依赖文件系统） ───────────────────────────────────

def _row(**kw) -> dict:
    base = {
        "slug": "p", "has_project_info": True, "days_since_update": 1.0,
        "latest_version": None, "days_since_release": None, "unreleased_changes": False,
        "status": "active", "owner_goal_id": None, "owner_goal_status": None,
        "broken_link_targets": [], "managed": True,
    }
    base.update(kw)
    return base


def test_version_gap_rule():
    rows = [_row(unreleased_changes=True, days_since_release=45, latest_version="1.0.0")]
    kinds = {f["kind"] for f in evaluate(rows, dict(UNCALIBRATED_THRESHOLDS))}
    assert "version_gap" in kinds


def test_no_version_gap_when_recently_released():
    rows = [_row(unreleased_changes=True, days_since_release=3, latest_version="1.0.0")]
    kinds = {f["kind"] for f in evaluate(rows, dict(UNCALIBRATED_THRESHOLDS))}
    assert "version_gap" not in kinds


def test_never_released_managed_project():
    rows = [_row(unreleased_changes=True, latest_version=None, managed=True)]
    kinds = {f["kind"] for f in evaluate(rows, dict(UNCALIBRATED_THRESHOLDS))}
    assert "never_released" in kinds


def test_broken_link_rule():
    rows = [_row(broken_link_targets=["goal_gone"])]
    findings = evaluate(rows, dict(UNCALIBRATED_THRESHOLDS))
    assert any(f["kind"] == "broken_link" and "goal_gone" in f["detail"] for f in findings)


def test_orphan_rule_for_terminal_statuses():
    for status in ("completed", "abandoned", "cancelled"):
        kinds = {f["kind"] for f in evaluate([_row(owner_goal_status=status)], dict(UNCALIBRATED_THRESHOLDS))}
        assert "orphan" in kinds, status


def test_findings_are_suggestions_only():
    findings = evaluate([_row(status="floating", has_project_info=False, days_since_update=999)],
                        dict(UNCALIBRATED_THRESHOLDS))
    assert findings and all("suggestion" in f and f["suggestion"] for f in findings)


# ── 4. 只读保证 ───────────────────────────────────────────────────────────────

def test_patrol_does_not_modify_projects_registry_or_goals(proj: Path):
    paths = AgentPaths(project_root=proj)
    b = GoalBacklog(paths)
    top = b.get_root_node()
    domain = b.add_node("domain", "工作", parent_id=top.id)
    goal = b.add_node("goal", "目标", parent_id=domain.id)
    mark_management_root(proj, goal.id, "目标")
    link_management_output(proj, goal.id, "novel", "build")

    out_before = _tree_hash(proj / "output_projects")
    goals_before = (proj / ".agent" / "goals.json").read_bytes()

    run_collect(proj, NOW)
    run_report(proj, now=NOW)

    assert _tree_hash(proj / "output_projects") == out_before, "巡检不得修改产出项目目录或注册表"
    assert (proj / ".agent" / "goals.json").read_bytes() == goals_before
