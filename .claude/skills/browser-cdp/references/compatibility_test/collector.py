"""
结果收集器

负责收集测试结果，生成测试报告。
"""

import json
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional

from .models import TestRun, TestResult

logger = logging.getLogger(__name__)


class ResultCollector:
    """结果收集器"""

    def __init__(self, output_dir: str = "test_reports"):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        self._runs: Dict[str, TestRun] = {}

    def register_run(self, run: TestRun) -> None:
        """
        注册测试执行记录

        Args:
            run: 测试执行记录
        """
        self._runs[run.run_id] = run
        logger.info(f"注册测试结果: {run.run_id}")

    def collect(self, run: TestRun) -> None:
        """
        收集测试结果

        Args:
            run: 测试执行记录
        """
        self.register_run(run)
        self._save_run_report(run)
        self._save_summary(run)

    def _save_run_report(self, run: TestRun) -> str:
        """
        保存测试执行报告

        Args:
            run: 测试执行记录

        Returns:
            报告文件路径
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{run.website_name}_{timestamp}.json"
        filepath = os.path.join(self.output_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(run.to_dict(), f, ensure_ascii=False, indent=2)

        logger.info(f"测试报告已保存: {filepath}")
        return filepath

    def _save_summary(self, run: TestRun) -> None:
        """
        保存测试摘要

        Args:
            run: 测试执行记录
        """
        summary = {
            "run_id": run.run_id,
            "website_name": run.website_name,
            "started_at": run.started_at.isoformat(),
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "total_cases": run.total_cases,
            "passed_cases": run.passed_cases,
            "failed_cases": run.failed_cases,
            "skipped_cases": run.skipped_cases,
            "success_rate": run.success_rate,
        }

        summary_path = os.path.join(self.output_dir, "summary.json")

        # 读取现有摘要
        summaries = []
        if os.path.exists(summary_path):
            with open(summary_path, "r", encoding="utf-8") as f:
                summaries = json.load(f)

        summaries.append(summary)

        # 只保留最近 100 条
        summaries = summaries[-100:]

        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summaries, f, ensure_ascii=False, indent=2)

    def get_run(self, run_id: str) -> Optional[TestRun]:
        """
        获取测试执行记录

        Args:
            run_id: 执行 ID

        Returns:
            测试执行记录，不存在则返回 None
        """
        return self._runs.get(run_id)

    def get_all_runs(self) -> List[TestRun]:
        """获取所有测试执行记录"""
        return list(self._runs.values())

    def get_runs_by_website(self, website_name: str) -> List[TestRun]:
        """
        按网站名称获取测试执行记录

        Args:
            website_name: 网站名称

        Returns:
            测试执行记录列表
        """
        return [
            run for run in self._runs.values()
            if run.website_name == website_name
        ]

    def generate_report(self, run: TestRun) -> str:
        """
        生成测试报告

        Args:
            run: 测试执行记录

        Returns:
            报告文件路径
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"report_{run.website_name}_{timestamp}.md"
        filepath = os.path.join(self.output_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(self._generate_markdown_report(run))

        logger.info(f"测试报告已生成: {filepath}")
        return filepath

    def _generate_markdown_report(self, run: TestRun) -> str:
        """
        生成 Markdown 格式测试报告

        Args:
            run: 测试执行记录

        Returns:
            Markdown 报告内容
        """
        lines = [
            f"# 网站兼容性测试报告",
            f"",
            f"## 基本信息",
            f"",
            f"- **网站名称**: {run.website_name}",
            f"- **执行 ID**: {run.run_id}",
            f"- **开始时间**: {run.started_at.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- **完成时间**: {run.completed_at.strftime('%Y-%m-%d %H:%M:%S') if run.completed_at else '进行中'}",
            f"- **总用例数**: {run.total_cases}",
            f"- **通过数**: {run.passed_cases}",
            f"- **失败数**: {run.failed_cases}",
            f"- **跳过数**: {run.skipped_cases}",
            f"- **成功率**: {run.success_rate:.1%}",
            f"",
            f"## 测试详情",
            f"",
        ]

        for result in run.results:
            status_icon = "✅" if result.status.value == "pass" else "❌"
            lines.append(f"### {status_icon} {result.case_id}: {result.error_message or '通过'}")
            lines.append(f"")
            lines.append(f"- **执行时长**: {result.duration:.2f} 秒")
            lines.append(f"- **状态**: {result.status.value}")
            if result.metrics:
                lines.append(f"- **评估指标**:")
                for metric, value in result.metrics.items():
                    lines.append(f"  - {metric}: {value:.2%}")
            lines.append(f"")

        return "\n".join(lines)

    def get_statistics(self) -> Dict[str, any]:
        """
        获取统计信息

        Returns:
            统计信息字典
        """
        if not self._runs:
            return {
                "total_runs": 0,
                "total_cases": 0,
                "total_passed": 0,
                "total_failed": 0,
                "overall_success_rate": 0.0,
            }

        total_cases = sum(run.total_cases for run in self._runs.values())
        total_passed = sum(run.passed_cases for run in self._runs.values())
        total_failed = sum(run.failed_cases for run in self._runs.values())

        return {
            "total_runs": len(self._runs),
            "total_cases": total_cases,
            "total_passed": total_passed,
            "total_failed": total_failed,
            "overall_success_rate": total_passed / total_cases if total_cases > 0 else 0.0,
        }
