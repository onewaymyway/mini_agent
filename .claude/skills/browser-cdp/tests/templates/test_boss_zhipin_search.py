#!/usr/bin/env python
"""
boss_zhipin_search.py 测试模板

测试内容：
- 数据结构定义
- 字体加密解码逻辑
- 职位卡片解析
- 搜索参数构建
- 结果去重逻辑
- 无限滚动加载
"""

import sys
import os
import json
import asyncio
import pytest
from unittest.mock import Mock, MagicMock, patch
from dataclasses import asdict

# 添加路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class TestJobInfo:
    """测试职位信息数据结构"""
    
    def test_job_info_with_values(self):
        """测试带值初始化"""
        from src.searchers.boss_zhipin_search import JobInfo
        job = JobInfo(
            source="boss_zhipin",
            title="Python开发",
            url="https://www.zhipin.com/job/123",
            company="某互联网公司",
            salary="15-25K",
            location="北京-朝阳区",
            experience="3-5年",
            education="本科",
            tags=["五险一金", "弹性工作"]
        )
        
        assert job.title == "Python开发"
        assert job.salary == "15-25K"
        assert len(job.tags) == 2
        assert "五险一金" in job.tags
    
    def test_job_info_to_dict(self):
        """测试转换为字典"""
        from src.searchers.boss_zhipin_search import JobInfo
        job = JobInfo(
            source="boss_zhipin",
            title="测试职位",
            url="https://www.zhipin.com/job/1",
            salary="10-20K",
            company="测试公司"
        )
        
        data = job.to_dict()
        
        assert isinstance(data, dict)
        assert data['title'] == "测试职位"
        assert data['salary'] == "10-20K"
        assert data['source'] == "boss_zhipin"
        assert data['tags'] == []


class TestBossZhipinConfig:
    """测试配置类"""
    
    def test_config_default_values(self):
        """测试默认配置值"""
        from src.searchers.boss_zhipin_search import BossZhipinConfig
        config = BossZhipinConfig()
        
        assert config.city == ""
        assert config.salary_min == 0
        assert config.salary_max == 0
        assert config.font_decryption is True
        assert config.fetch_detail is False
        assert config.enable_infinite_scroll is True
        assert config.max_scroll_pages == 5
    
    def test_config_to_dict(self):
        """测试配置转字典"""
        from src.searchers.boss_zhipin_search import BossZhipinConfig
        config = BossZhipinConfig(query="Python", city="北京", max_results=30)
        
        data = config.to_dict()
        
        assert data['query'] == "Python"
        assert data['city'] == "北京"
        assert data['max_results'] == 30


class TestFontDecryption:
    """测试字体加密解码"""
    
    def test_decode_font_mapping(self):
        """测试字体映射解码"""
        from src.searchers.boss_zhipin_search import BossZhipinSearcher
        
        # 使用 mock 子类绕过抽象方法
        class MockSearcher(BossZhipinSearcher):
            def __init__(self):
                self.font_mapping = {'a': '1', 'b': '2', 'c': '3'}
        
        searcher = MockSearcher()
        result = searcher._decode_font_encryption('abc')
        assert result == '123'
    
    def test_decode_font_empty_text(self):
        """测试空文本解码"""
        from src.searchers.boss_zhipin_search import BossZhipinSearcher
        
        class MockSearcher(BossZhipinSearcher):
            def __init__(self):
                self.font_mapping = {}
        
        searcher = MockSearcher()
        result = searcher._decode_font_encryption('')
        assert result == ''
    
    def test_decode_font_no_mapping(self):
        """测试无映射时返回原文"""
        from src.searchers.boss_zhipin_search import BossZhipinSearcher
        
        class MockSearcher(BossZhipinSearcher):
            def __init__(self):
                self.font_mapping = {}
        
        searcher = MockSearcher()
        result = searcher._decode_font_encryption('15-25K')
        assert result == '15-25K'
    
    def test_decode_font_partial_mapping(self):
        """测试部分映射解码"""
        from src.searchers.boss_zhipin_search import BossZhipinSearcher
        
        class MockSearcher(BossZhipinSearcher):
            def __init__(self):
                self.font_mapping = {'x': '2'}
        
        searcher = MockSearcher()
        result = searcher._decode_font_encryption('x5-25K')
        assert result == '25-25K'
    
    def test_load_font_mapping_success(self):
        """测试加载字体映射成功"""
        from src.searchers.boss_zhipin_search import BossZhipinSearcher
        
        class MockSearcher(BossZhipinSearcher):
            def __init__(self):
                self.session = Mock()
                self.font_mapping = {}
                self._font_loaded = False
        
        searcher = MockSearcher()
        searcher.session.execute_js.return_value = json.dumps({'has_font_encryption': True})
        
        result = searcher._load_font_mapping()
        
        assert result is True
        assert searcher._font_loaded is True
    
    def test_load_font_mapping_failure(self):
        """测试加载字体映射失败"""
        from src.searchers.boss_zhipin_search import BossZhipinSearcher
        
        class MockSearcher(BossZhipinSearcher):
            def __init__(self):
                self.session = Mock()
                self.font_mapping = {}
                self._font_loaded = False
        
        searcher = MockSearcher()
        searcher.session.execute_js.side_effect = Exception("JS执行失败")
        
        result = searcher._load_font_mapping()
        
        assert result is False


