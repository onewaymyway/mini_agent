"""风控拦截与登录失效的区分（回归：小红书风控页曾被误判为"需要重新登录"）。"""
import tempfile
import unittest
from pathlib import Path

from adapters.html_list import HtmlListAdapter  # noqa: F401  (确保适配器可导入)
from core.browser_session import (
    BrowserSession,
    LoginRequired,
    RiskControlBlocked,
    classify_landing,
)

# 用户实际遇到的风控跳转 URL（rejectUrl 里含 /login/，整串匹配会误判）
XHS_RISK_URL = (
    "https://www.xiaohongshu.com/website-login/error?redirectPath=https://www.xiaohongshu.com/explore"
    "&uuid=7a9f0774-90b4-4bcd-aed7-afc03fc777f3&error_code=300012"
    "&error_msg=%E5%BD%93%E5%89%8D%E7%BD%91%E7%BB%9C%E5%AD%98%E5%9C%A8%E9%A3%8E%E9%99%A9"
    "%EF%BC%8C%E8%AF%B7%E5%88%87%E6%8D%A2%E5%8F%AF%E9%9D%A0%E7%BD%91%E7%BB%9C%E5%90%8E%E9%87%8D%E8%AF%95"
    "&rejectUrl=edith.xiaohongshu.com/api/sns/web/v1/login/qrcode/create&verifyMsg="
)


class ClassifyLandingTests(unittest.TestCase):
    def test_risk_error_page_is_blocked_not_expired(self):
        res = classify_landing(XHS_RISK_URL, "/login")
        self.assertIsNotNone(res)
        kind, code, msg = res
        self.assertEqual(kind, "blocked")
        self.assertEqual(code, "300012")
        self.assertIn("网络", msg)

    def test_real_login_redirect_still_expired(self):
        self.assertEqual(classify_landing("https://www.zhihu.com/signin", "/signin"), ("expired", "", ""))
        self.assertEqual(classify_landing("https://x.com/login?next=/hot", "/login")[0], "expired")

    def test_normal_page_returns_none(self):
        self.assertIsNone(classify_landing("https://www.xiaohongshu.com/explore", "/login"))
        self.assertIsNone(classify_landing("https://www.zhihu.com/hot", "/signin"))

    def test_expired_pattern_in_query_alone_does_not_expire(self):
        # 只出现在 query 中的 /login 不应被视为登录失效
        self.assertIsNone(classify_landing("https://x.com/explore?redirect=/login", "/login"))


class FakePage:
    def __init__(self, url):
        self.url = url

    def goto(self, url, **kw):
        # 模拟服务端重定向：最终落地的是 self.url（构造时给定），不被请求 URL 覆盖
        return None

    def wait_for_selector(self, *a, **kw):
        return None

    def content(self):
        return "<html></html>"


class FakeCtx:
    def __init__(self, landing):
        self._landing = landing

    def new_page(self):
        return FakePage(self._landing)

    def close(self):
        pass


class FakePw:
    pass


class FetchFlowTests(unittest.TestCase):
    def _session(self, landing, tmp):
        return BrowserSession(
            Path(tmp) / "profile",
            Path(tmp) / "login_status.json",
            launcher=lambda headless: (FakePw(), FakeCtx(landing)),
        )

    def test_risk_page_raises_risk_and_records_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self._session(XHS_RISK_URL, tmp)
            with self.assertRaises(RiskControlBlocked) as cm:
                s.fetch_html("https://www.xiaohongshu.com/explore", site="xiaohongshu", expired_pattern="/login")
            self.assertEqual(cm.exception.error_code, "300012")
            self.assertEqual(s.login_status()["xiaohongshu"]["status"], "blocked")

    def test_risk_page_does_not_raise_login_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self._session(XHS_RISK_URL, tmp)
            with self.assertRaises(RiskControlBlocked):
                s.fetch_html("https://www.xiaohongshu.com/explore", site="xiaohongshu", expired_pattern="/login")
            # 确认不是 LoginRequired
            try:
                s.fetch_html("https://www.xiaohongshu.com/explore", site="xiaohongshu", expired_pattern="/login")
            except LoginRequired:
                self.fail("风控页不应被判为登录失效")
            except RiskControlBlocked:
                pass

    def test_login_redirect_still_raises_login_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self._session("https://www.zhihu.com/signin", tmp)
            with self.assertRaises(LoginRequired):
                s.fetch_html("https://www.zhihu.com/hot", site="zhihu", expired_pattern="/signin")
            self.assertEqual(s.login_status()["zhihu"]["status"], "expired")

    def test_normal_page_returns_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self._session("https://www.xiaohongshu.com/explore", tmp)
            html = s.fetch_html("https://www.xiaohongshu.com/explore", site="xiaohongshu", expired_pattern="/login")
            self.assertIn("<html>", html)


if __name__ == "__main__":
    unittest.main()
