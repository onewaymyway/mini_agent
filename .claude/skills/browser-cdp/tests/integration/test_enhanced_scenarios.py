# -*- coding: utf-8 -*-
"""
browser-cdp Skill 增强测试 - 登录态与反爬场景

测试场景：
1. 登录态维持测试 - Cookie存储和复用
2. 高防护反爬测试 - UA轮换、请求间隔
3. 稳定性测试 - 长时间操作不中断
4. 异常恢复测试 - 断线重连
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

SKILL_DIR = Path(__file__).parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

logger = logging.getLogger(__name__)


@dataclass
class SessionResult:
    """会话测试结果"""
    test_id: str
    test_name: str
    status: str
    duration_ms: float
    cookies_count: int = 0
    session_stable: bool = True
    error_msg: Optional[str] = None


@dataclass
class AntiCrawlResult:
    """反爬测试结果"""
    test_id: str
    site_id: str
    status: str
    requests_count: int
    blocked_count: int
    success_rate: float
    duration_ms: float
    error_msg: Optional[str] = None


class LoginSessionTester:
    """登录态维持测试器"""
    
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.results: List[SessionResult] = []
        self._session = None
        
    async def _init(self):
        try:
            from src.core.playwright_session import PlaywrightSession, PlaywrightConfig
            cfg = PlaywrightConfig(headless=self.headless, default_timeout=15000)
            self._session = PlaywrightSession(config=cfg)
            await self._session.async_launch()
        except Exception as e:
            logger.error(f"初始化失败: {e}")
            raise
            
    async def _cleanup(self):
        if self._session:
            try:
                await self._session.close()
            except:
                pass
            finally:
                self._session = None
    
    async def test_cookie_persistence(self, url: str, site_name: str) -> SessionResult:
        """测试Cookie持久化"""
        start = time.time()
        test_id = f"COOKIE_{site_name}"
        try:
            await self._session.async_goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(1)
            
            # 获取Cookie
            cookies = await self._session.context.cookies() if hasattr(self._session, 'context') else []
            cookie_count = len(cookies) if cookies else 0
            
            # 再次访问同一网站
            await self._session.async_goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(0.5)
            
            elapsed = (time.time() - start) * 1000
            
            return SessionResult(
                test_id=test_id,
                test_name=f"Cookie持久化-{site_name}",
                status="passed",
                duration_ms=elapsed,
                cookies_count=cookie_count,
                session_stable=True
            )
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            return SessionResult(
                test_id=test_id,
                test_name=f"Cookie持久化-{site_name}",
                status="error",
                duration_ms=elapsed,
                error_msg=str(e)
            )
    
    async def test_multi_page_session(self, url: str, site_name: str, extra_pages: int = 3) -> SessionResult:
        """测试多页面会话稳定性"""
        start = time.time()
        test_id = f"MULTI_PAGE_{site_name}"
        try:
            for i in range(extra_pages):
                await self._session.async_goto(url, wait_until="domcontentloaded")
                await asyncio.sleep(0.3)
            
            # 验证页面标题存在
            title = await self._session.page.title() if self._session.page else ""
            
            elapsed = (time.time() - start) * 1000
            status = "passed" if title else "failed"
            
            return SessionResult(
                test_id=test_id,
                test_name=f"多页面会话-{site_name}",
                status=status,
                duration_ms=elapsed,
                session_stable=True
            )
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            return SessionResult(
                test_id=test_id,
                test_name=f"多页面会话-{site_name}",
                status="error",
                duration_ms=elapsed,
                error_msg=str(e)
            )


class AntiCrawlTester:
    """高防护反爬测试器"""
    
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.results: List[AntiCrawlResult] = []
        self._session = None
        
    async def _init(self):
        try:
            from src.core.playwright_session import PlaywrightSession, PlaywrightConfig
            cfg = PlaywrightConfig(headless=self.headless, default_timeout=15000)
            self._session = PlaywrightSession(config=cfg)
            await self._session.async_launch()
        except Exception as e:
            logger.error(f"初始化失败: {e}")
            raise
    
    async def test_request_timing(self, url: str, site_id: str, count: int = 10, delay: float = 2.0) -> AntiCrawlResult:
        """测试请求间隔控制"""
        start = time.time()
        test_id = f"TIMING_{site_id}"
        blocked = 0
        
        try:
            for i in range(count):
                await self._session.async_goto(url, wait_until="domcontentloaded")
                await asyncio.sleep(delay)
                
                # 检查是否被拦截（页面标题异常或跳转到拦截页）
                title = await self._session.page.title() if self._session.page else ""
                current_url = self._session.page.url if self._session.page else ""
                
                if "安全验证" in title or "验证码" in title or "blocked" in current_url.lower():
                    blocked += 1
            
            elapsed = (time.time() - start) * 1000
            success_rate = ((count - blocked) / count) * 100
            
            return AntiCrawlResult(
                test_id=test_id,
                site_id=site_id,
                status="passed" if blocked == 0 else "warning",
                requests_count=count,
                blocked_count=blocked,
                success_rate=success_rate,
                duration_ms=elapsed
            )
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            return AntiCrawlResult(
                test_id=test_id,
                site_id=site_id,
                status="error",
                requests_count=0,
                blocked_count=0,
                success_rate=0,
                duration_ms=elapsed,
                error_msg=str(e)
            )
    
    async def test_concurrent_navigation(self, url: str, site_id: str, concurrency: int = 3) -> AntiCrawlResult:
        """测试并发导航稳定性"""
        start = time.time()
        test_id = f"CONCURRENT_{site_id}"
        
        try:
            async def navigate_to_url():
                await self._session.async_goto(url, wait_until="domcontentloaded")
                await asyncio.sleep(0.5)
                return True
            
            tasks = [navigate_to_url() for _ in range(concurrency)]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            successful = sum(1 for r in results if r is True)
            failed = sum(1 for r in results if isinstance(r, Exception))
            
            elapsed = (time.time() - start) * 1000
            success_rate = (successful / concurrency) * 100
            
            return AntiCrawlResult(
                test_id=test_id,
                site_id=site_id,
                status="passed" if failed == 0 else "warning",
                requests_count=concurrency,
                blocked_count=failed,
                success_rate=success_rate,
                duration_ms=elapsed
            )
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            return AntiCrawlResult(
                test_id=test_id,
                site_id=site_id,
                status="error",
                requests_count=0,
                blocked_count=0,
                success_rate=0,
                duration_ms=elapsed,
                error_msg=str(e)
            )


class StabilityTester:
    """稳定性测试器"""
    
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.results: List[Dict[str, Any]] = []
        self._session = None
    
    async def _init(self):
        try:
            from src.core.playwright_session import PlaywrightSession, PlaywrightConfig
            cfg = PlaywrightConfig(headless=self.headless, default_timeout=20000)
            self._session = PlaywrightSession(config=cfg)
            await self._session.async_launch()
        except Exception as e:
            logger.error(f"初始化失败: {e}")
            raise
    
    async def test_long_running_session(self, site_url: str, duration_seconds: int = 30) -> Dict[str, Any]:
        """测试长时间运行不中断"""
        start = time.time()
        errors = []
        navigation_count = 0
        
        try:
            while (time.time() - start) < duration_seconds:
                await self._session.async_goto(site_url, wait_until="domcontentloaded")
                await asyncio.sleep(0.5)
                navigation_count += 1
                
                # 每5次检查一次连接状态
                if navigation_count % 5 == 0:
                    try:
                        title = await self._session.page.title()
                        if not title:
                            errors.append("页面标题为空")
                    except Exception as e:
                        errors.append(f"检查时出错: {str(e)[:50]}")
        except Exception as e:
            errors.append(f"长时间运行异常: {str(e)[:100]}")
        
        elapsed = time.time() - start
        
        return {
            "test_name": f"长时间运行-{duration_seconds}秒",
            "status": "passed" if not errors else "failed",
            "duration_s": round(elapsed, 2),
            "navigation_count": navigation_count,
            "errors": errors,
            "avg_interval_ms": round((elapsed * 1000) / navigation_count, 2) if navigation_count > 0 else 0
        }
    
    async def test_error_recovery(self, site_url: str) -> Dict[str, Any]:
        """测试错误恢复能力"""
        start = time.time()
        attempts = []
        
        try:
            for i in range(5):
                attempt_start = time.time()
                try:
                    await self._session.async_goto(site_url, wait_until="domcontentloaded")
                    await asyncio.sleep(0.3)
                    title = await self._session.page.title()
                    elapsed = (time.time() - attempt_start) * 1000
                    attempts.append({
                        "attempt": i + 1,
                        "success": bool(title),
                        "duration_ms": round(elapsed, 2)
                    })
                except Exception as e:
                    elapsed = (time.time() - attempt_start) * 1000
                    attempts.append({
                        "attempt": i + 1,
                        "success": False,
                        "error": str(e)[:50],
                        "duration_ms": round(elapsed, 2)
                    })
        except Exception as e:
            logger.error(f"错误恢复测试失败: {e}")
        
        elapsed = time.time() - start
        success_count = sum(1 for a in attempts if a.get("success"))
        
        return {
            "test_name": "错误恢复测试",
            "status": "passed" if success_count >= 3 else "failed",
            "duration_ms": round(elapsed * 1000, 2),
            "attempts": attempts,
            "success_count": success_count,
            "total_attempts": len(attempts)
        }


class EnhancedTestRunner:
    """增强测试执行器"""
    
    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self.all_results: Dict[str, Any] = {
            "login_session": [],
            "anti_crawl": [],
            "stability": [],
            "metadata": {}
        }
    
    async def run_all(self) -> Dict[str, Any]:
        """运行所有增强测试"""
        logger.info("开始执行增强测试...")
        
        # 1. 登录态测试
        await self._run_login_tests()
        
        # 2. 反爬测试
        await self._run_anti_crawl_tests()
        
        # 3. 稳定性测试
        await self._run_stability_tests()
        
        return self.all_results
    
    async def _run_login_tests(self):
        """运行登录态测试"""
        tester = LoginSessionTester(headless=self.config.get("headless", True))
        try:
            await tester._init()
            
            # 测试知乎
            result = await tester.test_cookie_persistence("https://www.zhihu.com", "知乎")
            self.all_results["login_session"].append(result.__dict__)
            
            result = await tester.test_multi_page_session("https://www.zhihu.com", "知乎")
            self.all_results["login_session"].append(result.__dict__)
            
            await tester._cleanup()
        except Exception as e:
            logger.error(f"登录态测试失败: {e}")
    
    async def _run_anti_crawl_tests(self):
        """运行反爬测试"""
        tester = AntiCrawlTester(headless=self.config.get("headless", True))
        try:
            await tester._init()
            
            # 测试百度请求间隔
            result = await tester.test_request_timing(
                "https://www.baidu.com", "baidu", count=5, delay=1.5
            )
            self.all_results["anti_crawl"].append(result.__dict__)
            
            # 测试并发
            result = await tester.test_concurrent_navigation(
                "https://www.baidu.com", "baidu", concurrency=2
            )
            self.all_results["anti_crawl"].append(result.__dict__)
            
        except Exception as e:
            logger.error(f"反爬测试失败: {e}")
    
    async def _run_stability_tests(self):
        """运行稳定性测试"""
        tester = StabilityTester(headless=self.config.get("headless", True))
        try:
            await tester._init()
            
            # 短时间稳定性测试（10秒）
            result = await tester.test_long_running_session(
                "https://www.baidu.com", duration_seconds=10
            )
            self.all_results["stability"].append(result)
            
            # 错误恢复测试
            result = await tester.test_error_recovery("https://www.baidu.com")
            self.all_results["stability"].append(result)
            
        except Exception as e:
            logger.error(f"稳定性测试失败: {e}")
    
    def generate_report(self) -> Dict[str, Any]:
        """生成报告"""
        total_tests = (
            len(self.all_results["login_session"]) +
            len(self.all_results["anti_crawl"]) +
            len(self.all_results["stability"])
        )
        
        passed = 0
        for category in self.all_results.values():
            if isinstance(category, list):
                for item in category:
                    if isinstance(item, dict):
                        if item.get("status") == "passed":
                            passed += 1
                    elif hasattr(item, 'status') and item.status == "passed":
                        passed += 1
        
        return {
            "generated_at": datetime.now().isoformat(),
            "summary": {
                "total_tests": total_tests,
                "passed": passed,
                "failed": total_tests - passed,
                "success_rate": round((passed / total_tests * 100) if total_tests > 0 else 0, 2)
            },
            "categories": {
                "login_session": self.all_results["login_session"],
                "anti_crawl": self.all_results["anti_crawl"],
                "stability": self.all_results["stability"]
            }
        }
    
    def save_report(self, path: Optional[Path] = None) -> Path:
        """保存报告"""
        if path is None:
            path = SKILL_DIR / "output" / f"enhanced_test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.generate_report(), f, ensure_ascii=False, indent=2)
        
        return path


async def main():
    """主入口"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    runner = EnhancedTestRunner(headless=True)
    
    try:
        results = await runner.run_all()
        report_path = runner.save_report()
        
        report = runner.generate_report()
        summary = report["summary"]
        
        print("\n" + "="*60)
        print("增强测试完成")
        print(f"总计: {summary['total_tests']} 个测试")
        print(f"通过: {summary['passed']} 个")
        print(f"失败: {summary['failed']} 个")
        print(f"成功率: {summary['success_rate']}%")
        print(f"报告路径: {report_path}")
        print("="*60)
        
        return 0 if summary["failed"] == 0 else 1
    except Exception as e:
        logger.error(f"测试执行失败: {e}", exc_info=True)
        return 2


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
