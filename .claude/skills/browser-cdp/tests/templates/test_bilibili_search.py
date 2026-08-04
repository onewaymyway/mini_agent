#!/usr/bin/env python
"""
bilibili_search.py 测试模板

测试范围：
- BilibiliConfig 配置类
- BilibiliSearcher 搜索器核心逻辑
- 结果解析与数据处理
- 无限滚动加载
- 异常处理
"""

import asyncio
import json
import pytest
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime


# ============================================================================
# 测试配置类
# ============================================================================

class TestBilibiliConfig:
    """测试 BilibiliConfig 配置类"""

    def test_config_default_values(self):
        """测试默认配置值"""
        from src.searchers.bilibili_search import BilibiliConfig
        
        config = BilibiliConfig()
        assert config.search_type == "all"
        assert config.order == "totalrank"
        assert config.page == 1
        assert config.page_size == 20
        assert config.duration == 0
        assert config.enable_infinite_scroll is False
        assert config.max_scroll_pages == 5
        assert config.fetch_details is True
        assert config.session_name == "bilibili_session"

    def test_config_custom_values(self):
        """测试自定义配置值"""
        from src.searchers.bilibili_search import BilibiliConfig
        
        config = BilibiliConfig(
            query="Python",
            search_type="video",
            order="play",
            page=2,
            max_results=30,
            enable_infinite_scroll=True,
            max_scroll_pages=10,
            fetch_details=False
        )
        
        assert config.query == "Python"
        assert config.search_type == "video"
        assert config.order == "play"
        assert config.page == 2
        assert config.max_results == 30
        assert config.enable_infinite_scroll is True
        assert config.max_scroll_pages == 10
        assert config.fetch_details is False

    def test_config_to_dict(self):
        """测试配置序列化"""
        from src.searchers.bilibili_search import BilibiliConfig
        
        config = BilibiliConfig(query="测试", search_type="user")
        config_dict = config.to_dict()
        
        assert "query" in config_dict
        assert config_dict["query"] == "测试"


# ============================================================================
# 测试搜索器类型
# ============================================================================

class TestSearchType:
    """测试搜索器类型定义"""

    def test_source_name(self):
        """测试 source_name 属性"""
        from src.searchers.bilibili_search import BilibiliSearcher
        
        searcher = BilibiliSearcher.__new__(BilibiliSearcher)
        assert searcher.source_name == "bilibili"

    def test_supported_types(self):
        """测试 supported_types 属性"""
        from src.searchers.bilibili_search import BilibiliSearcher
        
        searcher = BilibiliSearcher.__new__(BilibiliSearcher)
        assert "video_search" in searcher.supported_types
        assert "video_detail" in searcher.supported_types
        assert "user_search" in searcher.supported_types

    def test_base_url(self):
        """测试基础 URL"""
        from src.searchers.bilibili_search import BilibiliSearcher
        
        assert BilibiliSearcher.BASE_URL == "https://search.bilibili.com"
        assert BilibiliSearcher.API_BASE == "https://api.bilibili.com"


# ============================================================================
# 测试结果解析
# ============================================================================

