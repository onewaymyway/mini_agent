"""
test_dynamic_page_support.py - 动态页面支持单元测试

测试范围：
1. CookieManager - Cookie 存储/加载/持久化
2. RouteChangeDetector - SPA 路由检测
3. AntiDetectionManager - 反检测策略应用
"""
import asyncio
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ============================================================================
# CookieManager 测试
# ============================================================================


class TestCookieEntry:
    """CookieEntry 数据类测试"""

    def test_to_dict(self):
        from src.core.cookie_manager import CookieEntry
        entry = CookieEntry(
            name="session_id",
            value="abc123",
            domain="example.com",
            secure=True,
            http_only=True,
            same_site="Strict",
            expires=1700000000.0,
        )
        d = entry.to_dict()
        assert d["name"] == "session_id"
        assert d["value"] == "abc123"
        assert d["secure"] is True
        assert d["httpOnly"] is True
        assert d["sameSite"] == "Strict"
        assert d["expires"] == 1700000000.0

    def test_from_dict(self):
        from src.core.cookie_manager import CookieEntry
        d = {
            "name": "token",
            "value": "xyz789",
            "domain": "test.com",
            "secure": False,
            "httpOnly": False,
            "sameSite": "Lax",
            "session": False,
            "expires": 1700000000.0,
        }
        entry = CookieEntry.from_dict(d)
        assert entry.name == "token"
        assert entry.value == "xyz789"
        assert entry.secure is False
        assert entry.session is False

    def test_is_expired_session_cookie(self):
        from src.core.cookie_manager import CookieEntry
        entry = CookieEntry(name="s", value="v", domain="d.com", session=True)
        assert entry.is_expired() is False

    def test_is_expired_with_future_expires(self):
        from src.core.cookie_manager import CookieEntry
        entry = CookieEntry(
            name="s", value="v", domain="d.com", session=False,
            expires=time.time() + 3600,
        )
        assert entry.is_expired() is False

    def test_is_expired_with_past_expires(self):
        from src.core.cookie_manager import CookieEntry
        entry = CookieEntry(
            name="s", value="v", domain="d.com", session=False,
            expires=time.time() - 3600,
        )
        assert entry.is_expired() is True

    def test_to_cdp_cookie(self):
        from src.core.cookie_manager import CookieEntry
        entry = CookieEntry(
            name="k", value="v", domain="d.com", path="/",
            secure=True, http_only=True, same_site="Strict",
            expires=1700000000.0, session=False,
        )
        cdp = entry.to_cdp_cookie()
        assert cdp["name"] == "k"
        assert cdp["secure"] is True
        assert cdp["httpOnly"] is True
        assert cdp["sameSite"] == "Strict"
        assert cdp["expires"] == 1700000000.0


class TestDomainCookies:
    """DomainCookies 集合测试"""

    def test_add_and_get(self):
        from src.core.cookie_manager import DomainCookies, CookieEntry
        dc = DomainCookies(domain="example.com")
        dc.add(CookieEntry(name="a", value="1", domain="example.com"))
        dc.add(CookieEntry(name="b", value="2", domain="example.com"))
        assert len(dc.cookies) == 2
        assert dc.get("a").value == "1"
        assert dc.get("b").value == "2"
        assert dc.get("missing") is None

    def test_add_overwrites_same_name(self):
        from src.core.cookie_manager import DomainCookies, CookieEntry
        dc = DomainCookies(domain="example.com")
        dc.add(CookieEntry(name="k", value="old", domain="example.com"))
        dc.add(CookieEntry(name="k", value="new", domain="example.com"))
        assert len(dc.cookies) == 1
        assert dc.get("k").value == "new"

    def test_remove(self):
        from src.core.cookie_manager import DomainCookies, CookieEntry
        dc = DomainCookies(domain="example.com")
        dc.add(CookieEntry(name="a", value="1", domain="example.com"))
        dc.add(CookieEntry(name="b", value="2", domain="example.com"))
        n = dc.remove("a")
        assert n == 1
        assert len(dc.cookies) == 1
        assert dc.get("a") is None

    def test_purge_expired(self):
        from src.core.cookie_manager import DomainCookies, CookieEntry
        dc = DomainCookies(domain="example.com")
        dc.add(CookieEntry(name="keep", value="v", domain="example.com", session=True))
        dc.add(CookieEntry(
            name="expire", value="v", domain="example.com", session=False,
            expires=time.time() - 100,
        ))
        n = dc.purge_expired()
        assert n == 1
        assert len(dc.cookies) == 1
        assert dc.get("keep") is not None

    def test_to_cdp_list_filters_expired(self):
        from src.core.cookie_manager import DomainCookies, CookieEntry
        dc = DomainCookies(domain="example.com")
        dc.add(CookieEntry(name="k", value="v", domain="example.com", session=True))
        dc.add(CookieEntry(
            name="e", value="v", domain="example.com", session=False,
            expires=time.time() - 100,
        ))
        cdp_list = dc.to_cdp_list()
        assert len(cdp_list) == 1
        assert cdp_list[0]["name"] == "k"


