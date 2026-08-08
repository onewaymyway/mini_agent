"""
tests/test_wiki_quarantine.py — wiki/quarantine.py + wiki/quarantine_repair.py 单元测试

背景：这套"发现解析失败页面 -> 记录到隔离区 -> cron 定时自动修复"的机制
在代码里已经存在（`wiki/quarantine.py` 负责发现/记录，
`wiki/quarantine_repair.py` 负责修复策略与 `sys:wiki_quarantine_repair`
cron job），但此前没有对应的测试文件——本文件补上这块空白，并且专门用
真实故障场景（`frontmatter.links` 写成裸字符串列表，如
`links: [tushare]` 而不是 `links: [{target: tushare}]`）作为端到端用例，
验证"发现 -> 记录 -> 自动修复 -> 自愈确认摘除"整条链路真的能跑通。
"""

from __future__ import annotations

import sys
import time

sys.path.insert(0, "src")

import pytest

from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki import quarantine as qz
from mini_agent.wiki import quarantine_repair as qzr
from mini_agent.wiki.parser import PageParseError, parse_page
from mini_agent.wiki.writer import write_page


@pytest.fixture()
def paths(tmp_path):
    p = AgentPaths(project_root=tmp_path)
    p.ensure_wiki_dirs()
    return p


def _write_bare_string_links_page(paths: AgentPaths, page_id: str = "role-data-fetcher") -> "Path":  # noqa: F821
    """复现真实故障：frontmatter.links 是裸字符串列表（`- tushare`），
    而不是规范的 `{target: ...}` 字典列表。"""
    text = (
        "---\n"
        f"id: {page_id}\n"
        "type: entity\n"
        "tags: [data-source]\n"
        "status: active\n"
        "confidence: 0.6\n"
        "created: 2026-08-01\n"
        "updated: 2026-08-01\n"
        "links:\n"
        "  - tushare\n"
        "---\n\n"
        "负责从 tushare 拉取行情数据。\n"
    )
    md_path = paths.wiki_entities_dir / f"{page_id}.md"
    md_path.write_text(text, encoding="utf-8")
    return md_path


# ────────────────────────── quarantine.py：发现 + 记录 ──────────────────────────


class TestRecordIssue:
    def test_record_issue_creates_new_record(self, paths):
        md_path = _write_bare_string_links_page(paths)
        try:
            parse_page(md_path)
            self.fail("预期应该抛出 PageParseError")  # 不会走到这里
        except PageParseError as exc:
            rec = qz.record_issue(paths, md_path, exc)

        assert rec.status == qz.STATUS_PENDING
        assert rec.error_type == "PageParseError"
        assert "target" in rec.error_message
        assert rec.detect_count == 1

        loaded = qz.load_quarantine(paths)
        assert str(md_path) in loaded

    def test_record_issue_merges_repeat_detection_instead_of_duplicating(self, paths):
        md_path = _write_bare_string_links_page(paths)
        try:
            parse_page(md_path)
        except PageParseError as exc:
            qz.record_issue(paths, md_path, exc)
        try:
            parse_page(md_path)
        except PageParseError as exc:
            rec2 = qz.record_issue(paths, md_path, exc)

        assert rec2.detect_count == 2
        assert len(qz.load_quarantine(paths)) == 1

    def test_record_issue_reopens_a_previously_repaired_record(self, paths):
        """页面之前被标记 repaired，现在又解析失败了（比如被重新编辑坏了）——
        应该重新置为 pending，且修复尝试计数清零，而不是继续沿用旧计数。"""
        md_path = _write_bare_string_links_page(paths)
        try:
            parse_page(md_path)
        except PageParseError as exc:
            qz.record_issue(paths, md_path, exc)

        records = qz.load_quarantine(paths)
        rec = records[str(md_path)]
        rec.status = qz.STATUS_REPAIRED
        rec.repair_attempts = 3
        records[str(md_path)] = rec
        qz.save_quarantine(paths, records)

        try:
            parse_page(md_path)
        except PageParseError as exc:
            reopened = qz.record_issue(paths, md_path, exc)

        assert reopened.status == qz.STATUS_PENDING
        assert reopened.repair_attempts == 0


class TestResolveIfPresent:
    def test_resolve_if_present_marks_repaired(self, paths):
        md_path = _write_bare_string_links_page(paths)
        try:
            parse_page(md_path)
        except PageParseError as exc:
            qz.record_issue(paths, md_path, exc)

        assert qz.resolve_if_present(paths, md_path) is True
        rec = qz.load_quarantine(paths)[str(md_path)]
        assert rec.status == qz.STATUS_REPAIRED
        assert rec.repaired_at is not None

    def test_resolve_if_present_is_noop_when_no_record(self, paths):
        md_path = paths.wiki_entities_dir / "not-tracked.md"
        assert qz.resolve_if_present(paths, md_path) is False


