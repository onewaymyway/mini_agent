"""
测试用例执行器

负责执行单个测试用例，处理浏览器操作和结果验证。
集成 browser-cdp 的真实浏览器管理能力。
"""

import asyncio
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from .models import (
    ActualResult,
    ExpectedResult,
    Step,
    TestCase,
    TestResult,
    TestStatus,
    WebsiteConfig,
)

logger = logging.getLogger(__name__)


class BrowserSession:
    """浏览器会话管理器，封装 browser-cdp 的核心能力"""

    def __init__(self, host: str = "127.0.0.1", port: int = 9222):
        self.host = host
        self.port = port
        self.session = None
        self.tab_id = None
        self._screenshot_dir = "screenshots"
        os.makedirs(self._screenshot_dir, exist_ok=True)

    def connect(self) -> bool:
        """连接到已启动的 Chrome 浏览器"""
        try:
            from src.core.cdp_client import list_tabs, connect_tab
            tabs = list_tabs(self.host, self.port)
            if not tabs:
                logger.error(f"未找到可连接的 tab，请确保 Chrome 已用 --remote-debugging-port={self.port} 启动")
                return False
            target = tabs[0]
            self.tab_id = target["id"]
            self.session = connect_tab(target, host=self.host, port=self.port)
            # 启用常用 domain
            for domain in ("Page", "DOM", "Runtime", "Network"):
                try:
                    self.session.send(f"{domain}.enable")
                except Exception:
                    pass
            logger.info(f"已连接到 tab: {self.tab_id}")
            return True
        except Exception as e:
            logger.error(f"连接浏览器失败: {e}")
            return False

    def navigate(self, url: str, timeout: float = 30.0) -> Dict[str, Any]:
        """导航到 URL"""
        try:
            from src.core.browser_nav import cmd_goto
            cmd_goto(
                self.session,
                url,
                wait_load=True,
                timeout=timeout,
                wait_for="networkidle",
                enable_stealth=True,
                smart_wait=True,
                tab_id=self.tab_id,
            )
            current_url = self.session.eval_js("location.href")
            title = self.session.eval_js("document.title")
            return {"success": True, "url": current_url, "title": title}
        except Exception as e:
            logger.error(f"导航失败: {e}")
            return {"success": False, "error": str(e)}

    def click(self, selector: str, timeout: float = 10.0) -> Dict[str, Any]:
        """点击元素"""
        try:
            from src.core.browser_input import click_selector
            click_selector(self.session, selector)
            return {"success": True}
        except Exception as e:
            logger.error(f"点击失败: {e}")
            return {"success": False, "error": str(e)}

    def input_text(self, selector: str, text: str, clear_first: bool = True) -> Dict[str, Any]:
        """输入文本"""
        try:
            from src.core.browser_input import type_text, click_selector
            click_selector(self.session, selector)
            if clear_first:
                self.session.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a", "modifiers": 2})
                self.session.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a", "modifiers": 2})
                from src.core.browser_input import dispatch_key
                dispatch_key(self.session, "Backspace")
            type_text(self.session, text)
            return {"success": True}
        except Exception as e:
            logger.error(f"输入失败: {e}")
            return {"success": False, "error": str(e)}

    def wait(self, selector: Optional[str] = None, timeout: float = 10.0) -> Dict[str, Any]:
        """等待元素出现或固定时间"""
        try:
            if selector:
                from src.core.browser_nav import wait_element
                result = wait_element(self.session, selector, timeout)
                return {"success": result}
            else:
                time.sleep(min(timeout, 5))
                return {"success": True}
        except Exception as e:
            logger.error(f"等待失败: {e}")
            return {"success": False, "error": str(e)}

    def scroll(self, direction: str = "bottom", amount: int = 500) -> Dict[str, Any]:
        """滚动页面"""
        try:
            from src.core.browser_input import scroll
            scroll(self.session, direction, amount)
            return {"success": True}
        except Exception as e:
            logger.error(f"滚动失败: {e}")
            return {"success": False, "error": str(e)}

    def screenshot(self, name: str) -> str:
        """截取屏幕截图"""
        try:
            from src.core.browser_screenshot import capture, save_screenshot
            png_bytes = capture(self.session, full_page=False)
            screenshot_path = os.path.join(self._screenshot_dir, f"{name}.png")
            save_screenshot(png_bytes, screenshot_path)
            logger.info(f"截图已保存: {screenshot_path}")
            return screenshot_path
        except Exception as e:
            logger.error(f"截图失败: {e}")
            return ""

    def get_page_info(self) -> Dict[str, Any]:
        """获取页面信息"""
        try:
            url = self.session.eval_js("location.href")
            title = self.session.eval_js("document.title")
            ready_state = self.session.eval_js("document.readyState")
            return {"url": url, "title": title, "readyState": ready_state}
        except Exception as e:
            return {"error": str(e)}

    def get_elements(self, selector: str) -> List[Dict]:
        """获取匹配的元素列表"""
        try:
            js = f"""
            () => {{
                const elements = document.querySelectorAll({selector!r});
                return Array.from(elements).map(el => {{
                    const rect = el.getBoundingClientRect();
                    return {{
                        tagName: el.tagName,
                        text: (el.innerText || el.textContent || '').trim().slice(0, 100),
                        rect: {{x: rect.x, y: rect.y, width: rect.width, height: rect.height}},
                        visible: rect.width > 0 && rect.height > 0
                    }};
                }});
            }}
            """
            return self.session.eval_js(js) or []
        except Exception as e:
            logger.error(f"获取元素失败: {e}")
            return []

    def close(self):
        """关闭会话"""
        if self.session:
            try:
                self.session.close()
            except Exception:
                pass
            self.session = None