class TestCookieManager:
    """CookieManager 集成测试"""

    @pytest.mark.asyncio
    async def test_save_and_load_cookies(self, tmp_path):
        from src.core.cookie_manager import CookieManager
        mgr = CookieManager(storage_dir=str(tmp_path / "cookies"))
        mock_session = AsyncMock()
        mock_session.eval_js = AsyncMock(return_value=[
            {"name": "session_id", "value": "abc123"},
            {"name": "user_pref", "value": "dark_mode"},
        ])
        count = await mgr.save_cookies(mock_session, "example.com", source_url="https://example.com")
        assert count == 2
        cookies = await mgr.load_cookies("example.com")
        assert len(cookies) == 2
        names = {c["name"] for c in cookies}
        assert names == {"session_id", "user_pref"}

    @pytest.mark.asyncio
    async def test_persist_to_disk(self, tmp_path):
        from src.core.cookie_manager import CookieManager
        mgr = CookieManager(storage_dir=str(tmp_path / "cookies"))
        mock_session = AsyncMock()
        mock_session.eval_js = AsyncMock(return_value=[{"name": "token", "value": "xyz"}])
        await mgr.save_cookies(mock_session, "test.com")
        cookie_files = list(tmp_path.glob("**/*.json"))
        assert len(cookie_files) >= 1
        data = json.loads(cookie_files[0].read_text(encoding="utf-8"))
        assert data["domain"] == "test.com"
        assert len(data["cookies"]) == 1
        assert data["cookies"][0]["name"] == "token"

    @pytest.mark.asyncio
    async def test_clear_cookies(self, tmp_path):
        from src.core.cookie_manager import CookieManager
        mgr = CookieManager(storage_dir=str(tmp_path / "cookies"))
        mock_session = AsyncMock()
        mock_session.eval_js = AsyncMock(return_value=[
            {"name": "a", "value": "1"},
            {"name": "b", "value": "2"},
        ])
        await mgr.save_cookies(mock_session, "example.com")
        assert len(await mgr.load_cookies("example.com")) == 2
        cleared = await mgr.clear_cookies("example.com")
        assert cleared == 2
        assert len(await mgr.load_cookies("example.com")) == 0

    @pytest.mark.asyncio
    async def test_add_cookies_to_browser(self, tmp_path):
        from src.core.cookie_manager import CookieManager
        mgr = CookieManager(storage_dir=str(tmp_path / "cookies"))
        mock_session = AsyncMock()
        mock_session.send = AsyncMock(return_value=None)
        mock_session.eval_js = AsyncMock(return_value=[])
        count = await mgr.add_cookies(
            mock_session,
            [
                {"name": "k1", "value": "v1", "domain": "example.com"},
                {"name": "k2", "value": "v2", "domain": "example.com"},
            ],
            "example.com",
        )
        assert count == 2
        mock_session.send.assert_called_once()
        call_args = mock_session.send.call_args
        assert call_args[0][0] == "Network.setCookies"

    @pytest.mark.asyncio
    async def test_auto_purge(self, tmp_path):
        from src.core.cookie_manager import CookieManager
        mgr = CookieManager(storage_dir=str(tmp_path / "cookies"), auto_purge=True)
        mock_session = AsyncMock()
        mock_session.eval_js = AsyncMock(return_value=[
            {"name": "session", "value": "keep", "session": True},
            {"name": "expired", "value": "gone", "session": False, "expires": time.time() - 1000},
        ])
        await mgr.save_cookies(mock_session, "example.com")
        mgr._last_purge = 0
        mgr._maybe_purge()
        cookies = await mgr.load_cookies("example.com")
        names = [c["name"] for c in cookies]
        assert "session" in names
        assert "expired" not in names

    def test_extract_domain(self):
        from src.core.cookie_manager import CookieManager
        assert CookieManager.extract_domain("https://example.com/path") == "example.com"
        assert CookieManager.extract_domain("http://sub.example.com") == "sub.example.com"