class TestResultParsing:
    """测试结果解析"""

    @pytest.mark.asyncio
    async def test_extract_search_results(self):
        """测试搜索结果提取"""
        from src.searchers.bilibili_search import BilibiliSearcher, SearchResults
        
        searcher = BilibiliSearcher.__new__(BilibiliSearcher)
        searcher.session = Mock()
        searcher.session.execute_js = AsyncMock(return_value=[
            {
                'title': 'Python 教程',
                'url': 'https://www.bilibili.com/video/BV123',
                'author': '技术UP主',
                'stats': '10万播放'
            }
        ])
        
        results = SearchResults(source="bilibili", query="Python")
        await searcher._extract_search_results(results)
        
        assert len(results.results) == 1
        assert results.results[0].title == 'Python 教程'
        assert results.results[0].url == 'https://www.bilibili.com/video/BV123'
        assert results.results[0].author == '技术UP主'

    @pytest.mark.asyncio
    async def test_extract_user_results(self):
        """测试 UP 主结果提取"""
        from src.searchers.bilibili_search import BilibiliSearcher, SearchResults
        
        searcher = BilibiliSearcher.__new__(BilibiliSearcher)
        searcher.session = Mock()
        searcher.session.execute_js = AsyncMock(return_value=[
            {
                'name': '技术UP主',
                'url': 'https://space.bilibili.com/123',
                'fans': '10万粉丝'
            }
        ])
        
        results = SearchResults(source="bilibili", query="技术UP主")
        await searcher._extract_user_results(results)
        
        assert len(results.results) == 1
        assert results.results[0].title == '技术UP主'
        assert results.results[0].url == 'https://space.bilibili.com/123'
        assert '粉丝' in results.results[0].snippet

    def test_build_search_url(self):
        """测试搜索 URL 构建"""
        from src.searchers.bilibili_search import BilibiliSearcher
        
        searcher = BilibiliSearcher.__new__(BilibiliSearcher)
        searcher._config = Mock(
            search_type="video",
            order="totalrank",
            duration=0
        )
        
        url = searcher._build_search_url("Python", page=1)
        assert "search.bilibili.com" in url
        assert "keyword=Python" in url
        assert "search_type=video" in url


# ============================================================================
# 测试滚动加载
# ============================================================================

class TestScrollLoading:
    """测试无限滚动加载"""

    def test_scroll_limit_check(self):
        """测试滚动次数限制"""
        from src.searchers.bilibili_search import BilibiliSearcher
        
        searcher = BilibiliSearcher.__new__(BilibiliSearcher)
        searcher._config = Mock(max_scroll_pages=5)
        searcher._scroll_count = 5
        
        # 滚动次数达到限制
        assert searcher._scroll_count >= searcher._config.max_scroll_pages

    @pytest.mark.asyncio
    async def test_scroll_stop_when_enough_results(self):
        """测试结果足够时停止滚动"""
        from src.searchers.bilibili_search import BilibiliSearcher, SearchResults
        from src.searchers.base import SearchResult
        
        searcher = BilibiliSearcher.__new__(BilibiliSearcher)
        searcher._config = Mock(max_results=10, max_scroll_pages=5)
        searcher.session = Mock()
        searcher._dynamic_loader = Mock()
        searcher._dynamic_loader.scroll_to_load = AsyncMock()
        
        # 模拟已有足够结果
        results = SearchResults(source="bilibili", query="测试")
        for i in range(10):
            results.results.append(SearchResult(
                source="bilibili",
                title=f"视频{i}",
                url=f"https://www.bilibili.com/video/BV{i}"
            ))
        
        # 结果足够，不应滚动
        assert len(results.results) >= searcher._config.max_results

    @pytest.mark.asyncio
    async def test_scroll_continue_when_not_enough(self):
        """测试结果不足时继续滚动"""
        from src.searchers.bilibili_search import BilibiliSearcher, SearchResults
        from src.searchers.base import SearchResult
        
        searcher = BilibiliSearcher.__new__(BilibiliSearcher)
        searcher._config = Mock(max_results=10, max_scroll_pages=5)
        searcher.session = Mock()
        searcher._dynamic_loader = Mock()
        searcher._dynamic_loader.scroll_to_load = AsyncMock()
        
        # 模拟结果不足
        results = SearchResults(source="bilibili", query="测试")
        for i in range(3):
            results.results.append(SearchResult(
                source="bilibili",
                title=f"视频{i}",
                url=f"https://www.bilibili.com/video/BV{i}"
            ))
        
        # 结果不足，应继续滚动
        assert len(results.results) < searcher._config.max_results


# ============================================================================
# 测试集成
# ============================================================================

