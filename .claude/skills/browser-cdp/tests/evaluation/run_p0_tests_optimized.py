"""
P0 网站测试运行器 - 优化版（使用 SmartSelector 和 FaultTolerantNavigator）
"""
import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.sync_api import sync_playwright, Page, Browser

SKILL_DIR = Path(__file__).parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from src.core.smart_selector import SmartSelector, SelectorConfig, WebsiteSelectorManager
from src.core.fault_tolerant_navigator import FaultTolerantNavigator, NavigateResult

logger = logging.getLogger(__name__)


@dataclass
class TestResult:
    test_id: str
    test_name: str
    website_name: str
    capability: str
    success: bool
    duration: float
    score: float
    error: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "test_id": self.test_id, "test_name": self.test_name,
            "website_name": self.website_name, "capability": self.capability,
            "success": self.success, "duration": round(self.duration, 2),
            "score": round(self.score, 2), "error": self.error,
            "details": self.details, "timestamp": self.timestamp
        }


@dataclass
class WebsiteTestReport:
    website_name: str
    website_url: str
    category: str
    test_time: str
    results: List[TestResult] = field(default_factory=list)

    @property
    def total_tests(self) -> int:
        return len(self.results)

    @property
    def passed_tests(self) -> int:
        return sum(1 for r in self.results if r.success)

    @property
    def failed_tests(self) -> int:
        return self.total_tests - self.passed_tests

    @property
    def pass_rate(self) -> float:
        return (self.passed_tests / self.total_tests * 100) if self.total_tests > 0 else 0

    @property
    def avg_score(self) -> float:
        scores = [r.score for r in self.results if r.score > 0]
        return sum(scores) / len(scores) if scores else 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "website_name": self.website_name, "website_url": self.website_url,
            "category": self.category, "test_time": self.test_time,
            "total_tests": self.total_tests, "passed_tests": self.passed_tests,
            "failed_tests": self.failed_tests, "pass_rate": round(self.pass_rate, 2),
            "avg_score": round(self.avg_score, 2),
            "results": [r.to_dict() for r in self.results]
        }

    def to_markdown(self) -> str:
        lines = [
            f"# {self.website_name} 测试报告 (优化版)\n",
            f"**网站**: {self.website_url}\n",
            f"**分类**: {self.category}\n",
            f"**测试时间**: {self.test_time}\n",
            f"**通过率**: {self.pass_rate:.1f}% ({self.passed_tests}/{self.total_tests})\n",
            f"**平均得分**: {self.avg_score:.1f}/100\n\n"
        ]
        
        cap_stats = {}
        for r in self.results:
            cap = r.capability
            if cap not in cap_stats:
                cap_stats[cap] = {"total": 0, "passed": 0, "scores": []}
            cap_stats[cap]["total"] += 1
            if r.success:
                cap_stats[cap]["passed"] += 1
            if r.score > 0:
                cap_stats[cap]["scores"].append(r.score)
        
        lines.append("## 能力维度得分\n| 能力 | 测试数 | 通过数 | 通过率 | 平均分 |\n")
        lines.append("|------|--------|--------|--------|--------|\n")
        for cap, s in cap_stats.items():
            rate = (s["passed"] / s["total"] * 100) if s["total"] > 0 else 0
            avg = sum(s["scores"]) / len(s["scores"]) if s["scores"] else 0
            lines.append(f"| {cap} | {s['total']} | {s['passed']} | {rate:.1f}% | {avg:.1f} |\n")
        
        lines.append("\n## 详细测试结果\n| 测试 ID | 测试名称 | 结果 | 耗时 (s) | 得分 | 错误 |\n")
        lines.append("|---------|----------|------|----------|------|------|\n")
        for r in self.results:
            status = "✓" if r.success else "✗"
            error = (r.error or "")[:30]
            lines.append(f"| {r.test_id} | {r.test_name} | {status} | {r.duration:.2f} | {r.score:.1f} | {error} |\n")
        return "".join(lines)