class TestSaveRestoreHelpers:
    """便捷函数测试"""

    @pytest.mark.asyncio
    async def test_save_session_cookies(self, tmp_path):
        from src.core.cookie_manager import save_session_cookies, CookieManager
        mgr = CookieManager(storage_dir=str(tmp_path / "cookies"))
        mock_session = AsyncMock()
        mock_session.eval_js = AsyncMock(return_value=[{"name": "x", "value": "y"}])
        n = await save_session_cookies(mock_session, mgr, "test.com")
        assert n == 1

    @pytest.mark.asyncio
    async def test_restore_session_cookies(self, tmp_path):
        from src.core.cookie_manager import restore_session_cookies, CookieManager
        mgr = CookieManager(storage_dir=str(tmp_path / "cookies"))
        mock_session_save = AsyncMock()
        mock_session_save.eval_js = AsyncMock(return_value=[{"name": "k", "value": "v"}])
        await mgr.save_cookies(mock_session_save, "test.com")
        mock_session_restore = AsyncMock()
        mock_session_restore.send = AsyncMock(return_value=None)
        n = await restore_session_cookies(mock_session_restore, mgr, "test.com")
        assert n == 1


# ============================================================================
# RouteChangeDetector 测试
# ============================================================================


class TestRouteChangeEvent:
    """RouteChangeEvent 枚举测试"""

    def test_all_events_have_values(self):
        from src.core.route_change_detector import RouteChangeEvent
        values = [e.value for e in list(RouteChangeEvent)]
        assert "navigate_started" in values
        assert "frame_navigated" in values
        assert "history_changed" in values
        assert "spa_route" in values


class TestRouteChangeRecord:
    """RouteChangeRecord 数据类测试"""

    def test_to_dict(self):
        from src.core.route_change_detector import RouteChangeRecord, RouteChangeEvent
        record = RouteChangeRecord(
            event_type=RouteChangeEvent.NAVIGATE_STARTED,
            old_url="https://a.com",
            new_url="https://a.com/page",
            frame_id="frame123",
        )
        d = record.to_dict()
        assert d["event_type"] == "navigate_started"
        assert d["old_url"] == "https://a.com"
        assert d["new_url"] == "https://a.com/page"
        assert d["frame_id"] == "frame123"

    def test_default_timestamp(self):
        from src.core.route_change_detector import RouteChangeRecord, RouteChangeEvent
        record = RouteChangeRecord(event_type=RouteChangeEvent.URL_CHANGED)
        assert isinstance(record.timestamp, float)
        assert record.timestamp > 0