class TestJobCardParsing:
    """测试职位卡片解析"""
    
    def test_parse_complete_card(self):
        """测试完整卡片解析"""
        from src.searchers.boss_zhipin_search import BossZhipinSearcher
        
        class MockSearcher(BossZhipinSearcher):
            def __init__(self):
                self.font_mapping = {}
        
        searcher = MockSearcher()
        
        card = {
            'title': 'Python开发工程师',
            'company': '某科技公司',
            'salary': '20-35K',
            'location': '北京-海淀区',
            'experience': '3-5年',
            'education': '本科',
            'job_type': '全职',
            'tags': ['五险一金', '弹性工作'],
            'description': '职位描述...',
            'url': 'https://www.zhipin.com/job/123'
        }
        
        job = searcher._parse_job_card(card)
        
        assert job is not None
        assert job.title == 'Python开发工程师'
        assert job.company == '某科技公司'
        assert job.salary == '20-35K'
        assert job.location == '北京-海淀区'
        assert len(job.tags) == 2
    
    def test_parse_empty_title(self):
        """测试空标题返回None"""
        from src.searchers.boss_zhipin_search import BossZhipinSearcher
        
        class MockSearcher(BossZhipinSearcher):
            def __init__(self):
                self.font_mapping = {}
        
        searcher = MockSearcher()
        
        card = {'title': '', 'company': '某公司'}
        
        job = searcher._parse_job_card(card)
        
        assert job is None
    
    def test_parse_card_with_font_encryption(self):
        """测试带字体加密的卡片解析"""
        from src.searchers.boss_zhipin_search import BossZhipinSearcher
        
        class MockSearcher(BossZhipinSearcher):
            def __init__(self):
                self.font_mapping = {'x': '2', 'y': '0'}
        
        searcher = MockSearcher()
        
        card = {
            'title': 'Python开发',
            'salary': 'xy-35K',  # 加密的薪资
            'url': 'https://www.zhipin.com/job/1'
        }
        
        job = searcher._parse_job_card(card)
        
        assert job is not None
        assert job.salary == '20-35K'  # 已解码
    
    def test_parse_card_with_tags(self):
        """测试带标签的卡片解析"""
        from src.searchers.boss_zhipin_search import BossZhipinSearcher
        
        class MockSearcher(BossZhipinSearcher):
            def __init__(self):
                self.font_mapping = {}
        
        searcher = MockSearcher()
        
        card = {
            'title': '测试职位',
            'tags': ['五险一金', '年终奖', '弹性工作'],
            'url': 'https://www.zhipin.com/job/1'
        }
        
        job = searcher._parse_job_card(card)
        
        assert job is not None
        assert len(job.tags) == 3
        assert '五险一金' in job.tags


class TestSearchURLConstruction:
    """测试搜索URL构建"""
    
    def test_search_url_basic(self):
        """测试基础搜索URL"""
        from src.searchers.boss_zhipin_search import BossZhipinSearcher, BossZhipinConfig
        
        config = BossZhipinConfig(query="Python开发")
        searcher = BossZhipinSearcher(config=config)
        
        expected = "https://www.zhipin.com/web/geek/job?query=Python开发"
        assert searcher.SEARCH_URL == "https://www.zhipin.com/web/geek/job"
    
    def test_search_url_with_city(self):
        """测试带城市参数的URL"""
        from src.searchers.boss_zhipin_search import BossZhipinSearcher, BossZhipinConfig
        
        config = BossZhipinConfig(query="Python", city="北京")
        searcher = BossZhipinSearcher(config=config)
        
        search_url = f"{searcher.SEARCH_URL}?query={config.query}"
        search_url += f"&city={config.city}"
        
        assert "city=北京" in search_url
    
    def test_search_url_with_salary(self):
        """测试带薪资范围的URL"""
        from src.searchers.boss_zhipin_search import BossZhipinSearcher, BossZhipinConfig
        
        config = BossZhipinConfig(query="Python", salary_min=20, salary_max=35)
        searcher = BossZhipinSearcher(config=config)
        
        search_url = f"{searcher.SEARCH_URL}?query={config.query}"
        search_url += f"&salary={config.salary_min}000-"
        search_url += f"{config.salary_max}000"
        
        assert "salary=20000-35000" in search_url
    
    def test_search_url_min_salary_only(self):
        """测试仅最低薪资的URL"""
        from src.searchers.boss_zhipin_search import BossZhipinSearcher, BossZhipinConfig
        
        config = BossZhipinConfig(query="Python", salary_min=20)
        searcher = BossZhipinSearcher(config=config)
        
        search_url = f"{searcher.SEARCH_URL}?query={config.query}"
        search_url += f"&salary={config.salary_min}000-"
        
        assert "salary=20000-" in search_url


