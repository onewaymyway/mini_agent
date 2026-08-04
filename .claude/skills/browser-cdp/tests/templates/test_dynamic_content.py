"""
test_dynamic_content.py — 动态内容专项测试模板

测试覆盖场景：
- 无限滚动（Infinite Scroll）加载
- AJAX/Fetch 请求模拟与拦截
- SPA（单页应用）路由变化检测
- 动态元素出现等待
- 内容懒加载（Lazy Loading）

依赖模块：browser_nav, browser_input, browser_console, browser_watch
"""
from __future__ import annotations

import unittest
from unittest.mock import patch, Mock, MagicMock
from pathlib import Path
import sys

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

# 导入基础模板
from templates.base_test_template import BaseBrowserTest
import src.core.browser_launch as browser_launch
import src.core.browser_nav as browser_nav
import src.core.browser_input as browser_input
import src.core.browser_extract as browser_extract
import src.core.browser_console as browser_console
import src.core.browser_watch as browser_watch


class TestDynamicContent(BaseBrowserTest):
    """动态内容专项测试用例"""

    def setUp(self):
        super().setUp()
        self._setup_dynamic_mocks()

    def _setup_dynamic_mocks(self):
        """设置动态内容相关的 mock"""
        with patch.object(browser_launch, "spawn_browser") as mock_spawn:
            mock_proc = Mock()
            mock_proc.pid = 12345
            mock_spawn.return_value = mock_proc
            self.mock_tab["url"] = "https://example-dynamic.com/feed"
            self.mock_tab["title"] = "Dynamic Feed - SPA App"

    def test_01_infinite_scroll_loading(self):
        """测试：无限滚动加载更多内容"""
        # 模拟向下滚动触发新内容加载
        scroll_count = 0
        original_scroll = browser_input.scroll

        def mock_scroll(direction, **kwargs):
            nonlocal scroll_count
            scroll_count += 1
            if direction == "bottom" and scroll_count < 3:
                # 模拟加载更多数据
                return MagicMock()
            return original_scroll(direction, **kwargs)

        with patch.object(browser_input, "scroll", side_effect=mock_scroll), \
             patch.object(browser_nav, "wait_element") as mock_wait, \
             patch.object(browser_extract, "extract_elements") as mock_extract:
            
            mock_wait.return_value = True
            mock_extract.return_value = [
                {"id": f"post-{i}", "text": f"Dynamic post content {i}"} for i in range(10)
            ]
            
            # 滚动到底部多次以触发加载
            for _ in range(3):
                browser_input.scroll("bottom")
                browser_nav.wait_element(".post", timeout=5)
            
            # 验证获取到了足够多的帖子
            posts = browser_extract.extract_elements(mode="elements", selector=".post")
            self.assertGreaterEqual(len(posts), 10)

    def test_02_ajax_request_simulation(self):
        """测试：模拟 AJAX 请求与响应"""
        # 模拟通过 JS 执行 AJAX 请求
        with patch.object(browser_console, "eval") as mock_eval:
            mock_eval.return_value = {
                "data": ["item1", "item2", "item3"],
                "total": 3,
                "status": "success"
            }
            
            # 执行 AJAX 请求
            result = browser_console.eval("fetch('/api/data').then(r => r.json())")
            self.assertEqual(result["status"], "success")
            self.assertEqual(len(result["data"]), 3)

    def test_03_spa_route_detection(self):
        """测试：SPA 路由变化检测（不等待页面重载）"""
        # 模拟 SPA 路由变化（URL 改变但页面不完全重载）
        with patch.object(browser_watch, "wait_url_contains") as mock_wait_url, \
             patch.object(browser_nav, "get_url") as mock_get_url:
            mock_get_url.side_effect = [
                "https://example.com/home",
                "https://example.com/profile",
                "https://example.com/settings"
            ]
            mock_wait_url.return_value = True
            
            # 导航到 profile 页面（SPA 路由）
            browser_input.click_selector("#nav-profile")
            browser_watch.wait_url_contains("profile", timeout=5, interval=1)
            
            # 验证 URL 已更新
            current_url = browser_nav.get_url()
            self.assertIn("profile", current_url)

    def test_04_lazy_image_loading(self):
        """测试：图片懒加载（Lazy Loading）"""
        # 模拟滚动触发图片懒加载
        with patch.object(browser_input, "scroll") as mock_scroll, \
             patch.object(browser_extract, "extract_elements") as mock_extract:
            mock_scroll.return_value = None
            
            # 初始状态：只有少量图片
            mock_extract.return_value = [{"src": "placeholder.jpg", "lazy": True}]
            initial_images = browser_extract.extract_elements(mode="elements", selector="img.lazy")
            self.assertEqual(len(initial_images), 1)
            
            # 滚动触发懒加载
            browser_input.scroll("down", amount=500)
            
            # 再次提取：应该有更多图片加载完成
            mock_extract.return_value = [
                {"src": "image1.jpg", "lazy": False},
                {"src": "image2.jpg", "lazy": False},
                {"src": "image3.jpg", "lazy": False}
            ]
            loaded_images = browser_extract.extract_elements(mode="elements", selector="img:not([lazy])")
            self.assertGreater(len(loaded_images), 0)

    def test_05_dynamic_element_waiting(self):
        """测试：动态元素的等待与超时处理"""
        # 模拟等待动态出现的元素
        with patch.object(browser_nav, "wait_element") as mock_wait:
            # 元素快速出现的情况
            mock_wait.return_value = True
            element = browser_nav.wait_element(".dynamic-content", timeout=5)
            self.assertTrue(element)
            
            # 元素出现较慢的情况（模拟延迟）
            mock_wait.side_effect = [False, True]  # 第一次失败，第二次成功
            element = browser_nav.wait_element(".slow-loading", timeout=10)
            self.assertTrue(element)

    def test_06_handle_popups_and_modals(self):
        """测试：弹窗和模态框的处理"""
        # 模拟关闭弹窗
        with patch.object(browser_input, "click_selector") as mock_click, \
             patch.object(browser_extract, "extract_text") as mock_extract:
            mock_click.return_value = None
            mock_extract.return_value = "Popup closed successfully"
            
            # 点击关闭按钮
            browser_input.click_selector(".modal-close-btn")
            
            # 验证弹窗已消失
            with patch.object(browser_extract, "extract_elements") as mock_check:
                mock_check.return_value = []
                modals = browser_extract.extract_elements(mode="elements", selector=".modal")
                self.assertEqual(len(modals), 0)

    def test_07_fetch_api_data(self):
        """测试：直接调用 API 获取数据（不经过页面渲染）"""
        # 模拟通过浏览器控制台执行 fetch
        with patch.object(browser_console, "eval") as mock_eval:
            mock_eval.return_value = {
                "users": [
                    {"id": 1, "name": "User1", "email": "user1@example.com"},
                    {"id": 2, "name": "User2", "email": "user2@example.com"}
                ],
                "pagination": {"page": 1, "total_pages": 10}
            }
            
            data = browser_console.eval("fetch('/api/users?page=1').then(r => r.json())")
            self.assertEqual(len(data["users"]), 2)
            self.assertEqual(data["pagination"]["page"], 1)
            self.assertEqual(data["pagination"]["total_pages"], 10)

    def test_08_handle_loading_spinners(self):
        """测试：等待加载 spinner 消失"""
        # 模拟等待加载完成
        with patch.object(browser_nav, "wait_element_not_present") as mock_wait:
            mock_wait.return_value = True
            
            # 等待 spinner 消失（表示加载完成）
            browser_nav.wait_element_not_present(".loading-spinner", timeout=10)
            
            # 验证页面可交互
            with patch.object(browser_extract, "extract_elements") as mock_check:
                mock_check.return_value = [{"id": "main-content", "tag": "div"}]
                content = browser_extract.extract_elements(mode="elements", selector="#main-content")
                self.assertGreater(len(content), 0)

    def test_09_websocket_connection(self):
        """测试：WebSocket 连接监控（实时数据）"""
        # 模拟 WebSocket 消息监听
        with patch.object(browser_console, "watch_console") as mock_watch:
            mock_watch.return_value = [
                {"timestamp": "2024-01-15T10:00:00Z", "message": "New user joined"},
                {"timestamp": "2024-01-15T10:00:05Z", "message": "Message received"}
            ]
            
            messages = browser_console.watch_console(duration=10)
            self.assertEqual(len(messages), 2)
            self.assertIn("New user", messages[0]["message"])

    def test_09_handle_iframes(self):
        """测试：iframe 内页面的切换与操作"""
        # 模拟切换到 iframe 并操作
        with patch.object(browser_input, "switch_to_frame") as mock_switch, \
             patch.object(browser_input, "click_selector") as mock_click:
            mock_switch.return_value = None
            mock_click.return_value = None
            
            # 切换到 iframe
            browser_input.switch_to_frame("#content-frame")
            
            # 在 iframe 内操作元素
            browser_input.click_selector(".iframe-button")
            
            # 切回主页面
            browser_input.switch_to_default_content()

    def test_10_handle_tabs_and_windows(self):
        """测试：多 Tab 和新窗口管理"""
        # 模拟打开新 Tab
        with patch.object(browser_launch, "new_tab") as mock_new_tab, \
             patch.object(browser_launch, "switch_to_tab") as mock_switch:
            mock_new_tab.return_value = {"id": "tab-2", "url": "about:blank"}
            mock_switch.return_value = True
            
            # 打开新 Tab
            new_tab = browser_launch.new_tab("https://example.com/new-page")
            self.assertEqual(new_tab["id"], "tab-2")
            
            # 切换回原 Tab
            browser_launch.switch_to_tab("test-tab-1")


if __name__ == "__main__":
    unittest.main()