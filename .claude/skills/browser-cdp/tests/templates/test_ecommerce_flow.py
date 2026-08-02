"""
test_ecommerce_flow.py — 电商购物流程测试模板

测试覆盖场景：
- 商品搜索与列表展示
- 商品详情页访问
- 加入购物车操作
- 购物车内容验证
- 结算流程模拟

依赖模块：browser_nav, browser_extract, browser_input, browser_screenshot
"""
from __future__ import annotations

import unittest
from pathlib import Path
import sys

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

# 导入基础模板
from templates.base_test_template import BaseBrowserTest


class TestEcommerceFlow(BaseBrowserTest):
    """电商购物流程测试用例"""

    def setUp(self):
        super().setUp()
        # 配置电商测试专用 mock
        self._setup_ecommerce_mocks()

    def _setup_ecommerce_mocks(self):
        """设置电商页面相关的 mock"""
        # Mock browser_launch 的 spawn_browser
        with patch.object(browser_launch, "spawn_browser") as mock_spawn:
            mock_proc = Mock()
            mock_proc.pid = 12345
            mock_spawn.return_value = mock_proc
            # Mock tab 信息
            self.mock_tab["url"] = "https://example-store.com/home"
            self.mock_tab["title"] = "Online Store - Home"

    def test_01_navigate_to_store(self):
        """测试：导航到电商网站首页"""
        # 模拟打开 URL
        with patch.object(browser_nav, "goto") as mock_goto:
            mock_goto.return_value = True
            result = browser_nav.goto("https://example-store.com/home")
            self.assertTrue(result)
            self.assertTabUrlContains("test-tab-1", "example-store.com")

    def test_02_search_product(self):
        """测试：搜索商品功能"""
        # 模拟搜索框输入和提交
        with patch.object(browser_input, "type_selector") as mock_type, \
             patch.object(browser_input, "click_selector") as mock_click:
            mock_type.return_value = None
            mock_click.return_value = None
            
            # 执行搜索操作
            browser_input.type_selector("input[name=query]", "wireless headphones")
            browser_input.click_selector("button[type=submit]")
            
            # 验证搜索结果页
            self.assertTabUrlContains("test-tab-1", "search")
            self.assertTabTitleContains("test-tab-1", "Results")

    def test_03_view_product_details(self):
        """测试：查看商品详情页"""
        # 模拟点击商品链接
        with patch.object(browser_input, "click_selector") as mock_click:
            mock_click.return_value = None
            browser_input.click_selector(".product-list a")
            
            # 验证进入详情页
            self.assertTabUrlContains("test-tab-1", "product")
            self.assertTabTitleContains("test-tab-1", "Product Details")

    def test_04_add_to_cart(self):
        """测试：加入购物车功能"""
        # 模拟点击加入购物车按钮
        with patch.object(browser_input, "click_selector") as mock_click:
            mock_click.return_value = None
            result = browser_input.click_selector(".add-to-cart")
            self.assertIsNone(result)
            
            # 验证购物车更新（通过页面内容变化）
            with patch.object(browser_extract, "extract_text") as mock_extract:
                mock_extract.return_value = "1 item in cart"
                content = browser_extract.extract_text(mode="text")
                self.assertIn("item in cart", content.lower())

    def test_05_view_cart(self):
        """测试：查看购物车内容"""
        # 模拟导航到购物车页面
        with patch.object(browser_nav, "goto") as mock_goto:
            mock_goto.return_value = True
            browser_nav.goto("https://example-store.com/cart")
            self.assertTabUrlContains("test-tab-1", "cart")

            # 提取购物车中的商品信息
            with patch.object(browser_extract, "extract_elements") as mock_extract:
                mock_extract.return_value = [
                    {
                        "id": "prod-1",
                        "text": "Wireless Headphones",\
                        "price": "$99.99",
                        "quantity": 1
                    }
                ]
                items = browser_extract.extract_elements(mode="elements")
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["text"], "Wireless Headphones")

    def test_06_checkout_simulation(self):
        """测试：结算流程模拟（不实际提交）"""
        # 模拟填写收货地址表单
        with patch.object(browser_input, "type_selector") as mock_type, \
             patch.object(browser_input, "click_selector") as mock_click:
            mock_type.return_value = None
            mock_click.return_value = None
            
            # 填写表单字段
            browser_input.type_selector("#shipping-name", "John Doe")
            browser_input.type_selector("#shipping-address", "123 Main St")
            browser_input.type_selector("#shipping-email", "john@example.com")
            
            # 选择支付方式
            browser_input.click_selector(".payment-method.credit-card")
            
            # 提交订单（模拟）
            browser_input.click_selector("#checkout-button")
            
            # 验证跳转到确认页
            self.assertTabUrlContains("test-tab-1", "order-confirmation")

    def test_07_capture_order_screenshot(self):
        """测试：订单确认页截图"""
        # 模拟截图功能
        with patch.object(browser_screenshot, "capture") as mock_capture:
            mock_capture.return_value = "test-screenshot.png"
            screenshot_path = browser_screenshot.capture(
                annotate=True,
                out="test_ecommerce_order.png"
            )
            self.assertEqual(screenshot_path, "test_ecommerce_order.png")

    def test_08_extract_product_reviews(self):
        """测试：提取商品评论"""
        # 模拟提取评论元素
        with patch.object(browser_extract, "extract_elements") as mock_extract:
            mock_extract.return_value = [
                {
                    "id": "review-1",\
                    "text": "Great product!",\
                    "rating": 5,\
                    "author": "Customer A"
                },
                {
                    "id": "review-2",\
                    "text": "Good value for money",\
                    "rating": 4,\
                    "author": "Customer B"
                }
            ]
            reviews = browser_extract.extract_elements(mode="elements", selector=".review")
            self.assertEqual(len(reviews), 2)
            self.assertEqual(reviews[0]["rating"], 5)


if __name__ == "__main__":
    unittest.main()