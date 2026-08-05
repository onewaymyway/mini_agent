"""
oauth_handler.py - OAuth 2.0 登录处理模块

支持 GitHub、Google 等主流 OAuth 提供商
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


@dataclass
class OAuthConfig:
    """OAuth 提供商配置"""
    client_id: str
    scope: str
    auth_url: str
    token_url: str
    callback_url: str
    userinfo_url: Optional[str] = None


@dataclass
class OAuthResult:
    """OAuth 登录结果"""
    success: bool
    provider: str
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    token_type: Optional[str] = None
    expires_in: Optional[int] = None
    userinfo: Optional[Dict[str, Any]] = None
    message: str = ""
    
    def __str__(self):
        status = "成功" if self.success else "失败"
        return f"[{status}] {self.provider}, token={'存在' if self.access_token else '无'}"


class OAuthHandler:
    """
    OAuth 2.0 登录处理器
    
    支持多种 OAuth 提供商的授权流程
    """
    
    # 预配置的提供商
    PROVIDERS: Dict[str, OAuthConfig] = {
        "github": OAuthConfig(
            client_id=os.getenv("GITHUB_CLIENT_ID", ""),
            scope="read:user repo",
            auth_url="https://github.com/login/oauth/authorize",
            token_url="https://github.com/login/oauth/access_token",
            callback_url="http://localhost:8080/callback",
            userinfo_url="https://api.github.com/user",
        ),
        "google": OAuthConfig(
            client_id=os.getenv("GOOGLE_CLIENT_ID", ""),
            scope="openid email profile",
            auth_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            callback_url="http://localhost:8080/callback",
            userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
        ),
        "twitter": OAuthConfig(
            client_id=os.getenv("TWITTER_CLIENT_ID", ""),
            scope="tweet.read users.read",
            auth_url="https://twitter.com/i/oauth2/authorize",
            token_url="https://api.twitter.com/2/oauth2/token",
            callback_url="http://localhost:8080/callback",
        ),
    }
    
    def __init__(self, session, provider: str = "github", callback_port: int = 8080):
        """
        Args:
            session: CDP session 对象
            provider: OAuth 提供商名称
            callback_port: 回调服务器端口
        """
        self.session = session
        self.provider = provider
        self.callback_port = callback_port
        self._config = self.PROVIDERS.get(provider)
        self._state = None
        self._auth_code = None
        self._callback_server = None
    
    async def authorize(self, timeout: int = 300) -> OAuthResult:
        """
        启动 OAuth 授权流程
        
        Args:
            timeout: 超时时间（秒）
        
        Returns:
            OAuthResult: 授权结果
        """
        if not self._config:
            return OAuthResult(
                success=False,
                provider=self.provider,
                message=f"不支持的提供商: {self.provider}"
            )
        
        # 1. 生成 state
        self._state = self._generate_state()
        
        # 2. 构建授权 URL
        auth_url = self._build_auth_url()
        logger.info(f"打开授权页面: {auth_url}")
        
        # 3. 打开授权页面
        await self.session.goto(auth_url)
        
        # 4. 启动回调服务器
        self._callback_server = await self._start_callback_server()
        
        # 5. 等待回调
        try:
            await asyncio.wait_for(
                self._wait_for_callback(),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            return OAuthResult(
                success=False,
                provider=self.provider,
                message="授权超时"
            )
        
        # 6. 用 code 换取 token
        token_result = await self._exchange_token()
        
        # 7. 获取用户信息
        if token_result.success and self._config.userinfo_url:
            userinfo = await self._get_userinfo(token_result.access_token)
            return OAuthResult(
                success=True,
                provider=self.provider,
                access_token=token_result.access_token,
                refresh_token=token_result.refresh_token,
                token_type=token_result.token_type,
                expires_in=token_result.expires_in,
                userinfo=userinfo,
                message="授权成功"
            )
        
        return token_result
    
    def _build_auth_url(self) -> str:
        """构建授权 URL"""
        params = {
            "client_id": self._config.client_id,
            "redirect_uri": self._config.callback_url,
            "scope": self._config.scope,
            "response_type": "code",
            "state": self._state,
        }
        return f"{self._config.auth_url}?{urllib.parse.urlencode(params)}"
    
    def _generate_state(self) -> str:
        """生成随机 state"""
        import secrets
        return secrets.token_urlsafe(32)
    
    async def _start_callback_server(self):
        """启动回调服务器"""
        import http.server
        import socketserver
        
        class CallbackHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                # 解析回调参数
                parsed = urllib.parse.urlparse(self.path)
                params = urllib.parse.parse_qs(parsed.query)
                
                if "code" in params and "state" in params:
                    code = params["code"][0]
                    state = params["state"][0]
                    
                    if state == self.server.handler._state:
                        self.server.handler._auth_code = code
                        self.send_response(200)
                        self.send_header("Content-type", "text/html")
                        self.end_headers()
                        self.wfile.write("<html><body><h1>授权成功！请关闭此页面</h1></body></html>".encode())
                    else:
                        self.send_response(400)
                        self.end_headers()
                        self.wfile.write("State 不匹配".encode())
                else:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write("缺少 code 或 state".encode())
            
            def log_message(self, format, *args):
                pass  # 抑制日志
        
        handler = CallbackHandler
        handler.server = self
        handler._state = self._state
        handler._auth_code = None
        
        server = socketserver.TCPServer(("", self.callback_port), handler)
        server.handler = handler
        
        import threading
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()
        
        logger.info(f"回调服务器已启动，端口: {self.callback_port}")
        return server
    
    async def _wait_for_callback(self):
        """等待回调"""
        while True:
            if self._callback_server.handler._auth_code:
                self._auth_code = self._callback_server.handler._auth_code
                self._callback_server.shutdown()
                return
            await asyncio.sleep(0.5)
    
    async def _exchange_token(self) -> OAuthResult:
        """用 code 换取 access_token"""
        try:
            data = urllib.parse.urlencode({
                "grant_type": "authorization_code",
                "code": self._auth_code,
                "redirect_uri": self._config.callback_url,
                "client_id": self._config.client_id,
                "client_secret": os.getenv(f"{self.provider.upper()}_CLIENT_SECRET", ""),
            }).encode()
            
            req = urllib.request.Request(
                self._config.token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST"
            )
            
            with urllib.request.urlopen(req, timeout=30) as response:
                body = response.read().decode()
                params = urllib.parse.parse_qs(body)
                
                return OAuthResult(
                    success=True,
                    provider=self.provider,
                    access_token=params.get("access_token", [None])[0],
                    refresh_token=params.get("refresh_token", [None])[0],
                    token_type=params.get("token_type", ["Bearer"])[0],
                    expires_in=int(params.get("expires_in", [0])[0]) if params.get("expires_in") else None,
                    message="Token 获取成功"
                )
        except Exception as e:
            logger.error(f"Token 交换失败: {e}")
            return OAuthResult(
                success=False,
                provider=self.provider,
                message=f"Token 交换失败: {str(e)}"
            )
    
    async def _get_userinfo(self, access_token: str) -> Optional[Dict[str, Any]]:
        """获取用户信息"""
        try:
            req = urllib.request.Request(
                self._config.userinfo_url,
                headers={"Authorization": f"Bearer {access_token}"}
            )
            
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.loads(response.read().decode())
        except Exception as e:
            logger.error(f"获取用户信息失败: {e}")
            return None
    
    async def refresh_token(self, refresh_token: str) -> OAuthResult:
        """
        刷新 access_token
        
        Args:
            refresh_token: 刷新令牌
        
        Returns:
            OAuthResult: 刷新结果
        """
        try:
            data = urllib.parse.urlencode({
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self._config.client_id,
                "client_secret": os.getenv(f"{self.provider.upper()}_CLIENT_SECRET", ""),
            }).encode()
            
            req = urllib.request.Request(
                self._config.token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST"
            )
            
            with urllib.request.urlopen(req, timeout=30) as response:
                body = response.read().decode()
                params = urllib.parse.parse_qs(body)
                
                return OAuthResult(
                    success=True,
                    provider=self.provider,
                    access_token=params.get("access_token", [None])[0],
                    refresh_token=params.get("refresh_token", [None])[0],
                    token_type=params.get("token_type", ["Bearer"])[0],
                    expires_in=int(params.get("expires_in", [0])[0]) if params.get("expires_in") else None,
                    message="Token 刷新成功"
                )
        except Exception as e:
            logger.error(f"Token 刷新失败: {e}")
            return OAuthResult(
                success=False,
                provider=self.provider,
                message=f"Token 刷新失败: {str(e)}"
            )


# 便捷函数
async def oauth_login(session, provider: str = "github", timeout: int = 300) -> OAuthResult:
    """
    OAuth 登录便捷函数
    
    Args:
        session: CDP session 对象
        provider: OAuth 提供商
        timeout: 超时时间
    
    Returns:
        OAuthResult: 登录结果
    """
    handler = OAuthHandler(session, provider)
    return await handler.authorize(timeout)
