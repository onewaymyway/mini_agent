"""Test Reporter Module for Browser CDP Tests

Generates comprehensive test reports in multiple formats (HTML, JSON, Markdown, JUnit XML)
from test execution data.
"""
import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from xml.etree import ElementTree as ET
from xml.dom import minidom

from .test_logger import TestContext, TestStep, TestLogger
from .exception_handler import TestError, ErrorCategory, ErrorSeverity


@dataclass
class TestResult:
    """Complete test result with all execution data."""
    test_name: str
    test_class: str = ""
    status: str = "unknown"  # passed, failed, error, skipped
    start_time: float = 0.0
    end_time: float = 0.0
    duration: float = 0.0
    steps: List[TestStep] = field(default_factory=list)
    errors: List[TestError] = field(default_factory=list)
    assertions: List[Dict[str, Any]] = field(default_factory=list)
    screenshots: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # Prevent pytest from collecting this as a test class
    __test__ = False
    
    @property
    def passed(self) -> bool:
        return self.status == "passed"
    
    @property
    def failed(self) -> bool:
        return self.status in ("failed", "error")
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "test_name": self.test_name,
            "test_class": self.test_class,
            "status": self.status,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration": self.duration,
            "steps": [s.to_dict() for s in self.steps],
            "errors": [e.to_dict() for e in self.errors],
            "assertions": self.assertions,
            "screenshots": self.screenshots,
            "metadata": self.metadata
        }


@dataclass
class TestSuiteResult:
    """Aggregated results for a test suite."""
    suite_name: str
    start_time: float = 0.0
    end_time: float = 0.0
    test_results: List[TestResult] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # Prevent pytest from collecting this as a test class
    __test__ = False
    
    @property
    def duration(self) -> float:
        return self.end_time - self.start_time if self.end_time else 0.0
    
    @property
    def total_tests(self) -> int:
        return len(self.test_results)
    
    @property
    def passed_tests(self) -> int:
        return sum(1 for r in self.test_results if r.passed)
    
    @property
    def failed_tests(self) -> int:
        return sum(1 for r in self.test_results if r.failed)
    
    @property
    def skipped_tests(self) -> int:
        return sum(1 for r in self.test_results if r.status == "skipped")
    
    @property
    def error_tests(self) -> int:
        return sum(1 for r in self.test_results if r.status == "error")
    
    @property
    def pass_rate(self) -> float:
        if self.total_tests == 0:
            return 0.0
        return self.passed_tests / self.total_tests * 100
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "suite_name": self.suite_name,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration": self.duration,
            "total_tests": self.total_tests,
            "passed_tests": self.passed_tests,
            "failed_tests": self.failed_tests,
            "skipped_tests": self.skipped_tests,
            "error_tests": self.error_tests,
            "pass_rate": self.pass_rate,
            "test_results": [r.to_dict() for r in self.test_results],
            "metadata": self.metadata
        }


