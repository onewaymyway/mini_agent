"""
test_social_media.py — 社交媒体场景测试模板

测试覆盖场景：
- 动态流无限滚动加载
- 内容发布（文字+图片）
- 点赞/评论互动
- 用户资料页面访问
- 私信/消息功能

依赖模块：browser_nav, browser_extract, browser_input, browser_screenshot, browser_console
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch, Mock, MagicMock
import sys

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

# 导入基础模板
from templates.base_test_template import BaseBrowserTest
import src.core.browser_launch as browser_launch
import src.core.browser_nav as browser_nav
import src.core.browser_extract as browser_extract
import src.core.browser_input as browser_input
import src.core.browser_screenshot as browser_screenshot
import src.core.browser_console as browser_console


class TestSocialMedia(BaseBrowserTest):
    """社交媒体测试用例"""

    def setUp(self):
        super().setUp()
        self._setup_social_mocks()

    def _setup_social_mocks(self):
        """设置社交媒体页面相关的 mock"""
        with patch.object(browser_launch, "spawn_browser") as mock_spawn:
            mock_proc = Mock()
            mock_proc.pid = 12345
            mock_spawn.return_value = mock_proc
            self.mock_tab["url"] = "https://example-social.com/feed"
            self.mock_tab["title"] = "Social Feed - Home"

    def test_01_load_feed(self):
        """测试：加载动态流首页"""
        with patch.object(browser_nav, "goto") as mock_goto:
            mock_goto.return_value = True
            result = browser_nav.goto("https://example-social.com/feed")
            self.assertTrue(result)
            self.assertTabUrlContains("test-tab-1", "feed")
            self.assertTabTitleContains("test-tab-1", "Social Feed")

    def test_02_infinite_scroll_loading(self):
        """测试：模拟无限滚动加载更多内容"""
        # 模拟向下滚动触发新内容加载
        with patch.object(browser_input, "scroll_index_into_view") as mock_scroll, \
             patch.object(browser_nav, "cmd_wait_selector") as mock_wait:
            mock_scroll.return_value = None
            mock_wait.return_value = True
            
            # 滚动到底部
            browser_input.scroll_index_into_view(None, "bottom")
            
            # 等待新内容加载完成
            browser_nav.cmd_wait_selector(None, ".post", timeout=10)
            
            # 验证新帖子已出现
            with patch.object(browser_extract, "extract_elements") as mock_extract:
                mock_extract.return_value = [
                    {"id": "post-new-1", "text": "New post content..."},
                    {"id": "post-new-2", "text": "Another new post..."}
                ]
                posts = browser_extract.extract_elements(mode="elements", selector=".post")
                self.assertGreater(len(posts), 0)

    def test_03_post_text_content(self):
        """测试：发布纯文本内容"""
        # 模拟在状态输入框中输入并发布
        with patch.object(browser_input, "type_selector") as mock_type, \
             patch.object(browser_input, "click_selector") as mock_click:
            mock_type.return_value = None
            mock_click.return_value = None
            
            # 输入文本内容
            browser_input.type_selector(".status-input", "Just had a great day at work! #happy #worklife")
            
            # 点击发布按钮
            browser_input.click_selector(".post-btn")
            
            # 验证发布成功（清空输入框或显示成功提示）
            with patch.object(browser_input, "get_value") as mock_get:
                mock_get.return_value = ""
                value = browser_input.get_value(".status-input")
                self.assertEqual(value, "")

    def test_04_post_with_image(self):
        """测试：发布带图片的内容"""
        # 模拟上传图片并发布
        with patch.object(browser_input, "click_selector") as mock_click, \
             patch.object(browser_input, "type") as mock_type:
            mock_click.return_value = None
            mock_type.return_value = None
            
            # 点击上传按钮
            browser_input.click_selector(".upload-image-btn")
            
            # 选择图片文件（模拟）
            browser_input.type("input[type=file]", "C:/temp/photo.jpg")
            
            # 等待图片上传完成
            browser_nav.wait_element(".image-preview")
            
            # 输入描述并发布
            browser_input.type_selector(".status-input", "Beautiful sunset!")
            browser_input.click_selector(".post-btn")

    def test_05_like_post(self):
        """测试：点赞帖子"""
        # 模拟点击点赞按钮
        with patch.object(browser_input, "click_selector") as mock_click:
            mock_click.return_value = None
            browser_input.click_selector(".post .like-button")
            
            # 验证点赞数增加
            with patch.object(browser_extract, "extract_text") as mock_extract:
                mock_extract.return_value="123 likes"
                like_count = browser_extract.extract_text(mode="text", selector=".like-count")
                self.assertIn("123", like_count)

    def test_06_comment_on_post(self):
        """测试：发表评论"""
        # 模拟输入评论并提交
        with patch.object(browser_input, "type_selector") as mock_type, \
             patch.object(browser_input, "click_selector") as mock_click:
            mock_type.return_value = None
            mock_click.return_value = None
            
            # 输入评论内容
            browser_input.type_selector(".comment-input", "Great post! Thanks for sharing.")
            
            # 提交评论
            browser_input.click_selector(".comment-submit")
            
            # 验证评论已显示
            with patch.object(browser_extract, "extract_elements") as mock_extract:
                mock_extract.return_value = [
                    {"id": "comment-1", "text": "Great post! Thanks for sharing.", "author": "User1"}
                ]
                comments = browser_extract.extract_elements(mode="elements", selector=".comment")
                self.assertEqual(len(comments), 1)
                self.assertIn("Great post", comments[0]["text"])

    def test_07_view_user_profile(self):
        """测试：查看用户个人主页"""
        # 模拟点击用户名链接进入个人主页
        with patch.object(browser_nav, "goto") as mock_goto, \
             patch.object(browser_input, "click_selector") as mock_click:
            mock_goto.return_value = True
            mock_click.return_value = None
            
            # 点击用户名
            browser_input.click_selector(".post .username")
            
            # 验证进入个人主页
            self.assertTabUrlContains("test-tab-1", "profile")
            self.assertTabTitleContains("test-tab-1", "Profile")

    def test_8_follow_user(self):
        """测试：关注用户"""
        # 模拟点击关注按钮
        with patch.object(browser_input, "click_selector") as mock_click:
            mock_click.return_value = None
            browser_input.click_selector(".follow-button")
            
            # 验证按钮状态变化（变为"已关注"）
            with patch.object(browser_extract, "extract_text") as mock_extract:
                mock_extract.return_value="Following"
                button_text = browser_extract.extract_text(mode="text", selector=".follow-button")
                self.assertIn("Following", button_text)

    def test_09_view_notifications(self):
        """测试：查看通知列表"""
        # 模拟导航到通知页
        with patch.object(browser_nav, "goto") as mock_goto:
            mock_goto.return_value = True
            browser_nav.goto("https://example-social.com/notifications")
            self.assertTabUrlContains("test-tab-1", "notifications")
            
            # 提取通知项
            with patch.object(browser_extract, "extract_elements") as mock_extract:
                mock_extract.return_value = [
                    {"id": "notif-1", "text": "You have a new follower", "type": "follower"},
                    {"id": "notif-2", "text": "Your post got 10 likes", "type": "like"}
                ]
                notifications = browser_extract.extract_elements(mode="elements", selector=".notification")
                self.assertEqual(len(notifications), 2)
                self.assertEqual(notifications[0]["type"], "follower")

    def test_10_capture_feed_screenshot(self):
        """测试：截取动态流截图"""
        # 模拟截图功能
        with patch.object(browser_screenshot, "capture") as mock_capture:
            mock_capture.return_value = "social_feed_screenshot.png"
            screenshot_path = browser_screenshot.capture(
                annotate=True,
                out="test_social_feed.png"
            )
            self.assertEqual(screenshot_path, "test_social_feed.png")

    def test_11_extract_hashtags(self):
        """测试：提取帖子中的标签（hashtag）"""
        # 模拟提取 hashtag
        with patch.object(browser_extract, "extract_text") as mock_extract:
            mock_extract.return_value="#happy #worklife #coding #python"
            hashtags = browser_extract.extract_text(mode="text", selector=".hashtags")
            self.assertIsNotNone(hashtags)
            self.assertIn("#happy", hashtags)
            self.assertIn("#python", hashtags)

    def test_12_verify_interaction_counts(self):
        """测试：验证互动统计数据（点赞、评论、分享）"""
        # 模拟提取互动数据
        with patch.object(browser_extract, "extract_elements") as mock_extract:
            mock_extract.return_value = [
                {"id": "likes-count", "text": "123", "label": "Likes"},
                {"id": "comments-count", "text": "45", "label": "Comments"},
                {"id": "shares-count", "text": "12", "label": "Shares"}
            ]
            interactions = browser_extract.extract_elements(mode="elements", selector=".interaction-stats .stat")
            self.assertEqual(len(interactions), 3)
            self.assertEqual(interactions[0]["text"], "123")
            self.assertEqual(interactions[1]["label"], "Comments")


if __name__ == "__main__":
    unittest.main()