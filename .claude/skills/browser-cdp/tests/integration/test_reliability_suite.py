# -*- coding: utf-8 -*-
"""
真实测试脚本 - 集成browser-cdp核心模块

整合四种测试模式：
1. real模式 - 使用PlaywrightSession进行真实浏览器测试
2. http模式 - 使用curl_cffi进行HTTP层连通性测试
3. compat模式 - 使用selenium进行跨浏览器兼容性验证
4. stress模式 - 使用aiohttp进行并发压力测试
"""
import asyncio
import json
import logging
import time
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# 添加skill目录到path
SKILL_DIR = Path(__file__).parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

logger = logging.getLogger(__name__)


# ==================== 网站配置 ====================

P0_WEBSITES = [
    {"id": "baidu", "name": "百度", "url": "https://www.baidu.com", "domain": "baidu.com",
     "category": "SEARCH", "search_kw": "AI大模型"},
    {"id": "zhihu", "name": "知乎", "url": "https://www.zhihu.com", "domain": "zhihu.com",
     "category": "SOCIAL", "search_kw": "人工智能"},
    {"id": "bing", "name": "必应", "url": "https://www.bing.com", "domain": "bing.com",
     "category": "SEARCH", "search_kw": "AI"},
    {"id": "sina_finance", "name": "新浪财经", "url": "https://finance.sina.com.cn", "domain": "finance.sina.com.cn",
     "category": "FINANCE", "search_kw": "股票"},
    {"id": "eastmoney", "name": "东方财富", "url": "https://www.eastmoney.com", "domain": "eastmoney.com",
     "category": "FINANCE", "search_kw": "行情"},
    {"id": "gov_cn", "name": "中国政府网", "url": "https://www.gov.cn", "domain": "gov.cn",
     "category": "GOV", "search_kw": "国务院"},
    {"id": "bilibili", "name": "B站", "url": "https://www.bilibili.com", "domain": "bilibili.com",
     "category": "VIDEO", "search_kw": "编程"},
    {"id": "xueqiu", "name": "雪球", "url": "https://xueqiu.com", "domain": "xueqiu.com",
     "category": "FINANCE", "search_kw": "股票"},
    {"id": "csdn", "name": "CSDN", "url": "https://www.csdn.net", "domain": "csdn.net",
     "category": "TECH", "search_kw": "Python"},
    {"id": "juejin", "name": "掘金", "url": "https://juejin.cn", "domain": "juejin.cn",
     "category": "TECH", "search_kw": "Vue"},
]


# ==================== 结果数据结构 ====================

@dataclass
class RealBrowserResult:
    """真实浏览器测试结果"""
    site_id: str
    site_name: str
    category: str
    url: str
    step: str
    status: str  # passed/failed/error
    duration_ms: float
    score: float
    error_msg: Optional[str] = None
    metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass 
class HTTPResult:
    """HTTP层测试结果"""
    site_id: str
    site_name: str
    url: str
    status_code: int
    response_time_ms: float
    content_length: int
    tls_fingerprint: str
    success: bool
    error_msg: Optional[str] = None


@dataclass
class CompatResult:
    """跨浏览器兼容性测试结果"""
    site_id: str
    site_name: str
    browser: str  # chrome/firefox/edge
    url: str
    page_load_time_ms: float
    title: str
    success: bool
    error_msg: Optional[str] = None


@dataclass
class StressResult:
    """压力测试结果"""
    site_id: str
    url: str
    concurrency: int
    total_requests: int
    successful: int
    failed: int
    avg_response_time_ms: float
    p95_response_time_ms: float
    min_response_time_ms: float
    max_response_time_ms: float
    timeout_count: int


# ==================== 1. Real模式 - PlaywrightSession ====================

