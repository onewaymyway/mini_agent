# -*- coding: utf-8 -*-
"""
pytest 适配器

将测试用例从 unittest/pytest 风格统一转换为 UnifiedTestRunner 的 TestRecord 格式。
支持 pytest fixture 注入、mark 过滤、报告导出。
"""
import asyncio
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# 避免循环导入
from tests.framework.unified_test_runner import TestRecord, TestStatus, TestMode


@pytest.fixture(scope="session")
def unified_runner(tmp_path_factory):
    """提供全局 UnifiedTestRunner 实例"""
    from tests.framework.unified_test_runner import UnifiedTestRunner
    output_dir = tmp_path_factory.mktemp("test_output") / "reports"
    return UnifiedTestRunner(output_dir=output_dir)


def pytest_addoption(parser):
    """注册自定义命令行选项"""
    parser.addoption(
        "--test-mode",
        action="store",
        default="mock",
        choices=["mock", "real", "stress", "hybrid"],
        help="Test execution mode",
    )
    parser.addoption(
        "--concurrency",
        action="store",
        type=int,
        default=5,
        help="Max concurrent requests",
    )
    parser.addoption(
        "--timeout",
        action="store",
        type=int,
        default=30,
        help="Request timeout in seconds",
    )
    parser.addoption(
        "--suite-name",
        action="store",
        default=None,
        help="Suite name for result grouping",
    )


def pytest_collection_modifyitems(config, items):
    """根据 --test-mode 过滤用例"""
    mode = config.getoption("--test-mode")
    skip_marks = {
        "mock": ["real"],
        "real": ["mock"],
        "stress": ["mock", "real"],
        "hybrid": [],
    }
    skip_by_mode = skip_marks.get(mode, [])
    for item in items:
        for mark_name in skip_by_mode:
            if item.get_closest_marker(mark_name):
                item.add_marker(
                    pytest.mark.skip(reason=f"Skipped for mode={mode}")
                )


class PytestAdapter:
    """
    pytest 测试结果适配器

    将 pytest 的 pytest-report 、pytest-html 等输出转换为统一的 TestRecord 格式，
    方便与 UnifiedTestRunner 的结果体系对接。
    """

    def __init__(self, runner=None, output_dir=None):
        self.runner = runner
        self.output_dir = output_dir or Path(__file__).parent.parent / "output" / "pytest_reports"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._records: List[TestRecord] = []

    def record_from_pytest_item(self, item, outcome: str, duration: float, 
                                 error: Optional[str] = None, **extra) -> TestRecord:
        """从 pytest 单项测试结果创建 TestRecord"""
        test_id = item.nodeid
        scenario_id = getattr(item, "scenario_id", item.name)
        site_id = getattr(item, "site_id", "unknown")
        site_name = getattr(item, "site_name", item.module.__name__ if hasattr(item, "module") else "unknown")
        mode = getattr(item, "test_mode", TestMode.MOCK)

        status_map = {
            "passed": TestStatus.PASSED,
            "failed": TestStatus.FAILED,
            "error": TestStatus.ERROR,
            "skipped": TestStatus.SKIPPED,
        }
        status = status_map.get(outcome, TestStatus.ERROR)

        return TestRecord(
            test_id=test_id,
            site_id=site_id,
            site_name=site_name,
            scenario_id=scenario_id,
            scenario_name=getattr(item, "scenario_name", scenario_id),
            mode=mode,
            status=status,
            duration_seconds=duration,
            error_message=error,
            metrics=extra,
        )

    def export_to_json(self, records: List[TestRecord], filename: str) -> str:
        """导出为 JSON 报告"""
        data = {
            "export_time": datetime.now().isoformat(),
            "total": len(records),
            "passed": sum(1 for r in records if r.status == TestStatus.PASSED),
            "failed": sum(1 for r in records if r.status == TestStatus.FAILED),
            "errors": sum(1 for r in records if r.status == TestStatus.ERROR),
            "skipped": sum(1 for r in records if r.status == TestStatus.SKIPPED),
            "records": [r.to_dict() for r in records],
        }
        path = self.output_dir / f"{filename}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        logger.info(f"Pytest report exported: {path}")
        return str(path)

    def export_to_html(self, records: List[TestRecord], filename: str) -> str:
        """导出为 HTML 报告"""
        passed = sum(1 for r in records if r.status == TestStatus.PASSED)
        failed = sum(1 for r in records if r.status == TestStatus.FAILED)
        errors = sum(1 for r in records if r.status == TestStatus.ERROR)
        skipped = sum(1 for r in records if r.status == TestStatus.SKIPPED)
        total = len(records)
        pass_rate = passed / max(total - skipped, 1) * 100

        rows = ""
        for r in records:
            status_cls = r.status.value
            rows += f"""
            <tr class="{status_cls}">
                <td>{r.test_id}</td>
                <td>{r.site_name}</td>
                <td>{r.scenario_name}</td>
                <td>{r.mode.value}</td>
                <td>{r.duration_seconds:.2f}s</td>
                <td>{r.error_message or '-'}</td>
            </tr>"""

        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Pytest Adapter Report</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; }}
        h1 {{ color: #333; }}
        .summary {{ background: #f5f5f5; padding: 20px; border-radius: 8px; margin-bottom: 20px; display: flex; gap: 30px; }}
        .metric-value {{ font-size: 24px; font-weight: bold; }}
        .metric-label {{ color: #666; font-size: 14px; }}
        table {{ width: 100%; border-collapse: collapse; }}
        th, td {{ padding: 10px; text-align: left; border-bottom: 1px solid #ddd; font-size: 13px; }}
        th {{ background: #f8f9fa; }}
        .passed {{ color: green; }}
        .failed {{ color: red; }}
        .error {{ color: orange; }}
        .skipped {{ color: gray; }}
    </style>
</head>
<body>
    <h1>Pytest Adapter Report</h1>
    <div class="summary">
        <div class="metric"><div class="metric-value">{total}</div><div class="metric-label">Total</div></div>
        <div class="metric"><div class="metric-value" style="color:green">{passed}</div><div class="metric-label">Passed</div></div>
        <div class="metric"><div class="metric-value" style="color:red">{failed}</div><div class="metric-label">Failed</div></div>
        <div class="metric"><div class="metric-value" style="color:orange">{errors}</div><div class="metric-label">Errors</div></div>
        <div class="metric"><div class="metric-value">{pass_rate:.1f}%</div><div class="metric-label">Pass Rate</div></div>
    </div>
    <table>
        <tr><th>Test ID</th><th>Site</th><th>Scenario</th><th>Mode</th><th>Duration</th><th>Error</th></tr>
        {rows}
    </table>
</body>
</html>"""
        path = self.output_dir / f"{filename}.html"
        path.write_text(html, encoding="utf-8")
        logger.info(f"HTML report exported: {path}")
        return str(path)


def pytest_configure(config):
    """Pytest 钩子：注册自定义 mark"""
    for marker in ["p0", "p1", "p2", "p3", "mock", "real", "stress", "stability",
                   "compatibility", "anti_crawl", "performance"]:
        config.addinivalue_line("markers", f"{marker}: {marker} priority/mode marker")
