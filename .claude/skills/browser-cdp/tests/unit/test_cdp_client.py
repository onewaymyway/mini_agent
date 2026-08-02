"""
cdp_client.py 单元测试
"""
import pytest
import sys
from pathlib import Path
import tempfile
import time
from unittest.mock import patch, Mock, MagicMock, AsyncMock

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from src.core import cdp_client
from src.core.cdp_client import CDPSession, CDPError


class TestCDPClientUtils:
    """CDP客户端工具函数单元测试"""
    
    def test_http_json_success(self):
        """测试：HTTP JSON请求成功"""
        with patch('requests.get') as mock_get:
            mock_response = Mock()
            mock_response.json.return_value = {'tabs': []}
            mock_response.raise_for_status.return_value = None
            mock_get.return_value = mock_response
            
            result = cdp_client.http_json('127.0.0.1', 9222, '/json')
            assert result == {'tabs': []}
            mock_get.assert_called_once()
    
    def test_http_json_error(self):
        """测试：HTTP JSON请求失败"""
        with patch('requests.get') as mock_get:
            mock_get.side_effect = Exception('Connection refused')
            
            with pytest.raises(Exception):
                cdp_client.http_json('127.0.0.1', 9222, '/json')
    
    def test_list_tabs_empty(self):
        """测试：列出空标签页"""
        with patch('src.core.cdp_client.http_json', return_value=[]):
            tabs = cdp_client.list_tabs('127.0.0.1', 9222)
            assert tabs == []
    
    def test_list_tabs_with_tabs(self):
        """测试：列出有标签页"""
        mock_tabs = [
            {'id': 'tab-1', 'url': 'https://example.com', 'title': 'Example', 'type': 'page'},
            {'id': 'tab-2', 'url': 'https://test.com', 'title': 'Test', 'type': 'page'}
        ]
        with patch('src.core.cdp_client.http_json', return_value=mock_tabs):
            tabs = cdp_client.list_tabs('127.0.0.1', 9222)
            assert len(tabs) == 2
            assert tabs[0]['id'] == 'tab-1'
    
    def test_version_info(self):
        """测试：获取版本信息"""
        with patch('src.core.cdp_client.http_json', return_value={'Browser': 'Chrome/120.0'}):
            info = cdp_client.version_info('127.0.0.1', 9222)
            assert 'Browser' in info
    
    def test_new_tab(self):
        """测试：新建标签页"""
        with patch('requests.post') as mock_post:
            mock_response = Mock()
            mock_response.json.return_value = {'id': 'new-tab-1'}
            mock_response.raise_for_status.return_value = None
            mock_post.return_value = mock_response

            result = cdp_client.new_tab('https://example.com', '127.0.0.1', 9222)
            assert result == {'id': 'new-tab-1'}

    def test_close_tab(self):
        """测试：关闭标签页"""
        with patch('src.core.cdp_client.http_json') as mock_http:
            cdp_client.close_tab('tab-1', '127.0.0.1', 9222)
            mock_http.assert_called_once_with('127.0.0.1', 9222, '/json/close/tab-1')
    
    def test_activate_tab(self):
        """测试：激活标签页"""
        with patch('requests.get') as mock_get:
            mock_response = Mock()
            mock_response.raise_for_status.return_value = None
            mock_get.return_value = mock_response
            
            cdp_client.activate_tab('tab-1', '127.0.0.1', 9222)
            mock_get.assert_called_once()
    
    def test_is_debug_port_alive(self):
        """测试：检测调试端口存活"""
        with patch('requests.get') as mock_get:
            mock_response = Mock()
            mock_response.raise_for_status.return_value = None
            mock_get.return_value = mock_response
            
            result = cdp_client.is_debug_port_alive('127.0.0.1', 9222)
            assert result is True
    
    def test_is_debug_port_not_alive(self):
        """测试：检测调试端口不存活"""
        with patch('requests.get') as mock_get:
            mock_get.side_effect = Exception('Connection refused')
            
            result = cdp_client.is_debug_port_alive('127.0.0.1', 9222)
            assert result is False


