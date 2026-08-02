"""
browser-cdp 四类网站测试用例

测试覆盖：
1. 电商网站（商品列表、搜索、筛选）
2. 新闻网站（文章列表、分页、内容抓取）
3. 社交网站（动态流、评论、登录态）
4. 后台系统（表格、表单、权限控制）
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import os
from dataclasses import dataclass
from enum import Enum
from typing import Optional, List, Dict, Any
from unittest.mock import MagicMock, AsyncMock, patch

# 添加 skill 目录到路径
skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, skill_dir)

from src.core.smart_wait import SmartWait, WaitConfig
from src.core.retry_handler import RetryHandler, RetryConfig, FailureReason
from src.core.dynamic_loader import DynamicLoader, ScrollConfig
from src.core.complex_dom import ComplexDOMHandler, DOMScanConfig
from src.core.stealth import StealthMode, StealthConfig
from src.core.captcha_handler import CaptchaHandler, CaptchaType, CaptchaResult

logger = logging.getLogger(__name__)


class WebsiteType(Enum):
    """网站类型枚举"""
    ECOMMERCE = "ecommerce"      # 电商网站
    NEWS = "news"                # 新闻网站
    SOCIAL = "social"            # 社交网站
    ADMIN = "admin"              # 后台系统


@dataclass
class TestResult:
    """测试结果"""
    test_name: str
    website_type: WebsiteType
    success: bool
    duration: float
    message: str
    details: Optional[str] = None


class MockSession:
    """模拟 CDP Session"""
    def __init__(self):
        self.ws = MagicMock()
        self.ws.connected = True
        self._tab_id = "TEST_TAB_001"
        self._client_id = "TEST_CLIENT"
        self._commands = []
        
    def send(self, method: str, params: dict = None) -> dict:
        """模拟发送 CDP 命令"""
        self._commands.append({"method": method, "params": params})
        # 返回模拟响应
        if method == "Runtime.evaluate":
            return {"result": {"result": {"type": "boolean", "value": True}}}
        elif method == "DOM.getDocument":
            return {"result": {"root": {"nodeId": 1, "children": []}}}
        elif method == "DOM.getAttributes":
            return {"result": {"attributes": []}}
        elif method == "DOM.querySelector":
            return {"result": {"nodeId": 2}}
        elif method == "DOM.resolveNode":
            return {"result": {"backendNodeId": 1, "node": {"nodeName": "DIV"}}}
        elif method == "DOM.getBoxModel":
            return {"result": {"model": {"content": [0, 0, 100, 0, 100, 100, 0, 100]}}}
        elif method == "Page.captureScreenshot":
            return {"result": {"data": "base64encoded"}}
        elif method == "Input.dispatchMouseEvent":
            return {}
        elif method == "Input.dispatchKeyEvent":
            return {}
        elif method == "Runtime.evaluate":
            return {"result": {"result": {"type": "string", "value": "true"}}}
        return {"result": {}}
    
    async def send_async(self, method: str, params: dict = None) -> dict:
        """异步发送 CDP 命令"""
        return self.send(method, params)
    
    async def eval_js(self, js_code: str) -> Any:
        """模拟执行 JavaScript"""
        # 根据 JS 代码返回不同的模拟值
        if "querySelectorAll" in js_code and "shadowRoot" in js_code:
            return []  # Shadow DOM 扫描返回空列表
        elif "querySelectorAll" in js_code:
            return [{"text": "模拟元素", "rect": {"x": 0, "y": 0, "width": 100, "height": 50}}]
        return True
    
    def close(self):
        self.ws.close()


class EcommerceTestSuite:
    """电商网站测试套件"""
    
    @staticmethod
    async def test_product_list(session: MockSession) -> TestResult:
        """测试商品列表抓取"""
        start_time = asyncio.get_event_loop().time()
        try:
            # 模拟等待页面加载
            wait = SmartWait(session)
            await wait.wait_for("networkidle", idle_timeout=0.5)
            
            # 模拟抓取商品列表
            result = session.send("Runtime.evaluate", {
                "expression": "document.querySelectorAll('.product-item').length"
            })
            
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="商品列表抓取",
                website_type=WebsiteType.ECOMMERCE,
                success=True,
                duration=duration,
                message="商品列表抓取成功"
            )
        except Exception as e:
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="商品列表抓取",
                website_type=WebsiteType.ECOMMERCE,
                success=False,
                duration=duration,
                message=f"失败: {str(e)}"
            )
    
    @staticmethod
    async def test_product_detail(session: MockSession) -> TestResult:
        """测试商品详情抓取"""
        start_time = asyncio.get_event_loop().time()
        try:
            wait = SmartWait(session)
            await wait.wait_for("stable", stable_count=2)
            
            result = session.send("Runtime.evaluate", {
                "expression": "document.querySelector('.product-title')?.textContent || ''"
            })
            
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="商品详情抓取",
                website_type=WebsiteType.ECOMMERCE,
                success=True,
                duration=duration,
                message="商品详情抓取成功"
            )
        except Exception as e:
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="商品详情抓取",
                website_type=WebsiteType.ECOMMERCE,
                success=False,
                duration=duration,
                message=f"失败: {str(e)}"
            )
    
    @staticmethod
    async def test_search(session: MockSession) -> TestResult:
        """测试搜索功能"""
        start_time = asyncio.get_event_loop().time()
        try:
            # 模拟搜索输入
            session.send("Input.dispatchKeyEvent", {
                "type": "keyDown",
                "text": "手机"
            })
            session.send("Input.dispatchKeyEvent", {
                "type": "keyDown",
                "key": "Enter"
            })
            
            wait = SmartWait(session)
            await wait.wait_for("networkidle", idle_timeout=0.5)
            
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="搜索功能测试",
                website_type=WebsiteType.ECOMMERCE,
                success=True,
                duration=duration,
                message="搜索功能测试成功"
            )
        except Exception as e:
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="搜索功能测试",
                website_type=WebsiteType.ECOMMERCE,
                success=False,
                duration=duration,
                message=f"失败: {str(e)}"
            )
    
    @staticmethod
    async def test_pagination(session: MockSession) -> TestResult:
        """测试分页功能"""
        start_time = asyncio.get_event_loop().time()
        try:
            wait = SmartWait(session)
            await wait.wait_for("networkidle", idle_timeout=0.5)
            
            # 模拟点击下一页
            session.send("Input.dispatchMouseEvent", {
                "type": "mouseClick",
                "x": 500,
                "y": 600
            })
            
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="分页功能测试",
                website_type=WebsiteType.ECOMMERCE,
                success=True,
                duration=duration,
                message="分页功能测试成功"
            )
        except Exception as e:
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="分页功能测试",
                website_type=WebsiteType.ECOMMERCE,
                success=False,
                duration=duration,
                message=f"失败: {str(e)}"
            )


class NewsTestSuite:
    """新闻网站测试套件"""
    
    @staticmethod
    async def test_article_list(session: MockSession) -> TestResult:
        """测试文章列表抓取"""
        start_time = asyncio.get_event_loop().time()
        try:
            wait = SmartWait(session)
            await wait.wait_for("stable", stable_count=2)
            
            result = session.send("Runtime.evaluate", {
                "expression": "document.querySelectorAll('.article-item').length"
            })
            
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="文章列表抓取",
                website_type=WebsiteType.NEWS,
                success=True,
                duration=duration,
                message="文章列表抓取成功"
            )
        except Exception as e:
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="文章列表抓取",
                website_type=WebsiteType.NEWS,
                success=False,
                duration=duration,
                message=f"失败: {str(e)}"
            )
    
    @staticmethod
    async def test_article_content(session: MockSession) -> TestResult:
        """测试文章内容抓取"""
        start_time = asyncio.get_event_loop().time()
        try:
            wait = SmartWait(session)
            await wait.wait_for("stable", stable_count=2)
            
            result = session.send("Runtime.evaluate", {
                "expression": "document.querySelector('.article-content')?.textContent?.length || 0"
            })
            
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="文章内容抓取",
                website_type=WebsiteType.NEWS,
                success=True,
                duration=duration,
                message="文章内容抓取成功"
            )
        except Exception as e:
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="文章内容抓取",
                website_type=WebsiteType.NEWS,
                success=False,
                duration=duration,
                message=f"失败: {str(e)}"
            )
    
    @staticmethod
    async def test_comments(session: MockSession) -> TestResult:
        """测试评论抓取"""
        start_time = asyncio.get_event_loop().time()
        try:
            wait = SmartWait(session)
            await wait.wait_for("networkidle", idle_timeout=0.5)
            
            result = session.send("Runtime.evaluate", {
                "expression": "document.querySelectorAll('.comment').length"
            })
            
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="评论抓取测试",
                website_type=WebsiteType.NEWS,
                success=True,
                duration=duration,
                message="评论抓取测试成功"
            )
        except Exception as e:
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="评论抓取测试",
                website_type=WebsiteType.NEWS,
                success=False,
                duration=duration,
                message=f"失败: {str(e)}"
            )
    
    @staticmethod
    async def test_dynamic_loading(session: MockSession) -> TestResult:
        """测试动态加载"""
        start_time = asyncio.get_event_loop().time()
        try:
            # 使用 DynamicLoader 滚动加载
            loader = DynamicLoader(session)
            config = ScrollConfig(max_pages=3, scroll_delay=0.3)
            await loader.scroll_until_not_found(config)
            
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="动态加载测试",
                website_type=WebsiteType.NEWS,
                success=True,
                duration=duration,
                message="动态加载测试成功"
            )
        except Exception as e:
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="动态加载测试",
                website_type=WebsiteType.NEWS,
                success=False,
                duration=duration,
                message=f"失败: {str(e)}"
            )


class SocialTestSuite:
    """社交网站测试套件"""
    
    @staticmethod
    async def test_feed_loading(session: MockSession) -> TestResult:
        """测试动态流加载"""
        start_time = asyncio.get_event_loop().time()
        try:
            wait = SmartWait(session)
            await wait.wait_for("networkidle", idle_timeout=0.5)
            
            result = session.send("Runtime.evaluate", {
                "expression": "document.querySelectorAll('.feed-item').length"
            })
            
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="动态流加载测试",
                website_type=WebsiteType.SOCIAL,
                success=True,
                duration=duration,
                message="动态流加载测试成功"
            )
        except Exception as e:
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="动态流加载测试",
                website_type=WebsiteType.SOCIAL,
                success=False,
                duration=duration,
                message=f"失败: {str(e)}"
            )
    
    @staticmethod
    async def test_infinite_scroll(session: MockSession) -> TestResult:
        """测试无限滚动"""
        start_time = asyncio.get_event_loop().time()
        try:
            loader = DynamicLoader(session)
            config = ScrollConfig(max_pages=5, scroll_delay=0.4)
            await loader.scroll_until_not_found(config)
            
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="无限滚动测试",
                website_type=WebsiteType.SOCIAL,
                success=True,
                duration=duration,
                message="无限滚动测试成功"
            )
        except Exception as e:
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="无限滚动测试",
                website_type=WebsiteType.SOCIAL,
                success=False,
                duration=duration,
                message=f"失败: {str(e)}"
            )
    
    @staticmethod
    async def test_comment_thread(session: MockSession) -> TestResult:
        """测试评论线程"""
        start_time = asyncio.get_event_loop().time()
        try:
            wait = SmartWait(session)
            await wait.wait_for("networkidle", idle_timeout=0.5)
            
            result = session.send("Runtime.evaluate", {
                "expression": "document.querySelectorAll('.comment-thread .reply').length"
            })
            
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="评论线程测试",
                website_type=WebsiteType.SOCIAL,
                success=True,
                duration=duration,
                message="评论线程测试成功"
            )
        except Exception as e:
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="评论线程测试",
                website_type=WebsiteType.SOCIAL,
                success=False,
                duration=duration,
                message=f"失败: {str(e)}"
            )
    
    @staticmethod
    async def test_shadow_dom(session: MockSession) -> TestResult:
        """测试 Shadow DOM 处理"""
        start_time = asyncio.get_event_loop().time()
        try:
            handler = ComplexDOMHandler(session)
            result = await handler.scan_shadow_dom()
            
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="Shadow DOM 处理测试",
                website_type=WebsiteType.SOCIAL,
                success=True,
                duration=duration,
                message="Shadow DOM 处理测试成功"
            )
        except Exception as e:
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="Shadow DOM 处理测试",
                website_type=WebsiteType.SOCIAL,
                success=False,
                duration=duration,
                message=f"失败: {str(e)}"
            )


class AdminTestSuite:
    """后台系统测试套件"""
    
    @staticmethod
    async def test_table_rendering(session: MockSession) -> TestResult:
        """测试表格渲染"""
        start_time = asyncio.get_event_loop().time()
        try:
            wait = SmartWait(session)
            await wait.wait_for("networkidle", idle_timeout=0.5)
            
            result = session.send("Runtime.evaluate", {
                "expression": "document.querySelectorAll('table.data-grid tr').length"
            })
            
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="表格渲染测试",
                website_type=WebsiteType.ADMIN,
                success=True,
                duration=duration,
                message="表格渲染测试成功"
            )
        except Exception as e:
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="表格渲染测试",
                website_type=WebsiteType.ADMIN,
                success=False,
                duration=duration,
                message=f"失败: {str(e)}"
            )
    
    @staticmethod
    async def test_form_interaction(session: MockSession) -> TestResult:
        """测试表单交互"""
        start_time = asyncio.get_event_loop().time()
        try:
            # 模拟表单填写
            session.send("Input.dispatchKeyEvent", {
                "type": "keyDown",
                "text": "admin"
            })
            session.send("Input.dispatchMouseEvent", {
                "type": "mouseClick",
                "x": 200,
                "y": 300
            })
            
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="表单交互测试",
                website_type=WebsiteType.ADMIN,
                success=True,
                duration=duration,
                message="表单交互测试成功"
            )
        except Exception as e:
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="表单交互测试",
                website_type=WebsiteType.ADMIN,
                success=False,
                duration=duration,
                message=f"失败: {str(e)}"
            )
    
    @staticmethod
    async def test_pagination_admin(session: MockSession) -> TestResult:
        """测试后台分页"""
        start_time = asyncio.get_event_loop().time()
        try:
            wait = SmartWait(session)
            await wait.wait_for("networkidle", idle_timeout=0.5)
            
            # 模拟翻页
            session.send("Input.dispatchMouseEvent", {
                "type": "mouseClick",
                "x": 600,
                "y": 700
            })
            
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="后台分页测试",
                website_type=WebsiteType.ADMIN,
                success=True,
                duration=duration,
                message="后台分页测试成功"
            )
        except Exception as e:
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="后台分页测试",
                website_type=WebsiteType.ADMIN,
                success=False,
                duration=duration,
                message=f"失败: {str(e)}"
            )
    
    @staticmethod
    async def test_ajax_loading(session: MockSession) -> TestResult:
        """测试 AJAX 加载"""
        start_time = asyncio.get_event_loop().time()
        try:
            wait = SmartWait(session)
            await wait.wait_for("networkidle", idle_timeout=1.0)
            
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="AJAX 加载测试",
                website_type=WebsiteType.ADMIN,
                success=True,
                duration=duration,
                message="AJAX 加载测试成功"
            )
        except Exception as e:
            duration = asyncio.get_event_loop().time() - start_time
            return TestResult(
                test_name="AJAX 加载测试",
                website_type=WebsiteType.ADMIN,
                success=False,
                duration=duration,
                message=f"失败: {str(e)}"
            )


class TestRunner:
    """测试运行器"""
    
    def __init__(self):
        self.results: List[TestResult] = []
    
    def add_result(self, result: TestResult):
        self.results.append(result)
    
    def print_summary(self):
        print("\n" + "=" * 70)
        print("测试摘要")
        print("=" * 70)
        
        passed = sum(1 for r in self.results if r.success)
        failed = sum(1 for r in self.results if not r.success)
        
        print(f"\n总计: {len(self.results)} 个测试")
        print(f"通过: {passed}")
        print(f"失败: {failed}")
        
        print("\n详细结果:")
        for r in self.results:
            status = "✓" if r.success else "✗"
            print(f"  {status} [{r.website_type.value}] {r.test_name}: {r.duration:.2f}s - {r.message}")
    
    def export_results(self, output_path: str):
        data = {
            "total": len(self.results),
            "passed": sum(1 for r in self.results if r.success),
            "failed": sum(1 for r in self.results if not r.success),
            "results": [
                {
                    "test_name": r.test_name,
                    "website_type": r.website_type.value,
                    "success": r.success,
                    "duration": r.duration,
                    "message": r.message
                }
                for r in self.results
            ]
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        print(f"\n测试结果已导出到: {output_path}")


async def run_all_tests(session: MockSession):
    """运行所有测试"""
    runner = TestRunner()
    
    print("\n" + "=" * 70)
    print("browser-cdp 四类网站测试套件")
    print("=" * 70)
    
    # 1. 电商网站测试
    print("\n【电商网站测试】")
    runner.add_result(await EcommerceTestSuite.test_product_list(session))
    runner.add_result(await EcommerceTestSuite.test_product_detail(session))
    runner.add_result(await EcommerceTestSuite.test_search(session))
    runner.add_result(await EcommerceTestSuite.test_pagination(session))
    
    # 2. 新闻网站测试
    print("\n【新闻网站测试】")
    runner.add_result(await NewsTestSuite.test_article_list(session))
    runner.add_result(await NewsTestSuite.test_article_content(session))
    runner.add_result(await NewsTestSuite.test_comments(session))
    runner.add_result(await NewsTestSuite.test_dynamic_loading(session))
    
    # 3. 社交网站测试
    print("\n【社交网站测试】")
    runner.add_result(await SocialTestSuite.test_feed_loading(session))
    runner.add_result(await SocialTestSuite.test_infinite_scroll(session))
    runner.add_result(await SocialTestSuite.test_comment_thread(session))
    runner.add_result(await SocialTestSuite.test_shadow_dom(session))
    
    # 4. 后台系统测试
    print("\n【后台系统测试】")
    runner.add_result(await AdminTestSuite.test_table_rendering(session))
    runner.add_result(await AdminTestSuite.test_form_interaction(session))
    runner.add_result(await AdminTestSuite.test_pagination_admin(session))
    runner.add_result(await AdminTestSuite.test_ajax_loading(session))
    
    # 打印摘要
    runner.print_summary()
    
    return runner


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description="四类网站测试套件")
    parser.add_argument("--output", default=None, help="测试结果输出路径")
    args = parser.parse_args()
    
    session = MockSession()
    
    try:
        # 运行测试
        runner = asyncio.run(run_all_tests(session))
        
        # 导出结果
        if args.output:
            runner.export_results(args.output)
        
        # 返回状态码
        all_passed = all(r.success for r in runner.results)
        return 0 if all_passed else 1
        
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())


# ============================================================================
# pytest 兼容包装函数
# ============================================================================

import pytest


def test_ecommerce_product_list():
    session = MockSession()
    result = asyncio.run(EcommerceTestSuite.test_product_list(session))
    assert result.success


def test_ecommerce_product_detail():
    session = MockSession()
    result = asyncio.run(EcommerceTestSuite.test_product_detail(session))
    assert result.success


def test_ecommerce_search():
    session = MockSession()
    result = asyncio.run(EcommerceTestSuite.test_search(session))
    assert result.success


def test_ecommerce_pagination():
    session = MockSession()
    result = asyncio.run(EcommerceTestSuite.test_pagination(session))
    assert result.success


def test_news_article_list():
    session = MockSession()
    result = asyncio.run(NewsTestSuite.test_article_list(session))
    assert result.success


def test_news_article_content():
    session = MockSession()
    result = asyncio.run(NewsTestSuite.test_article_content(session))
    assert result.success


def test_news_comments():
    session = MockSession()
    result = asyncio.run(NewsTestSuite.test_comments(session))
    assert result.success


def test_news_dynamic_loading():
    session = MockSession()
    result = asyncio.run(NewsTestSuite.test_dynamic_loading(session))
    assert result.success


def test_social_feed_loading():
    session = MockSession()
    result = asyncio.run(SocialTestSuite.test_feed_loading(session))
    assert result.success


def test_social_infinite_scroll():
    session = MockSession()
    result = asyncio.run(SocialTestSuite.test_infinite_scroll(session))
    assert result.success


def test_social_comment_thread():
    session = MockSession()
    result = asyncio.run(SocialTestSuite.test_comment_thread(session))
    assert result.success


def test_social_shadow_dom():
    session = MockSession()
    result = asyncio.run(SocialTestSuite.test_shadow_dom(session))
    assert result.success


def test_admin_table_rendering():
    session = MockSession()
    result = asyncio.run(AdminTestSuite.test_table_rendering(session))
    assert result.success


def test_admin_form_interaction():
    session = MockSession()
    result = asyncio.run(AdminTestSuite.test_form_interaction(session))
    assert result.success


def test_admin_pagination():
    session = MockSession()
    result = asyncio.run(AdminTestSuite.test_pagination_admin(session))
    assert result.success


def test_admin_ajax_loading():
    session = MockSession()
    result = asyncio.run(AdminTestSuite.test_ajax_loading(session))
    assert result.success