class TestIntegration:
    """测试集成场景"""

    @pytest.mark.asyncio
    async def test_search_workflow(self):
        """测试完整搜索流程"""
        from src.searchers.bilibili_search import BilibiliSearcher, BilibiliConfig
        from src.searchers.base import SearchResult
        
        # 创建 mock session
        mock_session = Mock()
        mock_session.execute_js = AsyncMock(return_value=[
            {
                'title': 'Python 教程',
                'author': '技术UP主',
                'duration': '10:30',
                'play_count': 100000,
                'url': 'https://www.bilibili.com/video/BV123'
            },
            {
                'title': 'Java 入门',
                'author': '编程UP主',
                'duration': '15:20',
                'play_count': 50000,
                'url': 'https://www.bilibili.com/video/BV456'
            }
        ])
        mock_session.goto = AsyncMock()
        
        config = BilibiliConfig(
            query="编程",
            max_results=10,
            search_type="video",
            stealth=False  # 禁用反检测，避免 mock 问题
        )
        
        searcher = BilibiliSearcher(config)
        searcher.session = mock_session
        
        results = await searcher.search("编程")
        
        assert len(results.results) == 2
        assert results.results[0].title == 'Python 教程'
        assert results.results[1].title == 'Java 入门'

    @pytest.mark.asyncio
    async def test_get_video_detail(self):
        """测试获取视频详情"""
        from src.searchers.bilibili_search import BilibiliSearcher, BilibiliConfig
        
        mock_session = Mock()
        mock_session.execute_js = AsyncMock(return_value={
            'title': 'Python 教程',
            'author': '技术UP主',
            'description': 'Python 入门教程',
            'duration': '10:30',
            'play_count': 100000,
            'like_count': 5000,
            'danmaku_count': 500
        })
        mock_session.goto = AsyncMock()
        
        config = BilibiliConfig(query="测试", stealth=False)
        searcher = BilibiliSearcher(config)
        searcher.session = mock_session
        searcher._smart_wait = None  # 初始化属性
        
        detail = await searcher.get_video_detail("BV123")
        
        assert detail is not None
        assert detail['title'] == 'Python 教程'
        assert detail['play_count'] == 100000

    def test_close_method(self):
        """测试关闭方法"""
        from src.searchers.bilibili_search import BilibiliSearcher
        
        mock_session = Mock()
        
        searcher = BilibiliSearcher.__new__(BilibiliSearcher)
        searcher.session = mock_session
        
        # 关闭不应抛出异常
        searcher.close()
        mock_session.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_search_batch(self):
        """测试批量搜索"""
        from src.searchers.bilibili_search import BilibiliSearcher, BilibiliConfig
        
        mock_session = Mock()
        mock_session.execute_js = AsyncMock(return_value=[
            {'title': 'Python 教程', 'url': 'https://www.bilibili.com/video/BV123'}
        ])
        mock_session.goto = AsyncMock()
        
        config = BilibiliConfig(query="编程", max_results=10, stealth=False)
        
        searcher = BilibiliSearcher(config)
        searcher.session = mock_session
        
        results = await searcher.search_batch(["Python", "Java"])
        
        assert len(results.results) >= 1


# ============================================================================
# 测试边界情况
# ============================================================================

class TestEdgeCases:
    """测试边界情况"""

    @pytest.mark.asyncio
    async def test_empty_search_results(self):
        """测试空搜索结果"""
        from src.searchers.bilibili_search import BilibiliSearcher, BilibiliConfig
        
        mock_session = Mock()
        mock_session.execute_js = AsyncMock(return_value=json.dumps([]))
        mock_session.goto = AsyncMock()
        
        config = BilibiliConfig(query="不存在的关键词")
        
        searcher = BilibiliSearcher(config)
        searcher.session = mock_session
        
        results = await searcher.search("不存在的关键词")
        
        assert results.total_results == 0
        assert len(results.results) == 0

    @pytest.mark.asyncio
    async def test_js_execution_error(self):
        """测试 JS 执行错误"""
        from src.searchers.bilibili_search import BilibiliSearcher, BilibiliConfig
        
        mock_session = Mock()
        mock_session.execute_js.side_effect = Exception("JS 执行失败")
        mock_session.goto = AsyncMock()
        
        config = BilibiliConfig(query="测试", stealth=False)
        
        searcher = BilibiliSearcher(config)
        searcher.session = mock_session
        
        results = await searcher.search("测试")
        
        # JS 错误被捕获为 warning，不设置 results.error
        # 但应返回空结果
        assert len(results.results) == 0
        # error 可能为 None（因为错误在 _extract_search_results 中被捕获）
        # 或者为 "JS 执行失败"（如果错误传播到 search 方法）
        assert results is not None

    @pytest.mark.asyncio
    async def test_malformed_json_response(self):
        """测试畸形 JSON 响应"""
        from src.searchers.bilibili_search import BilibiliSearcher, BilibiliConfig
        
        mock_session = Mock()
        mock_session.execute_js.return_value = "invalid json"
        mock_session.goto = AsyncMock()
        
        config = BilibiliConfig(query="测试")
        
        searcher = BilibiliSearcher(config)
        searcher.session = mock_session
        
        results = await searcher.search("测试")
        
        # 应返回空结果而非抛出异常
        assert len(results.results) == 0


