"""
browser-cdp 环境验证示例测试
验证浏览器自动化工具和测试框架可正常运行
"""
import pytest
import sys
import os
from pathlib import Path
import time
import subprocess

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))


class TestEnvironmentDemo:
    """环境验证测试类"""
    
    @pytest.mark.integration
    @pytest.mark.browser
    def test_browser_launch_via_cli(self, tmp_path):
        """测试：通过命令行启动浏览器并验证"""
        # 创建临时 profile 目录
        profile_dir = str(tmp_path / "test_profile")
        os.makedirs(profile_dir, exist_ok=True)
        
        # 启动浏览器（使用 CLI 方式，设置 PYTHONPATH）
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SKILL_DIR) + os.pathsep + env.get("PYTHONPATH", "")
        
        cmd = [
            sys.executable,
            str(SKILL_DIR / "src" / "core" / "browser_launch.py"),
            "--dedicated",
            "--name", "demo",
            "--headless",
            "--user-data-dir", profile_dir,
            "--start-url", "https://httpbin.org/get",
        ]
        
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
        
        # 检查启动是否成功
        assert proc.returncode == 0, f"浏览器启动失败: {proc.stderr}"
        assert "[ok]" in proc.stdout, f"未找到成功标记: {proc.stdout}"
        
        print(f"\n✅ 浏览器启动成功")
        print(f"   输出: {proc.stdout[:500]}")
        
        # 停止浏览器
        stop_cmd = [
            sys.executable,
            str(SKILL_DIR / "src" / "core" / "browser_launch.py"),
            "--stop-dedicated", "demo"
        ]
        subprocess.run(stop_cmd, capture_output=True, timeout=10, env=env)
        
        print(f"✅ 浏览器已停止")
    
    @pytest.mark.unit
    def test_import_modules(self):
        """测试：验证所有核心模块可导入"""
        from src.core import browser_launch
        from src.core import cdp_client
        from src.core import browser_nav
        from src.core import browser_screenshot
        from src.core import browser_extract
        from src.core import browser_input
        from src.core import browser_console
        from src.core import browser_watch
        
        print(f"\n✅ 所有核心模块导入成功")
        print(f"   - browser_launch")
        print(f"   - cdp_client")
        print(f"   - browser_nav")
        print(f"   - browser_screenshot")
        print(f"   - browser_extract")
        print(f"   - browser_input")
        print(f"   - browser_console")
        print(f"   - browser_watch")
    
    @pytest.mark.unit
    def test_searchers_import(self):
        """测试：验证搜索器模块可导入"""
        from src.searchers import baidu_search
        from src.searchers import bing_search
        from src.searchers import zhihu_search
        
        # 验证关键函数存在
        assert hasattr(baidu_search, 'search_baidu')
        assert hasattr(bing_search, 'search_bing')
        assert hasattr(zhihu_search, 'search_zhihu_via_baidu')
        
        print(f"\n✅ 搜索器模块导入成功")
        print(f"   - baidu_search (search_baidu)")
        print(f"   - bing_search (search_bing)")
        print(f"   - zhihu_search (search_zhihu_via_baidu)")


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])
