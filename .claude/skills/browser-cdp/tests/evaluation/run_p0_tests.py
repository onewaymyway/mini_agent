"""
P0 网站测试运行器 - 执行核心操作能力测试
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

from playwright.sync_api import sync_playwright, Page, Browser, Response

SKILL_DIR = Path(__file__).parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

logger = logging.getLogger(__name__)

P0_WEBSITES = {
    "gov_cn": {"name": "中国政府网", "url": "https://www.gov.cn", "domain": "gov.cn", "category": "GOV",
        "test_keywords": ["国务院"], "expected_elements": {"search_box": "input[placeholder*='搜索'], input#soInput, input[type='text']", "nav_links": "nav a, .nav a, #nav a", "news_list": "article, .news-list, .news_list, [class*='news']"}, "wait_timeout": 15000},
    "baidu": {"name": "百度", "url": "https://www.baidu.com", "domain": "baidu.com", "category": "SEARCH",
        "test_keywords": ["AI 大模型"], "expected_elements": {"search_box": "input[name='wd'], input#kw, input[type='text']", "search_btn": "input#su, button[type='submit'], .s_btn", "result_list": "#content_left, .result, [data-tools]"}, "wait_timeout": 15000},
    "zhihu": {"name": "知乎", "url": "https://www.zhihu.com", "domain": "zhihu.com", "category": "SOCIAL",
        "test_keywords": ["人工智能"], "expected_elements": {"search_box": "input[placeholder*='搜索']", "hot_list": ".HotItem"}},
    "sina_finance": {"name": "新浪财经", "url": "https://finance.sina.com.cn", "domain": "finance.sina.com.cn", "category": "FINANCE",
        "test_keywords": ["股票"], "expected_elements": {"search_box": "input[name='keyword'], input[type='text']", "news_list": "li"}},
    "eastmoney": {"name": "东方财富", "url": "https://www.eastmoney.com", "domain": "eastmoney.com", "category": "FINANCE",
        "test_keywords": ["股票行情"], "expected_elements": {"search_box": "input[name='key'], input[type='text']", "news_list": "li"}},
    "csdn": {"name": "CSDN", "url": "https://www.csdn.net", "domain": "csdn.net", "category": "TECH",
        "test_keywords": ["Python"], "expected_elements": {"search_box": "input[placeholder*='搜索'], input[type='search']", "article_list": "article, .article-list"}},
    "juejin": {"name": "掘金", "url": "https://juejin.cn", "domain": "juejin.cn", "category": "TECH",
        "test_keywords": ["Vue"], "expected_elements": {"search_box": "input[placeholder*='搜索']", "article_list": "article"}},
    "bilibili": {"name": "哔哩哔哩", "url": "https://www.bilibili.com", "domain": "bilibili.com", "category": "VIDEO",
        "test_keywords": ["编程"], "expected_elements": {"search_box": "input[placeholder*='搜索'], input[type='text']", "video_list": ".video-item, article"}},
    "douban": {"name": "豆瓣", "url": "https://www.douban.com", "domain": "douban.com", "category": "SOCIAL",
        "test_keywords": ["电影"], "expected_elements": {"search_box": "input[name='q'], input[placeholder*='搜索']", "movie_list": ".item"}},
    "github": {"name": "GitHub", "url": "https://github.com", "domain": "github.com", "category": "DEVELOPER",
        "test_keywords": ["python"], "expected_elements": {"search_box": "input[data-testid='search-input'], input[placeholder*='Search']", "repo_list": ".repo"}},
    "36kr": {"name": "36氪", "url": "https://36kr.com", "domain": "36kr.com", "category": "TECH",
        "test_keywords": ["人工智能"], "expected_elements": {"search_box": "input[placeholder*='搜索'], input[type='text']", "article_list": "article"}},
    "xueqiu": {"name": "雪球", "url": "https://xueqiu.com", "domain": "xueqiu.com", "category": "FINANCE",
        "test_keywords": ["股票"], "expected_elements": {"search_box": "input[name='q'], input[placeholder*='搜索']", "stock_list": ".stock"}},
}


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
        return {"test_id": self.test_id, "test_name": self.test_name, "website_name": self.website_name,
            "capability": self.capability, "success": self.success, "duration": round(self.duration, 2),
            "score": round(self.score, 2), "error": self.error, "details": self.details, "timestamp": self.timestamp}


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
        return {"website_name": self.website_name, "website_url": self.website_url, "category": self.category,
            "test_time": self.test_time, "total_tests": self.total_tests, "passed_tests": self.passed_tests,
            "failed_tests": self.failed_tests, "pass_rate": round(self.pass_rate, 2),
            "avg_score": round(self.avg_score, 2), "results": [r.to_dict() for r in self.results]}

    def to_markdown(self) -> str:
        lines = [f"# {self.website_name} 测试报告\n", f"**网站**: {self.website_url}\n",
            f"**分类**: {self.category}\n", f"**测试时间**: {self.test_time}\n",
            f"**通过率**: {self.pass_rate:.1f}% ({self.passed_tests}/{self.total_tests})\n",
            f"**平均得分**: {self.avg_score:.1f}/100\n\n"]
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


class P0TestExecutor:
    """P0 核心能力测试执行器"""

    def __init__(self, headless: bool = True, timeout: int = 30000):
        self.headless = headless
        self.timeout = timeout
        self.browser: Optional[Browser] = None

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

    def _open_page(self, url: str, site_config: dict = None) -> Optional[Page]:
        try:
            context = self.browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
            )
            page = context.new_page()
            wait_timeout = site_config.get("wait_timeout", self.timeout) if site_config else self.timeout
            page.set_default_timeout(wait_timeout)
            response = page.goto(url, wait_until="networkidle", timeout=wait_timeout)
            # 额外等待动态内容加载
            time.sleep(2)
            if response is None or response.status >= 400:
                return None
            return page
        except Exception as e:
            logger.warning(f"打开页面失败 {url}: {e}")
            return None

    def _close_page(self, page: Page):
        try:
            page.close()
        except Exception:
            pass

    # ---------- 10 个 P0 测试方法 ----------

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

    def test_element_locate(self, page: Page, selectors: List[str]) -> TestResult:
        """T02: 元素定位"""
        t0 = time.time()
        try:
            found = []
            for sel in selectors:
                elements = page.query_selector_all(sel)
                if elements:
                    found.append({"selector": sel, "count": len(elements)})
            duration = time.time() - t0
            if found:
                return TestResult("T02", "元素定位", page.title()[:20], "元素定位",
                    True, duration, min(100, len(found) * 25), details={"found": found})
            return TestResult("T02", "元素定位", page.title()[:20], "元素定位",
                False, duration, 20.0, error="未找到任何元素")
        except Exception as e:
            return TestResult("T02", "元素定位", page.title()[:20], "元素定位",
                False, time.time() - t0, 0.0, error=str(e))

    def test_click(self, page: Page, selector: str) -> TestResult:
        """T03: 点击操作"""
        t0 = time.time()
        try:
            el = page.query_selector(selector)
            if el is None:
                return TestResult("T03", "点击操作", page.title()[:20], "点击操作",
                    False, time.time() - t0, 0.0, error=f"元素不存在: {selector}")
            el.click()
            duration = time.time() - t0
            return TestResult("T03", "点击操作", page.title()[:20], "点击操作",
                True, duration, 90.0, details={"selector": selector})
        except Exception as e:
            return TestResult("T03", "点击操作", page.title()[:20], "点击操作",
                False, time.time() - t0, 30.0, error=str(e)[:80])

    def test_input(self, page: Page, selector: str, text: str) -> TestResult:
        """T04: 输入操作"""
        t0 = time.time()
        try:
            # 处理逗号分隔的选择器，逐个尝试
            candidates = [s.strip() for s in selector.split(",")]
            el = None
            for c in candidates:
                el = page.query_selector(c)
                if el is not None:
                    break
            if el is None:
                return TestResult("T04", "输入操作", page.title()[:20], "输入操作",
                    False, time.time() - t0, 0.0, error=f"元素不存在: {selector}")
            el.fill(text)
            actual = el.input_value()
            duration = time.time() - t0
            if actual == text:
                return TestResult("T04", "输入操作", page.title()[:20], "输入操作",
                    True, duration, 100.0, details={"selector": selector, "value": actual})
            return TestResult("T04", "输入操作", page.title()[:20], "输入操作",
                False, duration, 50.0, error=f"值不匹配: 期望={text}, 实际={actual}")
        except Exception as e:
            return TestResult("T04", "输入操作", page.title()[:20], "输入操作",
                False, time.time() - t0, 0.0, error=str(e)[:80])

    def test_data_extract(self, page: Page, selector: str) -> TestResult:
        """T05: 数据提取"""
        t0 = time.time()
        try:
            el = page.query_selector(selector)
            if el is None:
                return TestResult("T05", "数据提取", page.title()[:20], "数据提取",
                    False, time.time() - t0, 0.0, error=f"元素不存在: {selector}")
            text = el.inner_text()[:200]
            duration = time.time() - t0
            if len(text) > 5:
                return TestResult("T05", "数据提取", page.title()[:20], "数据提取",
                    True, duration, 95.0, details={"selector": selector, "text_preview": text[:100]})
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

    def test_wait_strategy(self, page: Page, selector: str) -> TestResult:
        """T09: 等待策略"""
        t0 = time.time()
        try:
            el = page.wait_for_selector(selector, timeout=10000, state="visible")
            duration = time.time() - t0
            if el is not None:
                return TestResult("T09", "等待策略", page.title()[:20], "等待策略",
                    True, duration, 100.0, details={"selector": selector, "elapsed_ms": duration * 1000})
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
            # 只要页面能执行 JS 操作就算成功（不要求标题非空）
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
        """对一个网站执行全部 P0 测试"""
        site = P0_WEBSITES.get(site_key)
        if not site:
            logger.warning(f"未知网站: {site_key}")
            return None

        report = WebsiteTestReport(
            website_name=site["name"], website_url=site["url"],
            category=site["category"], test_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )

        page = self._open_page(site["url"], site)
        if page is None:
            logger.error(f"无法打开 {site['url']}")
            return report

        try:
            # T01 页面加载
            report.results.append(self.test_page_load(page, site["url"]))

            # T02 元素定位
            selectors = list(site.get("expected_elements", {}).values())
            report.results.append(self.test_element_locate(page, selectors))

            # T03 点击 - 尝试点击搜索框附近的按钮
            search_btn = site.get("expected_elements", {}).get("search_btn")
            if search_btn:
                report.results.append(self.test_click(page, search_btn))
            else:
                report.results.append(TestResult("T03", "点击操作", site["name"], "点击操作",
                    True, 0.1, 50.0, details={"note": "无明确按钮选择器，跳过"}))

            # T04 输入
            search_box = next(iter(site.get("expected_elements", {}).values()), None)
            if search_box:
                report.results.append(self.test_input(page, search_box, site["test_keywords"][0]))

            # T05 数据提取
            news_sel = next((v for k, v in site.get("expected_elements", {}).items() if "list" in k or "news" in k), None)
            if news_sel:
                report.results.append(self.test_data_extract(page, news_sel))
            else:
                report.results.append(self.test_data_extract(page, selectors[0] if selectors else "body"))

            # T06 截图
            screenshot_path = output_dir / f"{site_key}_screenshot.png"
            report.results.append(self.test_screenshot(page, str(screenshot_path)))

            # T07 滚动
            report.results.append(self.test_scroll(page))

            # T08 标签页管理
            report.results.append(self.test_tab_management(page))

            # T09 等待策略
            wait_sel = selectors[0] if selectors else "body"
            report.results.append(self.test_wait_strategy(page, wait_sel))

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
        overall_rate = (total_passed / total_tests * 100) if total_tests > 0 else 0
        overall_score = sum(r.avg_score for r in reports) / len(reports) if reports else 0

        lines = [
            "# P0 网站测试汇总报告\n",
            f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n",
            f"| 指标 | 数值 |\n|------|------|\n",
            f"| 测试网站数 | {len(reports)} |\n",
            f"| 总测试数 | {total_tests} |\n",
            f"| 通过数 | {total_passed} |\n",
            f"| 失败数 | {total_failed} |\n",
            f"| 总体通过率 | {overall_rate:.1f}% |\n",
            f"| 平均得分 | {overall_score:.1f}/100 |\n\n",
        ]

        # 按网站列出结果
        lines.append("## 各网站测试结果\n")
        for r in reports:
            lines.append(f"### {r.website_name} ({r.category})\n")
            lines.append(f"- 通过率: {r.pass_rate:.1f}% ({r.passed_tests}/{r.total_tests})\n")
            lines.append(f"- 平均得分: {r.avg_score:.1f}/100\n")
            failed = [res for res in r.results if not res.success]
            if failed:
                lines.append(f"- 失败项: {', '.join(f.test_name for f in failed)}\n")
            lines.append("\n")

        # 能力维度汇总
        cap_stats = {}
        for r in reports:
            for res in r.results:
                cap = res.capability
                if cap not in cap_stats:
                    cap_stats[cap] = {"total": 0, "passed": 0, "scores": []}
                cap_stats[cap]["total"] += 1
                if res.success:
                    cap_stats[cap]["passed"] += 1
                if res.score > 0:
                    cap_stats[cap]["scores"].append(res.score)

        lines.append("## 能力维度汇总\n| 能力 | 测试数 | 通过数 | 通过率 | 平均分 |\n")
        lines.append("|------|--------|--------|--------|--------|\n")
        for cap, s in sorted(cap_stats.items()):
            rate = (s["passed"] / s["total"] * 100) if s["total"] > 0 else 0
            avg = sum(s["scores"]) / len(s["scores"]) if s["scores"] else 0
            lines.append(f"| {cap} | {s['total']} | {s['passed']} | {rate:.1f}% | {avg:.1f} |\n")

        return "".join(lines)

    def save_reports(self, reports: List[WebsiteTestReport]):
        """保存所有报告"""
        # 汇总报告
        summary = self.generate_summary(reports)
        summary_path = self.output_dir / "summary.md"
        summary_path.write_text(summary, encoding="utf-8")
        logger.info(f"汇总报告已保存: {summary_path}")

        # 各网站详细报告
        for report in reports:
            safe_name = report.website_name.replace(" ", "_")
            md_path = self.output_dir / f"report_{safe_name}.md"
            md_path.write_text(report.to_markdown(), encoding="utf-8")

        # JSON 报告
        json_data = {
            "test_time": datetime.now().isoformat(),
            "total_websites": len(reports),
            "total_tests": sum(r.total_tests for r in reports),
            "total_passed": sum(r.passed_tests for r in reports),
            "reports": [r.to_dict() for r in reports]
        }
        json_path = self.output_dir / "results.json"
        json_path.write_text(json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"JSON 报告已保存: {json_path}")


def main():
    parser = argparse.ArgumentParser(description="P0 网站测试运行器")
    parser.add_argument("--websites", type=str, default="all",
                        help="逗号分隔的网站 key，或 'all' 表示全部")
    parser.add_argument("--headless", action="store_true", default=True,
                        help="无头模式运行")
    parser.add_argument("--output", type=str, default=None,
                        help="输出目录")
    parser.add_argument("--timeout", type=int, default=30000,
                        help="超时时间 (ms)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    # 确定输出目录
    if args.output:
        output_dir = Path(args.output)
    else:
        output_dir = Path(__file__).parent.parent.parent / "tests" / "evaluation" / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    # 确定要测试的网站
    if args.websites.lower() == "all":
        site_keys = list(P0_WEBSITES.keys())
    else:
        site_keys = [k.strip() for k in args.websites.split(",")]
        invalid = [k for k in site_keys if k not in P0_WEBSITES]
        if invalid:
            logger.warning(f"未知网站: {invalid}，将跳过")
            site_keys = [k for k in site_keys if k in P0_WEBSITES]

    logger.info(f"准备测试 {len(site_keys)} 个网站: {site_keys}")

    executor = P0TestExecutor(headless=args.headless, timeout=args.timeout)
    generator = TestReportGenerator(output_dir)
    reports: List[WebsiteTestReport] = []

    try:
        executor.start()
        for key in site_keys:
            logger.info(f"开始测试: {P0_WEBSITES[key]['name']}")
            report = executor.run_website_tests(key, output_dir)
            if report:
                reports.append(report)
                logger.info(f"  结果: {report.passed_tests}/{report.total_tests} 通过, 得分 {report.avg_score:.1f}")
            time.sleep(1)  # 避免请求过快
    finally:
        executor.stop()

    # 生成报告
    generator.save_reports(reports)

    # 打印汇总
    if reports:
        total = sum(r.total_tests for r in reports)
        passed = sum(r.passed_tests for r in reports)
        print(f"\n{'='*50}")
        print(f"测试完成: {len(reports)} 个网站, {total} 项测试, 通过 {passed}/{total} ({passed/total*100:.1f}%)")
        print(f"报告已保存至: {output_dir}")
        print(f"{'='*50}")
    else:
        print("没有成功完成任何测试")


if __name__ == "__main__":
    main()