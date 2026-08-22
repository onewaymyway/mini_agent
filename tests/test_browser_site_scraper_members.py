"""
tests/test_browser_site_scraper_members.py

对应 next_doc/generative-capability-skill-plan.md 阶段十七。

覆盖一个真实观察到的问题：`baidu`/`zhihu` 这两个人工预置 member，在提取
JS 找不到任何结果容器、且没有命中已知反爬/登录墙关键词时，此前会直接
返回 `{"status": "success", "data": {"results": []}}`——调用方拿到一个
"看起来成功但什么信息都没有"的结果，且不带任何调试线索，无法判断到底是
真的没有结果、还是选择器过期/页面结构变化/内容还没渲染完。

修复后：这种情况应该返回 `status: fail`，并在 `error` 里附带调试快照
（url/title/正文摘要），而不是静默返回一个无从排查的空成功。

不依赖真实 Chrome：monkeypatch `session_manager.get_or_create_session`
返回一个假 session，其 `eval_js` 按脚本内容返回受控数据。
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
BROWSER_CORE_IMPL_DIR = REPO_ROOT / ".claude" / "skills" / "browser-core" / "impl"
SCRAPER_DIR = REPO_ROOT / ".claude" / "skills" / "browser-site-scraper"


def _load_module(path: Path, name_prefix: str):
    module_name = f"{name_prefix}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _load_member_script(member: str):
    impl_dir_str = str(BROWSER_CORE_IMPL_DIR.resolve())
    if impl_dir_str not in sys.path:
        sys.path.insert(0, impl_dir_str)
    return _load_module(SCRAPER_DIR / "members" / member / "script.py", f"member_{member}")


def _fresh_session_manager_module():
    """
    member 的 `run()` 内部是 `import session_manager`（flat import，函数
    局部执行），不是模块级属性——不能通过 `mock.patch.object(script_module,
    'session_manager', ...)` 打桩（那样只是给 script 模块加了一个从未被
    读取的属性）。真正要打桩的是 `sys.modules['session_manager']` 本身：
    局部 `import session_manager` 在 `sys.modules` 里已有同名模块时，只是
    绑定一个指向同一个模块对象的本地引用，所以 monkeypatch 这个模块对象的
    `get_or_create_session` 属性，member 内部的局部 import 也会看到打桩后
    的版本。用一个独立的模块名重新加载一份，避免和其他测试/真实运行共用
    同一个全局 `session_manager` 缓存产生串扰。
    """
    impl_dir_str = str(BROWSER_CORE_IMPL_DIR.resolve())
    if impl_dir_str not in sys.path:
        sys.path.insert(0, impl_dir_str)
    module = _load_module(BROWSER_CORE_IMPL_DIR / "session_manager.py", "session_manager_test")
    sys.modules["session_manager"] = module
    return module


class _FakeSession:
    """按脚本片段的关键内容返回受控数据，不做真实 JS 解析。"""

    def __init__(self, extract_return, blocked_reason=None):
        self._extract_return = extract_return
        self._blocked_reason = blocked_reason
        self.navigate_calls = []

    def navigate(self, url, timeout=20.0):
        self.navigate_calls.append(url)

    def eval_js(self, script: str):
        if "location.href" in script and "document.title" in script:
            # capture_debug_context 的探测脚本
            return {"url": "https://example.com/blocked", "title": "示例页面", "body_excerpt": "..."}
        if "indicators" in script:
            return self._blocked_reason
        # 剩下的就是各自的结果提取脚本
        return self._extract_return


class TestBaiduEmptyResultsAreReportedAsFailure(unittest.TestCase):
    def setUp(self):
        self.session_manager = _fresh_session_manager_module()
        self.script = _load_member_script("baidu")

    def _run_with_fake_session(self, fake_session):
        with mock.patch.object(self.session_manager, "get_or_create_session", return_value=fake_session):
            return self.script.run({"query": "test", "target": {"url": "https://www.baidu.com/s?wd=test"}})

    def test_empty_results_without_known_block_keyword_fails_with_debug_info(self):
        fake_session = _FakeSession(extract_return=[], blocked_reason=None)
        result = self._run_with_fake_session(fake_session)
        self.assertEqual(result["status"], "fail")
        self.assertIsNone(result["data"])
        self.assertIn("0 条结果", result["error"])
        # 调试信息（url/title）应该出现在错误里，而不是被丢弃
        self.assertIn("example.com", result["error"])

    def test_nonempty_results_still_succeed(self):
        fake_session = _FakeSession(
            extract_return=[{"title": "t", "url": "https://x", "snippet": "s", "published_time": ""}],
            blocked_reason=None,
        )
        result = self._run_with_fake_session(fake_session)
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["data"]["results"]), 1)

    def test_known_captcha_keyword_still_fails_with_specific_reason(self):
        fake_session = _FakeSession(extract_return=[], blocked_reason="验证码")
        result = self._run_with_fake_session(fake_session)
        self.assertEqual(result["status"], "fail")
        self.assertIn("验证码", result["error"])


class TestZhihuEmptyResultsAreReportedAsFailure(unittest.TestCase):
    def setUp(self):
        self.session_manager = _fresh_session_manager_module()
        self.script = _load_member_script("zhihu")

    def _run_with_fake_session(self, fake_session):
        with mock.patch.object(self.session_manager, "get_or_create_session", return_value=fake_session):
            return self.script.run({"query": "test", "target": {"url": "https://www.zhihu.com/search?q=test"}})

    def test_empty_results_without_known_login_wall_keyword_fails_with_debug_info(self):
        fake_session = _FakeSession(extract_return=[], blocked_reason=None)
        result = self._run_with_fake_session(fake_session)
        self.assertEqual(result["status"], "fail")
        self.assertIn("0 条结果", result["error"])
        self.assertIn("example.com", result["error"])

    def test_nonempty_results_still_succeed(self):
        fake_session = _FakeSession(
            extract_return=[{"title": "t", "url": "https://x", "snippet": "s", "author": "a"}],
            blocked_reason=None,
        )
        result = self._run_with_fake_session(fake_session)
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["data"]["results"]), 1)


if __name__ == "__main__":
    unittest.main()