class RealBrowserTester:
    """使用PlaywrightSession进行真实浏览器测试"""
    
    def __init__(self, headless: bool = True, timeout_ms: int = 15000):
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.results: List[RealBrowserResult] = []
    
    async def test_navigation(self, site: Dict) -> RealBrowserResult:
        """测试页面导航"""
        start = time.time()
        try:
            from src.core.playwright_session import PlaywrightSession, PlaywrightConfig
            cfg = PlaywrightConfig(headless=self.headless, default_timeout=self.timeout_ms)
            session = PlaywrightSession(config=cfg)
            await session.async_launch()
            await session.async_goto(site["url"], wait_until="networkidle")
            
            page = session.get_page()
            title = await page.title() if page else ""
            final_url = await page.url if page else site["url"]
            load_time = (time.time() - start) * 1000
            
            score = 100.0 if title else 50.0
            status = "passed" if title.strip() else "failed"
            
            await session.close()
            
            return RealBrowserResult(
                site_id=site["id"], site_name=site["name"], category=site["category"],
                url=site["url"], step="navigation", status=status,
                duration_ms=load_time, score=score,
                metrics={"title": title, "final_url": final_url, "load_time_ms": load_time}
            )
        except Exception as e:
            load_time = (time.time() - start) * 1000
            return RealBrowserResult(
                site_id=site["id"], site_name=site["name"], category=site["category"],
                url=site["url"], step="navigation", status="error",
                duration_ms=load_time, score=0.0, error_msg=str(e)
            )
    
    async def test_search(self, site: Dict) -> RealBrowserResult:
        """测试搜索功能"""
        start = time.time()
        try:
            from src.core.playwright_session import PlaywrightSession, PlaywrightConfig
            cfg = PlaywrightConfig(headless=self.headless, default_timeout=self.timeout_ms)
            session = PlaywrightSession(config=cfg)
            await session.async_launch()
            
            # 先导航到首页
            await session.async_goto(site["url"], wait_until="domcontentloaded")
            await asyncio.sleep(1)  # 等待页面稳定
            
            # 查找搜索框并输入
            page = session.get_page()
            search_selectors = [
                "input[name='wd']", "input[type='search']", "input[placeholder*='搜索']",
                "input#kw", "input[name='q']", "input[type='text']"
            ]
            
            search_box = None
            for sel in search_selectors:
                el = await page.query_selector(sel)
                if el:
                    search_box = el
                    break
            
            if search_box:
                await search_box.click()
                await search_box.fill(site["search_kw"])
                await search_box.press("Enter")
                await asyncio.sleep(2)
                
                # 检查搜索结果
                result_selectors = ["#content_left", ".result", ".search-result",
                                   "article", "[class*='result']", ".srp-results"]
                results_found = False
                for sel in result_selectors:
                    els = await page.query_selector_all(sel)
                    if els and len(els) > 0:
                        results_found = True
                        break
                
                title = await page.title() if page else ""
                load_time = (time.time() - start) * 1000
                
                return RealBrowserResult(
                    site_id=site["id"], site_name=site["name"], category=site["category"],
                    url=site["url"], step="search", status="passed" if results_found else "failed",
                    duration_ms=load_time, score=90.0 if results_found else 60.0,
                    metrics={"keywords": site["search_kw"], "results_found": results_found, "title": title}
                )
            else:
                await session.close()
                return RealBrowserResult(
                    site_id=site["id"], site_name=site["name"], category=site["category"],
                    url=site["url"], step="search", status="error",
                    duration_ms=(time.time() - start) * 1000, score=0.0,
                    error_msg="未找到搜索框"
                )
        except Exception as e:
            load_time = (time.time() - start) * 1000
            return RealBrowserResult(
                site_id=site["id"], site_name=site["name"], category=site["category"],
                url=site["url"], step="search", status="error",
                duration_ms=load_time, score=0.0, error_msg=str(e)
            )
    
    async def test_dom_parsing(self, site: Dict) -> RealBrowserResult:
        """测试DOM解析能力"""
        start = time.time()
        try:
            from src.core.playwright_session import PlaywrightSession, PlaywrightConfig
            cfg = PlaywrightConfig(headless=self.headless, default_timeout=self.timeout_ms)
            session = PlaywrightSession(config=cfg)
            await session.async_launch()
            await session.async_goto(site["url"], wait_until="domcontentloaded")
            await asyncio.sleep(1)
            
            page = session.get_page()
            common_selectors = [
                ("nav a", "nav"), ("article", "article"), ("[class*='list']", "list"),
                ("h1, h2, h3", "heading"), ("a[href]", "link")
            ]
            
            findings = []
            for selector, desc in common_selectors:
                els = await page.query_selector_all(selector)
                if els:
                    findings.append({"selector": selector, "count": len(els), "desc": desc})
            
            title = await page.title() if page else ""
            load_time = (time.time() - start) * 1000
            
            await session.close()
            
            return RealBrowserResult(
                site_id=site["id"], site_name=site["name"], category=site["category"],
                url=site["url"], step="dom_parsing", status="passed" if findings else "failed",
                duration_ms=load_time, score=85.0 if len(findings) >= 3 else 60.0,
                metrics={"findings": findings, "title": title, "selectors_tested": len(common_selectors)}
            )
        except Exception as e:
            load_time = (time.time() - start) * 1000
            return RealBrowserResult(
                site_id=site["id"], site_name=site["name"], category=site["category"],
                url=site["url"], step="dom_parsing", status="error",
                duration_ms=load_time, score=0.0, error_msg=str(e)
            )
    
    async def run_all_tests(self, sites: List[Dict]) -> List[RealBrowserResult]:
        """运行所有网站的测试"""
        all_results = []
        
        for site in sites:
            logger.info(f"Testing {site['name']} ({site['id']})...")
            
            # 顺序执行三个测试步骤
            nav_result = await self.test_navigation(site)
            all_results.append(nav_result)
            
            search_result = await self.test_search(site)
            all_results.append(search_result)
            
            dom_result = await self.test_dom_parsing(site)
            all_results.append(dom_result)
        
        return all_results


