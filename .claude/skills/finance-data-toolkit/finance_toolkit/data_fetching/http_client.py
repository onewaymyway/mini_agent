# -*- coding: utf-8 -*-
"""
HTTP 客户端连接池管理

提供统一的 HTTP 客户端管理，支持连接复用、超时配置、重试机制。

使用示例：
    from finance_toolkit.data_fetching.http_client import HTTPClientManager
    
    # 获取全局管理器
    manager = HTTPClientManager.get_instance()
    
    # 获取客户端
    client = manager.get_client(timeout=30)
    
    # 使用客户端
    async with client:
        response = await client.get('https://api.example.com/data')
"""

import logging
from typing import Optional, Dict, Any
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

# 尝试导入 httpx
try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False
    logger.warning("httpx 未安装，HTTP 客户端功能不可用。请运行：pip install httpx")


class HTTPClientManager:
    """
    HTTP 客户端连接池管理器
    
    管理异步 HTTP 客户端实例，支持连接复用和超时配置。
    使用单例模式确保全局只有一个管理器实例。
    """
    
    _instance: Optional['HTTPClientManager'] = None
    _clients: Dict[str, httpx.AsyncClient] = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        # 防止重复初始化
        if hasattr(self, '_initialized') and self._initialized:
            return
        self._initialized = True
        self._default_timeout = 30.0
        self._default_max_retries = 3
        self._default_retry_backoff = [1, 2, 5]
    
    @classmethod
    def get_instance(cls) -> 'HTTPClientManager':
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def get_client(
        self,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        retry_backoff: Optional[list] = None,
        trust_env: bool = True,
        headers: Optional[Dict[str, str]] = None
    ) -> httpx.AsyncClient:
        """
        获取 HTTP 客户端实例
        
        Args:
            timeout: 请求超时时间（秒）
            max_retries: 最大重试次数
            retry_backoff: 重试退避因子列表
            trust_env: 是否信任环境变量代理设置
            headers: 默认请求头
        
        Returns:
            httpx.AsyncClient 实例
        """
        if not HAS_HTTPX:
            raise ImportError("httpx 未安装")
        
        # 生成客户端 key
        key = f"{timeout}_{max_retries}_{retry_backoff}_{trust_env}"
        
        # 如果客户端已存在且配置相同，直接返回
        if key in self._clients:
            return self._clients[key]
        
        # 创建新客户端
        client_timeout = timeout or self._default_timeout
        client_headers = headers or {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json, text/plain, */*',
        }
        
        client = httpx.AsyncClient(
            timeout=client_timeout,
            trust_env=trust_env,
            headers=client_headers,
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
                keepalive_expiry=30.0,
            )
        )
        
        self._clients[key] = client
        logger.debug(f"创建新的 HTTP 客户端: {key}")
        
        return client
    
    async def close_all(self):
        """关闭所有客户端连接"""
        for key, client in self._clients.items():
            try:
                await client.aclose()
                logger.debug(f"关闭客户端: {key}")
            except Exception as e:
                logger.warning(f"关闭客户端失败 [{key}]: {e}")
        self._clients.clear()
    
    def close_sync(self):
        """同步关闭所有客户端连接（在事件循环外调用）"""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 如果事件循环正在运行，创建一个任务来关闭
                asyncio.create_task(self.close_all())
            else:
                # 如果事件循环未运行，直接运行
                loop.run_until_complete(self.close_all())
        except RuntimeError:
            # 没有事件循环，创建一个新的
            asyncio.run(self.close_all())
    
    @asynccontextmanager
    async def get_client_context(
        self,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        retry_backoff: Optional[list] = None,
        trust_env: bool = True,
        headers: Optional[Dict[str, str]] = None
    ):
        """
        获取 HTTP 客户端的上下文管理器
        
        使用示例：
            async with manager.get_client_context() as client:
                response = await client.get('https://api.example.com')
        """
        client = self.get_client(
            timeout=timeout,
            max_retries=max_retries,
            retry_backoff=retry_backoff,
            trust_env=trust_env,
            headers=headers
        )
        try:
            yield client
        finally:
            # 注意：这里不关闭客户端，因为它是共享的
            pass


class RetryableHTTPClient:
    """
    带重试功能的 HTTP 客户端
    
    封装 httpx.AsyncClient，提供自动重试和退避策略。
    """
    
    def __init__(
        self,
        manager: HTTPClientManager = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        retry_backoff: list = None,
        trust_env: bool = True,
        headers: Dict[str, str] = None
    ):
        self.manager = manager or HTTPClientManager.get_instance()
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff or [1, 2, 5]
        self.trust_env = trust_env
        self.headers = headers
        self._client: Optional[httpx.AsyncClient] = None
    
    @property
    def client(self) -> httpx.AsyncClient:
        """获取 HTTP 客户端实例"""
        if self._client is None:
            self._client = self.manager.get_client(
                timeout=self.timeout,
                trust_env=self.trust_env,
                headers=self.headers
            )
        return self._client
    
    async def get(
        self,
        url: str,
        params: Dict[str, Any] = None,
        headers: Dict[str, str] = None,
        **kwargs
    ) -> httpx.Response:
        """
        异步 GET 请求，带重试
        
        Args:
            url: 请求 URL
            params: 查询参数
            headers: 请求头
            **kwargs: 其他参数
        
        Returns:
            httpx.Response 对象
        
        Raises:
            httpx.HTTPStatusError: HTTP 错误
            httpx.TimeoutException: 请求超时
            Exception: 其他异常
        """
        last_error = None
        
        for attempt in range(self.max_retries):
            try:
                response = await self.client.get(url, params=params, headers=headers, **kwargs)
                response.raise_for_status()
                return response
            
            except httpx.TimeoutException as e:
                last_error = e
                logger.warning(f"请求超时 (尝试 {attempt + 1}/{self.max_retries}): {url}")
            
            except httpx.HTTPStatusError as e:
                last_error = e
                logger.warning(f"HTTP 错误 (尝试 {attempt + 1}/{self.max_retries}): {e}")
                
                # 对于 4xx 错误（除了 429），不重试
                if 400 <= e.response.status_code < 500 and e.response.status_code != 429:
                    raise
            
            except Exception as e:
                last_error = e
                logger.warning(f"请求失败 (尝试 {attempt + 1}/{self.max_retries}): {e}")
            
            if attempt < self.max_retries - 1:
                wait_time = self.retry_backoff[min(attempt, len(self.retry_backoff) - 1)]
                logger.debug(f"等待 {wait_time}s 后重试")
                await asyncio.sleep(wait_time)
        
        raise last_error
    
    async def post(
        self,
        url: str,
        data: Dict[str, Any] = None,
        json: Dict[str, Any] = None,
        headers: Dict[str, str] = None,
        **kwargs
    ) -> httpx.Response:
        """
        异步 POST 请求，带重试
        
        Args:
            url: 请求 URL
            data: 表单数据
            json: JSON 数据
            headers: 请求头
            **kwargs: 其他参数
        
        Returns:
            httpx.Response 对象
        """
        last_error = None
        
        for attempt in range(self.max_retries):
            try:
                response = await self.client.post(url, data=data, json=json, headers=headers, **kwargs)
                response.raise_for_status()
                return response
            
            except httpx.TimeoutException as e:
                last_error = e
                logger.warning(f"POST 请求超时 (尝试 {attempt + 1}/{self.max_retries}): {url}")
            
            except httpx.HTTPStatusError as e:
                last_error = e
                logger.warning(f"POST HTTP 错误 (尝试 {attempt + 1}/{self.max_retries}): {e}")
                
                # 对于 4xx 错误（除了 429），不重试
                if 400 <= e.response.status_code < 500 and e.response.status_code != 429:
                    raise
            
            except Exception as e:
                last_error = e
                logger.warning(f"POST 请求失败 (尝试 {attempt + 1}/{self.max_retries}): {e}")
            
            if attempt < self.max_retries - 1:
                wait_time = self.retry_backoff[min(attempt, len(self.retry_backoff) - 1)]
                await asyncio.sleep(wait_time)
        
        raise last_error
    
    async def close(self):
        """关闭客户端连接"""
        if self._client:
            await self._client.aclose()
            self._client = None
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, *args):
        await self.close()


# 全局管理器实例
_default_manager: Optional[HTTPClientManager] = None


def get_http_client_manager() -> HTTPClientManager:
    """获取全局 HTTP 客户端管理器"""
    global _default_manager
    if _default_manager is None:
        _default_manager = HTTPClientManager.get_instance()
    return _default_manager


def create_retryable_client(
    timeout: float = 30.0,
    max_retries: int = 3,
    retry_backoff: list = None,
    trust_env: bool = True,
    headers: Dict[str, str] = None
) -> RetryableHTTPClient:
    """
    创建带重试功能的 HTTP 客户端
    
    Args:
        timeout: 请求超时时间（秒）
        max_retries: 最大重试次数
        retry_backoff: 重试退避因子列表
        trust_env: 是否信任环境变量代理设置
        headers: 默认请求头
    
    Returns:
        RetryableHTTPClient 实例
    """
    return RetryableHTTPClient(
        manager=get_http_client_manager(),
        timeout=timeout,
        max_retries=max_retries,
        retry_backoff=retry_backoff,
        trust_env=trust_env,
        headers=headers
    )


# 便捷函数
async def async_get(
    url: str,
    params: Dict[str, Any] = None,
    headers: Dict[str, str] = None,
    timeout: float = 30.0,
    max_retries: int = 3,
    **kwargs
) -> httpx.Response:
    """
    异步 GET 请求便捷函数
    
    Args:
        url: 请求 URL
        params: 查询参数
        headers: 请求头
        timeout: 超时时间（秒）
        max_retries: 最大重试次数
        **kwargs: 其他参数
    
    Returns:
        httpx.Response 对象
    """
    client = create_retryable_client(timeout=timeout, max_retries=max_retries, headers=headers)
    return await client.get(url, params=params, **kwargs)


async def async_post(
    url: str,
    data: Dict[str, Any] = None,
    json: Dict[str, Any] = None,
    headers: Dict[str, str] = None,
    timeout: float = 30.0,
    max_retries: int = 3,
    **kwargs
) -> httpx.Response:
    """
    异步 POST 请求便捷函数
    
    Args:
        url: 请求 URL
        data: 表单数据
        json: JSON 数据
        headers: 请求头
        timeout: 超时时间（秒）
        max_retries: 最大重试次数
        **kwargs: 其他参数
    
    Returns:
        httpx.Response 对象
    """
    client = create_retryable_client(timeout=timeout, max_retries=max_retries, headers=headers)
    return await client.post(url, data=data, json=json, **kwargs)