class TestScanAndRecord:
    def test_scan_finds_new_broken_page(self, paths):
        write_page(paths, page_id="ok-page", page_type="entity", body="正常页面。", tags=[])
        _write_bare_string_links_page(paths)

        report = qz.scan_and_record(paths)
        assert report.scanned == 2
        assert report.newly_quarantined == 1
        assert len(qz.load_quarantine(paths)) == 1

    def test_scan_auto_resolves_manually_fixed_page(self, paths):
        md_path = _write_bare_string_links_page(paths)
        qz.scan_and_record(paths)
        assert qz.load_quarantine(paths)[str(md_path)].status == qz.STATUS_PENDING

        # 模拟人工手动把裸字符串改成规范的 {target: ...} 字典
        fixed_text = md_path.read_text(encoding="utf-8").replace(
            "links:\n  - tushare\n", "links:\n  - target: tushare\n"
        )
        md_path.write_text(fixed_text, encoding="utf-8")

        report = qz.scan_and_record(paths)
        assert report.auto_resolved == 1
        assert qz.load_quarantine(paths)[str(md_path)].status == qz.STATUS_REPAIRED


# ────────────────────────── quarantine_repair.py：自动修复策略 ──────────────────────────


class TestAttemptRepairPage:
    def test_fixes_bare_string_links_the_exact_tushare_case(self, paths):
        """直接对应用户报的故障：links[0] 缺少 target 字段: 'tushare'。"""
        md_path = _write_bare_string_links_page(paths)

        outcome = qzr.attempt_repair_page(md_path)
        assert outcome.fixed is True
        assert "fix_string_links" in outcome.applied_fixers

        page = parse_page(md_path)  # 修复后应该能正常解析
        assert page.strong_links()[0].target == "tushare"
        assert page.strong_links()[0].relation == "relates_to"

    def test_fixes_links_field_not_wrapped_in_list(self, paths):
        text = (
            "---\nid: role-x\ntype: entity\ntags: []\nstatus: active\n"
            "confidence: 0.5\ncreated: 2026-08-01\nupdated: 2026-08-01\n"
            "links: tushare\n---\n\n正文。\n"
        )
        md_path = paths.wiki_entities_dir / "role-x.md"
        md_path.write_text(text, encoding="utf-8")

        outcome = qzr.attempt_repair_page(md_path)
        assert outcome.fixed is True
        page = parse_page(md_path)
        assert page.strong_links()[0].target == "tushare"

    def test_does_not_write_file_when_repair_still_fails(self, paths):
        """修复策略改完之后，如果重新解析还是失败——不该猜测性地继续改，
        也不该落盘半吊子的修复结果。"""
        text = (
            "---\nid: role-y\ntype: entity\ntags: []\nstatus: active\n"
            "confidence: 0.5\ncreated: 2026-08-01\nupdated: 2026-08-01\n"
            "links:\n  - 123\n---\n\n正文。\n"  # 数字，不是字符串也不是 dict
        )
        md_path = paths.wiki_entities_dir / "role-y.md"
        original = text
        md_path.write_text(text, encoding="utf-8")

        outcome = qzr.attempt_repair_page(md_path)
        # 数字项既不是 str 分支也不是 dict 分支，两个已注册策略都不认识，
        # 不该有任何改动/落盘。
        assert outcome.fixed is False
        assert md_path.read_text(encoding="utf-8") == original

    def test_returns_reason_when_yaml_syntax_broken(self, paths):
        text = "---\nid: role-z\n  bad indent: [unterminated\n---\n\n正文。\n"
        md_path = paths.wiki_entities_dir / "role-z.md"
        md_path.write_text(text, encoding="utf-8")

        outcome = qzr.attempt_repair_page(md_path)
        assert outcome.fixed is False
        assert "yaml_syntax_error" in outcome.reason

    def test_returns_reason_when_file_missing(self, paths):
        outcome = qzr.attempt_repair_page(paths.wiki_entities_dir / "ghost.md")
        assert outcome.fixed is False
        assert outcome.reason == "file_not_found"


# ────────────────────────── 端到端：发现 + 定时修复整轮循环 ──────────────────────────