# ==================== 2. HTTP模式 - curl_cffi ====================

class HTTPTester:
    """使用curl_cffi进行HTTP层测试"""
    
    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self.results: List[HTTPResult] = []
    
    def test_http(self, site: Dict) -> HTTPResult:
        """测试HTTP连通性和TLS指纹"""
        start = time.time()
        try:
            from curl_cffi import requests as cffi_requests
            
            resp = cffi_requests.get(
                site["url"],
                timeout=self.timeout,
                impersonate="chrome110",
                allow_redirects=True
            )
            
            response_time = (time.time() - start) * 1000
            
            return HTTPResult(
                site_id=site["id"], site_name=site["name"], url=site["url"],
                status_code=resp.status_code,
                response_time_ms=response_time,
                content_length=len(resp.content),
                tls_fingerprint="chrome110",
                success=200 <= resp.status_code < 400
            )
        except Exception as e:
            response_time = (time.time() - start) * 1000
            return HTTPResult(
                site_id=site["id"], site_name=site["name"], url=site["url"],
                status_code=0, response_time_ms=response_time,
                content_length=0, tls_fingerprint="error",
                success=False, error_msg=str(e)
            )
    
    def run_all_tests(self, sites: List[Dict]) -> List[HTTPResult]:
        """运行所有网站的HTTP测试"""
        return [self.test_http(site) for site in sites]


# ==================== 3. Compat模式 - Selenium ====================

