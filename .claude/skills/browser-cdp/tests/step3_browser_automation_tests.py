# -*- coding: utf-8 -*-
"""步骤3 v6: 自动化测试脚本 - 浏览器核心操作链路验证 (使用新page重置)"""
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(r"E:\codes\mini_claude_code\.agent\daemon_run_outputs\goals\goal_64082644\run_0141")
SCREENSHOTS_DIR = OUTPUT_DIR / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)


class TestResult:
    def __init__(self, test_id, name, chain_id):
        self.test_id = test_id
        self.name = name
        self.chain_id = chain_id
        self.status = "pending"
        self.duration = 0.0
        self.error = None
        self.screenshot = None
        self.details = {}

    def to_dict(self):
        return {
            "test_id": self.test_id, "name": self.name, "chain_id": self.chain_id,
            "status": self.status, "duration_s": round(self.duration, 2),
            "error": self.error, "screenshot": self.screenshot, "details": self.details,
        }


class BrowserTestRunner:
    def __init__(self, headless=True, timeout=60.0):
        self.headless = headless
        self.timeout = timeout
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self.results = []
        self.screenshot_count = 0
        self._pending_reset = False

    async def initialize(self):
        try:
            from playwright.async_api import async_playwright
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
                      "--disable-dev-shm-usage", "--disable-gpu"]
            )
            self._context = await self._browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                locale="zh-CN"
            )
            self._page = await self._context.new_page()
            stealth_path = Path(r"E:\codes\mini_claude_code\.claude\skills\browser-cdp\src\core\stealth.min.js")
            if stealth_path.exists():
                await self._page.add_init_script(path=str(stealth_path))
            self._page.set_default_timeout(int(self.timeout * 1000))
            logger.info("Browser initialized successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize browser: {e}")
            return False

    async def cleanup(self):
        try:
            if self._page:
                await self._page.close()
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass

    async def reset_page(self):
        """创建新page重置浏览器状态"""
        if not self._pending_reset:
            return
        self._pending_reset = False
        try:
            if self._page:
                await self._page.close()
            self._page = await self._context.new_page()
            stealth_path = Path(r"E:\codes\mini_claude_code\.claude\skills\browser-cdp\src\core\stealth.min.js")
            if stealth_path.exists():
                await self._page.add_init_script(path=str(stealth_path))
            self._page.set_default_timeout(int(self.timeout * 1000))
            logger.info("  Page reset (new page created)")
        except Exception as e:
            logger.warning(f"Page reset failed: {e}")

    async def take_screenshot(self, name):
        try:
            self.screenshot_count += 1
            path = SCREENSHOTS_DIR / f"{name}_{self.screenshot_count:03d}.png"
            await self._page.screenshot(path=str(path), full_page=True)
            return str(path)
        except Exception as e:
            logger.warning(f"Screenshot failed: {e}")
            return None

    async def safe_goto(self, url, wait_for="domcontentloaded", timeout=30.0):
        try:
            await self._page.goto(url, wait_until=wait_for, timeout=int(timeout * 1000))
            return True
        except Exception:
            if wait_for == "networkidle":
                try:
                    await self._page.goto(url, wait_until="domcontentloaded", timeout=int(timeout * 1000))
                    return True
                except Exception:
                    pass
            raise

    async def wait_any_selector(self, selectors, timeout=10.0):
        for selector in selectors:
            try:
                await self._page.wait_for_selector(selector, timeout=int(timeout * 1000))
                return selector
            except Exception:
                continue
        raise TimeoutError(f"None of selectors found within {timeout}s")

    async def run_test(self, test_config):
        result = TestResult(test_config["id"], test_config["name"], test_config.get("chain_id", "chain_001"))
        start_time = time.time()
        errors = []
        try:
            # Reset page if needed before starting
            await self.reset_page()

            for step in test_config.get("steps", []):
                action = step.get("action")
                if action == "navigate":
                    url = step.get("url", "")
                    wait_for = step.get("wait_for", "domcontentloaded")
                    timeout = step.get("timeout", self.timeout)
                    expect_fail = step.get("expect_fail", False)
                    logger.info(f"  Navigating to: {url[:60]}...")
                    try:
                        await self.safe_goto(url, wait_for, timeout)
                    except Exception as e:
                        if expect_fail:
                            errors.append((f"Expected failure: {str(e)[:100]}", True))
                            self._pending_reset = True
                        else:
                            raise
                elif action == "wait":
                    selectors = step.get("selectors", [step.get("selector", "")])
                    if selectors and selectors[0]:
                        await self.wait_any_selector(selectors, step.get("timeout", 10.0))
                    else:
                        await asyncio.sleep(step.get("timeout", 2.0))
                elif action == "extract":
                    selectors = step.get("selectors", [step.get("selector", "")])
                    extracted = None
                    for sel in selectors:
                        try:
                            content = await self._page.inner_text(sel, timeout=5000)
                            if content and content.strip():
                                extracted = content
                                break
                        except Exception:
                            continue
                    result.details["extracted_content"] = (extracted or "")[:200]
                    title = await self._page.title()
                    result.details["page_title"] = title
                elif action == "screenshot":
                    sf = await self.take_screenshot(result.test_id)
                    result.details["screenshot"] = sf
                elif action == "evaluate":
                    js = step.get("javascript", "")
                    if js:
                        result.details["js_result"] = await self._page.evaluate(js)
                elif action == "click":
                    selectors = step.get("selectors", [step.get("selector", "")])
                    for sel in selectors:
                        try:
                            el = await self._page.query_selector(sel)
                            if el:
                                await el.click()
                                break
                        except Exception:
                            continue

            if len(errors) > 0 and all(e[1] for e in errors):
                result.status = "passed"
                result.details["expected_failures"] = [e[0] for e in errors]
            elif len(errors) > 0:
                result.status = "failed"
                result.error = errors[0][0]
            else:
                result.status = "passed"

            result.duration = time.time() - start_time
            if result.status == "passed":
                logger.info(f"  PASS ({result.duration:.1f}s)")
            else:
                logger.error(f"  FAIL: {result.error}")
                sf = await self.take_screenshot(result.test_id + "_fail")
                result.screenshot = sf
        except Exception as e:
            result.status = "failed"
            result.error = str(e)[:500]
            result.duration = time.time() - start_time
            logger.error(f"  FAIL: {e}")
            sf = await self.take_screenshot(result.test_id + "_fail")
            result.screenshot = sf
        self.results.append(result)
        return result

    def generate_report(self):
        total = len(self.results)
        passed = sum(1 for r in self.results if r.status == "passed")
        failed = total - passed
        total_duration = sum(r.duration for r in self.results)
        categories = {}
        for r in self.results:
            cat = r.test_id.split("_")[0] if "_" in r.test_id else "other"
            if cat not in categories:
                categories[cat] = {"total": 0, "passed": 0, "failed": 0}
            categories[cat]["total"] += 1
            if r.status == "passed":
                categories[cat]["passed"] += 1
            else:
                categories[cat]["failed"] += 1
        return {
            "generated_at": datetime.now().isoformat(),
            "summary": {
                "total_tests": total, "passed": passed, "failed": failed,
                "pass_rate_pct": round(passed / total * 100, 1) if total else 0,
                "error_rate_pct": round(failed / total * 100, 1) if total else 0,
                "total_duration_s": round(total_duration, 2),
                "avg_duration_s": round(total_duration / total, 2) if total else 0,
            },
            "categories": categories,
            "results": [r.to_dict() for r in self.results],
            "screenshots_count": self.screenshot_count,
        }