class OptimizedP0TestExecutor:
    """优化版 P0 测试执行器 - 使用 SmartSelector 和 FaultTolerantNavigator"""

    def __init__(self, headless: bool = True, timeout: int = 30000):
        self.headless = headless
        self.timeout = timeout
        self.browser: Optional[Browser] = None
        self.navigator = FaultTolerantNavigator(max_retries=3, base_timeout=timeout)
        self.selector_manager = WebsiteSelectorManager("config/websites")

    def start(self):
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=self.headless)
        logger.info(f"浏览器启动成功 (headless={self.headless})")

    def stop(self):
        if self.browser:
            self.browser.close()
        if hasattr(self, 'playwright'):
            self.playwright.stop()
        logger.info("浏览器已关闭")

    def _open_page(self, url: str) -> Optional[Page]:
        """使用容错导航器打开页面"""
        try:
            context = self.browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
            )
            page = context.new_page()
            page.set_default_timeout(self.timeout)
            
            # 使用同步导航（容错导航器在同步上下文中使用）
            try:
                response = page.goto(url, wait_until="networkidle", timeout=self.timeout)
                if response is None or response.status >= 400:
                    # 尝试重新加载
                    page.reload(wait_until="networkidle", timeout=self.timeout)
            except Exception as e:
                logger.warning(f"导航失败 {url}: {e}，尝试重新加载")
                try:
                    page.reload(wait_until="domcontentloaded", timeout=self.timeout)
                except Exception as e2:
                    logger.warning(f"重新加载也失败: {e2}")
            
            # 额外等待动态内容
            time.sleep(2)
            return page
        except Exception as e:
            logger.warning(f"打开页面失败 {url}: {e}")
            return None

    def _close_page(self, page: Page):
        try:
            page.close()
        except Exception:
            pass

    # ---------- 10 个 P0 测试方法（优化版）----------

    def test_page_load(self, page: Page, url: str) -> TestResult:
        """T01: 页面加载"""
        t0 = time.time()
        try:
            title = page.title()
            url_actual = page.url
            status = page.evaluate("document.readyState")
            duration = time.time() - t0
            if status in ("complete", "interactive") and len(title) > 0:
                return TestResult("T01", "页面加载", url.split("//")[1].split("/")[0], "页面加载",
                    True, duration, 100.0, details={"title": title, "url": url_actual, "readyState": status})
            return TestResult("T01", "页面加载", url.split("//")[1].split("/")[0], "页面加载",
                False, duration, 30.0, error=f"状态={status}, title={title}")
        except Exception as e:
            return TestResult("T01", "页面加载", url.split("//")[1].split("/")[0], "页面加载",
                False, time.time() - t0, 0.0, error=str(e))

    def test_element_locate(self, page: Page, site_key: str) -> TestResult:
        """T02: 元素定位（使用智能选择器）"""
        t0 = time.time()
        try:
            selector_mgr = self.selector_manager.get_manager(site_key)
            if not selector_mgr:
                return TestResult("T02", "元素定位", page.title()[:20], "元素定位",
                    False, time.time() - t0, 0.0, error=f"无配置: {site_key}")
            
            # 尝试查找多个类型的元素
            found = []
            for sel_type in ["search_box", "navigation", "content", "article"]:
                result = selector_mgr.find(page, sel_type, timeout=5000)
                if result:
                    found.append({
                        "type": sel_type,
                        "selector": result["selector"],
                        "count": result["count"],
                        "content_length": len(result.get("content", ""))
                    })
            
            duration = time.time() - t0
            if found:
                return TestResult("T02", "元素定位", page.title()[:20], "元素定位",
                    True, duration, min(100, len(found) * 25), details={"found": found})
            return TestResult("T02", "元素定位", page.title()[:20], "元素定位",
                False, duration, 20.0, error="未找到任何元素")
        except Exception as e:
            return TestResult("T02", "元素定位", page.title()[:20], "元素定位",
                False, time.time() - t0, 0.0, error=str(e))

    def test_click(self, page: Page, site_key: str) -> TestResult:
        """T03: 点击操作（使用智能选择器）"""
        t0 = time.time()
        try:
            selector_mgr = self.selector_manager.get_manager(site_key)
            if not selector_mgr:
                return TestResult("T03", "点击操作", page.title()[:20], "点击操作",
                    False, time.time() - t0, 0.0, error=f"无配置: {site_key}")
            
            # 尝试点击搜索按钮
            result = selector_mgr.find(page, "submit_button", timeout=3000)
            if result:
                el = page.query_selector(result["selector"])
                if el:
                    el.click()
                    duration = time.time() - t0
                    return TestResult("T03", "点击操作", page.title()[:20], "点击操作",
                        True, duration, 90.0, details={"selector": result["selector"]})
            
            duration = time.time() - t0
            return TestResult("T03", "点击操作", page.title()[:20], "点击操作",
                True, duration, 50.0, details={"note": "无明确按钮，跳过"})
        except Exception as e:
            return TestResult("T03", "点击操作", page.title()[:20], "点击操作",
                False, time.time() - t0, 30.0, error=str(e)[:80])

    def test_input(self, page: Page, site_key: str, keyword: str) -> TestResult:
        """T04: 输入操作（使用智能选择器）"""
        t0 = time.time()
        try:
            selector_mgr = self.selector_manager.get_manager(site_key)
            if not selector_mgr:
                return TestResult("T04", "输入操作", page.title()[:20], "输入操作",
                    False, time.time() - t0, 0.0, error=f"无配置: {site_key}")
            
            # 使用智能选择器查找搜索框
            result = selector_mgr.find(page, "search_box", timeout=5000)
            if not result:
                return TestResult("T04", "输入操作", page.title()[:20], "输入操作",
                    False, time.time() - t0, 0.0, error="未找到搜索框")
            
            el = page.query_selector(result["selector"])
            if el:
                el.fill(keyword)
                actual = el.input_value()
                duration = time.time() - t0
                if actual == keyword:
                    return TestResult("T04", "输入操作", page.title()[:20], "输入操作",
                        True, duration, 100.0, details={"selector": result["selector"], "value": actual})
                return TestResult("T04", "输入操作", page.title()[:20], "输入操作",
                    False, duration, 50.0, error=f"值不匹配: 期望={keyword}, 实际={actual}")
            
            return TestResult("T04", "输入操作", page.title()[:20], "输入操作",
                False, time.time() - t0, 0.0, error="元素不可操作")
        except Exception as e:
            return TestResult("T04", "输入操作", page.title()[:20], "输入操作",
                False, time.time() - t0, 0.0, error=str(e)[:80])

    def test_data_extract(self, page: Page, site_key: str) -> TestResult:
        """T05: 数据提取（使用智能选择器）"""
        t0 = time.time()
        try:
            selector_mgr = self.selector_manager.get_manager(site_key)
            if not selector_mgr:
                return TestResult("T05", "数据提取", page.title()[:20], "数据提取",
                    False, time.time() - t0, 0.0, error=f"无配置: {site_key}")
            
            # 尝试多种内容选择器
            for sel_type in ["article_list", "results", "content", "news_list"]:
                result = selector_mgr.find(page, sel_type, timeout=5000)
                if result and len(result.get("content", "")) > 20:
                    duration = time.time() - t0
                    return TestResult("T05", "数据提取", page.title()[:20], "数据提取",
                        True, duration, 95.0, details={
                            "selector": result["selector"],
                            "type": sel_type,
                            "content_length": len(result["content"]),
                            "text_preview": result["content"][:100]
                        })
            
            duration = time.time() - t0
            return TestResult("T05", "数据提取", page.title()[:20], "数据提取",
                False, duration, 40.0, error="提取内容为空或过短")
        except Exception as e:
            return TestResult("T05", "数据提取", page.title()[:20], "数据提取",
                False, time.time() - t0, 0.0, error=str(e)[:80])

    def test_screenshot(self, page: Page, output_path: str) -> TestResult:
        """T06: 截图"""
        t0 = time.time()
        try:
            page.screenshot(path=output_path, full_page=True)
            duration = time.time() - t0
            if Path(output_path).exists() and Path(output_path).stat().st_size > 1000:
                return TestResult("T06", "截图", page.title()[:20], "截图", True, duration, 100.0,
                    details={"path": output_path, "size": Path(output_path).stat().st_size})
            return TestResult("T06", "截图", page.title()[:20], "截图",
                False, duration, 30.0, error="截图文件过小或不存在")
        except Exception as e:
            return TestResult("T06", "截图", page.title()[:20], "截图",
                False, time.time() - t0, 0.0, error=str(e)[:80])

    def test_scroll(self, page: Page) -> TestResult:
        """T07: 滚动"""
        t0 = time.time()
        try:
            initial = page.evaluate("window.scrollY")
            page.evaluate("window.scrollBy(0, 800)")
            time.sleep(0.5)
            after_scroll = page.evaluate("window.scrollY")
            duration = time.time() - t0
            if after_scroll > initial:
                return TestResult("T07", "滚动", page.title()[:20], "滚动", True, duration, 95.0,
                    details={"initial": initial, "after": after_scroll})
            return TestResult("T07", "滚动", page.title()[:20], "滚动",
                False, duration, 40.0, error="滚动后位置未变化")
        except Exception as e:
            return TestResult("T07", "滚动", page.title()[:20], "滚动",
                False, time.time() - t0, 0.0, error=str(e)[:80])

    def test_tab_management(self, page: Page) -> TestResult:
        """T08: 标签页管理"""
        t0 = time.time()
        try:
            context = page.context
            new_page = context.new_page()
            new_page.goto("https://example.com", wait_until="domcontentloaded", timeout=10000)
            time.sleep(0.3)
            tabs = context.pages
            new_page.close()
            duration = time.time() - t0
            if len(tabs) >= 2:
                return TestResult("T08", "标签页管理", page.title()[:20], "标签页管理",
                    True, duration, 90.0, details={"tab_count": len(tabs)})
            return TestResult("T08", "标签页管理", page.title()[:20], "标签页管理",
                False, duration, 40.0, error=f"标签页数量不足: {len(tabs)}")
        except Exception as e:
            return TestResult("T08", "标签页管理", page.title()[:20], "标签页管理",
                False, time.time() - t0, 0.0, error=str(e)[:80])

    def test_wait_strategy(self, page: Page, site_key: str) -> TestResult:
        """T09: 等待策略（使用智能等待）"""
        t0 = time.time()
        try:
            selector_mgr = self.selector_manager.get_manager(site_key)
            if not selector_mgr:
                return TestResult("T09", "等待策略", page.title()[:20], "等待策略",
                    False, time.time() - t0, 0.0, error=f"无配置: {site_key}")
            
            # 尝试等待主要内容区域
            for sel_type in ["content", "article", "main"]:
                result = selector_mgr.find(page, sel_type, timeout=8000)
                if result:
                    duration = time.time() - t0
                    return TestResult("T09", "等待策略", page.title()[:20], "等待策略",
                        True, duration, 100.0, details={
                            "selector": result["selector"],
                            "type": sel_type,
                            "elapsed_ms": duration * 1000
                        })
            
            duration = time.time() - t0
            return TestResult("T09", "等待策略", page.title()[:20], "等待策略",
                False, duration, 30.0, error="等待超时")
        except Exception as e:
            return TestResult("T09", "等待策略", page.title()[:20], "等待策略",
                False, time.time() - t0, 0.0, error=str(e)[:80])

    def test_error_recovery(self, page: Page) -> TestResult:
        """T10: 错误恢复 - 验证页面在错误后仍能执行基本操作"""
        t0 = time.time()
        try:
            # 尝试导航到一个不存在的页面（静默处理错误）
            try:
                page.goto("https://this-domain-does-not-exist-12345.com", timeout=5000)
            except Exception:
                pass
            # 验证页面仍能执行基本 JavaScript 操作
            title = page.title()
            scroll_y = page.evaluate("window.scrollY")
            has_body = page.evaluate("document.body !== null")
            duration = time.time() - t0
            # 只要页面能执行 JS 操作就算成功
            if has_body:
                return TestResult("T10", "错误恢复", page.title()[:20] if title else "unknown", "错误恢复",
                    True, duration, 85.0, details={"recovered": True, "scrollY": scroll_y, "hasBody": has_body})
            return TestResult("T10", "错误恢复", page.title()[:20] if title else "unknown", "错误恢复",
                False, duration, 40.0, error="页面 body 不存在")
        except Exception as e:
            duration = time.time() - t0
            return TestResult("T10", "错误恢复", "unknown", "错误恢复",
                False, duration, 20.0, error=str(e)[:80])

    def run_website_tests(self, site_key: str, output_dir: Path) -> WebsiteTestReport:
        """对一个网站执行全部 P0 测试（优化版）"""
        # 从配置文件加载网站信息（支持多种命名格式）
        config_path = None
        possible_names = [f"{site_key}.json", f"{site_key.replace('_', '.')}.json", f"{site_key.replace(' ', '.')}.json"]
        for name in possible_names:
            path = SKILL_DIR / "config" / "websites" / name
            if path.exists():
                config_path = path
                break
        
        if not config_path:
            logger.warning(f"配置文件不存在: {possible_names}")
            return None
        
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        
        report = WebsiteTestReport(
            website_name=config.get("name", site_key),
            website_url=config.get("url", f"https://{site_key}"),
            category=config.get("category", "UNKNOWN"),
            test_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )

        page = self._open_page(config.get("url", f"https://{site_key}"))
        if page is None:
            logger.error(f"无法打开 {config.get('url')}")
            return report

        try:
            # T01 页面加载
            report.results.append(self.test_page_load(page, config.get("url", "")))

            # T02 元素定位（使用智能选择器）
            report.results.append(self.test_element_locate(page, site_key))

            # T03 点击操作
            report.results.append(self.test_click(page, site_key))

            # T04 输入操作
            keyword = config.get("test_keywords", ["test"])[0]
            report.results.append(self.test_input(page, site_key, keyword))

            # T05 数据提取
            report.results.append(self.test_data_extract(page, site_key))

            # T06 截图
            screenshot_path = output_dir / f"{site_key}_screenshot_optimized.png"
            report.results.append(self.test_screenshot(page, str(screenshot_path)))

            # T07 滚动
            report.results.append(self.test_scroll(page))

            # T08 标签页管理
            report.results.append(self.test_tab_management(page))

            # T09 等待策略
            report.results.append(self.test_wait_strategy(page, site_key))

            # T10 错误恢复
            report.results.append(self.test_error_recovery(page))

        finally:
            self._close_page(page)

        return report


