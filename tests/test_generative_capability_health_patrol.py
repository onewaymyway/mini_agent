"""
tests/test_generative_capability_health_patrol.py

对应文档: next_doc/browser_site_scraper_domain_dedup_and_matching_fix_plan.md
          阶段 C。

覆盖 `health_patrol.run_patrol()` 新增的"同域名重复 member"巡检项：
  1. 默认（`merge_duplicates=False`）只报告 `duplicate_domain_pattern`
     finding，不改动任何文件。
  2. `merge_duplicates=True` 时，把同一域名下的重复 member 合并成一个
     canonical（`success_count` 最高者），其余标记为 `dead`、从
     `_index.json` 移除检索摘要，但不删除 `members/` 目录下的脚本文件。
  3. 域名不同、或带路径片段的 pattern（如 baidu 现网配置）不应被误判为
     重复。
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path


class TestDuplicateDomainPatrol(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.skill_dir = self.tmp_dir / "browser-site-scraper"
        for member_id in ("arxiv_13", "arxiv_14", "baidu"):
            (self.skill_dir / "members" / member_id).mkdir(parents=True)

        index = {
            "members": [
                {
                    "member_id": "arxiv_13",
                    "description": "探索自动生成: 抓取arxiv论文完整正文",
                    "match": {"domain_pattern": "*.arxiv.org*", "keyword": ["k1"]},
                },
                {
                    "member_id": "arxiv_14",
                    "description": "探索自动生成: 抓取arxiv论文2509.26354",
                    "match": {"domain_pattern": "*.arxiv.org*", "keyword": ["k2"]},
                },
                {
                    "member_id": "baidu",
                    "description": "百度搜索",
                    "match": {"domain_pattern": "*.baidu.com/s*", "keyword": ["baidu"]},
                },
            ]
        }
        registry = {
            "members": {
                "arxiv_13": {"status": "trusted", "success_count": 5},
                "arxiv_14": {"status": "probation", "success_count": 1},
                "baidu": {"status": "trusted", "success_count": 10},
            }
        }
        (self.skill_dir / "_index.json").write_text(json.dumps(index), encoding="utf-8")
        (self.skill_dir / "registry.json").write_text(json.dumps(registry), encoding="utf-8")

    def _run(self, **kwargs):
        from mini_agent.skills.generative_capability.health_patrol import run_patrol

        return run_patrol(self.skill_dir, **kwargs)

    def test_report_only_flags_duplicates_without_writing(self):
        before_index = (self.skill_dir / "_index.json").read_text()
        before_registry = (self.skill_dir / "registry.json").read_text()

        report = self._run()

        kinds = {f.kind for f in report.findings}
        self.assertIn("duplicate_domain_pattern", kinds)
        flagged_ids = {f.member_id for f in report.findings if f.kind == "duplicate_domain_pattern"}
        self.assertEqual(flagged_ids, {"arxiv_13", "arxiv_14"})
        self.assertEqual(report.merged_members, [])
        # 默认不合并，不应改动任何落盘文件。
        self.assertEqual((self.skill_dir / "_index.json").read_text(), before_index)
        self.assertEqual((self.skill_dir / "registry.json").read_text(), before_registry)

    def test_baidu_path_scoped_pattern_not_flagged(self):
        report = self._run()
        baidu_findings = [f for f in report.findings if f.member_id == "baidu"]
        self.assertEqual(baidu_findings, [])

    def test_merge_duplicates_keeps_highest_success_count_as_canonical(self):
        report = self._run(merge_duplicates=True)

        self.assertEqual(report.merged_members, ["arxiv_14"])

        index = json.loads((self.skill_dir / "_index.json").read_text())
        remaining_ids = {m["member_id"] for m in index["members"]}
        self.assertEqual(remaining_ids, {"arxiv_13", "baidu"})

        canonical = next(m for m in index["members"] if m["member_id"] == "arxiv_13")
        self.assertEqual(canonical["match"]["keyword"], ["k1", "k2"])

        registry = json.loads((self.skill_dir / "registry.json").read_text())
        self.assertEqual(registry["members"]["arxiv_14"]["status"], "dead")
        self.assertIn("status_changed_at", registry["members"]["arxiv_14"])
        self.assertEqual(registry["members"]["arxiv_13"]["status"], "trusted")

        # 合并只改状态/检索摘要，不删除脚本文件。
        self.assertTrue((self.skill_dir / "members" / "arxiv_14").exists())
        self.assertTrue((self.skill_dir / "members" / "arxiv_13").exists())

    def test_no_duplicates_when_domains_differ(self):
        index = {
            "members": [
                {"member_id": "arxiv_13", "description": "a",
                 "match": {"domain_pattern": "*.arxiv.org*", "keyword": ["k1"]}},
                {"member_id": "baidu", "description": "b",
                 "match": {"domain_pattern": "*.baidu.com/s*", "keyword": ["k2"]}},
            ]
        }
        registry = {"members": {
            "arxiv_13": {"status": "trusted", "success_count": 5},
            "baidu": {"status": "trusted", "success_count": 10},
        }}
        (self.skill_dir / "_index.json").write_text(json.dumps(index), encoding="utf-8")
        (self.skill_dir / "registry.json").write_text(json.dumps(registry), encoding="utf-8")

        report = self._run()
        kinds = {f.kind for f in report.findings}
        self.assertNotIn("duplicate_domain_pattern", kinds)


if __name__ == "__main__":
    unittest.main()