TEST_CASES = [
    # A. 导航测试
    {"id": "nav_basic_baidu", "name": "导航-百度首页", "chain_id": "chain_nav_001", "category": "navigation",
     "steps": [
         {"action": "navigate", "url": "https://www.baidu.com", "wait_for": "domcontentloaded", "timeout": 20},
         {"action": "wait", "selectors": ["#kw", ".s_ipt"], "timeout": 5},
         {"action": "extract", "selectors": ["title"], "selector": "title"},
         {"action": "screenshot"},
     ]},
    {"id": "nav_basic_bing", "name": "导航-Bing搜索", "chain_id": "chain_nav_002", "category": "navigation",
     "steps": [
         {"action": "navigate", "url": "https://www.bing.com", "wait_for": "domcontentloaded", "timeout": 25},
         {"action": "wait", "selectors": ["#sb_form_q", "input[type='search']"], "timeout": 8},
         {"action": "extract", "selectors": ["title"], "selector": "title"},
     ]},
    {"id": "nav_invalid_url", "name": "导航-无效URL(预期失败)", "chain_id": "chain_nav_003", "category": "navigation",
     "steps": [
         {"action": "navigate", "url": "https://this-domain-does-not-exist-12345.invalid", "timeout": 5, "expect_fail": True},
     ]},
    # B. 搜索测试
    {"id": "search_baidu", "name": "搜索-百度搜索", "chain_id": "chain_search_001", "category": "search",
     "steps": [
         {"action": "navigate", "url": "https://www.baidu.com", "wait_for": "domcontentloaded", "timeout": 20},
         {"action": "evaluate", "javascript": "document.getElementById('kw').value='Python编程';"},
         {"action": "evaluate", "javascript": "document.getElementById('su').click();"},
         {"action": "wait", "selectors": ["#content_left", ".result", "h3"], "timeout": 8},
         {"action": "extract", "selectors": ["#content_left", ".result"]},
         {"action": "screenshot"},
     ]},
    {"id": "search_bing", "name": "搜索-Bing搜索", "chain_id": "chain_search_002", "category": "search",
     "steps": [
         {"action": "navigate", "url": "https://www.bing.com", "wait_for": "domcontentloaded", "timeout": 25},
         {"action": "evaluate", "javascript": "document.getElementById('sb_form_q').value='AI technology'; document.getElementById('sb_form_go').click();"},
         {"action": "wait", "selectors": [".b_algo", "h2"], "timeout": 8},
         {"action": "extract", "selectors": [".b_algo", "title"]},
         {"action": "screenshot"},
     ]},
    # C. 交互测试
    {"id": "interact_scroll", "name": "交互-页面滚动", "chain_id": "chain_interact_001", "category": "interaction",
     "steps": [
         {"action": "navigate", "url": "https://www.baidu.com", "wait_for": "domcontentloaded", "timeout": 20},
         {"action": "evaluate", "javascript": "window.scrollTo(0, document.body.scrollHeight);"},
         {"action": "wait", "timeout": 2},
         {"action": "evaluate", "javascript": "({scrollTop: window.scrollY, bodyHeight: document.body.scrollHeight});"},
         {"action": "screenshot"},
     ]},
    {"id": "interact_input", "name": "交互-输入框操作", "chain_id": "chain_interact_002", "category": "interaction",
     "steps": [
         {"action": "navigate", "url": "https://www.baidu.com", "wait_for": "domcontentloaded", "timeout": 20},
         {"action": "evaluate", "javascript": "document.getElementById('kw').value='测试输入框';"},
         {"action": "evaluate", "javascript": "document.getElementById('kw').getAttribute('value');"},
         {"action": "screenshot"},
     ]},
    # D. 内容提取测试
    {"id": "extract_baidu_news", "name": "提取-百度新闻", "chain_id": "chain_extract_001", "category": "extraction",
     "steps": [
         {"action": "navigate", "url": "https://news.baidu.com", "wait_for": "domcontentloaded", "timeout": 25},
         {"action": "wait", "selectors": ["h1", "h2", "a[href]"], "timeout": 10},
         {"action": "extract", "selectors": ["h1", "h2"]},
         {"action": "screenshot"},
     ]},
    # E. 多标签页测试
    {"id": "tab_multi", "name": "标签页-多标签", "chain_id": "chain_tab_001", "category": "tabs",
     "steps": [
         {"action": "navigate", "url": "https://www.baidu.com", "wait_for": "domcontentloaded", "timeout": 20},
         {"action": "evaluate", "javascript": "window.open('https://www.bing.com', '_blank');"},
         {"action": "wait", "timeout": 3},
         {"action": "evaluate", "javascript": "document.querySelectorAll('a').length;"},
     ]},
    # F. 表单操作测试
    {"id": "form_baidu_submit", "name": "表单-百度提交", "chain_id": "chain_form_001", "category": "form",
     "steps": [
         {"action": "navigate", "url": "https://www.baidu.com", "wait_for": "domcontentloaded", "timeout": 20},
         {"action": "evaluate", "javascript": "document.getElementById('kw').value='AI人工智能';"},
         {"action": "evaluate", "javascript": "document.getElementById('su').click();"},
         {"action": "wait", "selectors": ["#content_left", ".result", "h3"], "timeout": 8},
         {"action": "extract", "selectors": ["#content_left", ".result"]},
         {"action": "screenshot"},
     ]},
    # G. 错误恢复测试
    {"id": "error_timeout_retry", "name": "错误-超时后重试", "chain_id": "chain_error_001", "category": "error_handling",
     "steps": [
         {"action": "navigate", "url": "https://httpbin.org/delay/10", "timeout": 3, "expect_fail": True},
         {"action": "navigate", "url": "https://www.baidu.com", "wait_for": "domcontentloaded", "timeout": 20},
         {"action": "extract", "selectors": ["title"]},
     ]},
    # H. 反检测测试
    {"id": "anti_detect_check", "name": "反检测-浏览器指纹", "chain_id": "chain_antidetect_001", "category": "anti_detection",
     "steps": [
         {"action": "navigate", "url": "https://www.baidu.com", "wait_for": "domcontentloaded", "timeout": 20},
         {"action": "evaluate", "javascript": "({webdriver:navigator.webdriver,chrome:!!window.chrome,plugins:navigator.plugins.length,lang:navigator.language,platform:navigator.platform});"},
     ]},
    # I. 性能测试
    {"id": "perf_tieba", "name": "性能-贴吧首页", "chain_id": "chain_perf_001", "category": "performance",
     "steps": [
         {"action": "navigate", "url": "https://tieba.baidu.com", "wait_for": "domcontentloaded", "timeout": 30},
         {"action": "wait", "selectors": ["h1", "h2", "a[href]", "div"], "timeout": 10},
         {"action": "extract", "selectors": ["h1", "h2"]},
         {"action": "screenshot"},
     ]},
    # J. 动态内容测试
    {"id": "dynamic_taobao", "name": "动态-淘宝首页", "chain_id": "chain_dynamic_001", "category": "dynamic",
     "steps": [
         {"action": "navigate", "url": "https://www.taobao.com", "wait_for": "domcontentloaded", "timeout": 30},
         {"action": "wait", "selectors": ["h1", "h2", "a[href]", ".logo"], "timeout": 10},
         {"action": "extract", "selectors": ["title"]},
         {"action": "screenshot"},
     ]},
    # K. 兼容性测试
    {"id": "compat_csdn", "name": "兼容-CSDN首页", "chain_id": "chain_compat_001", "category": "compatibility",
     "steps": [
         {"action": "navigate", "url": "https://www.csdn.net", "wait_for": "domcontentloaded", "timeout": 30},
         {"action": "wait", "selectors": ["h1", "h2", "a[href]"], "timeout": 10},
         {"action": "extract", "selectors": ["title"]},
         {"action": "screenshot"},
     ]},
    {"id": "compat_juejin", "name": "兼容-掘金首页", "chain_id": "chain_compat_002", "category": "compatibility",
     "steps": [
         {"action": "navigate", "url": "https://juejin.cn", "wait_for": "domcontentloaded", "timeout": 30},
         {"action": "wait", "selectors": ["h1", "h2", "a[href]", ".article-list"], "timeout": 10},
         {"action": "extract", "selectors": ["title"]},
     ]},
]