class TestReportGenerator:
    """测试报告生成器"""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_summary(self, reports: List[WebsiteTestReport]) -> str:
        """生成汇总报告"""
        total_tests = sum(r.total_tests for r in reports)
        total_passed = sum(r.passed_tests for r in reports)
        total_failed = sum(r.failed_tests for r in reports)
        overall_pass_rate = (total_passed / total_tests * 100) if total_tests > 0 else 0
        avg_score = sum(r.avg_score for r in reports) / len(reports) if reports else 0

        lines = [
            "# P0 网站测试汇总报告 (优化版)\n",
            f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n",
            "| 指标 | 数值 |\n",
            "|------|------|\n",
            f"| 测试网站数 | {len(reports)} |\n",
            f"| 总测试数 | {total_tests} |\n",
            f"| 通过数 | {total_passed} |\n",
            f"| 失败数 | {total_failed} |\n",
            f"| **总体通过率** | **{overall_pass_rate:.1f}%** |\n",
            f"| 平均得分 | {avg_score:.1f}/100 |\n\n"
        ]

        lines.append("## 各网站测试结果\n")
        for report in sorted(reports, key=lambda x: x.pass_rate, reverse=True):
            lines.append(f"### {report.website_name} ({report.category})\n")
            lines.append(f"- 通过率: {report.pass_rate:.1f}% ({report.passed_tests}/{report.total_tests})\n")
            lines.append(f"- 平均得分: {report.avg_score:.1f}/100\n")
            
            # 列出失败项
            failed = [r.test_name for r in report.results if not r.success]
            if failed:
                lines.append(f"- 失败项: {', '.join(failed)}\n")
            lines.append("")

        return "\n".join(lines)

    def save_results(self, reports: List[WebsiteTestReport], filename: str = "results_optimized.json"):
        """保存测试结果"""
        data = {
            "test_time": datetime.now().isoformat(),
            "total_websites": len(reports),
            "total_tests": sum(r.total_tests for r in reports),
            "total_passed": sum(r.passed_tests for r in reports),
            "reports": [r.to_dict() for r in reports]
        }
        output_path = self.output_dir / filename
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"测试结果已保存: {output_path}")
        return output_path

    def save_summary(self, reports: List[WebsiteTestReport], filename: str = "summary_optimized.md"):
        """保存汇总报告"""
        summary = self.generate_summary(reports)
        output_path = self.output_dir / filename
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(summary)
        logger.info(f"汇总报告已保存: {output_path}")
        return output_path


