# -*- coding: utf-8 -*-
"""anti_scrape 单元测试"""

import pytest
import asyncio
from finance_toolkit.data_fetching.anti_scrape import AntiScrapeConfig, AntiScrapeStrategy


class TestAntiScrapeConfig:
    def test_default_config(self):
        cfg = AntiScrapeConfig()
        assert cfg.min_delay == 0.5
        assert cfg.max_delay == 2.0
        assert cfg.random_ua is True
        assert 'User-Agent' not in cfg.default_headers

    def test_custom_config(self):
        cfg = AntiScrapeConfig(min_delay=1.0, max_delay=3.0, random_ua=False)
        assert cfg.min_delay == 1.0
        assert cfg.max_delay == 3.0
        assert cfg.random_ua is False


class TestAntiScrapeStrategy:
    def setup_method(self):
        self.strategy = AntiScrapeStrategy()

    def test_get_headers_includes_ua(self):
        headers = self.strategy.get_headers()
        assert 'User-Agent' in headers
        assert headers['User-Agent'].startswith('Mozilla')

    def test_get_headers_with_extra(self):
        headers = self.strategy.get_headers(extra={'X-Custom': 'value'})
        assert headers['X-Custom'] == 'value'
        assert 'User-Agent' in headers

    def test_stats_initial(self):
        stats = self.strategy.stats
        assert stats['total_requests'] == 0
        assert stats['consecutive_errors'] == 0

    def test_record_success_decreases_errors(self):
        self.strategy.record_failure()
        self.strategy.record_failure()
        assert self.strategy.stats['consecutive_errors'] == 2
        self.strategy.record_success()
        assert self.strategy.stats['consecutive_errors'] == 1

    def test_record_failure_increases(self):
        for _ in range(5):
            self.strategy.record_failure()
        assert self.strategy.should_throttle() is True
        assert self.strategy.stats['consecutive_errors'] == 5

    def test_should_not_throttle_when_low_errors(self):
        self.strategy.record_failure()
        assert self.strategy.should_throttle() is False

    @pytest.mark.asyncio
    async def test_wait_if_needed(self):
        """验证等待逻辑不抛异常"""
        await self.strategy.wait_if_needed()
        assert self.strategy.stats['total_requests'] == 1

    @pytest.mark.asyncio
    async def test_throttle_backoff(self):
        """验证指数退避不抛异常"""
        for _ in range(3):
            self.strategy.record_failure()
        await self.strategy.throttle_backoff(base=1.0)
        assert self.strategy.stats['consecutive_errors'] == 0
