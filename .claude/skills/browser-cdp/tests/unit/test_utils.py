"""
utils.py 单元测试
"""
import pytest
import sys
from pathlib import Path
import tempfile
import time
from unittest.mock import patch, Mock, MagicMock
from io import StringIO

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).resolve().parent.parent.parent / '.claude' / 'skills' / 'browser-cdp'
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from src.core import utils
from src.core.cdp_client import CDPSession


class TestUtilsConnection:
    """连接工具函数单元测试"""
    
    def test_add_connection_args(self):
        """测试：添加连接参数"""
        import argparse
        parser = argparse.ArgumentParser()
        utils.add_connection_args(parser)
        
        args = parser.parse_args(['--host', '127.0.0.1', '--port', '9222', '--tab', 'tab-1'])
        assert args.host == '127.0.0.1'
        assert args.port == 9222
        assert args.tab_id == 'tab-1'
    
    def test_get_session_mock(self):
        """测试：获取会话（mock）"""
        mock_target = {'id': 'tab-1', 'webSocketDebuggerUrl': 'ws://127.0.0.1:9222/devtools/page/1'}

        with patch('src.core.cdp_client.list_tabs', return_value=[mock_target]), \
             patch('src.core.cdp_client.CDPSession') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session

            args = Mock(host='127.0.0.1', port=9222, tab_id='tab-1')
            session = utils.get_session(args)
            assert session == mock_session

    def test_get_session_with_ws_url(self):
        """测试：通过wsUrl获取会话（get_session 只接受 args 对象）"""
        with patch('src.core.cdp_client.CDPSession') as mock_session_class:
            mock_session = Mock()
            mock_session_class.return_value = mock_session

            args = Mock(host='127.0.0.1', port=9222, tab_id=None,
                        url_contains=None, title_contains=None)
            # get_session 内部调用 find_tab，find_tab 在无 tab 时会抛 CDPError
            # 这里只验证 args 对象能正常传入，不验证 ws_url 关键字参数（API 不支持）
            with patch('src.core.cdp_client.find_tab', side_effect=Exception('no tabs')):
                with pytest.raises(Exception):
                    utils.get_session(args)
            # 确认 CDPSession 未被直接调用（因为 find_tab 先抛异常）
            mock_session_class.assert_not_called()


class TestPrintJson:
    """JSON打印工具单元测试"""
    
    def test_print_json_dict(self, capsys):
        """测试：打印字典"""
        utils.print_json({'key': 'value'})
        captured = capsys.readouterr()
        assert 'key' in captured.out
        assert 'value' in captured.out
    
    def test_print_json_list(self, capsys):
        """测试：打印列表"""
        data = [1, 2, 3, 'test']
        utils.print_json(data)
        captured = capsys.readouterr()
        assert 'test' in captured.out
    
    def test_print_json_none(self, capsys):
        """测试：打印None"""
        utils.print_json(None)
        captured = capsys.readouterr()
        assert 'null' in captured.out


class TestDie:
    """错误处理工具单元测试"""
    
    def test_die_raises_system_exit(self):
        """测试：die函数抛出SystemExit"""
        with pytest.raises(SystemExit):
            utils.die('Test error')


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