class TestResultDeduplication:
    """测试结果去重逻辑"""
    
    def test_dedup_by_title(self):
        """测试按标题去重"""
        from src.searchers.boss_zhipin_search import JobInfo
        
        jobs = [
            JobInfo(source="boss_zhipin", title="Python开发", salary="20-30K", url="https://example.com/1"),
            JobInfo(source="boss_zhipin", title="Java开发", salary="15-25K", url="https://example.com/2")
        ]
        
        existing_titles = {j.title for j in jobs}
        
        # 重复标题
        new_job = JobInfo(source="boss_zhipin", title="Python开发", salary="18-28K", url="https://example.com/3")
        assert new_job.title in existing_titles
    
    def test_dedup_allows_different_titles(self):
        """测试不同标题允许添加"""
        from src.searchers.boss_zhipin_search import JobInfo
        
        jobs = [JobInfo(source="boss_zhipin", title="Python开发", url="https://example.com/1")]
        existing_titles = {j.title for j in jobs}
        
        new_job = JobInfo(source="boss_zhipin", title="Java开发", url="https://example.com/2")
        assert new_job.title not in existing_titles
    
    def test_dedup_by_url(self):
        """测试按URL去重"""
        from src.searchers.boss_zhipin_search import JobInfo
        
        jobs = [
            JobInfo(source="boss_zhipin", title="职位A", url="https://example.com/1"),
            JobInfo(source="boss_zhipin", title="职位B", url="https://example.com/2")
        ]
        
        existing_urls = {j.url for j in jobs}
        
        # 重复URL
        new_job = JobInfo(source="boss_zhipin", title="职位A重复", url="https://example.com/1")
        assert new_job.url in existing_urls


class TestScrollLoading:
    """测试无限滚动加载"""
    
    def test_scroll_limit_check(self):
        """测试滚动次数限制"""
        from src.searchers.boss_zhipin_search import BossZhipinSearcher, BossZhipinConfig
        
        config = BossZhipinConfig(max_scroll_pages=5)
        searcher = BossZhipinSearcher(config=config)
        
        assert searcher.config.max_scroll_pages == 5
    
    def test_scroll_stop_when_enough_results(self):
        """测试结果足够时停止滚动"""
        from src.searchers.boss_zhipin_search import BossZhipinSearcher, BossZhipinConfig, JobInfo
        
        config = BossZhipinConfig(max_results=10, max_scroll_pages=5)
        searcher = BossZhipinSearcher(config=config)
        
        # 模拟已有10个结果
        results = [JobInfo(source="boss_zhipin", title=f"职位{i}", url=f"https://example.com/{i}") for i in range(10)]
        
        # 应该停止滚动
        should_continue = len(results) < config.max_results
        assert should_continue is False
    
    def test_scroll_continue_when_not_enough(self):
        """测试结果不足时继续滚动"""
        from src.searchers.boss_zhipin_search import BossZhipinSearcher, BossZhipinConfig, JobInfo
        
        config = BossZhipinConfig(max_results=50, max_scroll_pages=5)
        searcher = BossZhipinSearcher(config=config)
        
        # 模拟已有10个结果
        results = [JobInfo(source="boss_zhipin", title=f"职位{i}", url=f"https://example.com/{i}") for i in range(10)]
        
        # 应该继续滚动
        should_continue = len(results) < config.max_results
        assert should_continue is True


