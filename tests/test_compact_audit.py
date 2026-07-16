"""
tests/test_compact_audit.py

覆盖 compact_mechanism_improvement_plan.md P2-A：
  history/compact_audit.py::audit_compact_quality           — 单次 LLM 审计调用
  agent/compaction.py::CompactionMixin._maybe_audit_compact_quality  — 触发条件门控
  agent/compaction.py::CompactionMixin._apply_compact_audit_issue    — 发现遗漏后的落地

设计上审计是"事后"、"尽力而为"的：任何一步失败都不应该抛出异常或影响主流程，
所以测试重点覆盖：
  - 开关/触发原因白名单门控是否生效
  - LLM 判断"无遗漏"和"有遗漏"两种路径
  - 有遗漏时正确追加 compact_supplement 历史条目 + 写入 activity_digest.jsonl
  - LLM 调用异常、digest 写入异常等失败路径不抛出
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mini_agent.agent.compaction import CompactionMixin
from mini_agent.history.compact_audit import (
    CompactAuditResult,
    audit_compact_quality,
)


# ════════════════════════════════════════════════════════════════════════════
# audit_compact_quality() 本体
# ════════════════════════════════════════════════════════════════════════════

def _make_llm_response(text: str):
    resp = MagicMock()
    resp.text = text
    return resp


def test_audit_no_issue_when_llm_says_no_issue():
    llm = MagicMock()
    llm.chat_with_retry.return_value = _make_llm_response("NO_ISSUE")
    history = [{"role": "user", "content": "帮我处理一下这个 bug", "_type": "user_input"}]
    result = audit_compact_quality(history, "已修复 bug", llm)
    assert result.has_issue is False


def test_audit_reports_issue_when_llm_finds_missing_info():
    llm = MagicMock()
    llm.chat_with_retry.return_value = _make_llm_response(
        "摘要遗漏了用户明确要求必须使用 PostgreSQL 而不是 MySQL 这一约束条件。"
    )
    history = [{"role": "user", "content": "必须用 PostgreSQL，不要用 MySQL", "_type": "user_input"}]
    result = audit_compact_quality(history, "已完成数据库迁移", llm)
    assert result.has_issue is True
    assert "PostgreSQL" in result.missing_info


def test_audit_returns_no_issue_on_empty_inputs():
    llm = MagicMock()
    assert audit_compact_quality([], "summary", llm).has_issue is False
    assert audit_compact_quality([{"role": "user", "content": "x"}], "", llm).has_issue is False
    assert audit_compact_quality([{"role": "user", "content": "x"}], "summary", None).has_issue is False
    llm.chat_with_retry.assert_not_called()


def test_audit_llm_exception_does_not_raise():
    llm = MagicMock()
    llm.chat_with_retry.side_effect = RuntimeError("network down")
    history = [{"role": "user", "content": "帮我处理一下", "_type": "user_input"}]
    result = audit_compact_quality(history, "summary", llm)
    assert result.has_issue is False
    assert "audit failed" in result.raw_response


def test_audit_llm_empty_response_treated_as_no_issue():
    llm = MagicMock()
    llm.chat_with_retry.return_value = _make_llm_response("   ")
    history = [{"role": "user", "content": "帮我处理一下", "_type": "user_input"}]
    result = audit_compact_quality(history, "summary", llm)
    assert result.has_issue is False


# ════════════════════════════════════════════════════════════════════════════
# CompactionMixin._maybe_audit_compact_quality — 门控逻辑
# ════════════════════════════════════════════════════════════════════════════

class _FakeCompressCfg:
    def __init__(self, **overrides):
        self.audit_enabled = False
        self.audit_compact_reasons = ["topic_shift_heuristic", "topic_shift_llm", "stuck_recovery_deep"]
        self.audit_async = False  # 测试里统一同步执行，避免线程时序不确定
        for k, v in overrides.items():
            setattr(self, k, v)


class _FakeTopCfg:
    def __init__(self, project_root=None, **compress_overrides):
        self.compress = _FakeCompressCfg(**compress_overrides)
        self.project_root = project_root


class _AuditHost(CompactionMixin):
    """最小宿主：只提供审计路径依赖的属性。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self._history = []
        self._hist = None
        self._llm = MagicMock()
        self._compact_audit_lock = threading.Lock()