class TestRouteChangeDetector:
    """RouteChangeDetector 单元测试"""

    @pytest.mark.asyncio
    async def test_start_stop_tracking(self):
        from src.core.route_change_detector import RouteChangeDetector
        mock_session = AsyncMock()
        mock_session.eval_js = AsyncMock(return_value="https://example.com")
        detector = RouteChangeDetector(mock_session)
        await detector.start_tracking()
        assert detector._tracking is True
        await detector.stop_tracking()
        assert detector._tracking is False

    @pytest.mark.asyncio
    async def test_get_current_url(self):
        from src.core.route_change_detector import RouteChangeDetector
        mock_session = AsyncMock()
        mock_session.eval_js = AsyncMock(return_value="https://example.com/page1")
        detector = RouteChangeDetector(mock_session)
        await detector.start_tracking()
        assert detector.get_current_url() == "https://example.com/page1"
        await detector.stop_tracking()

    @pytest.mark.asyncio
    async def test_events_tracking(self):
        from src.core.route_change_detector import RouteChangeDetector, RouteChangeEvent
        mock_session = AsyncMock()
        mock_session.eval_js = AsyncMock(return_value="https://example.com")
        detector = RouteChangeDetector(mock_session)
        await detector.start_tracking()
        from src.core.route_change_detector import RouteChangeRecord
        record = RouteChangeRecord(
            event_type=RouteChangeEvent.NAVIGATE_STARTED,
            old_url="https://example.com",
            new_url="https://example.com/new",
        )
        detector._append_event(record)
        assert detector.get_event_count() == 1
        assert detector.get_last_event().new_url == "https://example.com/new"
        await detector.stop_tracking()

    @pytest.mark.asyncio
    async def test_event_callback(self):
        from src.core.route_change_detector import RouteChangeDetector, RouteChangeEvent
        mock_session = AsyncMock()
        mock_session.eval_js = AsyncMock(return_value="https://example.com")
        detector = RouteChangeDetector(mock_session)
        await detector.start_tracking()
        received = []
        detector.on_event(lambda r: received.append(r))
        from src.core.route_change_detector import RouteChangeRecord
        record = RouteChangeRecord(
            event_type=RouteChangeEvent.SPA_ROUTE,
            old_url="https://example.com/a",
            new_url="https://example.com/b",
        )
        detector._fire_callbacks(record)
        assert len(received) == 1
        assert received[0].new_url == "https://example.com/b"
        await detector.stop_tracking()

    @pytest.mark.asyncio
    async def test_reset(self):
        from src.core.route_change_detector import RouteChangeDetector
        mock_session = AsyncMock()
        mock_session.eval_js = AsyncMock(return_value="https://example.com")
        detector = RouteChangeDetector(mock_session)
        await detector.start_tracking()
        from src.core.route_change_detector import RouteChangeRecord, RouteChangeEvent
        detector._append_event(RouteChangeRecord(event_type=RouteChangeEvent.URL_CHANGED))
        await detector.reset()
        assert detector.get_event_count() == 0
        assert detector._last_url == ""

    def test_spa_router_check_js_not_empty(self):
        from src.core.route_change_detector import RouteChangeDetector
        js = RouteChangeDetector.SPA_ROUTER_CHECK_JS
        assert len(js) > 100
        assert "vue" in js.lower()
        assert "react" in js.lower()
        assert "angular" in js.lower()


class TestWaitForSpaRoute:
    """wait_for_spa_route 便捷函数测试"""

    @pytest.mark.asyncio
    async def test_import(self):
        from src.core.route_change_detector import wait_for_spa_route
        assert callable(wait_for_spa_route)


# ============================================================================
# AntiDetectionManager 测试
# ============================================================================


class TestDetectionLevel:
    """DetectionLevel 枚举测试"""

    def test_all_levels(self):
        from src.core.anti_detection_manager import DetectionLevel
        values = [l.value for l in DetectionLevel]
        assert "none" in values
        assert "light" in values
        assert "medium" in values
        assert "strong" in values
        assert "extreme" in values


class TestAntiDetectionStrategy:
    """AntiDetectionStrategy 枚举测试"""

    def test_all_strategies(self):
        from src.core.anti_detection_manager import AntiDetectionStrategy
        values = [s.value for s in AntiDetectionStrategy]
        assert "basic" in values
        assert "stealth" in values
        assert "captcha" in values
        assert "proxy" in values
        assert "full" in values


