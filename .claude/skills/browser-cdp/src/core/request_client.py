"""
请求客户端封装 - 统一的HTTP请求层

提供同步/异步请求能力，集成重试、限流、UA轮换、代理池。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import aiohttp
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


@dataclass
class RequestConfig:
    """请求配置"""
    timeout: float = 30.0
    max_retries: int = 3
    backoff_factor: float = 0.5
    headers: Dict[str, str] = field(default_factory=dict)
    proxy: Optional[str] = None
    verify_ssl: bool = True
    allow_redirects: bool = True
    user_agent: Optional[str] = None
    session_cookies: Optional[Dict[str, str]] = None

    def get_headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Cache-Control": "max-age=0",
            "Upgrade-Insecure-Requests": "1",
        }
        if self.user_agent:
            headers["User-Agent"] = self.user_agent
        headers.update(self.headers)
        return headers


@dataclass
class HttpResponse:
    """HTTP响应封装"""
    url: str
    status_code: int
    headers: Dict[str, str]
    body: str
    elapsed_ms: float
    success: bool
    error: Optional[str] = None
    cookies: Dict[str, str] = field(default_factory=dict)
    final_url: str = ""
    content_type: str = ""

    @property
    def is_success(self) -> bool:
        return self.success and 200 <= self.status_code < 400

    @property
    def text(self) -> str:
        return self.body

    @property
    def json(self) -> Optional[Dict]:
        try:
            import json
            return json.loads(self.body)
        except Exception:
            return None


@dataclass
class RateLimitState:
    """限流状态"""
    domain: str
    last_request_time: float = 0.0
    request_count: int = 0
    window_start: float = 0.0
    window_limit: int = 10

    def can_request(self, min_interval: float = 0.5) -> bool:
        now = time.time()
        if now - self.last_request_time < min_interval:
            return False
        if now - self.window_start > 60:
            self.window_start = now
            self.request_count = 0
        if self.request_count >= self.window_limit:
            return False
        return True

    def record_request(self) -> None:
        self.last_request_time = time.time()
        self.request_count += 1


class RateLimiter:
    """域级别限流器"""

    def __init__(self, default_interval: float = 0.5, default_window_limit: int = 10):
        self._states: Dict[str, RateLimitState] = {}
        self._default_interval = default_interval
        self._default_window_limit = default_window_limit

    def _get_state(self, domain: str) -> RateLimitState:
        if domain not in self._states:
            self._states[domain] = RateLimitState(
                domain=domain,
                window_limit=self._default_window_limit,
            )
        return self._states[domain]

    def wait_if_needed(self, domain: str) -> None:
        state = self._get_state(domain)
        while not state.can_request(self._default_interval):
            time.sleep(self._default_interval)
        state.record_request()

    async def async_wait_if_needed(self, domain: str) -> None:
        state = self._get_state(domain)
        while not state.can_request(self._default_interval):
            await asyncio.sleep(self._default_interval)
        state.record_request()


class UaRotator:
    """UA轮换器"""

    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    ]

    @classmethod
    def random_ua(cls) -> str:
        import random
        return random.choice(cls.USER_AGENTS)

    @classmethod
    def get_ua(cls, index: int) -> str:
        return cls.USER_AGENTS[index % len(cls.USER_AGENTS)]


class SyncRequestClient:
    """同步HTTP请求客户端"""

    def __init__(
        self,
        default_timeout: float = 30.0,
        max_retries: int = 3,
        rate_limiter: Optional[RateLimiter] = None,
        ua_rotator: Optional[UaRotator] = None,
    ):
        self.default_timeout = default_timeout
        self.max_retries = max_retries
        self.rate_limiter = rate_limiter or RateLimiter()
        self.ua_rotator = ua_rotator or UaRotator()
        self._session: Optional[requests.Session] = None
        self._stats = {
            "total_requests": 0,
            "success_count": 0,
            "failure_count": 0,
            "retry_count": 0,
            "total_time_ms": 0.0,
        }

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            retry_strategy = Retry(
                total=self.max_retries,
                backoff_factor=0.5,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["GET", "POST"],
            )
            adapter = HTTPAdapter(max_retries=retry_strategy)
            self._session.mount("http://", adapter)
            self._session.mount("https://", adapter)
        return self._session

    def request(
        self,
        method: str,
        url: str,
        config: Optional[RequestConfig] = None,
        **kwargs,
    ) -> HttpResponse:
        cfg = config or RequestConfig()
        headers = cfg.get_headers()
        domain = self._extract_domain(url)
        self.rate_limiter.wait_if_needed(domain)

        start_time = time.time()
        last_error = None

        for attempt in range(cfg.max_retries + 1):
            try:
                session = self._get_session()
                resp = session.request(
                    method=method.upper(),
                    url=url,
                    headers=headers,
                    timeout=cfg.timeout,
                    proxies={"http": cfg.proxy, "https": cfg.proxy} if cfg.proxy else None,
                    verify=cfg.verify_ssl,
                    allow_redirects=cfg.allow_redirects,
                    **kwargs,
                )
                elapsed_ms = (time.time() - start_time) * 1000
                self._stats["total_requests"] += 1
                self._stats["success_count"] += 1
                self._stats["total_time_ms"] += elapsed_ms
                return HttpResponse(
                    url=url,
                    status_code=resp.status_code,
                    headers=dict(resp.headers),
                    body=resp.text,
                    elapsed_ms=elapsed_ms,
                    success=True,
                    cookies=dict(resp.cookies),
                    final_url=resp.url,
                    content_type=resp.headers.get("Content-Type", ""),
                )
            except requests.exceptions.Timeout:
                last_error = f"Timeout after {cfg.timeout}s"
                logger.warning(f"[{method}] {url} 超时 (attempt {attempt+1})")
            except requests.exceptions.ConnectionError as e:
                last_error = f"Connection error: {e}"
                logger.warning(f"[{method}] {url} 连接错误 (attempt {attempt+1})")
            except requests.exceptions.HTTPError as e:
                last_error = f"HTTP error: {e}"
                if resp.status_code >= 500:
                    logger.warning(f"[{method}] {url} 服务器错误 {resp.status_code}")
                else:
                    break
            except Exception as e:
                last_error = f"Unknown error: {e}"
                logger.error(f"[{method}] {url} 未知错误: {e}")

            if attempt < cfg.max_retries:
                delay = cfg.backoff_factor * (2 ** attempt)
                logger.debug(f"重试等待 {delay:.1f}s: {url}")
                time.sleep(delay)
                self._stats["retry_count"] += 1

        self._stats["total_requests"] += 1
        self._stats["failure_count"] += 1
        elapsed_ms = (time.time() - start_time) * 1000
        self._stats["total_time_ms"] += elapsed_ms
        logger.error(f"[{method}] {url} 最终失败: {last_error}")
        return HttpResponse(
            url=url, status_code=0, headers={}, body="",
            elapsed_ms=elapsed_ms, success=False, error=last_error,
        )

    def get(self, url: str, config: Optional[RequestConfig] = None, **kwargs) -> HttpResponse:
        return self.request("GET", url, config, **kwargs)

    def post(self, url: str, config: Optional[RequestConfig] = None, **kwargs) -> HttpResponse:
        return self.request("POST", url, config, **kwargs)

    def _extract_domain(self, url: str) -> str:
        try:
            import urllib.parse
            parsed = urllib.parse.urlparse(url)
            return parsed.netloc.lower().replace("www.", "", 1)
        except Exception:
            return url

    def get_stats(self) -> Dict[str, Any]:
        return self._stats.copy()

    def reset_stats(self) -> None:
        self._stats = {"total_requests": 0, "success_count": 0, "failure_count": 0, "retry_count": 0, "total_time_ms": 0.0}

    def close(self) -> None:
        if self._session:
            self._session.close()
            self._session = None


class AsyncRequestClient:
    """异步HTTP请求客户端"""

    def __init__(
        self,
        default_timeout: float = 30.0,
        max_retries: int = 3,
        rate_limiter: Optional[RateLimiter] = None,
        ua_rotator: Optional[UaRotator] = None,
        concurrency: int = 5,
    ):
        self.default_timeout = default_timeout
        self.max_retries = max_retries
        self.rate_limiter = rate_limiter or RateLimiter()
        self.ua_rotator = ua_rotator or UaRotator()
        self.concurrency = concurrency
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._stats = {"total_requests": 0, "success_count": 0, "failure_count": 0, "retry_count": 0, "total_time_ms": 0.0}
        self._session: Optional[aiohttp.ClientSession] = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.default_timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def _acquire_semaphore(self) -> None:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.concurrency)
        await self._semaphore.acquire()

    async def _release_semaphore(self) -> None:
        if self._semaphore:
            self._semaphore.release()

    async def request(
        self,
        method: str,
        url: str,
        config: Optional[RequestConfig] = None,
        **kwargs,
    ) -> HttpResponse:
        cfg = config or RequestConfig()
        headers = cfg.get_headers()
        domain = self._extract_domain(url)
        await self.rate_limiter.async_wait_if_needed(domain)
        await self._acquire_semaphore()

        try:
            session = await self._ensure_session()
            start_time = time.time()
            last_error = None

            for attempt in range(cfg.max_retries + 1):
                try:
                    async with session.request(
                        method=method.upper(), url=url, headers=headers,
                        ssl=cfg.verify_ssl, allow_redirects=cfg.allow_redirects, **kwargs,
                    ) as resp:
                        body = await resp.text()
                        elapsed_ms = (time.time() - start_time) * 1000
                        self._stats["total_requests"] += 1
                        self._stats["success_count"] += 1
                        self._stats["total_time_ms"] += elapsed_ms
                        return HttpResponse(
                            url=url, status_code=resp.status, headers=dict(resp.headers),
                            body=body, elapsed_ms=elapsed_ms, success=True,
                            final_url=str(resp.url), content_type=resp.content_type or "",
                        )
                except asyncio.TimeoutError:
                    last_error = f"Timeout after {cfg.timeout}s"
                    logger.warning(f"[{method}] {url} 超时 (attempt {attempt+1})")
                except aiohttp.ClientError as e:
                    last_error = f"Client error: {e}"
                    logger.warning(f"[{method}] {url} 客户端错误 (attempt {attempt+1})")
                except Exception as e:
                    last_error = f"Unknown error: {e}"
                    logger.error(f"[{method}] {url} 未知错误: {e}")

                if attempt < cfg.max_retries:
                    delay = cfg.backoff_factor * (2 ** attempt)
                    logger.debug(f"重试等待 {delay:.1f}s: {url}")
                    await asyncio.sleep(delay)
                    self._stats["retry_count"] += 1

            self._stats["total_requests"] += 1
            self._stats["failure_count"] += 1
            elapsed_ms = (time.time() - start_time) * 1000
            self._stats["total_time_ms"] += elapsed_ms
            logger.error(f"[{method}] {url} 最终失败: {last_error}")
            return HttpResponse(url=url, status_code=0, headers={}, body="", elapsed_ms=elapsed_ms, success=False, error=last_error)
        finally:
            await self._release_semaphore()

    async def get(self, url: str, config: Optional[RequestConfig] = None, **kwargs) -> HttpResponse:
        return await self.request("GET", url, config, **kwargs)

    async def post(self, url: str, config: Optional[RequestConfig] = None, **kwargs) -> HttpResponse:
        return await self.request("POST", url, config, **kwargs)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _extract_domain(self, url: str) -> str:
        try:
            import urllib.parse
            parsed = urllib.parse.urlparse(url)
            return parsed.netloc.lower().replace("www.", "", 1)
        except Exception:
            return url

    def get_stats(self) -> Dict[str, Any]:
        return self._stats.copy()

    def reset_stats(self) -> None:
        self._stats = {"total_requests": 0, "success_count": 0, "failure_count": 0, "retry_count": 0, "total_time_ms": 0.0}


def create_sync_client(timeout: float = 30.0, max_retries: int = 3, rate_limit_interval: float = 0.5) -> SyncRequestClient:
    return SyncRequestClient(default_timeout=timeout, max_retries=max_retries, rate_limiter=RateLimiter(default_interval=rate_limit_interval))


async def create_async_client(timeout: float = 30.0, max_retries: int = 3, rate_limit_interval: float = 0.5, concurrency: int = 5) -> AsyncRequestClient:
    client = AsyncRequestClient(default_timeout=timeout, max_retries=max_retries, rate_limiter=RateLimiter(default_interval=rate_limit_interval), concurrency=concurrency)
    await client._ensure_session()
    return client


__all__ = ["RequestConfig", "HttpResponse", "RateLimiter", "RateLimitState", "UaRotator", "SyncRequestClient", "AsyncRequestClient", "create_sync_client", "create_async_client"]