class TestReporter:
    """Generates test reports in multiple formats."""
    
    # Prevent pytest from collecting this as a test class
    __test__ = False
    
    def __init__(self, output_dir: Union[str, Path] = "test_reports"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.suite_results: List[TestSuiteResult] = []
        self.current_suite: Optional[TestSuiteResult] = None
    
    def start_suite(self, suite_name: str, **metadata) -> TestSuiteResult:
        """Start a new test suite."""
        self.current_suite = TestSuiteResult(
            suite_name=suite_name,
            start_time=datetime.now().timestamp(),
            metadata=metadata
        )
        return self.current_suite
    
    def end_suite(self) -> Optional[TestSuiteResult]:
        """End the current test suite."""
        if self.current_suite:
            self.current_suite.end_time = datetime.now().timestamp()
            self.suite_results.append(self.current_suite)
            suite = self.current_suite
            self.current_suite = None
            return suite
        return None
    
    def add_test_result(self, result: TestResult):
        """Add a test result to the current suite."""
        if self.current_suite:
            self.current_suite.test_results.append(result)
    
    def add_test_result_from_context(self, context: TestContext, **extra):
        """Create and add test result from TestContext."""
        result = TestResult(
            test_name=context.test_name,
            test_class=context.test_class,
            status=context.status,
            start_time=context.start_time,
            end_time=context.end_time or datetime.now().timestamp(),
            duration=context.duration,
            steps=context.steps,
            metadata={**context.metadata, **extra}
        )
        self.add_test_result(result)
        return result
    
    def generate_json_report(self, filename: str = None) -> Path:
        """Generate JSON report."""
        if filename is None:
            filename = f"test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        filepath = self.output_dir / filename
        
        report_data = {
            "generated_at": datetime.now().isoformat(),
            "suites": [s.to_dict() for s in self.suite_results]
        }
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)
        
        return filepath
    
    def generate_junit_xml(self, filename: str = None) -> Path:
        """Generate JUnit XML report for CI/CD integration."""
        if filename is None:
            filename = f"junit_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xml"
        
        filepath = self.output_dir / filename
        
        testsuites = ET.Element("testsuites")
        testsuites.set("name", "Browser CDP Tests")
        testsuites.set("timestamp", datetime.now().isoformat())
        
        total_tests = 0
        total_failures = 0
        total_errors = 0
        total_skipped = 0
        total_time = 0.0
        
        for suite in self.suite_results:
            testsuite = ET.SubElement(testsuites, "testsuite")
            testsuite.set("name", suite.suite_name)
            testsuite.set("tests", str(suite.total_tests))
            testsuite.set("failures", str(suite.failed_tests))
            testsuite.set("errors", str(suite.error_tests))
            testsuite.set("skipped", str(suite.skipped_tests))
            testsuite.set("time", f"{suite.duration:.3f}")
            testsuite.set("timestamp", datetime.fromtimestamp(suite.start_time).isoformat())
            
            total_tests += suite.total_tests
            total_failures += suite.failed_tests
            total_errors += suite.error_tests
            total_skipped += suite.skipped_tests
            total_time += suite.duration
            
            for result in suite.test_results:
                testcase = ET.SubElement(testsuite, "testcase")
                testcase.set("name", result.test_name)
                testcase.set("classname", result.test_class or "Unknown")
                testcase.set("time", f"{result.duration:.3f}")
                
                if result.status == "skipped":
                    skipped = ET.SubElement(testcase, "skipped")
                    skipped.set("message", "Test skipped")
                elif result.status == "failed":
                    failure = ET.SubElement(testcase, "failure")
                    failure.set("message", "Test failed")
                    if result.errors:
                        failure.text = "\n".join(e.message for e in result.errors)
                elif result.status == "error":
                    error_elem = ET.SubElement(testcase, "error")
                    error_elem.set("message", "Test error")
                    if result.errors:
                        error_elem.text = "\n".join(e.message for e in result.errors)
                
                # Add system-out with steps
                if result.steps:
                    system_out = ET.SubElement(testcase, "system-out")
                    steps_text = "\n".join(
                        f"  {s.name}: {s.status} ({s.duration:.3f}s)"
                        for s in result.steps
                    )
                    system_out.text = f"Steps:\n{steps_text}"
        
        testsuites.set("tests", str(total_tests))
        testsuites.set("failures", str(total_failures))
        testsuites.set("errors", str(total_errors))
        testsuites.set("skipped", str(total_skipped))
        testsuites.set("time", f"{total_time:.3f}")
        
        # Pretty print XML
        rough_string = ET.tostring(testsuites, encoding='unicode')
        reparsed = minidom.parseString(rough_string)
        pretty_xml = reparsed.toprettyxml(indent="  ")
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(pretty_xml)
        
        return filepath
    
    def generate_html_report(self, filename: str = None) -> Path:
        """Generate HTML report with interactive elements."""
        if filename is None:
            filename = f"test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        
        filepath = self.output_dir / filename
        
        html = self._generate_html_content()
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(html)
        
        return filepath
    
    def _generate_html_content(self) -> str:
        """Generate HTML content for the report."""
        total_tests = sum(s.total_tests for s in self.suite_results)
        total_passed = sum(s.passed_tests for s in self.suite_results)
        total_failed = sum(s.failed_tests for s in self.suite_results)
        total_skipped = sum(s.skipped_tests for s in self.suite_results)
        total_errors = sum(s.error_tests for s in self.suite_results)
        total_duration = sum(s.duration for s in self.suite_results)
        
        pass_rate = (total_passed / total_tests * 100) if total_tests > 0 else 0
        
        # Determine overall status color
        if total_failed > 0 or total_errors > 0:
            status_color = "#dc3545"  # red
            status_text = "FAILED"
        elif total_skipped == total_tests:
            status_color = "#6c757d"  # gray
            status_text = "SKIPPED"
        else:
            status_color = "#28a745"  # green
            status_text = "PASSED"
        
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Browser CDP Test Report</title>
    <style>
        * {{ box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: white; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); overflow: hidden; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; }}
        .header h1 {{ margin: 0; font-size: 28px; }}
        .header .meta {{ margin-top: 10px; opacity: 0.9; font-size: 14px; }}
        .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; padding: 20px; background: #f8f9fa; border-bottom: 1px solid #eee; }}
        .stat-card {{ background: white; padding: 20px; border-radius: 8px; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        .stat-value {{ font-size: 32px; font-weight: bold; }}
        .stat-label {{ font-size: 12px; color: #666; text-transform: uppercase; margin-top: 5px; }}
        .stat-passed {{ color: #28a745; }}
        .stat-failed {{ color: #dc3545; }}
        .stat-skipped {{ color: #6c757d; }}
        .stat-errors {{ color: #fd7e14; }}
        .stat-duration {{ color: #667eea; }}
        .status-badge {{ display: inline-block; padding: 8px 20px; border-radius: 20px; font-weight: bold; font-size: 14px; color: white; background: {status_color}; }}
        .suite {{ border-bottom: 1px solid #eee; }}
        .suite:last-child {{ border-bottom: none; }}
        .suite-header {{ padding: 20px; background: #f8f9fa; cursor: pointer; display: flex; justify-content: space-between; align-items: center; }}
        .suite-header:hover {{ background: #e9ecef; }}
        .suite-title {{ font-size: 18px; font-weight: 600; }}
        .suite-description {{ font-size: 13px; color: #666; margin-top: 4px; font-style: italic; }}
        .suite-stats {{ display: flex; gap: 15px; font-size: 14px; }}
        .suite-content {{ padding: 20px; display: none; }}
        .suite-content.open {{ display: block; }}
        .test {{ padding: 15px; margin-bottom: 10px; border-radius: 6px; border: 1px solid #eee; background: #fafafa; }}
        .test.passed {{ border-left: 4px solid #28a745; }}
        .test.failed {{ border-left: 4px solid #dc3545; }}
        .test.error {{ border-left: 4px solid #fd7e14; }}
        .test.skipped {{ border-left: 4px solid #6c757d; }}
        .test-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }}
        .test-name {{ font-weight: 600; font-size: 16px; }}
        .test-class {{ color: #666; font-size: 13px; }}
        .test-duration {{ color: #667eea; font-size: 13px; }}
        .test-status {{ padding: 4px 12px; border-radius: 12px; font-size: 12px; font-weight: 600; text-transform: uppercase; }}
        .test-status.passed {{ background: #d4edda; color: #155724; }}
        .test-status.failed {{ background: #f8d7da; color: #721c24; }}
        .test-status.error {{ background: #fff3cd; color: #856404; }}
        .test-status.skipped {{ background: #e2e3e5; color: #383d41; }}
        .test-steps {{ margin-top: 10px; }}
        .step {{ padding: 8px 12px; margin: 5px 0; background: white; border-radius: 4px; border: 1px solid #eee; display: flex; justify-content: space-between; }}
        .step.passed {{ border-left: 3px solid #28a745; }}
        .step.failed {{ border-left: 3px solid #dc3545; }}
        .step.skipped {{ border-left: 3px solid #6c757d; }}
        .step-name {{ font-weight: 500; }}
        .step-duration {{ color: #667eea; font-size: 12px; }}
        .step-status {{ padding: 2px 8px; border-radius: 8px; font-size: 11px; font-weight: 600; }}
        .step-status.passed {{ background: #d4edda; color: #155724; }}
        .step-status.failed {{ background: #f8d7da; color: #721c24; }}
        .step-status.running {{ background: #cce5ff; color: #004085; }}
        .step-status.skipped {{ background: #e2e3e5; color: #383d41; }}
        .errors {{ margin-top: 10px; padding: 10px; background: #f8d7da; border-radius: 4px; border: 1px solid #f5c6cb; }}
        .error-item {{ margin: 5px 0; font-family: monospace; font-size: 13px; }}
        .toggle-icon {{ transition: transform 0.2s; }}
        .toggle-icon.open {{ transform: rotate(90deg); }}
        .assertions {{ margin-top: 10px; }}
        .assertion {{ padding: 8px 12px; margin: 5px 0; background: white; border-radius: 4px; border: 1px solid #eee; font-family: monospace; font-size: 13px; }}
        .assertion.passed {{ border-left: 3px solid #28a745; }}
        .assertion.failed {{ border-left: 3px solid #dc3545; }}
        .screenshots {{ margin-top: 10px; }}
        .screenshot {{ display: inline-block; margin: 5px; }}
        .screenshot img {{ max-width: 200px; max-height: 150px; border: 1px solid #ddd; border-radius: 4px; cursor: pointer; }}
        .footer {{ padding: 20px; text-align: center; color: #666; font-size: 13px; background: #f8f9fa; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Browser CDP Test Report</h1>
            <div class="meta">
                Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                <span class="status-badge">{status_text}</span>
            </div>
        </div>
        
        <div class="summary">
            <div class="stat-card">
                <div class="stat-value stat-passed">{total_passed}</div>
                <div class="stat-label">Passed</div>
            </div>
            <div class="stat-card">
                <div class="stat-value stat-failed">{total_failed}</div>
                <div class="stat-label">Failed</div>
            </div>
            <div class="stat-card">
                <div class="stat-value stat-errors">{total_errors}</div>
                <div class="stat-label">Errors</div>
            </div>
            <div class="stat-card">
                <div class="stat-value stat-skipped">{total_skipped}</div>
                <div class="stat-label">Skipped</div>
            </div>
            <div class="stat-card">
                <div class="stat-value stat-duration">{total_duration:.2f}s</div>
                <div class="stat-label">Duration</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{pass_rate:.1f}%</div>
                <div class="stat-label">Pass Rate</div>
            </div>
        </div>
        
        <div class="suites">
"""
        
        for suite in self.suite_results:
            suite_desc = suite.metadata.get('description', '') if suite.metadata else ''
            desc_html = f'<div class="suite-description">{suite_desc}</div>' if suite_desc else ''
            html += f"""
            <div class="suite">
                <div class="suite-header" onclick="toggleSuite(this)">
                    <div>
                        <div class="suite-title">{suite.suite_name}</div>
                        {desc_html}
                        <div class="suite-stats">
                            <span>Tests: {suite.total_tests}</span>
                            <span style="color: #28a745;">Passed: {suite.passed_tests}</span>
                            <span style="color: #dc3545;">Failed: {suite.failed_tests}</span>
                            <span style="color: #fd7e14;">Errors: {suite.error_tests}</span>
                            <span style="color: #667eea;">{suite.duration:.2f}s</span>
                        </div>
                    </div>
                    <span class="toggle-icon">▶</span>
                </div>
                <div class="suite-content">
"""
            
            for result in suite.test_results:
                status_class = result.status
                html += f"""
                <div class="test {status_class}">
                    <div class="test-header">
                        <div>
                            <div class="test-name">{result.test_name}</div>
                            <div class="test-class">{result.test_class or 'Unknown'}</div>
                        </div>
                        <div>
                            <span class="test-status {status_class}">{result.status}</span>
                            <span class="test-duration">{result.duration:.3f}s</span>
                        </div>
                    </div>
"""
                
                if result.steps:
                    html += '<div class="test-steps">'
                    for step in result.steps:
                        step_status = step.status
                        desc_html = ''
                        if step.description:
                            desc_html = f'<span style="margin-left: 10px; color: #666; font-size: 12px;">{step.description}</span>'
                        html += f'''
                        <div class="step {step_status}">
                            <div>
                                <span class="step-name">{step.name}</span>
                                {desc_html}
                            </div>
                            <div>
                                <span class="step-status {step_status}">{step.status}</span>
                                <span class="step-duration">{step.duration:.3f}s</span>
                            </div>
                        </div>
'''
                        if step.error:
                            html += f'<div class="error-item">Error: {step.error}</div>'
                    html += '</div>'
                
                if result.errors:
                    html += "<div class=\"errors\"><strong>Errors:</strong>"
                    for error in result.errors:
                        html += f"<div class=\"error-item\">Error: {error.message}</div>"
                    html += "</div>"
                
                if result.assertions:
                    html += "<div class=\"assertions\"><strong>Assertions:</strong>"
                    for assertion in result.assertions:
                        passed = assertion.get('passed', False)
                        html += f"<div class=\"assertion {'passed' if passed else 'failed'}\">{assertion.get('message', '')}</div>"
                    html += "</div>"
                
                if result.screenshots:
                    html += "<div class=\"screenshots\"><strong>Screenshots:</strong>"
                    for screenshot in result.screenshots:
                        html += f"<div class=\"screenshot\"><img src=\"{screenshot}\" alt=\"Screenshot\" onclick=\"openImage(this.src)\"></div>"
                    html += "</div>"
                
                html += "</div>"  # close test
            
            html += """
                </div>
            </div>
"""
        
        html += """
        </div>
        <div class="footer">
            Browser CDP Test Framework | Report generated at """ + datetime.now().strftime('%Y-%m-%d %H:%M:%S') + """
        </div>
    </div>
    
    <script>
        function toggleSuite(header) {
            const content = header.nextElementSibling;
            const icon = header.querySelector('.toggle-icon');
            content.classList.toggle('open');
            icon.classList.toggle('open');
        }
        
        function openImage(src) {
            window.open(src, '_blank');
        }
    </script>
</body>
</html>"""
        
        return html
    
    def generate_markdown_report(self, filename: str = None) -> Path:
        """Generate Markdown report."""
        if filename is None:
            filename = f"test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        
        filepath = self.output_dir / filename
        
        total_tests = sum(s.total_tests for s in self.suite_results)
        total_passed = sum(s.passed_tests for s in self.suite_results)
        total_failed = sum(s.failed_tests for s in self.suite_results)
        total_skipped = sum(s.skipped_tests for s in self.suite_results)
        total_errors = sum(s.error_tests for s in self.suite_results)
        total_duration = sum(s.duration for s in self.suite_results)
        pass_rate = (total_passed / total_tests * 100) if total_tests > 0 else 0
        
        md = f"""# Browser CDP Test Report

**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## Summary

| Metric | Value |
|--------|-------|
| Total Tests | {total_tests} |
| Passed | {total_passed} |
| Failed | {total_failed} |
| Errors | {total_errors} |
| Skipped | {total_skipped} |
| Duration | {total_duration:.2f}s |
| Pass Rate | {pass_rate:.1f}% |

"""
        
        for suite in self.suite_results:
            md += f"## {suite.suite_name}\n\n"
            suite_desc = suite.metadata.get('description', '') if suite.metadata else ''
            if suite_desc:
                md += f"*{suite_desc}*\n\n"
            md += f"- **Tests:** {suite.total_tests} | **Passed:** {suite.passed_tests} | **Failed:** {suite.failed_tests} | **Errors:** {suite.error_tests} | **Skipped:** {suite.skipped_tests} | **Duration:** {suite.duration:.2f}s | **Pass Rate:** {suite.pass_rate:.1f}%\n\n"
            
            for result in suite.test_results:
                status_emoji = {
                    'passed': '✅',
                    'failed': '❌',
                    'error': '⚠️',
                    'skipped': '⏭️'
                }.get(result.status, '❓')
                
                md += f"### {status_emoji} {result.test_name} ({result.test_class or 'Unknown'})\n\n"
                md += f"- **Status:** {result.status}\n"
                md += f"- **Duration:** {result.duration:.3f}s\n"
                
                if result.steps:
                    md += "\n**Steps:**\n\n"
                    md += "| Step | Status | Duration | Description |\n"
                    md += "|------|--------|----------|-------------|\n"
                    for step in result.steps:
                        desc = step.description.replace('|', '\\|') if step.description else ''
                        md += f"| {step.name} | {step.status} | {step.duration:.3f}s | {desc} |\n"
                    md += "\n"
                
                if result.errors:
                    md += "**Errors:**\n\n"
                    for error in result.errors:
                        md += f"- {error.message}\n"
                    md += "\n"
                
                if result.assertions:
                    md += "**Assertions:**\n\n"
                    for assertion in result.assertions:
                        passed = assertion.get('passed', False)
                        emoji = '✅' if passed else '❌'
                        md += f"- {emoji} {assertion.get('message', '')}\n"
                    md += "\n"
                
                if result.screenshots:
                    md += "**Screenshots:**\n\n"
                    for screenshot in result.screenshots:
                        md += f"- ![Screenshot]({screenshot})\n"
                    md += "\n"
                
                md += "---\n\n"
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(md)
        
        return filepath
    
    def generate_all_reports(self, base_filename: str = None) -> Dict[str, Path]:
        """Generate all report formats."""
        if base_filename is None:
            base_filename = f"test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        return {
            'json': self.generate_json_report(f"{base_filename}.json"),
            'junit': self.generate_junit_xml(f"{base_filename}.xml"),
            'html': self.generate_html_report(f"{base_filename}.html"),
            'markdown': self.generate_markdown_report(f"{base_filename}.md")
        }
    
    def get_summary(self) -> Dict[str, Any]:
        """Get overall summary statistics."""
        total_tests = sum(s.total_tests for s in self.suite_results)
        total_passed = sum(s.passed_tests for s in self.suite_results)
        total_failed = sum(s.failed_tests for s in self.suite_results)
        total_skipped = sum(s.skipped_tests for s in self.suite_results)
        total_errors = sum(s.error_tests for s in self.suite_results)
        total_duration = sum(s.duration for s in self.suite_results)
        
        return {
            'total_tests': total_tests,
            'passed': total_passed,
            'failed': total_failed,
            'skipped': total_skipped,
            'errors': total_errors,
            'duration': total_duration,
            'pass_rate': (total_passed / total_tests * 100) if total_tests > 0 else 0,
            'suites': len(self.suite_results)
        }


# Convenience function for quick report generation
def generate_test_report(
    test_results: List[TestResult],
    output_dir: Union[str, Path] = "test_reports",
    suite_name: str = "Browser CDP Tests",
    formats: List[str] = None
) -> Dict[str, Path]:
    """Quick function to generate reports from test results."""
    if formats is None:
        formats = ['json', 'junit', 'html', 'markdown']
    
    reporter = TestReporter(output_dir)
    reporter.start_suite(suite_name)
    
    for result in test_results:
        reporter.add_test_result(result)
    
    reporter.end_suite()
    
    results = {}
    if 'json' in formats:
        results['json'] = reporter.generate_json_report()
    if 'junit' in formats:
        results['junit'] = reporter.generate_junit_xml()
    if 'html' in formats:
        results['html'] = reporter.generate_html_report()
    if 'markdown' in formats:
        results['markdown'] = reporter.generate_markdown_report()
    
    return results


__all__ = [
    'TestResult',
    'TestSuiteResult',
    'TestReporter',
    'generate_test_report',
]