class TestCaseExecutor:
    """测试用例执行器"""

    def __init__(self, config: WebsiteConfig, browser_host: str = "127.0.0.1", browser_port: int = 9222):
        self.config = config
        self.browser = BrowserSession(browser_host, browser_port)
        self._logs: List[str] = []
        self._screenshots: List[str] = []
        self._start_time = 0

    def _log(self, message: str) -> None:
        """记录日志"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] {message}"
        self._logs.append(log_entry)
        logger.info(log_entry)

    def _take_screenshot(self, name: str) -> str:
        """截取屏幕截图"""
        screenshot_path = self.browser.screenshot(f"{self.config.name}_{name}_{int(time.time())}")
        if screenshot_path:
            self._screenshots.append(screenshot_path)
        return screenshot_path

    async def execute(self, test_case: TestCase) -> TestResult:
        """
        执行测试用例

        Args:
            test_case: 测试用例

        Returns:
            TestResult: 测试结果
        """
        run_id = f"{self.config.name}_{test_case.case_id}_{int(time.time())}"
        result = TestResult(
            run_id=run_id,
            website_name=self.config.name,
            case_id=test_case.case_id,
        )

        self._log(f"开始执行用例: {test_case.name}")
        test_case.status = TestStatus.RUNNING
        test_case.started_at = datetime.now()
        self._start_time = time.time()

        # 连接浏览器
        if not self.browser.connect():
            result.mark_fail("无法连接到浏览器")
            result.duration = time.time() - self._start_time
            result.logs = self._logs
            result.screenshots = self._screenshots
            return result

        try:
            # 执行测试步骤
            for i, step in enumerate(test_case.steps):
                self._log(f"执行步骤 {i+1}/{len(test_case.steps)}: {step.action} - {step.target}")

                step_result = await self._execute_step(step)
                if not step_result["success"]:
                    error_msg = step_result.get("error", "步骤执行失败")
                    self._take_screenshot(f"step_{i+1}_fail")
                    result.mark_fail(error_msg)
                    result.duration = time.time() - self._start_time
                    result.logs = self._logs
                    result.screenshots = self._screenshots
                    return result

            # 验证预期结果
            for expected in test_case.expected_results:
                actual = await self._verify_expected(expected)
                result.actual_results.append(actual.__dict__)

            # 计算评估指标
            result.metrics = self._calculate_metrics(test_case)

            # 判断是否通过
            if self._check_pass_criteria(result.metrics, test_case.pass_criteria):
                result.mark_pass()
                self._log(f"用例通过: {test_case.name}")
            else:
                result.mark_fail("未通过评估标准")
                self._log(f"用例失败: {test_case.name}")

        except Exception as e:
            result.mark_fail(str(e))
            self._log(f"执行异常: {e}")
            self._take_screenshot("exception")

        finally:
            result.duration = time.time() - self._start_time
            result.logs = self._logs
            result.screenshots = self._screenshots
            test_case.completed_at = datetime.now()
            self.browser.close()

        return result

    async def _execute_step(self, step: Step) -> Dict[str, Any]:
        """
        执行单个步骤

        Args:
            step: 测试步骤

        Returns:
            执行结果
        """
        try:
            if step.action == "navigate":
                return await asyncio.to_thread(self.browser.navigate, step.target, step.timeout)
            elif step.action == "click":
                return await asyncio.to_thread(self.browser.click, step.target)
            elif step.action == "input":
                return await asyncio.to_thread(self.browser.input_text, step.target, step.value or "")
            elif step.action == "wait":
                return await asyncio.to_thread(self.browser.wait, step.target, step.timeout)
            elif step.action == "scroll":
                direction = step.target if step.target in ("top", "bottom") else "bottom"
                return await asyncio.to_thread(self.browser.scroll, direction)
            else:
                return {"success": False, "error": f"未知动作: {step.action}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _verify_expected(self, expected: ExpectedResult) -> ActualResult:
        """
        验证预期结果

        Args:
            expected: 预期结果

        Returns:
            ActualResult: 实际结果
        """
        try:
            # 解析条件
            if expected.condition.startswith("contains:"):
                selector = expected.condition.replace("contains:", "").strip()
                elements = self.browser.get_elements(selector)
                actual_value = len(elements)
                passed = actual_value > 0
                return ActualResult(
                    condition=expected.condition,
                    actual_value=actual_value,
                    passed=passed,
                )
            elif expected.condition.startswith("equals:"):
                selector = expected.condition.replace("equals:", "").strip()
                elements = self.browser.get_elements(selector)
                if elements:
                    actual_value = elements[0].get("text", "")
                else:
                    actual_value = ""
                passed = actual_value == expected.expected_value
                return ActualResult(
                    condition=expected.condition,
                    actual_value=actual_value,
                    passed=passed,
                )
            elif expected.condition.startswith("url_contains:"):
                current_url = self.browser.get_page_info().get("url", "")
                passed = expected.expected_value in current_url
                return ActualResult(
                    condition=expected.condition,
                    actual_value=current_url,
                    passed=passed,
                )
            elif expected.condition.startswith("title_contains:"):
                current_title = self.browser.get_page_info().get("title", "")
                passed = expected.expected_value in current_title
                return ActualResult(
                    condition=expected.condition,
                    actual_value=current_title,
                    passed=passed,
                )
            else:
                # 默认：检查元素是否存在
                elements = self.browser.get_elements(expected.condition)
                passed = len(elements) > 0
                return ActualResult(
                    condition=expected.condition,
                    actual_value=len(elements),
                    passed=passed,
                )
        except Exception as e:
            return ActualResult(
                condition=expected.condition,
                actual_value=None,
                passed=False,
                error_message=str(e),
            )

    def _calculate_metrics(self, test_case: TestCase) -> Dict[str, float]:
        """
        计算评估指标

        Args:
            test_case: 测试用例

        Returns:
            评估指标字典
        """
        metrics = {
            "step_success_rate": 1.0,
            "element_locate_accuracy": 1.0,
            "data_extraction_success_rate": 1.0,
            "page_access_success_rate": 1.0,
            "stability": 1.0,
            "anti_detection_ability": 1.0,
        }

        # 根据实际执行结果计算指标
        if test_case.steps:
            # 步骤成功率（这里假设所有步骤都成功执行）
            metrics["step_success_rate"] = 1.0

        # 元素定位准确率
        if test_case.expected_results:
            passed_count = sum(
                1 for ar in test_case.actual_results if ar.get("passed", False)
            )
            metrics["element_locate_accuracy"] = passed_count / len(test_case.expected_results)

        # 数据提取成功率
        metrics["data_extraction_success_rate"] = metrics["element_locate_accuracy"]

        return metrics

    def _check_pass_criteria(
        self,
        metrics: Dict[str, float],
        pass_criteria: Dict[str, float],
    ) -> bool:
        """
        检查是否通过评估标准

        Args:
            metrics: 评估指标
            pass_criteria: 通过标准

        Returns:
            是否通过
        """
        if not pass_criteria:
            return True

        for metric_name, threshold in pass_criteria.items():
            actual_value = metrics.get(metric_name, 0.0)
            if actual_value < threshold:
                return False

        return True

    def get_logs(self) -> List[str]:
        """获取执行日志"""
        return self._logs.copy()

    def get_screenshots(self) -> List[str]:
        """获取截图列表"""
        return self._screenshots.copy()


class BrowserManager:
    """浏览器管理器，用于管理多个测试执行器的浏览器会话"""

    def __init__(self, host: str = "127.0.0.1", port: int = 9222):
        self.host = host
        self.port = port
        self._sessions: Dict[str, BrowserSession] = {}

    def get_session(self, config_name: str) -> BrowserSession:
        """获取或创建浏览器会话"""
        if config_name not in self._sessions:
            self._sessions[config_name] = BrowserSession(self.host, self.port)
        return self._sessions[config_name]

    def close_all(self):
        """关闭所有会话"""
        for session in self._sessions.values():
            session.close()
        self._sessions.clear()
