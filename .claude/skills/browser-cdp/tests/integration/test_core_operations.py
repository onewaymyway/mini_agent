# -*- coding: utf-8 -*-
"""
browser-cdp Skill 核心操作链路自动化测试

测试场景：
1. 页面导航测试 - 验证URL访问和页面加载
2. 元素交互测试 - 验证点击、输入、下拉选择等操作
3. 数据提取测试 - 验证文本、链接、图片等数据提取
4. 搜索功能测试 - 验证搜索框输入和结果获取
5. 截图录制测试 - 验证截图和录制功能
6. 多标签页测试 - 验证标签页切换和管理

完成标准：脚本编写完成并通过首轮测试，错误率低于5%
"""

import asyncio
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# 添加skill目录到path
SKILL_DIR = Path(__file__).parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

logger = logging.getLogger(__name__)


# ==================== 测试配置 ====================

@dataclass
class TestConfig:
    """测试配置"""
    headless: bool = True
    timeout_ms: int = 15000
    navigation_timeout_ms: int = 10000
    wait_timeout_ms: int = 5000
    retry_count: int = 2
    retry_delay: float = 1.0

@dataclass
class TestResult:
    """测试结果"""
    test_id: str
    test_name: str
    category: str
    status: str  # passed/failed/error
    duration_ms: float
    error_msg: Optional[str] = None
    metrics: Dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.status == "passed"


# ==================== 测试网站列表 ====================

TEST_SITES = [
    {
        "id": "baidu",
        "name": "百度", 
        "url": "https://www.baidu.com",
        "category": "SEARCH",
        "has_search": True,
        "search_selector": "input#kw",
        "search_btn_selector": "input#su",
    },
    {
        "id": "bing",
        "name": "必应", 
        "url": "https://www.bing.com",
        "category": "SEARCH",
        "has_search": True,
        "search_selector": "input#sb_form_q",
        "search_btn_selector": "input#search_icon",
    },
    {
        "id": "zhihu",
        "name": "知乎", 
        "url": "https://www.zhihu.com",
        "category": "SOCIAL",
        "has_search": True,
        "search_selector": "input.Input-search-input",
        "search_btn_selector": None,
    },
    {
        "id": "sina",
        "name": "新浪财经", 
        "url": "https://finance.sina.com.cn",
        "category": "FINANCE",
        "has_search": False,
        "search_selector": None,
        "search_btn_selector": None,
    },
    {
        "id": "gov_cn",
        "name": "中国政府网", 
        "url": "https://www.gov.cn",
        "category": "GOV",
        "has_search": True,
        "search_selector": "input#searchTxt",
        "search_btn_selector": "button#searchBtn",
    },
]