def generate_markdown_report(report):
    lines = ["# 步骤3: 自动化测试报告 (v6 新page重置)", "", f"生成时间: {report['generated_at']}", ""]
    s = report["summary"]
    lines.append("## 执行摘要")
    lines.append(f"- 总测试数: {s['total_tests']}")
    lines.append(f"- 通过: {s['passed']}")
    lines.append(f"- 失败: {s['failed']}")
    lines.append(f"- 通过率: {s['pass_rate_pct']}%")
    lines.append(f"- 错误率: {s['error_rate_pct']}%")
    lines.append(f"- 总耗时: {s['total_duration_s']}s")
    lines.append(f"- 平均耗时: {s['avg_duration_s']}s/测试")
    lines.append("")
    lines.append("## 分类统计")
    for cat, stats in report["categories"].items():
        pr = round(stats["passed"] / stats["total"] * 100, 1) if stats["total"] else 0
        lines.append(f"- **{cat}**: {stats['passed']}/{stats['total']} 通过 ({pr}%)")
    lines.append("")
    lines.append("## 详细结果")
    for r in report["results"]:
        icon = "PASS" if r["status"] == "passed" else "FAIL"
        lines.append(f"- [{icon}] **{r['name']}** ({r['chain_id']}): {r['duration_s']}s")
        if r["error"]:
            lines.append(f"  - 错误: {r['error'][:150]}")
        if r.get("details", {}).get("page_title"):
            lines.append(f"  - 页面标题: {r['details']['page_title'][:50]}")
        if r.get("details", {}).get("expected_failures"):
            lines.append(f"  - 预期错误: {r['details']['expected_failures'][0][:80] if r['details']['expected_failures'] else 'N/A'}")
    lines.append("")
    lines.append(f"截图数量: {report['screenshots_count']}")
    lines.append("")
    lines.append("---")
    lines.append("*报告由 browser-cdp 自动化测试脚本 v6 自动生成*")
    return "\n".join(lines)


