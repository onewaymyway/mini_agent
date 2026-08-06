"""
登录+搜索集成测试

测试登录、搜索、表单、抓取的完整流程。
"""
import pytest
import json
import time
from unittest.mock import MagicMock, patch

import sys
sys.path.insert(0, '.')

from src.core.login.cookie_manager import CookieManager, CookieInfo
from src.core.login.login_form_detector import LoginFormDetector
from src.core.login.session_manager import SessionManager
from src.core.login.login_state_detector import LoginStateDetector
from src.searchers.search_pagination import PaginationDetector, AutoPagination
from src.searchers.search_query_builder import QueryBuilder
from src.searchers.search_result_parser import ResultParser, ParsedResult
from src.core.login.dynamic_form_handler import DynamicFormField, DynamicFormHandler
from src.core.infinite_scroll import InfiniteScrollHandler


class TestLoginSearchIntegration:
    """登录+搜索集成测试"""
    
    def setup_method(self):
        self.session = MagicMock()
        self.storage_dir = "temp_data/test_integration"
        
        # 初始化各模块
        self.cookie_mgr = CookieManager(self.session, self.storage_dir)
        self.form_detector = LoginFormDetector(self.session)
        self.session_mgr = SessionManager(self.session, self.storage_dir)
        self.state_detector = LoginStateDetector(self.session)
        self.pagination_detector = PaginationDetector(self.session)
        self.query_builder = QueryBuilder()
        self.result_parser = ResultParser(self.session)
        self.form_handler = DynamicFormHandler(self.session)
        self.scroll_handler = InfiniteScrollHandler(self.session)
    
    def test_full_login_flow(self):
        """测试完整登录流程"""
        # 1. 检测登录状态
        self.session.eval_js.return_value = {"isLoggedIn": False, "confidence": 0.9}
        state = self.state_detector.check_login_state()
        assert state.is_logged_in is False
        
        # 2. 检测登录表单
        self.session.eval_js.return_value = {
            "selector": "form#login",
            "fields": [
                {"selector": "#username", "type": "username"},
                {"selector": "#password", "type": "password"},
            ],
        }
        form = self.form_detector.detect()
        assert form is not None
        assert len(form.fields) == 2
        
        # 3. 创建会话
        with patch.object(self.session_mgr, '_get_current_url', return_value='https://example.com/login'), \
             patch.object(self.session_mgr, '_get_current_title', return_value='Login'):
            session_info = self.session_mgr.create_session("test_login")
            assert session_info.session_id == "test_login"
        
        # 4. 模拟登录成功
        self.session.eval_js.return_value = {"isLoggedIn": True, "confidence": 0.95}
        state = self.state_detector.check_login_state()
        assert state.is_logged_in is True
        
        # 5. 保存 Cookie
        cookies = [CookieInfo(name="session", value="abc123", domain=".example.com")]
        self.cookie_mgr.save_cookies(cookies, "example.com")
        
        # 6. 更新会话状态
        self.session_mgr.update_session("test_login", is_logged_in=True)
        updated = self.session_mgr.get_session("test_login")
        assert updated.is_logged_in is True
    
    def test_search_with_pagination(self):
        """测试带分页的搜索"""
        # 1. 构建查询
        expansion = self.query_builder.expand("手机")
        assert expansion.original == "手机"
        
        # 2. 检测分页
        self.session.eval_js.side_effect = [5, 100, 1, None, None]
        pagination = self.pagination_detector.detect()
        assert pagination.total_pages == 5
        assert pagination.total_results == 100
        
        # 3. 解析结果
        result = ParsedResult(
            title="测试手机",
            url="https://example.com/phone",
            snippet="手机摘要",
        )
        assert result.title == "测试手机"
        
        # 4. 去重
        results = [
            ParsedResult(title="A", url="https://a.com"),
            ParsedResult(title="B", url="https://b.com"),
        ]
        unique = self.result_parser.deduplicate(results)
        assert len(unique) == 2
    
    def test_dynamic_form_filling(self):
        """测试动态表单填写"""
        # 1. 注册动态字段
        field = DynamicFormField(
            selector="#username",
            field_type="text",
            value="test",
        )
        self.form_handler.register_field(field)
        
        # 2. 填写表单
        self.session.eval_js.return_value = True
        result = self.form_handler.fill_dynamic_form({"#username": "test"})
        assert result is True
        
        # 3. 多步骤表单
        self.form_handler.register_multi_step_form("test_form", [
            {"step": 1, "fields": ["name"]},
            {"step": 2, "fields": ["email"]},
        ])
        
        # 4. 保存步骤状态
        self.form_handler.save_step_state("test_form", {"name": "test"})
        state = self.form_handler.restore_step_state("test_form", 0)
        assert state == {"name": "test"}
    
    def test_infinite_scroll(self):
        """测试无限滚动"""
        # 1. 滚动到底部
        self.session.eval_js.side_effect = [False, True]
        count = self.scroll_handler.scroll_to_bottom(max_scrolls=3)
        assert count > 0
        
        # 2. 获取滚动状态
        self.session.eval_js.return_value = 500
        state = self.scroll_handler.get_scroll_state()
        assert state.current_position == 500.0
        
        # 3. 等待内容加载
        self.session.eval_js.return_value = 1000
        result = self.scroll_handler.wait_for_content_load(timeout=1, check_interval=0.1)
        assert result is True
    
    def test_cookie_persistence(self):
        """测试 Cookie 持久化"""
        # 1. 保存 Cookie
        cookies = [
            CookieInfo(name="session", value="abc", domain=".example.com", expires=time.time() + 3600),
            CookieInfo(name="token", value="xyz", domain=".example.com", expires=time.time() + 3600),
        ]
        file_path = self.cookie_mgr.save_cookies(cookies, "example.com")
        assert file_path.endswith(".json")
        
        # 2. 加载 Cookie
        loaded = self.cookie_mgr.load_cookies("example.com")
        assert len(loaded) == 2
        
        # 3. 恢复 Cookie
        restored = self.cookie_mgr.restore_cookies("example.com")
        assert restored == 2
    
    def test_session_management(self):
        """测试会话管理"""
        with patch.object(self.session_mgr, '_get_current_url', return_value='https://example.com'), \
             patch.object(self.session_mgr, '_get_current_title', return_value='Example'):
            # 1. 创建会话
            session = self.session_mgr.create_session("test_session")
            assert session.session_id == "test_session"
            
            # 2. 获取会话
            retrieved = self.session_mgr.get_session("test_session")
            assert retrieved is not None
            
            # 3. 更新会话
            updated = self.session_mgr.update_session("test_session", is_logged_in=True)
            assert updated.is_logged_in is True
            
            # 4. 列出会话
            sessions = self.session_mgr.list_sessions()
            assert len(sessions) >= 1
            
            # 5. 检查有效性
            valid = self.session_mgr.check_session_valid("test_session")
            assert valid is True
            
            # 6. 删除会话
            deleted = self.session_mgr.delete_session("test_session")
            assert deleted is True


