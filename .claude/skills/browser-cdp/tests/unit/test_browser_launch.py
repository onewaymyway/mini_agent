"""
browser_launch.py 单元测试
"""
import pytest
import sys
import os
from pathlib import Path
import tempfile
import time
import json
from unittest.mock import patch, Mock, MagicMock

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from src.core import browser_launch


class TestBrowserLaunch:
    """浏览器启动模块单元测试"""
    
    def test_load_registry(self):
        """测试：加载注册表"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            test_registry = f.name
        
        try:
            # 保存原始注册表路径
            original_registry = browser_launch.REGISTRY_PATH
            browser_launch.REGISTRY_PATH = test_registry
            
            # 写入测试数据
            test_data = {
                "test_instance": {
                    "name": "test_instance",
                    "port": 9333,
                    "profile_dir": "temp_cdp/cdp_browser_data/test_instance",
                    "pid": 12345,
                    "start_url": "https://example.com",
                    "created_at": "2024-01-01T00:00:00"
                }
            }
            with open(test_registry, 'w') as f:
                json.dump(test_data, f)
            
            # 测试加载
            loaded = browser_launch._load_registry()
            assert loaded == test_data
        finally:
            browser_launch.REGISTRY_PATH = original_registry
            if os.path.exists(test_registry):
                os.unlink(test_registry)
    
    def test_save_registry(self):
        """测试：保存注册表"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            test_registry = f.name
        
        try:
            original_registry = browser_launch.REGISTRY_PATH
            browser_launch.REGISTRY_PATH = test_registry
            
            test_data = {"test": {"port": 9333}}
            browser_launch._save_registry(test_data)
            
            with open(test_registry, 'r') as f:
                loaded = json.load(f)
            assert loaded == test_data
        finally:
            browser_launch.REGISTRY_PATH = original_registry
            if os.path.exists(test_registry):
                os.unlink(test_registry)
    
    def test_extract_debug_ports_from_cmdlines(self):
        """测试：从命令行提取调试端口"""
        cmdlines = [
            "chrome.exe --remote-debugging-port=9222 --user-data-dir=/tmp/chrome",
            "msedge.exe --remote-debugging-port=9333",
            "chrome.exe --no-sandbox",
        ]
        ports = browser_launch._extract_debug_ports_from_cmdlines(cmdlines)
        assert 9222 in ports
        assert 9333 in ports
        assert len(ports) == 2
    
    def test_remove_singleton_locks(self):
        """测试：移除单实例锁文件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建锁文件
            lock_files = ["SingletonLock", "SingletonCookie", "SingletonSocket"]
            for lf in lock_files:
                with open(os.path.join(tmpdir, lf), 'w') as f:
                    f.write("lock")
            
            # 创建正常文件（不应被删除）
            with open(os.path.join(tmpdir, "Cookies"), 'w') as f:
                f.write("cookies")
            
            browser_launch._remove_singleton_locks(tmpdir)
            
            # 锁文件应被删除
            for lf in lock_files:
                assert not os.path.exists(os.path.join(tmpdir, lf))
            # 正常文件应保留
            assert os.path.exists(os.path.join(tmpdir, "Cookies"))
    
    @patch('src.core.browser_launch.platform.system')
    @patch('src.core.browser_launch.subprocess.Popen')
    def test_spawn_browser(self, mock_popen, mock_system):
        """测试：启动浏览器进程"""
        mock_system.return_value = "Windows"
        mock_proc = Mock()
        mock_proc.pid = 12345
        mock_popen.return_value = mock_proc
        
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = browser_launch.spawn_browser(
                binary="chrome",
                port=9333,
                user_data_dir=tmpdir,
                headless=False,
                start_url="https://example.com"
            )
            
            assert proc == mock_proc
            mock_popen.assert_called_once()
            args = mock_popen.call_args[0][0]
            assert "--remote-debugging-port=9333" in args
            assert f"--user-data-dir={tmpdir}" in args
            assert "https://example.com" in args


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