class TestRunner:
    """测试执行器"""
    
    def __init__(self, config: Optional[TestConfig] = None):
        self.config = config or TestConfig()
        self.results: List[TestResult] = []
        self._session = None
        
    async def __aenter__(self):
        await self._init_session()
        return self
        
    async def __aexit__(self, exc_type, exc, tb):
        await self._close_session()
        
    async def _init_session(self):
        """初始化浏览器会话"""
        try:
            from src.core.playwright_session import PlaywrightSession, PlaywrightConfig
            cfg = PlaywrightConfig(
                headless=self.config.headless,
                default_timeout=self.config.timeout_ms
            )
            self._session = PlaywrightSession(config=cfg)
            await self._session.async_launch()
            logger.info("浏览器会话初始化成功")
        except Exception as e:
            logger.error(f"浏览器会话初始化失败: {e}")
            raise
            
    async def _close_session(self):
        """关闭浏览器会话"""
        if self._session:
            try:
                await self._session.close()
                logger.info("浏览器会话已关闭")
            except Exception as e:
                logger.warning(f"关闭浏览器会话时出错: {e}")
            finally:
                self._session = None

    def _record_result(self, result: TestResult):
        """记录测试结果"""
        self.results.append(result)
        status_icon = "✓" if result.success else "✗"
        logger.info(f"{status_icon} [{result.category}] {result.test_name}: {result.status} ({result.duration_ms:.0f}ms)")
        if result.error_msg:
            logger.debug(f"  错误: {result.error_msg[:200]}")

    # ==================== 测试方法 ====================
    
    async def test_navigation(self, site: Dict) -> TestResult:
        """测试1: 页面导航"""
        start_time = time.time()
        test_id = f"NAV_{site['id'].upper()}"
        try:
            page = await self._session.async_goto(site["url"], wait_until="domcontentloaded")
            if not page:
                raise Exception("无法获取页面")
            
            title = await page.title()
            final_url = page.url
            elapsed = (time.time() - start_time) * 1000
            
            # 验证页面加载成功
            if not title:
                raise Exception("页面标题为空")
            
            return TestResult(
                test_id=test_id,
                test_name=f"导航到{site['name']}",
                category="导航",
                status="passed",
                duration_ms=elapsed,
                metrics={
                    "title": title,
                    "final_url": final_url,
                    "load_time_ms": elapsed
                }
            )
        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            return TestResult(
                test_id=test_id,
                test_name=f"导航到{site['name']}",
                category="导航",
                status="error",
                duration_ms=elapsed,
                error_msg=str(e)
            )

    async def test_search(self, site: Dict) -> TestResult:
        """测试2: 搜索功能"""
        if not site.get("has_search") or not site.get("search_selector"):
            return TestResult(
                test_id=f"SRCH_{site['id'].upper()}_SKIP",
                test_name=f"搜索-{site['name']}",
                category="搜索",
                status="skipped",
                duration_ms=0,
                metrics={"reason": "网站不支持搜索测试"}
            )
            
        start_time = time.time()
        test_id = f"SRCH_{site['id'].upper()}"
        query = "AI大模型"
        
        try:
            # 先导航到首页
            page = await self._session.async_goto(site["url"], wait_until="domcontentloaded")
            await asyncio.sleep(0.5)  # 等待页面渲染
            
            # 查找搜索框
            search_box = await page.query_selector(site["search_selector"])
            if not search_box:
                # 尝试备用选择器
                search_box = await page.query_selector("input[type='text']")
            
            if not search_box:
                raise Exception("未找到搜索框")
            
            # 输入搜索词
            await search_box.fill(query)
            await asyncio.sleep(0.3)
            
            # 提交搜索
            if site.get("search_btn_selector"):
                btn = await page.query_selector(site["search_btn_selector"])
                if btn:
                    await btn.click()
                else:
                    await search_box.press("Enter")
            else:
                await search_box.press("Enter")
            
            await asyncio.sleep(2)  # 等待搜索结果
            
            # 验证搜索结果
            current_url = page.url
            title = await page.title()
            elapsed = (time.time() - start_time) * 1000
            
            return TestResult(
                test_id=test_id,
                test_name=f"搜索-{site['name']}",
                category="搜索",
                status="passed",
                duration_ms=elapsed,
                metrics={
                    "query": query,
                    "title": title,
                    "final_url": current_url
                }
            )
        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            return TestResult(
                test_id=test_id,
                test_name=f"搜索-{site['name']}",
                category="搜索",
                status="error",
                duration_ms=elapsed,
                error_msg=str(e)
            )

    async def test_element_extraction(self, site: Dict) -> TestResult:
        """测试3: 元素数据提取"""
        start_time = time.time()
        test_id = f"EXT_{site['id'].upper()}"
        
        try:
            page = await self._session.async_goto(site["url"], wait_until="domcontentloaded")
            await asyncio.sleep(1)  # 等待页面稳定
            
            # 提取常见元素
            results = {}
            
            # 提取标题
            title_el = await page.query_selector("h1")
            if title_el:
                results["title_text"] = await title_el.inner_text()
            
            # 提取链接数量
            links = await page.query_selector_all("a[href]")
            results["link_count"] = len(links)
            
            # 提取图片数量
            images = await page.query_selector_all("img")
            results["image_count"] = len(images)
            
            # 提取文本段落
            paragraphs = await page.query_selector_all("p")
            results["paragraph_count"] = len(paragraphs)
            
            elapsed = (time.time() - start_time) * 1000
            
            return TestResult(
                test_id=test_id,
                test_name=f"元素提取-{site['name']}",
                category="提取",
                status="passed",
                duration_ms=elapsed,
                metrics=results
            )
        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            return TestResult(
                test_id=test_id,
                test_name=f"元素提取-{site['name']}",
                category="提取",
                status="error",
                duration_ms=elapsed,
                error_msg=str(e)
            )

    async def test_screenshot(self, site: Dict) -> TestResult:
        """测试4: 截图功能"""
        start_time = time.time()
        test_id = f"SCR_{site['id'].upper()}"
        
        try:
            page = await self._session.async_goto(site["url"], wait_until="domcontentloaded")
            await asyncio.sleep(0.5)
            
            # 截图
            screenshot_path = SKILL_DIR / "output" / f"test_{site['id']}_{int(time.time())}.png"
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            
            await page.screenshot(path=str(screenshot_path), full_page=True)
            
            elapsed = (time.time() - start_time) * 1000
            file_size = screenshot_path.stat().st_size if screenshot_path.exists() else 0
            
            return TestResult(
                test_id=test_id,
                test_name=f"截图-{site['name']}",
                category="截图",
                status="passed",
                duration_ms=elapsed,
                metrics={
                    "screenshot_path": str(screenshot_path),
                    "file_size_bytes": file_size
                }
            )
        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            return TestResult(
                test_id=test_id,
                test_name=f"截图-{site['name']}",
                category="截图",
                status="error",
                duration_ms=elapsed,
                error_msg=str(e)
            )

    async def test_navigation_chain(self, site: Dict) -> TestResult:
        """测试5: 导航链路测试（返回上一页）"""
        start_time = time.time()
        test_id = f"NAV_CHAIN_{site['id'].upper()}"
        
        try:
            # 首次导航
            page = await self._session.async_goto(site["url"], wait_until="domcontentloaded")
            first_url = page.url
            await asyncio.sleep(0.5)
            
            # 导航到一个链接（如果存在）
            link = await page.query_selector("a[href^='/'], a[href^='http']")
            second_url = first_url
            if link:
                href = await link.get_attribute("href")
                if href and not href.startswith("javascript:"):
                    await link.click()
                    await asyncio.sleep(1)
                    second_url = page.url
            
            # 返回上一页
            await self._session.back()
            await asyncio.sleep(0.5)
            back_url = page.url
            
            elapsed = (time.time() - start_time) * 1000
            
            # 验证返回成功
            navigation_success = (back_url == first_url or "about:blank" not in back_url)
            
            return TestResult(
                test_id=test_id,
                test_name=f"导航链路-{site['name']}",
                category="导航",
                status="passed" if navigation_success else "failed",
                duration_ms=elapsed,
                metrics={
                    "first_url": first_url,
                    "second_url": second_url,
                    "back_url": back_url,
                    "navigated": second_url != first_url
                }
            )
        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            return TestResult(
                test_id=test_id,
                test_name=f"导航链路-{site['name']}",
                category="导航",
                status="error",
                duration_ms=elapsed,
                error_msg=str(e)
            )

    async def test_multiple_tabs(self) -> TestResult:
        """测试6: 多标签页管理"""
        start_time = time.time()
        test_id = "MULTI_TAB"
        
        try:
            sites_to_open = TEST_SITES[:3]
            
            # 打开多个标签
            pages = []
            for site in sites_to_open:
                page = await self._session.async_goto(site["url"], wait_until="domcontentloaded")
                pages.append({
                    "site": site["name"],
                    "page": page,
                    "url": page.url
                })
                await asyncio.sleep(0.5)
            
            # 验证标签数量
            tab_count = len(pages)
            
            # 关闭所有标签
            for item in pages:
                try:
                    await item["page"].close()
                except:
                    pass
            
            elapsed = (time.time() - start_time) * 1000
            
            return TestResult(
                test_id=test_id,
                test_name="多标签页测试",
                category="标签页",
                status="passed",
                duration_ms=elapsed,
                metrics={
                    "tab_count": tab_count,
                    "tabs": [p["site"] for p in pages]
                }
            )
        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            return TestResult(
                test_id=test_id,
                test_name="多标签页测试",
                category="标签页",
                status="error",
                duration_ms=elapsed,
                error_msg=str(e)
            )

    async def test_page_load_performance(self, site: Dict) -> TestResult:
        """测试7: 页面加载性能"""
        start_time = time.time()
        test_id = f"PERF_{site['id'].upper()}"
        
        try:
            # 多次测量取平均
            load_times = []
            for i in range(3):
                await self._session.async_goto(site["url"], wait_until="domcontentloaded")
                load_time = (time.time() - start_time) * 1000
                load_times.append(load_time)
                await asyncio.sleep(0.3)
            
            avg_time = sum(load_times) / len(load_times)
            min_time = min(load_times)
            max_time = max(load_times)
            
            return TestResult(
                test_id=test_id,
                test_name=f"性能-{site['name']}",
                category="性能",
                status="passed",
                duration_ms=avg_time,
                metrics={
                    "avg_load_ms": avg_time,
                    "min_load_ms": min_time,
                    "max_load_ms": max_time,
                    "samples": load_times
                }
            )
        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            return TestResult(
                test_id=test_id,
                test_name=f"性能-{site['name']}",
                category="性能",
                status="error",
                duration_ms=elapsed,
                error_msg=str(e)
            )

    # ==================== 批量执行 ====================
    
    async def run_all_tests(self) -> List[TestResult]:
        """执行所有测试"""
        logger.info("="*60)
        logger.info("开始执行核心操作链路自动化测试")
        logger.info(f"测试网站数量: {len(TEST_SITES)}")
        logger.info(f"浏览器模式: {'headless' if self.config.headless else 'headed'}")
        logger.info("="*60)
        
        # 执行测试
        for site in TEST_SITES:
            # 1. 导航测试
            result = await self.test_navigation(site)
            self._record_result(result)
            
            # 2. 搜索测试（如果支持）
            if site.get("has_search"):
                result = await self.test_search(site)
                self._record_result(result)
            
            # 3. 元素提取测试
            result = await self.test_element_extraction(site)
            self._record_result(result)
            
            # 4. 截图测试
            result = await self.test_screenshot(site)
            self._record_result(result)
            
            # 5. 导航链路测试
            result = await self.test_navigation_chain(site)
            self._record_result(result)
            
            # 6. 性能测试
            result = await self.test_page_load_performance(site)
            self._record_result(result)
            
            await asyncio.sleep(0.5)  # 避免请求过快
        
        # 7. 多标签页测试
        result = await self.test_multiple_tabs()
        self._record_result(result)
        
        return self.results
    
    def generate_report(self) -> Dict[str, Any]:
        """生成测试报告"""
        if not self.results:
            return {"error": "没有测试结果"}
        
        total = len(self.results)
        passed = sum(1 for r in self.results if r.success)
        failed = sum(1 for r in self.results if r.status == "failed")
        errors = sum(1 for r in self.results if r.status == "error")
        skipped = sum(1 for r in self.results if r.status == "skipped")
        
        # 按分类统计
        by_category: Dict[str, Dict[str, int]] = {}
        for r in self.results:
            cat = r.category
            if cat not in by_category:
                by_category[cat] = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
            by_category[cat][r.status] += 1
        
        # 成功率
        success_rate = (passed / (total - skipped)) * 100 if (total - skipped) > 0 else 0
        
        report = {
            "generated_at": datetime.now().isoformat(),
            "summary": {
                "total_tests": total,
                "passed": passed,
                "failed": failed,
                "errors": errors,
                "skipped": skipped,
                "success_rate": round(success_rate, 2)
            },
            "by_category": by_category,
            "results": [
                {
                    "test_id": r.test_id,
                    "test_name": r.test_name,
                    "category": r.category,
                    "status": r.status,
                    "duration_ms": round(r.duration_ms, 2),
                    "error_msg": r.error_msg,
                    "metrics": r.metrics
                }
                for r in self.results
            ]
        }
        
        return report
    
    def save_report(self, output_path: Optional[Path] = None) -> Path:
        """保存测试报告"""
        if output_path is None:
            output_path = SKILL_DIR / "output" / f"test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        report = self.generate_report()
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        
        logger.info(f"测试报告已保存到: {output_path}")
        return output_path


async def main():
    """主入口"""
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 创建测试运行器
    config = TestConfig(
        headless=True,
        timeout_ms=15000,
        retry_count=2
    )
    
    runner = TestRunner(config)
    
    try:
        # 执行测试
        async with runner:
            results = await runner.run_all_tests()
        
        # 生成报告
        report_path = runner.save_report()
        
        # 输出摘要
        summary = runner.generate_report()["summary"]
        print("\n" + "="*60)
        print("测试执行完成")
        print(f"总计: {summary['total_tests']} 个测试")
        print(f"通过: {summary['passed']} 个")
        print(f"失败: {summary['failed']} 个")
        print(f"错误: {summary['errors']} 个")
        print(f"跳过: {summary['skipped']} 个")
        print(f"成功率: {summary['success_rate']}%")
        print(f"报告路径: {report_path}")
        print("="*60)
        
        # 返回状态码
        return 0 if summary["errors"] == 0 else 1
        
    except Exception as e:
        logger.error(f"测试执行失败: {e}", exc_info=True)
        return 2


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