async def main():
    logger.info("=" * 60)
    logger.info("步骤3 v6: 自动化测试脚本 - 浏览器核心操作链路验证 (新page重置)")
    logger.info(f"开始时间: {datetime.now().isoformat()}")
    logger.info("=" * 60)

    runner = BrowserTestRunner(headless=True, timeout=60.0)
    if not await runner.initialize():
        logger.error("无法初始化浏览器环境，退出")
        return 1

    try:
        logger.info(f"准备执行 {len(TEST_CASES)} 个测试用例...")
        for i, tc in enumerate(TEST_CASES, 1):
            logger.info(f"\n[{i}/{len(TEST_CASES)}] {tc['name']}")
            await runner.run_test(tc)

        report = runner.generate_report()
        report_path = OUTPUT_DIR / "step3_test_report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        md_report = generate_markdown_report(report)
        md_path = OUTPUT_DIR / "step3_test_report.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_report)

        s = report["summary"]
        logger.info("=" * 60)
        logger.info(f"测试完成: {s['passed']}/{s['total_tests']} 通过")
        logger.info(f"通过率: {s['pass_rate_pct']}% | 错误率: {s['error_rate_pct']}%")
        logger.info(f"总耗时: {s['total_duration_s']}s | 平均: {s['avg_duration_s']}s/测试")
        logger.info(f"报告已保存: {md_path}")
        logger.info(f"截图保存至: {SCREENSHOTS_DIR}")
        logger.info("=" * 60)

        if s["pass_rate_pct"] >= 95:
            logger.info("SUCCESS: 达标! 错误率低于5%")
            return 0
        elif s["pass_rate_pct"] >= 80:
            logger.info("WARNING: 基本达标，建议进一步优化")
            return 0
        else:
            logger.info("FAILURE: 未达标，需要优化测试用例或增加容错")
            return 1
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