class TestCDPSession:
    """CDPSession类单元测试"""
    
    def test_session_init(self):
        """测试：会话初始化"""
        with patch('websocket.create_connection') as mock_ws:
            mock_ws.return_value = Mock()
            session = CDPSession('ws://127.0.0.1:9222/devtools/page/1')
            assert session.ws_url == 'ws://127.0.0.1:9222/devtools/page/1'
            session.close()
    
    def test_session_close(self):
        """测试：会话关闭"""
        with patch('websocket.create_connection') as mock_ws:
            mock_ws.return_value = Mock()
            session = CDPSession('ws://127.0.0.1:9222/devtools/page/1')
            session.close()
            mock_ws.return_value.close.assert_called_once()
    
    def test_session_context_manager(self):
        """测试：上下文管理器"""
        with patch('websocket.create_connection') as mock_ws:
            mock_ws.return_value = Mock()
            with CDPSession('ws://127.0.0.1:9222/devtools/page/1') as session:
                assert session is not None
    
    def test_send_command_success(self):
        """测试：发送命令成功"""
        with patch('websocket.create_connection') as mock_ws:
            mock_ws.return_value = Mock()
            mock_ws.return_value.recv.return_value = '{"id":1,"result":{}}'
            session = CDPSession('ws://127.0.0.1:9222/devtools/page/1')
            result = session.send('Page.enable')
            assert result == {}
            session.close()
    
    def test_send_command_error(self):
        """测试：发送命令失败"""
        with patch('websocket.create_connection') as mock_ws:
            mock_ws.return_value = Mock()
            mock_ws.return_value.recv.return_value = '{"id":1,"error":{"message":"Error"}}'
            session = CDPSession('ws://127.0.0.1:9222/devtools/page/1')
            with pytest.raises(CDPError):
                session.send('Page.enable')
            session.close()
    
    def test_eval_js_success(self):
        """测试：JS执行成功"""
        with patch('websocket.create_connection') as mock_ws:
            mock_ws.return_value = Mock()
            mock_ws.return_value.recv.return_value = '{"id":1,"result":{"result":{"value":"test"}}}'
            session = CDPSession('ws://127.0.0.1:9222/devtools/page/1')
            result = session.eval_js('1+1')
            assert result == 'test'
            session.close()
    
    def test_eval_js_exception(self):
        """测试：JS执行异常"""
        with patch('websocket.create_connection') as mock_ws:
            mock_ws.return_value = Mock()
            mock_ws.return_value.recv.return_value = '{"id":1,"result":{"result":{"type":"string","value":"Error"}}}'
            session = CDPSession('ws://127.0.0.1:9222/devtools/page/1')
            result = session.eval_js('throw new Error("test")')
            assert result == 'Error'
            session.close()
    
    def test_cookie_operations(self):
        """测试：Cookie操作"""
        with patch('websocket.create_connection') as mock_ws:
            mock_ws.return_value = Mock()
            mock_ws.return_value.recv.return_value = '{"id":1,"result":{"cookies":[]}}'
            session = CDPSession('ws://127.0.0.1:9222/devtools/page/1')
            cookies = session.get_all_cookies()
            assert cookies == []
            session.close()


class TestFindTab:
    """标签页查找功能单元测试"""
    
    def test_find_tab_by_id(self):
        """测试：按ID查找标签页"""
        mock_tabs = [
            {'id': 'tab-1', 'url': 'https://example.com'},
            {'id': 'tab-2', 'url': 'https://test.com'}
        ]
        with patch('src.core.cdp_client.list_tabs', return_value=mock_tabs):
            tab = cdp_client.find_tab(tab_id='tab-1')
            assert tab['id'] == 'tab-1'
    
    def test_find_tab_by_url_contains(self):
        """测试：按URL包含查找标签页"""
        mock_tabs = [
            {'id': 'tab-1', 'url': 'https://example.com/dashboard'},
            {'id': 'tab-2', 'url': 'https://test.com'}
        ]
        with patch('src.core.cdp_client.list_tabs', return_value=mock_tabs):
            tab = cdp_client.find_tab(url_contains='dashboard')
            assert tab['id'] == 'tab-1'
    
    def test_find_tab_by_title_contains(self):
        """测试：按标题包含查找标签页"""
        mock_tabs = [
            {'id': 'tab-1', 'title': 'Example Dashboard'},
            {'id': 'tab-2', 'title': 'Test Page'}
        ]
        with patch('src.core.cdp_client.list_tabs', return_value=mock_tabs):
            tab = cdp_client.find_tab(title_contains='Dashboard')
            assert tab['id'] == 'tab-1'
    
    def test_find_tab_first(self):
        """测试：查找第一个标签页"""
        mock_tabs = [
            {'id': 'tab-1', 'url': 'https://example.com'},
            {'id': 'tab-2', 'url': 'https://test.com'}
        ]
        with patch('src.core.cdp_client.list_tabs', return_value=mock_tabs):
            tab = cdp_client.find_tab()
            assert tab['id'] == 'tab-1'
    
    def test_find_tab_not_found(self):
        """测试：查找不存在的标签页"""
        with patch('src.core.cdp_client.list_tabs', return_value=[]):
            with pytest.raises(CDPError):
                cdp_client.find_tab(tab_id='non-existent')


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
