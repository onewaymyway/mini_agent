# -*- coding: utf-8 -*-
"""
Phase1 站点自动化测试执行器

整合配置加载器与测试框架，执行 Phase1 十个 P0 站点的核心测试用例。
"""
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# 添加 skill 路径到 sys.path
SKILL_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SKILL_DIR))

from tests.fixtures.test_config_loader import load_test_config, TestEnvironmentConfig
from tests.framework.test_runner import TestRunner
from tests.framework.base_test_case import BaseTestCase, TestResult

logger = logging.getLogger(__name__)


class Phase1TestSuite:
    """Phase1 测试套件"""

    def __init__(self, config: TestEnvironmentConfig, output_dir: Path):
        self.config = config
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.runner = TestRunner(output_dir=output_dir)
        self.site_results: Dict[str, Dict] = {}

    async def run_site_tests(self, site_id: str, test_cases: List[str]) -> Dict:
        """为单个站点执行指定测试用例"""
        site = self.config.get_site_by_id(site_id)
        if not site:
            return {"status": "error", "message": f"Site {site_id} not found"}

        logger.info(f"Running tests for site: {site.name} ({site_id})")
        results = []

        for case_id in test_cases:
            # 根据测试用例ID创建对应的测试实例
            test_case = self._create_test_case(site, case_id)
            if test_case:
                result = await test_case.run_with_retry()
                results.append(result)
            else:
                results.append(TestResult(
                    test_id=f"{case_id}_stub",
                    website=site.name,
                    scenario_name=f"Stub for {case_id}",
                    status="skipped",
                    duration_seconds=0,
                    error_message=f"Test case {case_id} implementation pending"
                ))

        summary = {
            "site_id": site_id,
            "site_name": site.name,
            "priority": site.priority,
            "category": site.category,
            "test_cases_run": len(results),
            "passed": sum(1 for r in results if r.status == "passed"),
            "failed": sum(1 for r in results if r.status == "failed"),
            "errors": sum(1 for r in results if r.status == "error"),
            "skipped": sum(1 for r in results if r.status == "skipped"),
            "results": [r.to_dict() for r in results],
            "timestamp": datetime.now().isoformat(),
        }
        self.site_results[site_id] = summary
        return summary

    def _create_test_case(self, site, case_id: str) -> Optional[BaseTestCase]:
        """根据测试用例ID创建测试实例（占位实现）"""
        # TODO: 实现具体的测试用例类映射
        return None

    async def run_all_phase1(self) -> Dict:
        """执行所有 Phase1 站点测试"""
        logger.info(f"Starting Phase1 test suite with {len(self.config.phase1_sites)} sites")

        all_summaries = []
        for site in self.config.phase1_sites:
            test_cases = self.config.get_test_cases_for_site(site.site_id)
            summary = await self.run_site_tests(site.site_id, test_cases)
            all_summaries.append(summary)

        return self._generate_overall_summary(all_summaries)

    def _generate_overall_summary(self, site_summaries: List[Dict]) -> Dict:
        """生成总体摘要"""
        total_tests = sum(s.get("test_cases_run", 0) for s in site_summaries)
        total_passed = sum(s.get("passed", 0) for s in site_summaries)
        total_failed = sum(s.get("failed", 0) for s in site_summaries)
        total_errors = sum(s.get("errors", 0) for s in site_summaries)
        total_skipped = sum(s.get("skipped", 0) for s in site_summaries)

        return {
            "run_timestamp": datetime.now().isoformat(),
            "total_sites": len(site_summaries),
            "total_tests": total_tests,
            "passed": total_passed,
            "failed": total_failed,
            "errors": total_errors,
            "skipped": total_skipped,
            "pass_rate": round(total_passed / max(total_tests, 1) * 100, 2),
            "site_details": site_summaries,
        }

    def save_results(self, overall_summary: Dict):
        """保存测试结果到文件"""
        # 保存总体摘要
        summary_path = self.output_dir / f"phase1_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        summary_path.write_text(json.dumps(overall_summary, ensure_ascii=False, indent=2, default=str))
        logger.info(f"Overall summary saved to {summary_path}")

        # 保存各站点详情
        for site_id, details in self.site_results.items():
            site_path = self.output_dir / f"phase1_site_{site_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            site_path.write_text(json.dumps(details, ensure_ascii=False, indent=2, default=str))
            logger.info(f"Site {site_id} results saved to {site_path}")

        return str(summary_path)


def main():
    """主入口函数"""
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # 加载配置
    try:
        config = load_test_config()
        logger.info(f"Loaded config: {len(config.phase1_sites)} Phase1 sites")
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        sys.exit(1)

    # 创建输出目录
    output_dir = Path(__file__).parent.parent.parent / "output" / "test_reports" / "phase1"
    output_dir.mkdir(parents=True, exist_ok=True)

    # 执行测试
    suite = Phase1TestSuite(config, output_dir)

    async def run():
        return await suite.run_all_phase1()

    summary = asyncio.run(run())

    # 保存结果
    summary_path = suite.save_results(summary)

    # 输出摘要
    print("\n" + "=" * 60)
    print("Phase1 测试执行摘要")
    print("=" * 60)
    print(f"总站点数: {summary['total_sites']}")
    print(f"总测试用例: {summary['total_tests']}")
    print(f"通过: {summary['passed']}")
    print(f"失败: {summary['failed']}")
    print(f"错误: {summary['errors']}")
    print(f"跳过: {summary['skipped']}")
    print(f"通过率: {summary['pass_rate']}%")
    print(f"\n详细报告: {summary_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
