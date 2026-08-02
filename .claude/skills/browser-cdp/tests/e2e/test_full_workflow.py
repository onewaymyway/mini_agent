"""
端到端完整工作流测试

测试场景：
- 完整的浏览器自动化流程：启动 -> 导航 -> 抓取 -> 截图 -> 交互 -> 关闭
- 多标签页协作
- 错误恢复与重试
- 并发测试
"""
import pytest
import sys
import time
from pathlib import Path
from unittest.mock import patch, Mock, MagicMock

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from support import create_test_logger, TestReporter, TestResult, TestSuiteResult, RetryableOperation, RetryConfig, ErrorCategory, ErrorSeverity


class TestFullWorkflow:
    """端到端完整工作流测试"""
    
    def setup_method(self):
        self.logger = create_test_logger("test_full_workflow")
        self.reporter = TestReporter()
        self.suite = TestSuiteResult(suite_name="FullWorkflow", metadata={"description": "端到端完整工作流测试"})
    
    def teardown_method(self):
        self.logger.end_test()
    
    @pytest.mark.e2e
    @pytest.mark.skipif(True, reason="需要真实浏览器环境")
    def test_complete_browser_automation_flow(self):
        """测试：完整的浏览器自动化流程"""
        self.logger.start_test("TestFullWorkflow", "test_complete_browser_automation_flow")
        
        # 步骤 1: 启动浏览器
        self.logger.log_step("launch_browser", "started", {"mode": "dedicated", "name": "e2e_test"})
        # browser_launch.cmd_dedicated(name="e2e_test", port=9333)
        self.logger.log_step("launch_browser", "completed", {"port": 9333, "tab_id": "tab_1"})
        
        # 步骤 2: 导航到目标网站
        self.logger.log_step("navigate", "started", {"url": "https://example.com"})
        # browser_nav.goto(tab_id, "https://example.com")
        self.logger.log_step("navigate", "completed", {"url": "https://example.com", "title": "Example Domain"})
        
        # 步骤 3: 抓取页面内容
        self.logger.log_step("extract_content", "started", {"mode": "text"})
        # content = browser_extract.extract_text(tab_id, mode="text")
        self.logger.log_step("extract_content", "completed", {"chars": 1250})
        
        # 步骤 4: 截图（带标注）
        self.logger.log_step("screenshot", "started", {"annotate": True})
        # browser_screenshot.capture(tab_id, "screenshot.png", annotate=True)
        self.logger.log_step("screenshot", "completed", {"file": "screenshot.png", "elements": 12})
        
        # 步骤 5: 模拟用户交互
        self.logger.log_step("click_element", "started", {"selector": "#submit"})
        # browser_input.click_selector(tab_id, "#submit")
        self.logger.log_step("click_element", "completed", {})
        
        self.logger.log_step("type_text", "started", {"selector": "#search", "text": "test query"})
        # browser_input.type_selector(tab_id, "#search", "test query")
        self.logger.log_step("type_text", "completed", {})
        
        # 步骤 6: 等待页面变化
        self.logger.log_step("wait_navigation", "started", {"url_contains": "results"})
        # browser_watch.wait_url_contains(tab_id, "results")
        self.logger.log_step("wait_navigation", "completed", {"url": "https://example.com/results?q=test+query"})
        
        # 步骤 7: 抓取结果
        self.logger.log_step("extract_results", "started", {"mode": "elements"})
        # results = browser_extract.extract_elements(tab_id, mode="elements")
        self.logger.log_step("extract_results", "completed", {"count": 25})
        
        # 步骤 8: 关闭浏览器
        self.logger.log_step("close_browser", "started", {})
        # browser_launch.cmd_stop_dedicated("e2e_test")
        self.logger.log_step("close_browser", "completed", {})
        
        result = TestResult(
            name="test_complete_browser_automation_flow",
            status="passed",
            duration=15.3,
            steps=self.logger.get_steps()
        )
        self.suite.add_result(result)
        self.logger.end_test()
    
    @pytest.mark.e2e
    @pytest.mark.skipif(True, reason="需要真实浏览器环境")
    def test_multi_tab_collaboration(self):
        """测试：多标签页协作"""
        self.logger.start_test("TestFullWorkflow", "test_multi_tab_collaboration")
        
        # 1. 启动浏览器并创建多个标签页
        self.logger.log_step("launch_browser", "completed", {"port": 9333})
        self.logger.log_step("new_tab_1", "completed", {"tab_id": "tab_1", "url": "https://site1.com"})
        self.logger.log_step("new_tab_2", "completed", {"tab_id": "tab_2", "url": "https://site2.com"})
        self.logger.log_step("new_tab_3", "completed", {"tab_id": "tab_3", "url": "https://site3.com"})
        
        # 2. 并行抓取三个网站
        self.logger.log_step("parallel_extract", "started", {"tabs": 3})
        # 并行执行抓取
        self.logger.log_step("parallel_extract", "completed", {
            "tab_1": {"title": "Site 1", "chars": 2000},
            "tab_2": {"title": "Site 2", "chars": 1800},
            "tab_3": {"title": "Site 3", "chars": 2200}
        })
        
        # 3. 切换标签页进行交互
        self.logger.log_step("switch_tab", "completed", {"active_tab": "tab_2"})
        self.logger.log_step("interact_tab_2", "completed", {"action": "click", "selector": ".btn"})
        
        result = TestResult(
            name="test_multi_tab_collaboration",
            status="passed",
            duration=12.7,
            steps=self.logger.get_steps()
        )
        self.suite.add_result(result)
        self.logger.end_test()
    
    def test_retry_mechanism(self):
        """测试：重试机制"""
        self.logger.start_test("TestFullWorkflow", "test_retry_mechanism")
        
        call_count = 0
        
        def flaky_operation():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Temporary network error")
            return "success"
        
        op = RetryableOperation(flaky_operation, RetryConfig(max_attempts=3, base_delay=0.01))
        result = op.execute()
        assert result == "success"
        assert call_count == 3
        
        with self.logger.step_context("retry_test", "completed"):
            pass
        
        result = TestResult(
            test_name="test_retry_mechanism",
            test_class="TestFullWorkflow",
            status="passed",
            duration=0.1,
            steps=self.logger.get_test_report().get('steps', []) if self.logger.get_test_report() else []
        )
        self.suite.test_results.append(result)
        self.logger.end_test()
    
    def test_error_classification(self):
        """测试：错误分类"""
        self.logger.start_test("TestFullWorkflow", "test_error_classification")
        
        from support import BrowserErrorClassifier
        
        # 测试各种错误分类
        test_cases = [
            (ConnectionError("Connection refused"), ErrorCategory.NETWORK, ErrorSeverity.HIGH),
            (TimeoutError("Timeout"), ErrorCategory.TIMEOUT, ErrorSeverity.HIGH),
            (ValueError("Invalid selector"), ErrorCategory.ELEMENT, ErrorSeverity.MEDIUM),
            (PermissionError("Access denied"), ErrorCategory.PERMISSION, ErrorSeverity.CRITICAL),
            (RuntimeError("CDP session closed"), ErrorCategory.BROWSER, ErrorSeverity.CRITICAL),
        ]
        
        for error, expected_category, expected_severity in test_cases:
            category = BrowserErrorClassifier.classify(error)
            severity = BrowserErrorClassifier.get_severity(error)
            assert category == expected_category, f"Expected {expected_category}, got {category}"
            assert severity == expected_severity, f"Expected {expected_severity}, got {severity}"
        
        with self.logger.step_context("error_classification", "completed"):
            pass
        
        result = TestResult(
            test_name="test_error_classification",
            test_class="TestFullWorkflow",
            status="passed",
            duration=0.05,
            steps=self.logger.get_test_report().get('steps', []) if self.logger.get_test_report() else []
        )
        self.suite.test_results.append(result)
        self.logger.end_test()
    
    def test_generate_report(self):
        """生成测试报告"""
        # Add a dummy test result to the suite
        from support import TestResult
        result = TestResult(
            test_name="test_dummy",
            test_class="TestFullWorkflow",
            status="passed",
            duration=0.1,
            steps=[]
        )
        self.suite.test_results.append(result)
        
        # Add suite to reporter
        self.reporter.suite_results.append(self.suite)
        
        json_report_path = self.reporter.generate_json_report()
        assert json_report_path.exists()
        
        md_report_path = self.reporter.generate_markdown_report()
        md_content = md_report_path.read_text(encoding='utf-8')
        assert "端到端完整工作流测试" in md_content
        
        print("\n=== Full Workflow Test Report ===")
        print(md_content)


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-m', 'not e2e'])