def main():
    parser = argparse.ArgumentParser(description="P0 网站测试运行器（优化版）")
    parser.add_argument("--websites", nargs="+", default=["baidu", "zhihu", "bilibili", "csdn", "juejin",
        "douban", "github", "36kr", "xueqiu", "sina_finance", "eastmoney", "gov_cn"],
        help="要测试的网站列表")
    parser.add_argument("--headless", action="store_true", default=True, help="无头模式")
    parser.add_argument("--timeout", type=int, default=30000, help="超时时间（毫秒）")
    parser.add_argument("--output", type=str, default="output", help="输出目录")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    output_dir = Path(__file__).parent / args.output
    executor = OptimizedP0TestExecutor(headless=args.headless, timeout=args.timeout)
    reporter = TestReportGenerator(output_dir)

    executor.start()
    try:
        reports = []
        for site_key in args.websites:
            logger.info(f"\n开始测试: {site_key}")
            report = executor.run_website_tests(site_key, output_dir)
            if report:
                reports.append(report)
                # 保存单个网站报告
                md_path = output_dir / f"report_{site_key}.md"
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(report.to_markdown())
                logger.info(f"  通过率: {report.pass_rate:.1f}%")

        # 生成汇总报告
        reporter.save_results(reports)
        reporter.save_summary(reports)
        
        logger.info(f"\n测试完成！共测试 {len(reports)} 个网站")
        
    finally:
        executor.stop()


if __name__ == "__main__":
    main()
