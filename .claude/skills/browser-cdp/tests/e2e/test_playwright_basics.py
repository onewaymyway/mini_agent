"""
Playwright 基础能力验证测试
验证浏览器启动、页面访问、搜索交互等核心功能
"""
import pytest
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
import time
import os


# 测试网站列表（国内优先）
TEST_SITES = [
    {"name": "百度", "url": "https://www.baidu.com", "type": "search"},
    {"name": "Bing", "url": "https://www.bing.com", "type": "search"},
    {"name": "淘宝", "url": "https://www.taobao.com", "type": "ecommerce"},
    {"name": "京东", "url": "https://www.jd.com", "type": "ecommerce"},
    {"name": "新浪财经", "url": "https://finance.sina.com.cn", "type": "finance"},
    {"name": "东方财富", "url": "https://www.eastmoney.com", "type": "finance"},
    {"name": "雪球", "url": "https://xueqiu.com", "type": "finance"},
    {"name": "知乎", "url": "https://www.zhihu.com", "type": "content"},
    {"name": "Bilibili", "url": "https://www.bilibili.com", "type": "video"},
]


class TestPlaywrightBasics:
    """Playwright 基础能力测试"""
    
    @pytest.fixture(scope="class")
    def browser(self):
        """创建浏览器实例"""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            yield browser
            browser.close()
    
    def test_browser_launch(self, browser):
        """测试浏览器启动"""
        assert browser is not None
        assert browser.is_connected()
    
    @pytest.mark.parametrize("site", TEST_SITES)
    def test_site_access(self, browser, site):
        """测试网站访问"""
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = context.new_page()
        
        try:
            response = page.goto(site["url"], timeout=15000)
            assert response is not None
            # 允许 4xx/5xx，只要页面能加载
            print(f"✓ {site['name']}: HTTP {response.status}")
        except PlaywrightTimeout:
            pytest.skip(f"{site['name']} 访问超时（网络限制）")
        finally:
            context.close()
    
    def test_bing_search(self, browser):
        """测试 Bing 搜索"""
        context = browser.new_context()
        page = context.new_page()
        
        try:
            page.goto("https://www.bing.com", timeout=15000)
            # 填写搜索框
            page.fill('#sb_form_q', 'Python programming')
            page.press('#sb_form_q', 'Enter')
            
            # 等待搜索结果
            page.wait_for_url('**/search*', timeout=10000)
            
            # 验证搜索结果页面
            title = page.title()
            assert 'Python' in title or 'search' in title.lower()
            print(f"✓ Bing 搜索成功: {title}")
        except PlaywrightTimeout:
            pytest.skip("Bing 搜索超时")
        finally:
            context.close()
    
    def test_baidu_search_js(self, browser):
        """测试百度搜索（JS 注入方式）"""
        context = browser.new_context()
        page = context.new_page()
        
        try:
            page.goto("https://www.baidu.com", timeout=15000)
            
            # 使用 JS 注入搜索词（避免 fill 超时）
            page.evaluate('document.getElementById("kw").value = "AI 人工智能"')
            
            # 点击搜索按钮
            page.click('#su')
            
            # 等待结果
            page.wait_for_timeout(3000)
            
            # 检查是否触发安全验证
            current_url = page.url
            if "secbr" in current_url or "verify" in current_url:
                pytest.skip("百度触发安全验证（正常现象）")
            
            title = page.title()
            print(f"✓ 百度搜索成功: {title[:50]}")
        except PlaywrightTimeout:
            pytest.skip("百度搜索超时")
        except Exception as e:
            if "verify" in str(e).lower() or "secbr" in str(e).lower():
                pytest.skip("百度安全验证")
            raise
        finally:
            context.close()
    
    def test_page_screenshot(self, browser):
        """测试页面截图"""
        context = browser.new_context()
        page = context.new_page()
        
        try:
            page.goto("https://www.baidu.com", timeout=15000)
            screenshot_path = "/tmp/baidu_test.png"
            page.screenshot(path=screenshot_path)
            assert os.path.exists(screenshot_path)
            print(f"✓ 截图成功: {screenshot_path}")
        except PlaywrightTimeout:
            pytest.skip("截图测试超时")
        finally:
            context.close()
    
    def test_multiple_tabs(self, browser):
        """测试多标签页"""
        context = browser.new_context()
        
        try:
            # 打开多个标签
            page1 = context.new_page()
            page2 = context.new_page()
            
            page1.goto("https://www.baidu.com", timeout=15000)
            page2.goto("https://www.bing.com", timeout=15000)
            
            assert len(context.pages) == 2
            print(f"✓ 多标签页测试成功: {len(context.pages)} 个标签")
        except PlaywrightTimeout:
            pytest.skip("多标签测试超时")
        finally:
            context.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])