class TestCrossModuleIntegration:
    """跨模块集成测试"""
    
    def test_login_then_search(self):
        """测试登录后搜索"""
        session = MagicMock()
        
        # 模拟登录状态检测
        state_detector = LoginStateDetector(session)
        session.eval_js.return_value = {"isLoggedIn": True, "confidence": 0.9}
        state = state_detector.check_login_state()
        assert state.is_logged_in is True
        
        # 模拟搜索分页检测
        pagination_detector = PaginationDetector(session)
        session.eval_js.side_effect = [5, 100, 1, None, None]
        pagination = pagination_detector.detect()
        assert pagination.total_pages == 5
        
        # 模拟查询构建
        query_builder = QueryBuilder()
        expansion = query_builder.expand("测试")
        assert expansion.original == "测试"
    
    def test_scroll_and_extract(self):
        """测试滚动后抓取"""
        session = MagicMock()
        
        # 模拟滚动
        scroll_handler = InfiniteScrollHandler(session)
        session.eval_js.side_effect = [False, True]
        count = scroll_handler.scroll_to_bottom(max_scrolls=3)
        assert count > 0
        
        # 模拟结果解析
        parser = ResultParser(session)
        result = parser.parse_result(
            title="测试标题",
            url="https://example.com",
            snippet="测试摘要",
        )
        assert result.title == "测试标题"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