class TestSiteProfile:
    """SiteProfile 测试"""

    def test_to_dict_roundtrip(self):
        from src.core.anti_detection_manager import SiteProfile, DetectionLevel, AntiDetectionStrategy
        profile = SiteProfile(
            domain="test.com",
            detection_level=DetectionLevel.STRONG,
            strategy=AntiDetectionStrategy.FULL,
            requires_stealth=True,
            rate_limit_rpm=30,
            custom_headers={"X-Custom": "value"},
        )
        d = profile.to_dict()
        assert d["domain"] == "test.com"
        assert d["detection_level"] == "strong"
        assert d["requires_stealth"] is True
        assert d["custom_headers"]["X-Custom"] == "value"
        restored = SiteProfile.from_dict(d)
        assert restored.domain == "test.com"
        assert restored.requires_stealth is True

    def test_default_values(self):
        from src.core.anti_detection_manager import SiteProfile, DetectionLevel, AntiDetectionStrategy
        profile = SiteProfile(
            domain="simple.com",
            detection_level=DetectionLevel.LIGHT,
            strategy=AntiDetectionStrategy.BASIC,
        )
        assert profile.requires_proxy is False
        assert profile.rate_limit_rpm == 60
        assert profile.user_agents == []
        assert profile.notes == ""


class TestDefaultSiteProfiles:
    """预定义网站配置测试"""

    def test_zhihu_profile(self):
        from src.core.anti_detection_manager import default_site_profiles
        zhihu = default_site_profiles.get("zhihu.com")
        assert zhihu is not None
        assert zhihu.requires_stealth is True
        assert zhihu.rate_limit_rpm == 30

    def test_gov_cn_profile(self):
        from src.core.anti_detection_manager import default_site_profiles
        gov = default_site_profiles.get("gov.cn")
        assert gov is not None
        assert gov.detection_level.value == "light"
        assert gov.requires_stealth is False

    def test_xueqiu_strong(self):
        from src.core.anti_detection_manager import default_site_profiles
        xq = default_site_profiles.get("xueqiu.com")
        assert xq is not None
        assert xq.requires_proxy is True
        assert xq.requires_captcha is True


class TestAntiDetectionManager:
    """AntiDetectionManager 单元测试"""

    @pytest.mark.asyncio
    async def test_resolve_known_domain(self):
        from src.core.anti_detection_manager import AntiDetectionManager
        mgr = AntiDetectionManager(AsyncMock())
        profile = mgr._resolve_profile("zhihu.com")
        assert profile.domain == "zhihu.com"
        assert profile.requires_stealth is True

    @pytest.mark.asyncio
    async def test_resolve_subdomain(self):
        from src.core.anti_detection_manager import AntiDetectionManager
        mgr = AntiDetectionManager(AsyncMock())
        profile = mgr._resolve_profile("www.zhihu.com")
        assert profile.requires_stealth is True

    @pytest.mark.asyncio
    async def test_resolve_unknown_domain_fallback(self):
        from src.core.anti_detection_manager import AntiDetectionManager
        mgr = AntiDetectionManager(AsyncMock())
        profile = mgr._resolve_profile("unknown.test.xyz")
        assert profile.detection_level.value == "light"
        assert profile.strategy.value == "basic"

    def test_extract_domain(self):
        from src.core.anti_detection_manager import AntiDetectionManager
        mgr = AntiDetectionManager(AsyncMock())
        assert mgr._extract_domain("https://example.com/path?q=1") == "example.com"
        assert mgr._extract_domain("http://sub.example.com/page") == "sub.example.com"

    @pytest.mark.asyncio
    async def test_get_stats_empty(self):
        from src.core.anti_detection_manager import AntiDetectionManager
        mgr = AntiDetectionManager(AsyncMock())
        stats = mgr.get_stats()
        assert stats["total_requests"] == 0
        assert stats["success_count"] == 0
        assert stats["blocked_count"] == 0

    @pytest.mark.asyncio
    async def test_log_event(self):
        from src.core.anti_detection_manager import AntiDetectionManager
        mgr = AntiDetectionManager(AsyncMock())
        mgr._log_event("apply", domain="test.com")
        mgr._log_event("scrape_success", url="https://test.com")
        log = mgr.get_log()
        assert len(log) == 2
        assert log[0]["event"] == "apply"
        assert log[1]["event"] == "scrape_success"

    @pytest.mark.asyncio
    async def test_log_size_limit(self):
        from src.core.anti_detection_manager import AntiDetectionManager
        mgr = AntiDetectionManager(AsyncMock())
        for i in range(600):
            mgr._log_event("test", index=i)
        log = mgr.get_log()
        assert len(log) <= 500

    @pytest.mark.asyncio
    async def test_quick_apply_protection(self):
        from src.core.anti_detection_manager import quick_apply_protection
        mock_session = AsyncMock()
        mock_session.eval_js = AsyncMock(return_value="https://example.com")
        profile = await quick_apply_protection(mock_session, "gov.cn")
        assert profile is not None
        assert profile.domain == "gov.cn"

    @pytest.mark.asyncio
    async def test_batch_scrape_protected(self):
        from src.core.anti_detection_manager import batch_scrape_protected
        mock_session = AsyncMock()
        mock_session.query_selector_all = AsyncMock(return_value=[])
        mock_session.get_attribute = AsyncMock(return_value=None)
        urls = ["https://example.com/a", "https://example.com/b"]
        results = await batch_scrape_protected(mock_session, urls, delay_between=0.01)
        assert len(results) == 2
        assert all(r["success"] for r in results)


