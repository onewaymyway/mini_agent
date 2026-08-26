"""
tests/test_external_projects_review.py — 阶段 4（review session 任务
模板构建）验收测试

对应 next_doc/stock_watch_continuous_improvement_plan.md 阶段 4。全部
离线：不发起真实 agent 会话，只测 `manifest.py` 的 `review:` 字段解析
和 `review.py` 的材料收集/文本拼装是否正确。
"""

from __future__ import annotations

import sys

import pytest

from mini_agent.external_projects.backlog import append_item
from mini_agent.external_projects.ledger import record_run
from mini_agent.external_projects.manifest import (
    ProjectManifestError,
    ReviewSpec,
    parse_manifest,
)
from mini_agent.external_projects.review import (
    REVIEW_SESSION_TOOLS,
    build_review_task_template,
    build_review_task_template_for,
    cadence_to_cron,
    gather_review_briefing,
)

VALID_YAML = """
name: {name}
entrypoints:
  work:
    cmd: "{python} -c \\"pass\\""
"""


def test_manifest_default_review_disabled():
    manifest = parse_manifest(VALID_YAML.format(name="p", python=sys.executable))
    assert manifest.review == ReviewSpec(cadence="weekly", enabled=False)


def test_manifest_parses_review_block():
    text = VALID_YAML.format(name="p", python=sys.executable) + (
        "review:\n  cadence: monthly\n  enabled: true\n"
    )
    manifest = parse_manifest(text)
    assert manifest.review.cadence == "monthly"
    assert manifest.review.enabled is True


def test_manifest_rejects_bad_review_block():
    text = VALID_YAML.format(name="p", python=sys.executable) + "review:\n  enabled: \"yes\"\n"
    with pytest.raises(ProjectManifestError):
        parse_manifest(text)


def test_gather_review_briefing_reads_ledger_and_backlog(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "project.yaml").write_text(
        VALID_YAML.format(name="proj", python=sys.executable)
        + "review:\n  cadence: weekly\n  enabled: true\n",
        encoding="utf-8",
    )

    record_run(root, "work", 0, "manual")
    record_run(root, "work", 1, "manual", error_summary="boom")
    append_item(root, source="user_feedback", summary="报告漏了热点股")

    from mini_agent.external_projects.manifest import load_manifest

    manifest = load_manifest(root)
    briefing = gather_review_briefing(manifest)

    assert briefing.project_name == "proj"
    assert len(briefing.recent_runs) == 2
    assert len(briefing.open_backlog) == 1
    assert briefing.cadence == "weekly"


def test_gather_review_briefing_requires_source_dir():
    manifest = parse_manifest(VALID_YAML.format(name="p", python=sys.executable))
    with pytest.raises(ValueError):
        gather_review_briefing(manifest)


def test_build_review_task_template_mentions_project_and_backlog_items():
    from mini_agent.external_projects.backlog import BacklogItem
    from mini_agent.external_projects.review import ReviewBriefing

    briefing = ReviewBriefing(
        project_name="stock_watch",
        recent_runs=[],
        open_backlog=[
            BacklogItem(
                id="a1", source="outcome_review", summary="打分逻辑可能高估某来源",
                evidence_ref=None, status="open", opened_at="2026-01-01T00:00:00+00:00",
            )
        ],
        cadence="weekly",
    )
    text = build_review_task_template(briefing)
    assert "stock_watch" in text
    assert "打分逻辑可能高估某来源" in text
    assert "propose_fix" in text
    assert "change_type=" in text
    assert "enhancement" in text


def test_build_review_task_template_handles_empty_backlog_and_runs():
    from mini_agent.external_projects.review import ReviewBriefing

    briefing = ReviewBriefing(
        project_name="p", recent_runs=[], open_backlog=[], cadence="daily",
    )
    text = build_review_task_template(briefing)
    assert "（暂无执行记录）" in text
    assert "（暂无）" in text


def test_build_review_task_template_for_end_to_end(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "project.yaml").write_text(
        VALID_YAML.format(name="proj", python=sys.executable), encoding="utf-8",
    )
    append_item(root, source="health_trend", summary="问财周一早高峰经常429")

    from mini_agent.external_projects.manifest import load_manifest

    manifest = load_manifest(root)
    text = build_review_task_template_for(manifest)
    assert "proj" in text
    assert "问财周一早高峰经常429" in text


def test_cadence_to_cron_known_and_unknown():
    assert cadence_to_cron("weekly") == "0 9 * * 1"
    assert cadence_to_cron("Weekly") == "0 9 * * 1"  # 大小写不敏感
    assert cadence_to_cron("daily") == "0 9 * * *"
    assert cadence_to_cron("monthly") == "0 9 1 * *"
    assert cadence_to_cron("fortnightly") is None


def test_review_session_tools_excludes_landing_and_write_tools():
    """review session 只应该拿到只读探查/写待办/生成提案分支的工具，
    不应该拿到任何能直接落地到目标项目主分支的能力（呼应第5节
    "enhancement 类改动的落地永远保留给人工"）。"""
    assert "propose_fix" in REVIEW_SESSION_TOOLS
    assert "trigger_run" not in REVIEW_SESSION_TOOLS
    assert not any("land" in t for t in REVIEW_SESSION_TOOLS)
