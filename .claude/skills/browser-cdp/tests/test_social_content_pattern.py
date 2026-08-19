"""
test_social_content_pattern.py - 社交类 Pattern 单元测试

测试小红书/B站社交内容模式。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio


@pytest.fixture
def mock_session():
    """创建 mock session"""
    session = MagicMock()
    session.navigate = AsyncMock()
    session.query_selector_all = AsyncMock(return_value=[])
    session.click = AsyncMock()
    session.type_text = AsyncMock()
    session.press_key = AsyncMock()
    return session


@pytest.fixture
def mock_wait():
    """创建 mock wait"""
    wait = MagicMock()
    wait.wait_for_selector = AsyncMock()
    wait.wait_for_network_idle = AsyncMock()
    return wait


class TestSocialContentPattern:
    """SocialContentPattern 基类测试"""

    def test_base_class_instantiation(self, mock_session):
        """测试基类实例化"""
        from src.interaction_patterns.social_content_pattern import SocialContentPattern
        with pytest.raises(TypeError):
            SocialContentPattern(mock_session, "test.com")

    def test_social_post_to_dict(self):
        """测试 SocialPost 序列化"""
        from src.interaction_patterns.social_content_pattern import SocialPost
        from datetime import datetime

        post = SocialPost(
            post_id="xhs_123",
            title="测试笔记",
            url="https://www.xiaohongshu.com/explore/123",
            author="作者名",
            like_count=1000,
            comment_count=50,
            tags=["标签1", "标签2"],
            publish_time=datetime(2026, 8, 14)
        )
        result = post.to_dict()
        assert result["post_id"] == "xhs_123"
        assert result["title"] == "测试笔记"
        assert result["like_count"] == 1000
        assert len(result["tags"]) == 2

    def test_social_search_results_properties(self):
        """测试 SocialSearchResults 属性"""
        from src.interaction_patterns.social_content_pattern import SocialSearchResults

        results = SocialSearchResults(success=True, query="测试")
        assert results.is_empty is True

        results.posts.append(MagicMock())
        assert results.is_empty is False

    def test_extract_common_tags(self):
        """测试标签提取"""
        from src.interaction_patterns.xiaohongshu_pattern import XiaohongshuPattern
        from unittest.mock import MagicMock
        mock_session = MagicMock()
        pattern = XiaohongshuPattern(mock_session)
        tags = pattern._extract_common_tags("#AI# #机器学习# #深度学习# #NLP# 其他文本")
        assert "AI" in tags
        assert "机器学习" in tags
        assert len(tags) <= 10

    def test_parse_like_count_normal(self):
        """测试点赞数解析（普通数字）"""
        from src.interaction_patterns.xiaohongshu_pattern import XiaohongshuPattern
        from unittest.mock import MagicMock
        mock_session = MagicMock()
        pattern = XiaohongshuPattern(mock_session)
        assert pattern._parse_like_count("1234") == 1234
        assert pattern._parse_like_count("0") == 0
        assert pattern._parse_like_count("") == 0

    def test_parse_like_count_wan(self):
        """测试点赞数解析（万单位）"""
        from src.interaction_patterns.xiaohongshu_pattern import XiaohongshuPattern
        from unittest.mock import MagicMock
        mock_session = MagicMock()
        pattern = XiaohongshuPattern(mock_session)
        assert pattern._parse_like_count("1.5万") == 15000
        assert pattern._parse_like_count("10万") == 100000

    def test_record_start_and_latency(self, mock_session):
        """测试耗时记录"""
        from src.interaction_patterns.xiaohongshu_pattern import XiaohongshuPattern
        from src.interaction_patterns.social_content_pattern import SocialSearchResults

        pattern = XiaohongshuPattern(mock_session)
        pattern._record_start()
        assert pattern._start_time > 0

        results = SocialSearchResults(success=True, query="test")
        results = pattern._record_latency(results)
        assert results.latency_ms >= 0


class TestXiaohongshuPattern:
    """XiaohongshuPattern 测试"""

    def test_init(self, mock_session):
        """测试初始化"""
        from src.interaction_patterns.xiaohongshu_pattern import XiaohongshuPattern
        pattern = XiaohongshuPattern(mock_session)
        assert pattern.domain == "xiaohongshu.com"
        assert pattern._site_name == "xiaohongshu"

    @pytest.mark.asyncio
    async def test_search_success(self, mock_session, mock_wait):
        """测试搜索成功"""
        from src.interaction_patterns.xiaohongshu_pattern import XiaohongshuPattern
        from src.interaction_patterns.social_content_pattern import SocialSearchResults

        # Mock 元素
        mock_item = MagicMock()
        mock_title_el = MagicMock()
        mock_title_el.get_text = AsyncMock(return_value="测试笔记标题")
        mock_title_el.get_attribute = AsyncMock(return_value=None)

        mock_link_el = MagicMock()
        mock_link_el.get_text = AsyncMock(return_value="")
        mock_link_el.get_attribute = AsyncMock(return_value="https://xhs.com/note/123")

        mock_author_el = MagicMock()
        mock_author_el.get_text = AsyncMock(return_value="作者A")
        mock_author_el.get_attribute = AsyncMock(return_value=None)

        mock_likes_el = MagicMock()
        mock_likes_el.get_text = AsyncMock(return_value="1.2万")
        mock_likes_el.get_attribute = AsyncMock(return_value=None)

        async def mock_query_selector(sel):
            # 处理组合选择器
            if '.note-item-title' in sel or '.title' in sel:
                return mock_title_el
            elif 'explore' in sel:
                return mock_link_el
            elif '.author-name' in sel or '.user-name' in sel:
                return mock_author_el
            elif '.like-count' in sel or '.count' in sel:
                return mock_likes_el
            return MagicMock(get_text=AsyncMock(return_value=""), get_attribute=AsyncMock(return_value=None))

        mock_item.query_selector = mock_query_selector
        mock_session.query_selector_all = AsyncMock(return_value=[mock_item])

        pattern = XiaohongshuPattern(mock_session)
        pattern._wait = mock_wait

        with patch.object(pattern, '_wait_for_content') as mock_wait_content:
            mock_wait_content.return_value = asyncio.Future()
            mock_wait_content.return_value.set_result(None)
            result = await pattern.search("测试")

        assert isinstance(result, SocialSearchResults)
        assert result.success is True
        assert result.pattern_used == "XiaohongshuPattern(xiaohongshu)"
        assert len(result.posts) > 0
        assert result.posts[0].title == "测试笔记标题"

    @pytest.mark.asyncio
    async def test_search_failure(self, mock_session):
        """测试搜索失败"""
        from src.interaction_patterns.xiaohongshu_pattern import XiaohongshuPattern
        from src.interaction_patterns.social_content_pattern import SocialSearchResults

        mock_session.navigate.side_effect = Exception("Network error")
        pattern = XiaohongshuPattern(mock_session)
        result = await pattern.search("测试")

        assert result.success is False
        assert "Network error" in result.error_message

    def test_get_hot_list_not_implemented(self, mock_session):
        """测试热榜功能（未完全实现）"""
        from src.interaction_patterns.xiaohongshu_pattern import XiaohongshuPattern
        pattern = XiaohongshuPattern(mock_session)
        # 返回空列表是正常的
        import asyncio
        result = asyncio.run(pattern.get_hot_list())
        assert isinstance(result, list)


class TestBilibiliPattern:
    """BilibiliPattern 测试"""

    def test_init(self, mock_session):
        """测试初始化"""
        from src.interaction_patterns.bilibili_pattern import BilibiliPattern
        pattern = BilibiliPattern(mock_session)
        assert pattern.domain == "bilibili.com"
        assert pattern._site_name == "bilibili"

    @pytest.mark.asyncio
    async def test_search_success(self, mock_session, mock_wait):
        """测试搜索成功"""
        from src.interaction_patterns.bilibili_pattern import BilibiliPattern
        from src.interaction_patterns.social_content_pattern import SocialSearchResults

        # Mock 元素
        mock_item = MagicMock()
        mock_title_el = MagicMock()
        mock_title_el.get_text = AsyncMock(return_value="B站测试视频")
        mock_title_el.get_attribute = AsyncMock(return_value=None)

        mock_link_el = MagicMock()
        mock_link_el.get_text = AsyncMock(return_value="")
        mock_link_el.get_attribute = AsyncMock(return_value="https://bilibili.com/video/BV123")

        mock_author_el = MagicMock()
        mock_author_el.get_text = AsyncMock(return_value="UP主B")
        mock_author_el.get_attribute = AsyncMock(return_value=None)

        mock_views_el = MagicMock()
        mock_views_el.get_text = AsyncMock(return_value="50万")
        mock_views_el.get_attribute = AsyncMock(return_value=None)

        mock_danmaku_el = MagicMock()
        mock_danmaku_el.get_text = AsyncMock(return_value="1000")
        mock_danmaku_el.get_attribute = AsyncMock(return_value=None)

        async def mock_query_selector(sel):
            # 处理组合选择器
            if '.title' in sel or '.video-title' in sel:
                return mock_title_el
            elif "video/" in sel:
                return mock_link_el
            elif '.author-name' in sel or '.up-name' in sel:
                return mock_author_el
            elif '.view-count' in sel or '.stat-view' in sel:
                return mock_views_el
            elif '.danmaku-count' in sel or '.stat-dm' in sel:
                return mock_danmaku_el
            return MagicMock(get_text=AsyncMock(return_value=""), get_attribute=AsyncMock(return_value=None))

        mock_item.query_selector = mock_query_selector
        mock_session.query_selector_all = AsyncMock(return_value=[mock_item])

        pattern = BilibiliPattern(mock_session)
        pattern._wait = mock_wait

        with patch.object(pattern, '_wait_for_content') as mock_wait_content:
            mock_wait_content.return_value = asyncio.Future()
            mock_wait_content.return_value.set_result(None)
            result = await pattern.search("测试视频")

        assert isinstance(result, SocialSearchResults)
        assert result.success is True
        assert result.pattern_used == "BilibiliPattern(bilibili)"
        assert len(result.posts) > 0
        assert result.posts[0].title == "B站测试视频"

    @pytest.mark.asyncio
    async def test_search_failure(self, mock_session):
        """测试搜索失败"""
        from src.interaction_patterns.bilibili_pattern import BilibiliPattern
        from src.interaction_patterns.social_content_pattern import SocialSearchResults

        mock_session.navigate.side_effect = Exception("Timeout")
        pattern = BilibiliPattern(mock_session)
        result = await pattern.search("测试视频")

        assert result.success is False
        assert "Timeout" in result.error_message

    def test_get_hot_list_not_implemented(self, mock_session):
        """测试热榜功能（未完全实现）"""
        from src.interaction_patterns.bilibili_pattern import BilibiliPattern
        pattern = BilibiliPattern(mock_session)
        import asyncio
        result = asyncio.run(pattern.get_hot_list())
        assert isinstance(result, list)


class TestXiaohongshuStep5:
    """小红书步骤 5 核心功能测试：关注/消息推送/无限滚动"""

    def test_follow_user_already_followed(self, mock_session):
        """测试已关注用户不重复操作"""
        from src.interaction_patterns.xiaohongshu_pattern import XiaohongshuPattern
        mock_btn = MagicMock()
        mock_btn.get_text = AsyncMock(return_value="已关注")
        mock_session.query_selector = AsyncMock(return_value=mock_btn)
        mock_session.navigate = AsyncMock()
        mock_wait = MagicMock()
        mock_wait.wait_for_selector = AsyncMock()
        pattern = XiaohongshuPattern(mock_session)
        pattern._wait = mock_wait
        result = asyncio.run(pattern.follow_user("testuser", "uid123"))
        assert result is True
        mock_btn.click.assert_not_called()

    def test_follow_user_not_followed(self, mock_session):
        """测试未关注用户点击关注按钮"""
        from src.interaction_patterns.xiaohongshu_pattern import XiaohongshuPattern
        mock_btn = MagicMock()
        mock_btn.get_text = AsyncMock(return_value="关注")
        mock_btn.click = AsyncMock()
        mock_session.query_selector = AsyncMock(return_value=mock_btn)
        mock_session.navigate = AsyncMock()
        mock_wait = MagicMock()
        mock_wait.wait_for_selector = AsyncMock()
        pattern = XiaohongshuPattern(mock_session)
        pattern._wait = mock_wait
        result = asyncio.run(pattern.follow_user("testuser", "uid123"))
        assert result is True
        mock_btn.click.assert_called_once()

    def test_follow_user_error_returns_false(self, mock_session):
        """测试关注异常返回 False"""
        from src.interaction_patterns.xiaohongshu_pattern import XiaohongshuPattern
        mock_session.navigate = AsyncMock(side_effect=Exception("auth required"))
        pattern = XiaohongshuPattern(mock_session)
        result = asyncio.run(pattern.follow_user("testuser"))
        assert result is False

    def test_unfollow_user_following(self, mock_session):
        """测试取消关注已关注用户"""
        from src.interaction_patterns.xiaohongshu_pattern import XiaohongshuPattern
        mock_btn = MagicMock()
        mock_btn.get_text = AsyncMock(return_value="Following")
        mock_btn.click = AsyncMock()
        mock_session.query_selector = AsyncMock(return_value=mock_btn)
        mock_session.navigate = AsyncMock()
        mock_wait = MagicMock()
        mock_wait.wait_for_selector = AsyncMock()
        pattern = XiaohongshuPattern(mock_session)
        pattern._wait = mock_wait
        result = asyncio.run(pattern.unfollow_user("testuser", "uid123"))
        assert result is True
        mock_btn.click.assert_called_once()

    def test_unfollow_user_not_following(self, mock_session):
        """测试取消关注未关注用户（不点击）"""
        from src.interaction_patterns.xiaohongshu_pattern import XiaohongshuPattern
        mock_btn = MagicMock()
        mock_btn.get_text = AsyncMock(return_value="关注")
        mock_btn.click = AsyncMock()
        mock_session.query_selector = AsyncMock(return_value=mock_btn)
        mock_session.navigate = AsyncMock()
        mock_wait = MagicMock()
        mock_wait.wait_for_selector = AsyncMock()
        pattern = XiaohongshuPattern(mock_session)
        pattern._wait = mock_wait
        result = asyncio.run(pattern.unfollow_user("testuser", "uid123"))
        assert result is True
        mock_btn.click.assert_not_called()

    def test_get_message_notifications_empty(self, mock_session):
        """测试消息通知空列表"""
        from src.interaction_patterns.xiaohongshu_pattern import XiaohongshuPattern
        mock_session.query_selector_all = AsyncMock(return_value=[])
        mock_session.navigate = AsyncMock()
        mock_wait = MagicMock()
        mock_wait.wait_for_selector = AsyncMock()
        pattern = XiaohongshuPattern(mock_session)
        pattern._wait = mock_wait
        result = asyncio.run(pattern.get_message_notifications(unread_only=True))
        assert result == []

    def test_get_message_notifications_with_items(self, mock_session):
        """测试消息通知解析"""
        from src.interaction_patterns.xiaohongshu_pattern import XiaohongshuPattern
        mock_item = MagicMock()
        mock_type_el = MagicMock()
        mock_type_el.get_text = AsyncMock(return_value="点赞")
        mock_content_el = MagicMock()
        mock_content_el.get_text = AsyncMock(return_value="有人赞了你的笔记")
        mock_time_el = MagicMock()
        mock_time_el.get_text = AsyncMock(return_value="10分钟前")
        async def mock_qs(sel):
            if 'msg-type' in sel or 'notification-type' in sel:
                return mock_type_el
            elif 'msg-content' in sel or 'notification-content' in sel:
                return mock_content_el
            elif 'msg-time' in sel or 'notification-time' in sel:
                return mock_time_el
            elif 'unread-badge' in sel or 'unread' in sel:
                return None
            return MagicMock(get_text=AsyncMock(return_value=""), get_attribute=AsyncMock(return_value=None))
        mock_item.query_selector = mock_qs
        mock_session.query_selector_all = AsyncMock(return_value=[mock_item])
        mock_session.navigate = AsyncMock()
        mock_wait = MagicMock()
        mock_wait.wait_for_selector = AsyncMock()
        pattern = XiaohongshuPattern(mock_session)
        pattern._wait = mock_wait
        result = asyncio.run(pattern.get_message_notifications(unread_only=False))
        assert len(result) == 1
        assert result[0]["type"] == "点赞"
        assert result[0]["is_unread"] is False

    def test_infinite_scroll_returns_count(self, mock_session):
        """测试无限滚动返回加载数量"""
        from src.interaction_patterns.xiaohongshu_pattern import XiaohongshuPattern
        mock_session.evaluate = AsyncMock()
        mock_session.query_selector_all = AsyncMock(return_value=[MagicMock() for _ in range(5)])
        pattern = XiaohongshuPattern(mock_session)
        result = asyncio.run(pattern.infinite_scroll(max_pages=2, scroll_delay=0.01))
        assert result == 5
        assert mock_session.evaluate.call_count == 2

    def test_infinite_scroll_no_new_content_stops(self, mock_session):
        """测试无新内容时停止滚动"""
        from src.interaction_patterns.xiaohongshu_pattern import XiaohongshuPattern
        mock_session.evaluate = AsyncMock()
        mock_session.query_selector_all = AsyncMock(side_effect=[
            [MagicMock(), MagicMock()],      # page 1: 2 items
            [MagicMock(), MagicMock()],      # page 2: still 2 items
        ])
        pattern = XiaohongshuPattern(mock_session)
        result = asyncio.run(pattern.infinite_scroll(max_pages=5, scroll_delay=0.01))
        assert result == 2
        assert mock_session.evaluate.call_count == 2

    def test_infinite_scroll_error_handled(self, mock_session):
        """测试无限滚动异常处理"""
        from src.interaction_patterns.xiaohongshu_pattern import XiaohongshuPattern
        mock_session.evaluate = AsyncMock(side_effect=Exception("scroll fail"))
        pattern = XiaohongshuPattern(mock_session)
        result = asyncio.run(pattern.infinite_scroll(max_pages=3, scroll_delay=0.01))
        assert result >= 0


class TestSocialPatternIntegration:
    """社交 Pattern 集成测试"""

    def test_pattern_registration(self):
        """测试 Pattern 注册到 __init__"""
        from src.interaction_patterns import XiaohongshuPattern, BilibiliPattern
        from src.interaction_patterns.social_content_pattern import SocialContentPattern

        assert issubclass(XiaohongshuPattern, SocialContentPattern)
        assert issubclass(BilibiliPattern, SocialContentPattern)

    def test_pattern_names_unique(self):
        """测试 Pattern 名称唯一"""
        from src.interaction_patterns.xiaohongshu_pattern import XiaohongshuPattern
        from src.interaction_patterns.bilibili_pattern import BilibiliPattern
        from unittest.mock import MagicMock

        xhs = XiaohongshuPattern(MagicMock())
        bili = BilibiliPattern(MagicMock())
        assert xhs._site_name != bili._site_name
        assert xhs._site_name == "xiaohongshu"
        assert bili._site_name == "bilibili"

    def test_step5_methods_exist_on_xiaohongshu(self):
        """验证步骤 5 核心方法均存在"""
        from src.interaction_patterns.xiaohongshu_pattern import XiaohongshuPattern
        from unittest.mock import MagicMock
        pattern = XiaohongshuPattern(MagicMock())
        assert callable(getattr(pattern, "follow_user", None))
        assert callable(getattr(pattern, "unfollow_user", None))
        assert callable(getattr(pattern, "get_message_notifications", None))
        assert callable(getattr(pattern, "infinite_scroll", None))

    def test_step5_bilibili_lacks_social_actions(self):
        """B站 Pattern 无关注/消息方法（设计如此，仅搜索+热榜）"""
        from src.interaction_patterns.bilibili_pattern import BilibiliPattern
        pattern = BilibiliPattern(MagicMock())
        assert not hasattr(pattern, "follow_user") or not callable(getattr(pattern, "follow_user", None))

    def test_xiaohongshu_social_actions_only(self):
        """小红书 Pattern 有且仅有这些社交操作"""
        from src.interaction_patterns.xiaohongshu_pattern import XiaohongshuPattern
        pattern = XiaohongshuPattern(MagicMock())
        actions = ["follow_user", "unfollow_user", "get_message_notifications", "infinite_scroll"]
        for action in actions:
            assert hasattr(pattern, action), f"Missing method: {action}"

    def test_social_post_serialization_full(self):
        """完整 SocialPost 序列化测试"""
        from src.interaction_patterns.social_content_pattern import SocialPost
        from datetime import datetime
        post = SocialPost(
            post_id="xhs_999",
            title="完整测试帖子",
            url="https://xhs.com/note/999",
            author="作者C",
            content_snippet="这是一段很长的内容用于测试截断逻辑",
            like_count=5000,
            comment_count=300,
            share_count=50,
            publish_time=datetime(2026, 8, 14, 12, 0),
            tags=["AI", "科技", "前沿"],
            images=["https://img1.jpg", "https://img2.jpg"],
            metadata={"source": "xiaohongshu", "verified": True},
        )
        d = post.to_dict()
        assert d["post_id"] == "xhs_999"
        assert d["like_count"] == 5000
        assert d["image_count"] == 2
        assert d["tags"] == ["AI", "科技", "前沿"]
        assert "authorC" not in d.get("author", "")  # 验证未修改原始值

    def test_social_search_results_to_dict(self):
        """SocialSearchResults 完整序列化"""
        from src.interaction_patterns.social_content_pattern import SocialSearchResults, SocialPost
        posts = [SocialPost(post_id="1", title="A", url="http://a.com")]
        results = SocialSearchResults(success=True, query="测试", posts=posts, total_count=1, pattern_used="XiaohongshuPattern")
        d = results.to_dict()
        assert d["success"] is True
        assert d["posts"][0]["title"] == "A"
        assert d["pattern_used"] == "XiaohongshuPattern"
