# -*- coding: utf-8 -*-
"""
反爬策略模块

提供请求头生成、请求限速、随机延迟、UA轮换等反爬功能。
"""

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 常见浏览器 UA 列表
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
]


@dataclass
class AntiScrapeConfig:
    """反爬配置"""
    # 请求间隔（秒）
    min_delay: float = 0.5
    max_delay: float = 2.0
    # UA 策略
    random_ua: bool = True
    # 是否启用代理
    use_proxy: bool = False
    # 请求头模板
    default_headers: Dict[str, str] = field(default_factory=lambda: {
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Referer': '',
    })


class AntiScrapeStrategy:
    """反爬策略执行器"""

    def __init__(self, config: Optional[AntiScrapeConfig] = None):
        self._config = config or AntiScrapeConfig()
        self._last_request_time = 0.0
        self._request_count = 0
        self._error_count = 0

    def get_headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """生成请求头，随机UA"""
        headers = dict(self._config.default_headers)
        if self._config.random_ua:
            headers['User-Agent'] = random.choice(USER_AGENTS)
        if extra:
            headers.update(extra)
        return headers

    async def wait_if_needed(self) -> None:
        """按需等待，避免请求过快"""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._config.min_delay:
            delay = random.uniform(self._config.min_delay, self._config.max_delay) - elapsed
            if delay > 0:
                await asyncio.sleep(delay)
        self._last_request_time = time.time()
        self._request_count += 1

    def record_success(self) -> None:
        self._error_count = max(0, self._error_count - 1)

    def record_failure(self) -> None:
        self._error_count += 1
        logger.debug(f"请求失败累计: {self._error_count} 次")

    @property
    def stats(self) -> Dict:
        return {
            'total_requests': self._request_count,
            'consecutive_errors': self._error_count,
            'min_delay': self._config.min_delay,
            'max_delay': self._config.max_delay,
        }

    def should_throttle(self) -> bool:
        """连续错误达到阈值时建议限速"""
        return self._error_count >= 5

    async def throttle_backoff(self, base: float = 2.0) -> None:
        """指数退避"""
        delay = base ** min(self._error_count, 5)
        jitter = random.uniform(0, 1)
        await asyncio.sleep(delay + jitter)
        self._error_count = 0