class CompatTester:
    """使用selenium进行跨浏览器兼容性测试"""
    
    def __init__(self, timeout: int = 15):
        self.timeout = timeout
        self.results: List[CompatResult] = []
    
    def test_chrome(self, site: Dict) -> CompatResult:
        """Chrome浏览器测试"""
        return self._test_browser(site, "chrome")
    
    def _test_browser(self, site: Dict, browser: str) -> CompatResult:
        """通用浏览器测试"""
        start = time.time()
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
            
            opts = Options()
            opts.add_argument("--headless=new")
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            
            driver = webdriver.Chrome(options=opts)
            driver.set_page_load_timeout(self.timeout)
            driver.get(site["url"])
            
            load_time = (time.time() - start) * 1000
            title = driver.title
            driver.quit()
            
            return CompatResult(
                site_id=site["id"], site_name=site["name"], browser=browser,
                url=site["url"], page_load_time_ms=load_time, title=title,
                success=bool(title.strip())
            )
        except Exception as e:
            load_time = (time.time() - start) * 1000
            return CompatResult(
                site_id=site["id"], site_name=site["name"], browser=browser,
                url=site["url"], page_load_time_ms=load_time, title="",
                success=False, error_msg=str(e)
            )
    
    def run_all_tests(self, sites: List[Dict]) -> List[CompatResult]:
        """运行所有网站的Chrome兼容性测试"""
        results = []
        for site in sites:
            result = self.test_chrome(site)
            results.append(result)
            logger.info(f"Selenium Chrome test: {site['name']} -> {'OK' if result.success else 'FAIL'}")
        return results


# ==================== 4. Stress模式 - aiohttp ====================

class StressTester:
    """使用aiohttp进行并发压力测试"""
    
    def __init__(self, concurrency: int = 10, timeout: int = 10):
        self.concurrency = concurrency
        self.timeout = timeout
        self.results: List[StressResult] = []
    
    async def test_concurrent(self, site: Dict) -> StressResult:
        """并发压力测试"""
        import aiohttp
        
        timings = []
        successes = 0
        failures = 0
        timeouts = 0
        
        semaphore = asyncio.Semaphore(self.concurrency)
        
        async def single_request(session, url, idx):
            nonlocal successes, failures, timeouts
            async with semaphore:
                start = time.time()
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=self.timeout)) as resp:
                        elapsed = (time.time() - start) * 1000
                        timings.append(elapsed)
                        if 200 <= resp.status < 400:
                            successes += 1
                        else:
                            failures += 1
                except asyncio.TimeoutError:
                    timeouts += 1
                    failures += 1
                except Exception:
                    failures += 1
        
        connector = aiohttp.TCPConnector(limit=self.concurrency)
        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = [single_request(session, site["url"], i) for i in range(self.concurrency)]
            await asyncio.gather(*tasks)
        
        if timings:
            timings.sort()
            p95_idx = int(len(timings) * 0.95)
            return StressResult(
                site_id=site["id"], url=site["url"],
                concurrency=self.concurrency, total_requests=len(timings),
                successful=successes, failed=failures,
                avg_response_time_ms=sum(timings)/len(timings),
                p95_response_time_ms=timings[min(p95_idx, len(timings)-1)],
                min_response_time_ms=min(timings),
                max_response_time_ms=max(timings),
                timeout_count=timeouts
            )
        else:
            return StressResult(
                site_id=site["id"], url=site["url"],
                concurrency=self.concurrency, total_requests=0,
                successful=0, failed=0, avg_response_time_ms=0,
                p95_response_time_ms=0, min_response_time_ms=0,
                max_response_time_ms=0, timeout_count=0
            )
    
    async def run_all_tests(self, sites: List[Dict]) -> List[StressResult]:
        """运行所有网站的压力测试"""
        tasks = [self.test_concurrent(site) for site in sites]
        return await asyncio.gather(*tasks)


# ==================== 报告生成 ====================

def generate_report(
    real_results: List[RealBrowserResult],
    http_results: List[HTTPResult],
    compat_results: List[CompatResult],
    stress_results: List[StressResult],
    output_path: Path
) -> Dict[str, Any]:
    """生成综合测试报告"""
    
    # 汇总统计
    real_passed = sum(1 for r in real_results if r.status == "passed")
    real_total = len(real_results)
    http_ok = sum(1 for r in http_results if r.success)
    http_total = len(http_results)
    compat_ok = sum(1 for r in compat_results if r.success)
    compat_total = len(compat_results)
    
    report = {
        "report_time": datetime.now().isoformat(),
        "summary": {
            "real_browser": {"passed": real_passed, "total": real_total,
                           "pass_rate": round(real_passed/max(real_total,1)*100, 1)},
            "http_layer": {"ok": http_ok, "total": http_total,
                          "pass_rate": round(http_ok/max(http_total,1)*100, 1)},
            "compatibility": {"ok": compat_ok, "total": compat_total,
                             "pass_rate": round(compat_ok/max(compat_total,1)*100, 1)},
        },
        "real_results": [r.__dict__ for r in real_results],
        "http_results": [r.__dict__ for r in http_results],
        "compat_results": [r.__dict__ for r in compat_results],
        "stress_results": [r.__dict__ for r in stress_results],
    }
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    logger.info(f"Report saved to {output_path}")
    return report


