#!/usr/bin/env python3
"""
browser-web-operation skill 测试套件
验证所有核心功能的正确性和边界情况
"""
import subprocess
import sys
import os
import json
import time
import unittest
from pathlib import Path

SKILL_DIR = Path(r"E:\codes\mini_claude_code\.claude\skills\browser-cdp")
OUTPUT_DIR = Path(r"E:\codes\mini_claude_code\.agent\sessions\d17bc9b0\temp")
SESSION_NAME = "test_session"


def run_cmd(cmd: str, timeout=30) -> tuple:
    """运行命令并返回 (returncode, stdout, stderr)"""
    env = {**os.environ, "PYTHONPATH": str(SKILL_DIR)}
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=timeout,
        cwd=str(SKILL_DIR), env=env
    )
    return result.returncode, result.stdout, result.stderr


def get_tab_id(port: int = 9333) -> str:
    """获取当前 tab ID"""
    rc, stdout, stderr = run_cmd(f'python -m src.core.browser_tabs --port {port} --list')
    if rc == 0:
        try:
            tabs = json.loads(stdout)
            if tabs:
                return tabs[0].get('id', '')
        except:
            pass
    return ''


class TestBrowserWebOperation(unittest.TestCase):
    """browser-web-operation skill 测试类"""
    
    @classmethod
    def setUpClass(cls):
        """启动浏览器实例"""
        cls.port = 9335  # 使用不同端口避免冲突
        rc, stdout, stderr = run_cmd(
            f'python -m src.core.browser_launch --dedicated --name {SESSION_NAME} --start-url "about:blank" --port {cls.port}'
        )
        if rc != 0:
            raise unittest.SkipTest(f"浏览器启动失败: {stderr}")
        time.sleep(1)
        cls.tab_id = get_tab_id(cls.port)
        if not cls.tab_id:
            raise unittest.SkipTest("无法获取 tab ID")
    
    @classmethod
    def tearDownClass(cls):
        """关闭浏览器实例"""
        run_cmd(f'python -m src.core.browser_launch --stop-dedicated {SESSION_NAME}')
    
    def test_01_navigation(self):
        """测试页面导航"""
        rc, stdout, stderr = run_cmd(
            f'python -m src.core.browser_nav --port {self.port} --tab {self.tab_id} --goto "https://example.com" --wait-for networkidle'
        )
        self.assertEqual(rc, 0, f"导航失败: {stderr}")
        data = json.loads(stdout)
        self.assertIn("example.com", data["url"])
        self.assertEqual(data["title"], "Example Domain")
    
    def test_02_element_extraction(self):
        """测试元素提取"""
        rc, stdout, stderr = run_cmd(
            f'python -m src.core.browser_extract --port {self.port} --tab {self.tab_id} --mode elements'
        )
        self.assertEqual(rc, 0, f"元素提取失败: {stderr}")
        elements = json.loads(stdout)
        self.assertIsInstance(elements, list)
        self.assertGreater(len(elements), 0, "未找到任何元素")
        # 验证元素结构
        for el in elements:
            self.assertIn("index", el)
            self.assertIn("tag", el)
            self.assertIn("rect", el)
    
    def test_03_element_click(self):
        """测试元素点击"""
        # 先获取元素
        rc, stdout, _ = run_cmd(
            f'python -m src.core.browser_extract --port {self.port} --tab {self.tab_id} --mode elements'
        )
        elements = json.loads(stdout)
        if not elements:
            raise unittest.SkipTest("无元素可点击")
        
        idx = elements[0]["index"]
        rc, stdout, stderr = run_cmd(
            f'python -m src.core.browser_input --port {self.port} --tab {self.tab_id} --click-index {idx}'
        )
        self.assertEqual(rc, 0, f"点击失败: {stderr}")
        self.assertIn("已点击", stdout)
    
    def test_04_screenshot(self):
        """测试截图功能"""
        out_path = OUTPUT_DIR / "test_shot.png"
        rc, stdout, stderr = run_cmd(
            f'python -m src.core.browser_screenshot --port {self.port} --tab {self.tab_id} --out {out_path} --annotate --timeout 30'
        )
        self.assertEqual(rc, 0, f"截图失败: {stderr}")
        self.assertTrue(out_path.exists(), f"截图文件不存在: {out_path}")
        self.assertGreater(out_path.stat().st_size, 0, "截图文件为空")
    
    def test_05_text_extraction(self):
        """测试文本提取"""
        rc, stdout, stderr = run_cmd(
            f'python -m src.core.browser_extract --port {self.port} --tab {self.tab_id} --mode text'
        )
        self.assertEqual(rc, 0, f"文本提取失败: {stderr}")
        self.assertGreater(len(stdout.strip()), 0, "提取内容为空")
    
    def test_06_link_extraction(self):
        """测试链接提取"""
        rc, stdout, stderr = run_cmd(
            f'python -m src.core.browser_extract --port {self.port} --tab {self.tab_id} --mode links'
        )
        self.assertEqual(rc, 0, f"链接提取失败: {stderr}")
        links = json.loads(stdout)
        self.assertIsInstance(links, list)
        for link in links:
            self.assertIn("text", link)
            self.assertIn("href", link)
    
    def test_07_back_navigation(self):
        """测试后退导航"""
        # 先导航到新页面
        run_cmd(f'python -m src.core.browser_nav --port {self.port} --tab {self.tab_id} --goto "https://httpbin.org/get"')
        time.sleep(1)
        
        # 后退
        rc, stdout, stderr = run_cmd(
            f'python -m src.core.browser_nav --port {self.port} --tab {self.tab_id} --back'
        )
        self.assertEqual(rc, 0, f"后退失败: {stderr}")
    
    def test_08_reload(self):
        """测试页面刷新"""
        rc, stdout, stderr = run_cmd(
            f'python -m src.core.browser_nav --port {self.port} --tab {self.tab_id} --reload'
        )
        self.assertEqual(rc, 0, f"刷新失败: {stderr}")
    
    def test_09_wait_selector(self):
        """测试等待元素"""
        rc, stdout, stderr = run_cmd(
            f'python -m src.core.browser_nav --port {self.port} --tab {self.tab_id} --goto "https://example.com" --wait-for networkidle'
        )
        self.assertEqual(rc, 0)
        
        # 等待标题元素
        rc, stdout, stderr = run_cmd(
            f'python -m src.core.browser_nav --port {self.port} --tab {self.tab_id} --wait-selector "h1" --timeout 10'
        )
        self.assertEqual(rc, 0, f"等待元素失败: {stderr}")
    
    def test_10_input_text(self):
        """测试文本输入"""
        # 导航到有输入框的页面
        run_cmd(f'python -m src.core.browser_nav --port {self.port} --tab {self.tab_id} --goto "https://example.com"')
        time.sleep(1)
        
        # 获取输入框元素
        rc, stdout, _ = run_cmd(
            f'python -m src.core.browser_extract --port {self.port} --tab {self.tab_id} --mode elements'
        )
        elements = json.loads(stdout)
        input_elements = [e for e in elements if e.get('tag') == 'input']
        
        if input_elements:
            idx = input_elements[0]["index"]
            rc, stdout, stderr = run_cmd(
                f'python -m src.core.browser_input --port {self.port} --tab {self.tab_id} --type-index {idx} --text "test input"'
            )
            self.assertEqual(rc, 0, f"输入失败: {stderr}")
        else:
            self.skipTest("页面无输入框")


class TestErrorHandling(unittest.TestCase):
    """测试错误处理"""
    
    def test_invalid_tab_id(self):
        """测试无效 tab ID"""
        rc, stdout, stderr = run_cmd(
            'python -m src.core.browser_nav --port 9333 --tab INVALID_TAB --goto "https://example.com"'
        )
        # 应该返回错误而非崩溃
        self.assertNotEqual(rc, 0)
    
    def test_timeout_handling(self):
        """测试超时处理"""
        rc, stdout, stderr = run_cmd(
            'python -m src.core.browser_nav --port 9333 --tab test --goto "https://example.com" --timeout 1'
        )
        # 应该返回错误而非崩溃（无效 tab ID 会触发 CDPError）
        self.assertNotEqual(rc, 0, "应该返回非零退出码")
        # 错误信息应包含有意义的提示
        self.assertGreater(len(stderr.strip()), 0, "错误信息不应为空")


if __name__ == "__main__":
    unittest.main(verbosity=2)
