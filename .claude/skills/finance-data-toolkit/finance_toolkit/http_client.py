# -*- coding: utf-8 -*-
"""
HTTP 客户端模块

提供异步 HTTP 请求功能：
- 基于 httpx 的异步 HTTP 客户端
- 请求头、超时、重试配置
- 响应解析（JSON、文本、二进制）
- 代理支持
- 请求/响应日志

使用示例：
    from finance_toolkit.http_client import HttpClient
    
    client = HttpClient(timeout=10.0, max_retries=3)
    response = await client.get('https://api.example.com/data')
    data = response.json()
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx

from .proxy_manager import ProxyManager, get_proxy_manager

logger = logging.getLogger(__name__)


class HttpResponse:
    """HTTP 响应封装"""
    
    def __init__(
        self,
        status_code: int,
        headers: Dict[str, str],
        content: bytes,
        url: str,
        elapsed: float,
        request_headers: Optional[Dict[str, str]] = None,
    ):
        self.status_code = status_code
        self.headers = headers
        self.content = content
        self.url = url
        self.elapsed = elapsed
        self.request_headers = request_headers or {}
    
    @property
    def text(self) -> str:
        """获取响应文本"""
        return self.content.decode('utf-8', errors='replace')
    
    @property
    def encoding(self) -> str:
        """获取响应编码"""
        content_type = self.headers.get('content-type', '')
        if 'charset=' in content_type:
            return content_type.split('charset=')[1].split(';')[0].strip()
        return 'utf-8'
    
    def json(self) -> Any:
        """解析 JSON 响应"""
        import json
        return json.loads(self.content)
    
    def raise_for_status(self) -> None:
        """状态码非 2xx 时抛出异常"""
        if 400 <= self.status_code < 600:
            raise HttpError(
                f"HTTP {self.status_code}: {self.text[:200]}",
                status_code=self.status_code,
                response=self,
            )
    
    def __repr__(self) -> str:
        return f"HttpResponse(status={self.status_code}, url={self.url}, elapsed={self.elapsed:.3f}s)"


class HttpError(Exception):
    """HTTP 请求异常"""
    
    def __init__(self, message: str, status_code: Optional[int] = None, response: Optional[HttpResponse] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class HttpClient:
    """
    异步 HTTP 客户端
    
    支持：
    - 请求超时配置
    - 自动重试
    - 代理支持
    - 请求/响应日志
    - 连接池管理
    """
    
    def __init__(
        self,
        timeout: float = 30.0,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        user_agent: str = "FinanceDataToolkit/1.0",
        proxy_manager: Optional[ProxyManager] = None,
        follow_redirects: bool = True,
        max_connections: int = 100,
        verify_ssl: bool = True,
    ):
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.user_agent = user_agent
        self.proxy_manager = proxy_manager or get_proxy_manager()
        self.follow_redirects = follow_redirects
        self.max_connections = max_connections
        self.verify_ssl = verify_ssl
        
        self._client: Optional[httpx.AsyncClient] = None
        self._stats = {
            'total_requests': 0,
            'successful_requests': 0,
            'failed_requests': 0,
            'retries': 0,
            'errors': {},
        }
    
    def _get_headers(self, custom_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """构建请求头"""
        headers = {
            'User-Agent': self.user_agent,
            'Accept': 'application/json, text/plain, */*',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
        }
        if custom_headers:
            headers.update(custom_headers)
        return headers
    
    def _get_proxy_url(self) -> Optional[str]:
        """从代理管理器获取代理 URL"""
        return self.proxy_manager.get_proxy()
    
    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建 HTTP 客户端"""
        if self._client is None or self._client.is_closed:
            proxies = None
            proxy_url = self._get_proxy_url()
            if proxy_url:
                proxies = {'http://': proxy_url, 'https://': proxy_url}
            
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=self.follow_redirects,
                limits=httpx.Limits(max_connections=self.max_connections),
                verify=self.verify_ssl,
                proxies=proxies,
            )
        return self._client
    
    async def request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
    ) -> HttpResponse:
        """
        发送 HTTP 请求
        
        Args:
            method: HTTP 方法 (GET, POST, etc.)
            url: 请求 URL
            headers: 自定义请求头
            params: URL 参数
            json_data: JSON 请求体
            data: 表单请求体
            timeout: 请求超时（秒）
            retries: 重试次数（默认使用 max_retries）
        
        Returns:
            HttpResponse 对象
        
        Raises:
            HttpError: 请求失败时抛出
        """
        effective_retries = retries if retries is not None else self.max_retries
        effective_timeout = timeout if timeout is not None else self.timeout
        
        last_exception = None
        
        for attempt in range(effective_retries + 1):
            self._stats['total_requests'] += 1
            start_time = time.time()
            
            try:
                client = await self._get_client()
                req_headers = self._get_headers(headers)
                
                kwargs = {
                    'headers': req_headers,
                    'params': params,
                }
                if json_data is not None:
                    kwargs['json'] = json_data
                if data is not None:
                    kwargs['data'] = data
                
                response = await client.request(method, url, **kwargs)
                
                elapsed = time.time() - start_time
                
                http_response = HttpResponse(
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    content=response.content,
                    url=url,
                    elapsed=elapsed,
                    request_headers=req_headers,
                )
                
                self._stats['successful_requests'] += 1
                logger.debug(f"{method} {url} -> {response.status_code} ({elapsed:.2f}s)")
                
                return http_response
                
            except httpx.TimeoutException as e:
                last_exception = e
                self._stats['errors']['timeout'] = self._stats.get('errors', {}).get('timeout', 0) + 1
                logger.warning(f"请求超时: {url} - {e}")
                
            except httpx.HTTPStatusError as e:
                last_exception = e
                self._stats['errors']['http_error'] = self._stats.get('errors', {}).get('http_error', 0) + 1
                logger.warning(f"HTTP 错误: {url} - {e.response.status_code}")
                
            except httpx.ConnectError as e:
                last_exception = e
                self._stats['errors']['connection_error'] = self._stats.get('errors', {}).get('connection_error', 0) + 1
                logger.warning(f"连接错误: {url} - {e}")
                
            except Exception as e:
                last_exception = e
                error_type = type(e).__name__
                self._stats['errors'][error_type] = self._stats.get('errors', {}).get(error_type, 0) + 1
                logger.error(f"请求异常: {url} - {type(e).__name__}: {str(e)[:100]}")
            
            # 重试逻辑
            if attempt < effective_retries:
                delay = self.retry_delay * (2 ** attempt)  # 指数退避
                logger.info(f"第 {attempt + 1} 次重试 {url}，等待 {delay:.1f}s")
                await asyncio.sleep(delay)
                self._stats['retries'] += 1
        
        self._stats['failed_requests'] += 1
        raise HttpError(
            f"请求失败: {url}，已重试 {effective_retries} 次",
            response=last_exception.response if hasattr(last_exception, 'response') else None,
        ) from last_exception
    
    async def get(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> HttpResponse:
        """GET 请求"""
        return await self.request('GET', url, headers=headers, params=params, timeout=timeout)
    
    async def post(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> HttpResponse:
        """POST 请求"""
        return await self.request(
            'POST', url,
            headers=headers,
            json_data=json_data,
            data=data,
            params=params,
            timeout=timeout,
        )
    
    async def close(self):
        """关闭 HTTP 客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
            logger.debug("HTTP 客户端已关闭")
    
    def get_stats(self) -> Dict[str, Any]:
        """获取请求统计"""
        return self._stats.copy()
    
    def reset_stats(self):
        """重置统计"""
        self._stats = {
            'total_requests': 0,
            'successful_requests': 0,
            'failed_requests': 0,
            'retries': 0,
            'errors': {},
        }
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


# ============== 便捷函数 ==============

_default_client: Optional[HttpClient] = None


def get_http_client() -> HttpClient:
    """
    获取全局 HTTP 客户端实例（单例模式）
    
    Returns:
        HttpClient 实例
    """
    global _default_client
    if _default_client is None:
        _default_client = HttpClient()
    return _default_client


def reset_http_client():
    """重置全局 HTTP 客户端（用于测试）"""
    global _default_client
    if _default_client:
        asyncio.run(_default_client.close())
    _default_client = None


if __name__ == '__main__':
    # 测试
    async def test_http_client():
        client = HttpClient(timeout=10.0, max_retries=1)
        
        try:
            # 测试 GET 请求
            response = await client.get('https://httpbin.org/get')
            print(f"状态码: {response.status_code}")
            print(f"响应时间: {response.elapsed:.3f}s")
            
            # 测试 JSON 解析
            data = response.json()
            print(f"JSON keys: {list(data.keys())}")
            
            # 测试统计
            print(f"统计: {client.get_stats()}")
        finally:
            await client.close()
    
    asyncio.run(test_http_client())