# ==================== 主入口 ====================

async def run_full_test_suite(output_dir: Path, sites: List[Dict], headless: bool = True):
    """运行完整测试套件"""
    logger.info("=" * 60)
    logger.info("Browser-CDP Reliability Test Suite - Starting")
    logger.info("=" * 60)
    
    total_start = time.time()
    
    # 1. Real模式测试 (PlaywrightSession)
    logger.info("\n[1/4] Running Real Browser Tests (PlaywrightSession)...")
    real_tester = RealBrowserTester(headless=headless)
    real_results = await real_tester.run_all_tests(sites)
    real_time = sum(r.duration_ms for r in real_results)
    logger.info(f"  Real tests completed: {sum(1 for r in real_results if r.status == 'passed')}/{len(real_results)} passed")
    logger.info(f"  Total duration: {real_time/1000:.1f}s")
    
    # 2. HTTP模式测试 (curl_cffi)
    logger.info("\n[2/4] Running HTTP Layer Tests (curl_cffi)...")
    http_tester = HTTPTester()
    http_results = http_tester.run_all_tests(sites)
    http_time = sum(r.response_time_ms for r in http_results)
    logger.info(f"  HTTP tests completed: {sum(1 for r in http_results if r.success)}/{len(http_results)} ok")
    logger.info(f"  Total duration: {http_time/1000:.1f}s")
    
    # 3. 兼容模式测试 (Selenium)
    logger.info("\n[3/4] Running Cross-Browser Compatibility Tests (Selenium)...")
    compat_tester = CompatTester()
    compat_results = compat_tester.run_all_tests(sites)
    compat_time = sum(r.page_load_time_ms for r in compat_results)
    logger.info(f"  Compat tests completed: {sum(1 for r in compat_results if r.success)}/{len(compat_results)} ok")
    logger.info(f"  Total duration: {compat_time/1000:.1f}s")
    
    # 4. 压力模式测试 (aiohttp)
    logger.info("\n[4/4] Running Stress Tests (aiohttp)...")
    stress_tester = StressTester(concurrency=5)
    stress_results = await stress_tester.run_all_tests(sites)
    logger.info(f"  Stress tests completed for {len(stress_results)} sites")
    
    # 生成报告
    report_path = output_dir / f"test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    report = generate_report(real_results, http_results, compat_results, stress_results, report_path)
    
    total_time = time.time() - total_start
    logger.info("\n" + "=" * 60)
    logger.info(f"Test Suite Complete - Total Duration: {total_time:.1f}s")
    logger.info(f"Overall Pass Rate: {report['summary']['real_browser']['pass_rate']}% (browser) | "
                f"{report['summary']['http_layer']['pass_rate']}% (HTTP) | "
                f"{report['summary']['compatibility']['pass_rate']}% (compat)")
    logger.info("=" * 60)
    
    return report


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Browser-CDP Reliability Test Suite")
    parser.add_argument("--headless", action="store_true", default=True, help="Run in headless mode")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent.parent / "output" / "test_reports")
    parser.add_argument("--sites", type=str, default=None, help="Comma-separated site IDs (default: all P0)")
    args = parser.parse_args()
    
    # 选择测试网站
    sites = P0_WEBSITES
    if args.sites:
        site_ids = set(args.sites.split(","))
        sites = [s for s in sites if s["id"] in site_ids]
    
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    
    asyncio.run(run_full_test_suite(args.output_dir, sites, args.headless))