# ============================================================================
# 集成测试：完整流程
# ============================================================================


class TestIntegration:
    """Cookie + Route + AntiDetection 集成流程测试"""

    @pytest.mark.asyncio
    async def test_cookie_save_then_restore(self, tmp_path):
        from src.core.cookie_manager import CookieManager
        mgr = CookieManager(storage_dir=str(tmp_path / "cookies"))
        mock_session = AsyncMock()
        mock_session.eval_js = AsyncMock(return_value=[
            {"name": "sid", "value": "secret123"},
        ])
        await mgr.save_cookies(mock_session, "api.example.com")
        cookies = await mgr.load_cookies("api.example.com")
        assert len(cookies) == 1
        assert cookies[0]["name"] == "sid"
        assert cookies[0]["value"] == "secret123"

    @pytest.mark.asyncio
    async def test_route_detector_tracks_changes(self):
        from src.core.route_change_detector import RouteChangeDetector, RouteChangeRecord, RouteChangeEvent
        mock_session = AsyncMock()
        mock_session.eval_js = AsyncMock(return_value="https://app.com")
        mock_session.send = AsyncMock()
        detector = RouteChangeDetector(mock_session)
        await detector.start_tracking()
        record = RouteChangeRecord(
            event_type=RouteChangeEvent.NAVIGATE_STARTED,
            old_url="https://app.com/home",
            new_url="https://app.com/dashboard",
        )
        detector._append_event(record)
        assert detector.get_event_count() == 1
        assert detector.get_last_event().new_url == "https://app.com/dashboard"
        await detector.stop_tracking()

    @pytest.mark.asyncio
    async def test_anti_detection_applies_profile(self):
        from src.core.anti_detection_manager import AntiDetectionManager
        mock_session = AsyncMock()
        mock_session.eval_js = AsyncMock(return_value="https://zhihu.com")
        mgr = AntiDetectionManager(mock_session)
        profile = await mgr.apply("zhihu.com")
        assert profile.requires_stealth is True
        assert mgr._active_profile is profile

    @pytest.mark.asyncio
    async def test_full_pipeline_cookie_and_route(self, tmp_path):
        from src.core.cookie_manager import CookieManager
        from src.core.route_change_detector import RouteChangeDetector, RouteChangeRecord, RouteChangeEvent

        mgr = CookieManager(storage_dir=str(tmp_path / "cookies"))
        mock_session = AsyncMock()
        mock_session.eval_js = AsyncMock(side_effect=[
            [{"name": "token", "value": "abc"}],
            "https://target.com",
            "{vue: null, react: null}",  # SPA router check
        ])
        mock_session.send = AsyncMock()

        # Step 1: Save cookies
        await mgr.save_cookies(mock_session, "target.com")
        # Step 2: Route tracking
        detector = RouteChangeDetector(mock_session)
        await detector.start_tracking()
        record = RouteChangeRecord(
            event_type=RouteChangeEvent.URL_CHANGED,
            old_url="https://target.com",
            new_url="https://target.com/page2",
        )
        detector._append_event(record)
        assert detector.get_current_url() == "https://target.com"  # start_tracking sets initial URL
        assert detector.get_last_event().new_url == "https://target.com/page2"
        await detector.stop_tracking()
        # Both modules work independently
        assert len(await mgr.load_cookies("target.com")) == 1


# ============================================================================
# 运行入口
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