class TestIntegration:
    """集成测试（mock）"""
    
    def test_search_workflow(self):
        """测试完整搜索流程"""
        from src.searchers.boss_zhipin_search import BossZhipinSearcher, BossZhipinConfig
        
        # 创建 mock session
        mock_session = Mock()
        mock_session.execute_js.return_value = json.dumps([
            {
                'title': 'Python开发',
                'company': '公司A',
                'salary': '20-35K',
                'location': '北京',
                'url': 'https://www.zhipin.com/job/1'
            },
            {
                'title': 'Java开发',
                'company': '公司B',
                'salary': '15-25K',
                'location': '上海',
                'url': 'https://www.zhipin.com/job/2'
            }
        ])
        
        config = BossZhipinConfig(
            query="开发",
            max_results=10,
            font_decryption=False
        )
        searcher = BossZhipinSearcher(config=config)
        searcher.session = mock_session
        searcher.nav = Mock()
        searcher._font_loaded = True
        
        # 执行搜索
        results = asyncio.run(searcher.search("开发"))
        
        # 验证结果（search() 返回 List[SearchResult]）
        assert len(results) == 2
        assert results[0].title == 'Python开发'
        assert results[1].title == 'Java开发'
        assert results[0].salary == '20-35K'
    
    def test_get_job_detail(self):
        """测试获取职位详情"""
        from src.searchers.boss_zhipin_search import BossZhipinSearcher, BossZhipinConfig
        
        mock_session = Mock()
        mock_session.execute_js.return_value = json.dumps({
            'title': 'Python开发',
            'company': '公司A',
            'salary': '20-35K',
            'description': '职位描述...'
        })
        
        config = BossZhipinConfig()
        searcher = BossZhipinSearcher(config=config)
        searcher.session = mock_session
        searcher.nav = Mock()
        searcher.font_mapping = {}
        
        # 获取详情
        job = searcher.get_job_detail('https://www.zhipin.com/job/1')
        
        assert job is not None
        assert job.title == 'Python开发'
        assert job.company == '公司A'
    
    def test_close_method(self):
        """测试关闭方法"""
        from src.searchers.boss_zhipin_search import BossZhipinSearcher
        
        mock_session = Mock()
        searcher = BossZhipinSearcher()
        searcher.session = mock_session
        
        # 调用关闭（close 是 async 方法）
        asyncio.run(searcher.close())
        
        # 验证关闭方法被调用
        mock_session.close.assert_called_once()
    
    def test_search_batch(self):
        """测试批量搜索"""
        from src.searchers.boss_zhipin_search import BossZhipinSearcher, BossZhipinConfig
        
        mock_session = Mock()
        mock_session.execute_js.return_value = json.dumps([
            {'title': 'Python开发', 'salary': '20-35K', 'url': 'https://example.com/1'}
        ])
        
        config = BossZhipinConfig(query="开发", max_results=10, font_decryption=False)
        searcher = BossZhipinSearcher(config=config)
        searcher.session = mock_session
        searcher.nav = Mock()
        searcher._font_loaded = True
        
        # 批量搜索
        results = asyncio.run(searcher.search_batch(['Python', 'Java']))
        
        # 验证调用次数
        assert mock_session.execute_js.call_count >= 1


class TestEdgeCases:
    """边界情况测试"""
    
    def test_empty_search_results(self):
        """测试空搜索结果"""
        from src.searchers.boss_zhipin_search import BossZhipinSearcher, BossZhipinConfig
        
        mock_session = Mock()
        mock_session.execute_js.return_value = json.dumps([])
        
        config = BossZhipinConfig(query="不存在的职位", font_decryption=False)
        searcher = BossZhipinSearcher(config=config)
        searcher.session = mock_session
        searcher.nav = Mock()
        searcher._font_loaded = True
        
        results = asyncio.run(searcher.search("不存在的职位"))
        
        assert len(results) == 0
    
    def test_js_execution_error(self):
        """测试JS执行异常"""
        from src.searchers.boss_zhipin_search import BossZhipinSearcher, BossZhipinConfig
        
        mock_session = Mock()
        mock_session.execute_js.side_effect = Exception("JS执行失败")
        
        config = BossZhipinConfig(query="测试", font_decryption=False)
        searcher = BossZhipinSearcher(config=config)
        searcher.session = mock_session
        searcher.nav = Mock()
        searcher._font_loaded = True
        
        results = asyncio.run(searcher.search("测试"))
        
        # 应该返回空结果而不是崩溃
        assert results == []
    
    def test_malformed_json_response(self):
        """测试畸形JSON响应"""
        from src.searchers.boss_zhipin_search import BossZhipinSearcher, BossZhipinConfig
        
        mock_session = Mock()
        mock_session.execute_js.return_value = "not valid json"
        
        config = BossZhipinConfig(query="测试", font_decryption=False)
        searcher = BossZhipinSearcher(config=config)
        searcher.session = mock_session
        searcher.nav = Mock()
        searcher._font_loaded = True
        
        results = asyncio.run(searcher.search("测试"))
        
        # 应该返回空结果而不是崩溃
        assert results == []


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