class TestRunQuarantineRepairCycle:
    def test_full_cycle_repairs_the_tushare_case(self, paths):
        """完整复现用户报的场景：写入一个 links 是裸字符串的页面 -> 跑一轮
        cron handler 主体 -> 页面被自动改好 -> 隔离区记录状态变成
        repaired -> parse_page 不再抛异常。"""
        md_path = _write_bare_string_links_page(paths)

        report = qzr.run_quarantine_repair_cycle(paths)

        assert report.newly_quarantined == 1
        assert report.repaired == 1
        assert report.ok

        rec = qz.load_quarantine(paths)[str(md_path)]
        assert rec.status == qz.STATUS_REPAIRED
        assert rec.repaired_by == "fix_string_links"

        page = parse_page(md_path)  # 不再抛异常
        assert page.strong_links()[0].target == "tushare"

    def test_two_cycles_do_not_repeat_work_on_already_repaired_page(self, paths):
        md_path = _write_bare_string_links_page(paths)
        first = qzr.run_quarantine_repair_cycle(paths)
        assert first.repaired == 1

        second = qzr.run_quarantine_repair_cycle(paths)
        # 已经修好的页面这次扫描应该正常解析，不会再进入 repair_attempted。
        assert second.repair_attempted == 0
        assert second.newly_quarantined == 0

    def test_unrepairable_page_eventually_marked_needs_human(self, paths):
        text = (
            "---\nid: role-unfixable\ntype: entity\ntags: []\nstatus: active\n"
            "confidence: 0.5\ncreated: 2026-08-01\nupdated: 2026-08-01\n"
            "links:\n  - 123\n---\n\n正文。\n"
        )
        md_path = paths.wiki_entities_dir / "role-unfixable.md"
        md_path.write_text(text, encoding="utf-8")

        for _ in range(qz.DEFAULT_MAX_REPAIR_ATTEMPTS):
            qzr.run_quarantine_repair_cycle(paths, max_repair_attempts=qz.DEFAULT_MAX_REPAIR_ATTEMPTS)

        rec = qz.load_quarantine(paths)[str(md_path)]
        assert rec.status == qz.STATUS_NEEDS_HUMAN
        assert rec.repair_attempts >= qz.DEFAULT_MAX_REPAIR_ATTEMPTS

    def test_healthy_wiki_produces_clean_report(self, paths):
        write_page(paths, page_id="healthy", page_type="entity", body="没问题。", tags=[])
        report = qzr.run_quarantine_repair_cycle(paths)
        assert report.scanned == 1
        assert report.newly_quarantined == 0
        assert report.repaired == 0
        assert report.ok


# ────────────────────────── daemon 接线：cron job 注册 ──────────────────────────


class _FakeCronJob:
    def __init__(self, job_id: str):
        self.id = job_id


class _FakeCronScheduler:
    """`ensure_wiki_quarantine_repair_job` 只依赖 `list_jobs()` /
    `ensure_job()` / `register_local_handler()` 三个方法，这里给一个最小
    的假实现，不需要拉起真正的 CronScheduler。"""

    def __init__(self, existing_ids=()):
        self._jobs = [_FakeCronJob(i) for i in existing_ids]
        self.ensured = []
        self.handlers = {}

    def list_jobs(self):
        return self._jobs

    def ensure_job(self, *, job_id, **kwargs):
        self.ensured.append(job_id)
        self._jobs.append(_FakeCronJob(job_id))

    def register_local_handler(self, job_id, handler):
        self.handlers[job_id] = handler


class TestEnsureWikiQuarantineRepairJob:
    def test_registers_job_and_handler_when_missing(self, paths):
        scheduler = _FakeCronScheduler(existing_ids=[])
        newly_added = qzr.ensure_wiki_quarantine_repair_job(paths, scheduler)

        assert newly_added is True
        assert qzr.JOB_ID in scheduler.ensured
        assert qzr.JOB_ID in scheduler.handlers

    def test_does_not_report_newly_added_when_already_registered(self, paths):
        scheduler = _FakeCronScheduler(existing_ids=[qzr.JOB_ID])
        newly_added = qzr.ensure_wiki_quarantine_repair_job(paths, scheduler)

        assert newly_added is False
        # 即使已经注册过，也仍然应该（重新）挂上 handler——daemon 每次
        # 启动都是新进程，内存里的 handler 注册表是空的，必须重新挂。
        assert qzr.JOB_ID in scheduler.handlers

    def test_handler_runs_a_full_repair_cycle(self, paths):
        _write_bare_string_links_page(paths)
        scheduler = _FakeCronScheduler(existing_ids=[])
        qzr.ensure_wiki_quarantine_repair_job(paths, scheduler)

        handler = scheduler.handlers[qzr.JOB_ID]
        ok = handler(_FakeCronJob(qzr.JOB_ID))

        assert ok is True
        rec = qz.load_quarantine(paths)[str(paths.wiki_entities_dir / "role-data-fetcher.md")]
        assert rec.status == qz.STATUS_REPAIRED


if __name__ == "__main__":
    import unittest
    unittest.main()