def test_audit_skipped_when_disabled():
    host = _AuditHost(_FakeTopCfg(audit_enabled=False))
    host._maybe_audit_compact_quality("topic_shift_heuristic", [{"role": "user", "content": "x"}], "summary")
    host._llm.chat_with_retry.assert_not_called()


def test_audit_skipped_when_reason_not_in_whitelist():
    host = _AuditHost(_FakeTopCfg(audit_enabled=True))
    host._maybe_audit_compact_quality("turn_count", [{"role": "user", "content": "x"}], "summary")
    host._llm.chat_with_retry.assert_not_called()


def test_audit_skipped_when_reason_is_none():
    host = _AuditHost(_FakeTopCfg(audit_enabled=True))
    host._maybe_audit_compact_quality(None, [{"role": "user", "content": "x"}], "summary")
    host._llm.chat_with_retry.assert_not_called()


def test_audit_runs_for_whitelisted_reason_and_appends_supplement(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "mini_agent.history.compact_audit.audit_compact_quality",
        lambda *a, **kw: CompactAuditResult(has_issue=True, missing_info="遗漏了约束 X"),
    )
    host = _AuditHost(_FakeTopCfg(project_root=tmp_path, audit_enabled=True))
    host._maybe_audit_compact_quality(
        "topic_shift_heuristic", [{"role": "user", "content": "x", "_type": "user_input"}], "summary",
    )

    assert len(host._history) == 1
    assert host._history[0]["_type"] == "compact_supplement"
    assert "遗漏了约束 X" in host._history[0]["content"]

    digest_path = tmp_path / ".agent" / "activity_digest.jsonl"
    assert digest_path.exists()
    lines = digest_path.read_text(encoding="utf-8").strip().splitlines()
    record = json.loads(lines[-1])
    assert record["type"] == "compact_audit_issue"
    assert record["trigger_reason"] == "topic_shift_heuristic"


def test_audit_no_history_change_when_no_issue_found(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "mini_agent.history.compact_audit.audit_compact_quality",
        lambda *a, **kw: CompactAuditResult(has_issue=False),
    )
    host = _AuditHost(_FakeTopCfg(project_root=tmp_path, audit_enabled=True))
    host._maybe_audit_compact_quality(
        "topic_shift_llm", [{"role": "user", "content": "x", "_type": "user_input"}], "summary",
    )
    assert host._history == []


def test_audit_inner_exception_does_not_raise(tmp_path, monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr("mini_agent.history.compact_audit.audit_compact_quality", _boom)
    host = _AuditHost(_FakeTopCfg(project_root=tmp_path, audit_enabled=True))
    # 不应该抛出异常
    host._maybe_audit_compact_quality(
        "stuck_recovery_deep", [{"role": "user", "content": "x", "_type": "user_input"}], "summary",
    )
    assert host._history == []


def test_digest_write_failure_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "mini_agent.history.compact_audit.audit_compact_quality",
        lambda *a, **kw: CompactAuditResult(has_issue=True, missing_info="X"),
    )
    # project_root 指向一个无法创建子目录的路径（一个文件而不是目录）
    bad_root = tmp_path / "not_a_dir"
    bad_root.write_text("x")
    host = _AuditHost(_FakeTopCfg(project_root=bad_root, audit_enabled=True))
    host._maybe_audit_compact_quality(
        "topic_shift_heuristic", [{"role": "user", "content": "x", "_type": "user_input"}], "summary",
    )
    # 历史条目依然应该追加成功（两个副作用互相独立，一个失败不影响另一个）
    assert len(host._history) == 1
