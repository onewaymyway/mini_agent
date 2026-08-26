"""
tests/test_external_projects_backlog.py — 改进积压账本验收测试

对应 next_doc/stock_watch_continuous_improvement_plan.md 阶段 1。
不依赖任何真实外部项目/网络，只测 backlog.py 的读写/容错/状态流转，
以及 propose_maintenance_fix 新增 change_type 参数的透传行为。
"""

from __future__ import annotations

import json

import pytest

from mini_agent.external_projects.backlog import (
    BacklogError,
    append_item,
    read_backlog,
    update_status,
)


def test_append_and_read_roundtrip(tmp_path):
    item = append_item(
        tmp_path, source="user_feedback", summary="候选池报告漏了明显的热点股",
        evidence_ref="reports/candidate_pool/20260826.md",
    )
    assert item.status == "open"
    assert item.id

    items = read_backlog(tmp_path)
    assert len(items) == 1
    assert items[0].id == item.id
    assert items[0].summary == "候选池报告漏了明显的热点股"


def test_append_rejects_invalid_source(tmp_path):
    with pytest.raises(BacklogError):
        append_item(tmp_path, source="not_a_real_source", summary="x")


def test_append_rejects_empty_summary(tmp_path):
    with pytest.raises(BacklogError):
        append_item(tmp_path, source="user_feedback", summary="   ")


def test_read_backlog_missing_file_returns_empty(tmp_path):
    assert read_backlog(tmp_path) == []


def test_read_backlog_skips_corrupted_lines(tmp_path):
    path = tmp_path / ".agent" / "improvement_backlog.jsonl"
    path.parent.mkdir(parents=True)
    good = {
        "id": "abc123", "source": "outcome_review", "summary": "ok",
        "evidence_ref": None, "status": "open", "opened_at": "2026-01-01T00:00:00+00:00",
        "resolved_at": None,
    }
    path.write_text(
        json.dumps(good, ensure_ascii=False) + "\n" + "{not valid json\n" + "\n",
        encoding="utf-8",
    )
    items = read_backlog(tmp_path)
    assert len(items) == 1
    assert items[0].id == "abc123"


def test_read_backlog_filters_by_status(tmp_path):
    a = append_item(tmp_path, source="health_trend", summary="a")
    append_item(tmp_path, source="health_trend", summary="b")
    update_status(tmp_path, a.id, "dismissed")

    open_items = read_backlog(tmp_path, status="open")
    dismissed_items = read_backlog(tmp_path, status="dismissed")
    assert {i.summary for i in open_items} == {"b"}
    assert {i.summary for i in dismissed_items} == {"a"}


def test_update_status_sets_resolved_at_only_on_terminal_states(tmp_path):
    item = append_item(tmp_path, source="user_feedback", summary="x")

    proposed = update_status(tmp_path, item.id, "proposed")
    assert proposed.status == "proposed"
    assert proposed.resolved_at is None

    landed = update_status(tmp_path, item.id, "landed")
    assert landed.status == "landed"
    assert landed.resolved_at is not None


def test_update_status_unknown_id_raises(tmp_path):
    with pytest.raises(BacklogError):
        update_status(tmp_path, "does-not-exist", "landed")


def test_update_status_invalid_status_raises(tmp_path):
    item = append_item(tmp_path, source="user_feedback", summary="x")
    with pytest.raises(BacklogError):
        update_status(tmp_path, item.id, "not_a_real_status")


def test_workspace_backlog_path(tmp_path):
    from mini_agent.workspace import Workspace

    ws = Workspace(root=tmp_path)
    assert ws.backlog_path == tmp_path / ".agent" / "improvement_backlog.jsonl"
    assert ws.backlog_path != ws.run_status_path


def test_propose_maintenance_fix_change_type_defaults_to_fix(tmp_path):
    """change_type 是纯附加字段：不传时行为与阶段 5 完成时完全一致。"""
    import subprocess

    from mini_agent.external_projects.maintenance import propose_maintenance_fix

    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)

    result = propose_maintenance_fix(
        root, {"a.txt": "hello\n"}, "add a.txt", slug="t", tier="T0",
    )
    assert result.ok
    assert result.change_type == "fix"


def test_propose_maintenance_fix_change_type_enhancement_is_tagged(tmp_path):
    import subprocess

    from mini_agent.external_projects.maintenance import propose_maintenance_fix

    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)

    result = propose_maintenance_fix(
        root, {"a.txt": "hello\n"}, "tune scoring weights",
        slug="t", tier="T0", change_type="enhancement",
    )
    assert result.ok
    assert result.change_type == "enhancement"


def test_propose_maintenance_fix_rejects_bad_change_type(tmp_path):
    import subprocess

    from mini_agent.external_projects.maintenance import MaintenanceError, propose_maintenance_fix

    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)

    with pytest.raises(MaintenanceError):
        propose_maintenance_fix(
            root, {"a.txt": "hi\n"}, "msg", slug="t", change_type="not-a-real-type",
        )