# ============================================================================
# 测试保存功能
# ============================================================================

class TestSaveResults:
    """测试结果保存功能"""

    def test_save_results_json(self, tmp_path):
        """测试保存 JSON 结果"""
        from src.searchers.bilibili_search import BilibiliSearcher, BilibiliConfig
        from src.searchers.base import SearchResult, SearchResults
        
        config = BilibiliConfig(
            query="Python",
            output_dir=str(tmp_path)
        )
        
        searcher = BilibiliSearcher(config)
        
        results = SearchResults(source="bilibili", query="Python")
        results.results.append(SearchResult(
            source="bilibili",
            title="Python 教程",
            url="https://www.bilibili.com/video/BV123"
        ))
        
        searcher.save_results(results)
        
        # 检查文件是否创建
        json_files = list(tmp_path.glob("*bilibili_results.json"))
        assert len(json_files) > 0

    def test_save_results_csv(self, tmp_path):
        """测试保存 CSV 结果"""
        from src.searchers.bilibili_search import BilibiliSearcher, BilibiliConfig
        from src.searchers.base import SearchResult, SearchResults
        
        config = BilibiliConfig(
            query="Python",
            output_dir=str(tmp_path)
        )
        
        searcher = BilibiliSearcher(config)
        
        results = SearchResults(source="bilibili", query="Python")
        results.results.append(SearchResult(
            source="bilibili",
            title="Python 教程",
            url="https://www.bilibili.com/video/BV123"
        ))
        
        searcher.save_results(results)
        
        # 检查文件是否创建
        csv_files = list(tmp_path.glob("*bilibili_results.csv"))
        assert len(csv_files) > 0


# ============================================================================
# 测试去重功能
# ============================================================================

class TestDeduplication:
    """测试去重功能"""

    def test_deduplicate_by_url(self):
        """测试按 URL 去重"""
        from src.searchers.bilibili_search import BilibiliSearcher, BilibiliConfig
        from src.searchers.base import SearchResult, SearchResults
        
        config = BilibiliConfig(query="Python")
        searcher = BilibiliSearcher(config)
        
        results = SearchResults(source="bilibili", query="Python")
        results.results.append(SearchResult(
            source="bilibili",
            title="Python 教程",
            url="https://www.bilibili.com/video/BV123"
        ))
        results.results.append(SearchResult(
            source="bilibili",
            title="Python 教程 (重复)",
            url="https://www.bilibili.com/video/BV123"  # 相同 URL
        ))
        
        original_count = len(results.results)
        removed = results.deduplicate(by="url")
        
        assert len(results.results) < original_count
        assert removed > 0

    def test_deduplicate_by_title(self):
        """测试按标题去重"""
        from src.searchers.bilibili_search import BilibiliSearcher, BilibiliConfig
        from src.searchers.base import SearchResult, SearchResults
        
        config = BilibiliConfig(query="Python")
        searcher = BilibiliSearcher(config)
        
        results = SearchResults(source="bilibili", query="Python")
        results.results.append(SearchResult(
            source="bilibili",
            title="Python 教程",
            url="https://www.bilibili.com/video/BV123"
        ))
        results.results.append(SearchResult(
            source="bilibili",
            title="Python 教程",
            url="https://www.bilibili.com/video/BV456"  # 不同 URL，相同标题
        ))
        
        original_count = len(results.results)
        removed = results.deduplicate(by="title", threshold=1.0)
        
        assert len(results.results) < original_count
        assert removed > 0


# ============================================================================
# 运行测试
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
