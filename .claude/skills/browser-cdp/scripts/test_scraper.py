"""
test_scraper.py - 网站抓取框架测试脚本

测试 WebsiteScraper 和 BrowserManager 的基本功能。

用法：
  python scripts/test_scraper.py                    # 运行所有测试
  python scripts/test_scraper.py --quick            # 仅运行快速测试
  python scripts/test_scraper.py --url https://example.com  # 测试指定 URL
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import List, Dict, Any

# 添加 skill 根目录到路径
SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SKILL_ROOT)

from src.core.website_scraper import WebsiteScraper, ScrapeConfig, ScrapeResult
from src.core.browser_manager import BrowserManager, get_manager, reset_manager


class TestRunner:
    """测试运行器"""
    
    def __init__(self):
        self.results: List[Dict[str, Any]] = []
        self.manager = BrowserManager()
    
    def run_test(self, name: str, test_func):
        """运行单个测试"""
        print(f"\n{'='*60}")
        print(f"测试: {name}")
        print('='*60)
        
        start = time.time()
        try:
            test_func()
            duration = int((time.time() - start) * 1000)
            self.results.append({
                "name": name,
                "status": "passed",
                "duration_ms": duration,
            })
            print(f"✓ 通过 ({duration}ms)")
            return True
        except Exception as e:
            duration = int((time.time() - start) * 1000)
            self.results.append({
                "name": name,
                "status": "failed",
                "error": str(e),
                "duration_ms": duration,
            })
            print(f"✗ 失败 ({duration}ms): {e}")
            return False
    
    def test_browser_manager(self):
        """测试浏览器管理器"""
        # 测试 Playwright 启动
        session = self.manager.launch_playwright(headless=True)
        assert session is not None, "Playwright 启动失败"
        assert session.is_alive(), "Playwright 会话未存活"
        
        # 测试获取页面
        page = session.get_page()
        assert page is not None, "无法获取页面"
        
        # 测试导航
        page.goto("https://httpbin.org/get", timeout=15000)
        assert "httpbin.org" in page.url, f"导航失败: {page.url}"
        
        # 测试 JS 执行
        result = page.evaluate("1 + 1")
        assert result == 2, f"JS 执行失败: {result}"
        
        # 测试截图
        screenshot_path = os.path.join(SKILL_ROOT, "temp", f"test_{int(time.time())}.png")
        os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
        page.screenshot(path=screenshot_path)
        assert os.path.exists(screenshot_path), "截图失败"
        
        print(f"  浏览器管理器测试通过")
        print(f"  - Playwright 启动: OK")
        print(f"  - 页面导航: OK")
        print(f"  - JS 执行: OK")
        print(f"  - 截图: OK ({screenshot_path})")
    
    def test_website_scraper(self, url: str = "https://httpbin.org/get"):
        """测试网站抓取器"""
        import asyncio
        config = ScrapeConfig(
            timeout=30.0,
            stealth_mode=True,
            wait_for_network_idle=True,
            screenshot_on_error=True,
        )
        
        scraper = WebsiteScraper(url, config)
        
        # 检测是否在 asyncio 循环中
        try:
            asyncio.get_running_loop()
            # 在 asyncio 循环中，使用 ThreadPoolExecutor 运行同步方法
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                result = executor.submit(scraper.scrape).result()
                # 在同一个线程中关闭
                executor.submit(scraper.close).result()
        except RuntimeError:
            # 不在 asyncio 循环中，使用同步 scrape
            result = scraper.scrape()
            scraper.close()
        
        assert result.success, f"抓取失败: {result.error}"
        assert len(result.data) > 0, "未提取到数据"
        assert "title" in result.data, "未提取到标题"
        
        print(f"  抓取结果:")
        print(f"  - URL: {result.url}")
        print(f"  - 标题: {result.data.get('title', 'N/A')}")
        print(f"  - 数据键: {list(result.data.keys())}")
        if result.screenshot:
            print(f"  - 截图: {result.screenshot}")
    
    def test_batch_scraper(self):
        """测试批量抓取"""
        from src.core.website_scraper import BatchScraper
        
        urls = [
            "https://httpbin.org/get",
            "https://httpbin.org/html",
        ]
        
        batch = BatchScraper(concurrent=2)
        results = batch.scrape_batch(urls)
        
        assert len(results) == 2, f"预期 2 个结果，得到 {len(results)}"
        
        summary = batch.get_summary()
        print(f"  批量抓取摘要:")
        print(f"  - 总数: {summary['total']}")
        print(f"  - 成功: {summary['success']}")
        print(f"  - 失败: {summary['failed']}")
        print(f"  - 成功率: {summary['success_rate']:.1%}")
    
    def test_custom_extraction(self):
        """测试自定义数据提取"""
        url = "https://httpbin.org/html"
        
        config = ScrapeConfig(
            extract_selectors={
                "heading": "h1",
                "paragraphs": "p",
            },
            extract_js="""
                () => {
                    return {
                        body_text: document.body.innerText.substring(0, 500),
                        link_count: document.querySelectorAll('a').length
                    };
                }
            """,
        )
        
        scraper = WebsiteScraper(url, config)
        result = scraper.scrape()
        scraper.close()
        
        assert result.success, f"抓取失败: {result.error}"
        assert "heading" in result.data, "未提取到 heading"
        assert "paragraphs" in result.data, "未提取到 paragraphs"
        assert "body_text" in result.data, "未提取到 body_text"
        
        print(f"  自定义提取结果:")
        print(f"  - heading: {result.data.get('heading', 'N/A')[:50]}...")
        print(f"  - paragraphs count: {len(result.data.get('paragraphs', []))}")
        print(f"  - body_text length: {len(result.data.get('body_text', ''))}")
    
    def test_error_handling(self):
        """测试错误处理"""
        # 测试无效 URL
        scraper = WebsiteScraper("https://this-domain-does-not-exist-12345.com")
        result = scraper.scrape()
        scraper.close()
        
        assert not result.success, "无效 URL 应该失败"
        assert result.error is not None, "应该返回错误信息"
        
        print(f"  错误处理测试:")
        print(f"  - 无效 URL 正确返回失败")
        print(f"  - 错误信息: {result.error[:100]}...")
    
    def generate_report(self):
        """生成测试报告"""
        passed = sum(1 for r in self.results if r["status"] == "passed")
        failed = sum(1 for r in self.results if r["status"] == "failed")
        total = len(self.results)
        
        report = {
            "timestamp": datetime.now().isoformat(),
            "total": total,
            "passed": passed,
            "failed": failed,
            "results": self.results,
        }
        
        # 保存报告
        output_dir = os.path.join(SKILL_ROOT, "output", "test_reports")
        os.makedirs(output_dir, exist_ok=True)
        report_path = os.path.join(output_dir, f"test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        
        print(f"\n{'='*60}")
        print(f"测试报告")
        print('='*60)
        print(f"总计: {total}")
        print(f"通过: {passed}")
        print(f"失败: {failed}")
        print(f"报告: {report_path}")
        print('='*60)
        
        return report


def main():
    parser = argparse.ArgumentParser(description="网站抓取框架测试")
    parser.add_argument("--quick", action="store_true", help="仅运行快速测试")
    parser.add_argument("--url", help="测试指定 URL")
    parser.add_argument("--verbose", action="store_true", help="详细输出")
    
    args = parser.parse_args()
    
    runner = TestRunner()
    
    # 运行测试
    tests = [
        ("浏览器管理器", runner.test_browser_manager),
        ("网站抓取器", lambda: runner.test_website_scraper(args.url or "https://httpbin.org/get")),
        ("批量抓取", runner.test_batch_scraper),
        ("自定义提取", runner.test_custom_extraction),
        ("错误处理", runner.test_error_handling),
    ]
    
    if args.quick:
        tests = tests[:2]  # 仅运行前两个测试
    
    for name, test_func in tests:
        runner.run_test(name, test_func)
    
    # 生成报告
    report = runner.generate_report()
    
    # 清理
    runner.manager.close_all()
    reset_manager()
    
    # 返回退出码
    sys.exit(0 if report["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
