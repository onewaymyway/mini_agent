"""
测试执行器

提供批量测试执行、结果聚合和报告生成功能。
"""
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Type

from .base_test_case import BaseTestCase, TestResult

logger = logging.getLogger(__name__)


class TestRunner:
    """测试执行器"""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or Path(__file__).parent.parent / "output" / "reports"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.all_results: List[TestResult] = []
        self._start_time: Optional[float] = None

    async def run_tests(self, test_cases: List[BaseTestCase], max_concurrent: int = 3) -> Dict:
        """批量执行测试用例"""
        self._start_time = asyncio.get_event_loop().time()
        logger.info(f"Starting test run with {len(test_cases)} test cases")

        semaphore = asyncio.Semaphore(max_concurrent)

        async def run_with_semaphore(tc: BaseTestCase) -> List[TestResult]:
            async with semaphore:
                await tc.setup()
                try:
                    result = await tc.run_with_retry()
                    return [result]
                finally:
                    await tc.teardown()

        tasks = [run_with_semaphore(tc) for tc in test_cases]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result_batch in results:
            if isinstance(result_batch, list):
                self.all_results.extend(result_batch)
            elif isinstance(result_batch, Exception):
                logger.error(f"Test run failed with exception: {result_batch}")

        duration = asyncio.get_event_loop().time() - self._start_time
        return self._generate_summary(duration)

    def _generate_summary(self, duration: float) -> Dict:
        """生成测试摘要"""
        passed = sum(1 for r in self.all_results if r.status == "passed")
        failed = sum(1 for r in self.all_results if r.status == "failed")
        error = sum(1 for r in self.all_results if r.status == "error")
        skipped = sum(1 for r in self.all_results if r.status == "skipped")

        summary = {
            "run_timestamp": datetime.now().isoformat(),
            "duration_seconds": round(duration, 2),
            "total_tests": len(self.all_results),
            "passed": passed,
            "failed": failed,
            "errors": error,
            "skipped": skipped,
            "pass_rate": round(passed / len(self.all_results) * 100, 2) if self.all_results else 0,
        }
        return summary

    def save_summary(self, summary: Dict, filename: Optional[str] = None) -> str:
        """保存测试摘要到JSON文件"""
        if not filename:
            filename = f"summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        path = self.output_dir / f"{filename}.json"
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        logger.info(f"Summary saved to {path}")
        return str(path)

    def generate_html_report(self, summary: Dict) -> str:
        """生成HTML测试报告"""
        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>Browser CDP Test Report</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; }}
        h1 {{ color: #333; }}
        .summary {{ background: #f5f5f5; padding: 20px; border-radius: 8px; margin-bottom: 20px; }}
        .metric {{ display: inline-block; margin-right: 30px; }}
        .metric-value {{ font-size: 24px; font-weight: bold; }}
        .metric-label {{ color: #666; font-size: 14px; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
        th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }}
        th {{ background: #f8f9fa; }}
        .passed {{ color: green; }}
        .failed {{ color: red; }}
        .error {{ color: orange; }}
    </style>
</head>
<body>
    <h1>Browser CDP Test Report</h1>
    <div class="summary">
        <div class="metric">
            <div class="metric-value">{summary['total_tests']}</div>
            <div class="metric-label">Total Tests</div>
        </div>
        <div class="metric">
            <div class="metric-value" style="color: green;">{summary['passed']}</div>
            <div class="metric-label">Passed</div>
        </div>
        <div class="metric">
            <div class="metric-value" style="color: red;">{summary['failed']}</div>
            <div class="metric-label">Failed</div>
        </div>
        <div class="metric">
            <div class="metric-value" style="color: orange;">{summary['errors']}</div>
            <div class="metric-label">Errors</div>
        </div>
        <div class="metric">
            <div class="metric-value">{summary['pass_rate']}%</div>
            <div class="metric-label">Pass Rate</div>
        </div>
    </div>
    <h2>Test Details</h2>
    <table>
        <tr>
            <th>Test ID</th>
            <th>Website</th>
            <th>Scenario</th>
            <th>Status</th>
            <th>Duration</th>
            <th>Error</th>
        </tr>
        {self._generate_rows()}
    </table>
</body>
</html>"""
        path = self.output_dir / "test_report.html"
        path.write_text(html_content, encoding="utf-8")
        logger.info(f"HTML report saved to {path}")
        return str(path)

    def _generate_rows(self) -> str:
        """生成HTML表格行"""
        rows = []
        for r in self.all_results:
            status_class = r.status
            rows.append(f"""<tr>
                <td>{r.test_id}</td>
                <td>{r.website}</td>
                <td>{r.scenario_name}</td>
                <td class="{status_class}">{r.status}</td>
                <td>{r.duration_seconds:.2f}s</td>
                <td>{r.error_message[:50] if r.error_message else ''}</td>
            </tr>""")
        return "\n".join(rows)
