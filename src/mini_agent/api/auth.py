"""
api/auth.py — 鉴权中间件

支持：
  1. Bearer token  —— Authorization: Bearer <token>
  2. Query param   —— ?token=<token>  （便于浏览器直接访问 SSE / 下载文件）
  3. IP 白名单     —— 默认只允许 127.0.0.1 / ::1
  4. 健康检查豁免  —— GET /v1/health 不需要 token

Token 生命周期：
  - 若配置文件中指定了 api_token，直接使用
  - 否则启动时随机生成 32 字节 hex token，打印到终端并写入 <project_root>/agent_api.key
  - agent_api.key 权限设为 0600，避免其他用户读取
"""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path
from typing import Optional

from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

_TOKEN_FILE = ".agent/agent_api.key"  # <project_root>/.agent/agent_api.key

# ── Token 管理 ────────────────────────────────────────────────────────────────

def load_or_generate_token(project_root: Path, configured_token: str = "") -> str:
    """
    返回最终使用的 API token：
      1. 配置文件指定了 → 直接用
      2. project_root/agent_api.key 存在 → 读取复用（避免重启后 token 变化）
      3. 都没有 → 随机生成并写入文件
    """
    if configured_token:
        return configured_token

    key_path = project_root / _TOKEN_FILE
    if key_path.exists():
        token = key_path.read_text(encoding="utf-8").strip()
        if len(token) >= 32:
            return token

    token = secrets.token_hex(32)
    key_path.write_text(token + "\n", encoding="utf-8")
    try:
        key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)   # 0600
    except Exception:
        pass
    return token


def print_token_banner(token: str, host: str, port: int) -> None:
    """启动时把 token 和访问地址打印到终端。"""
    print("\n" + "═" * 60)
    print("  🌐  HTTP API server started")
    print(f"  URL  : http://{host}:{port}/v1")
    print(f"  Token: {token}")
    print(f"  Key file: {_TOKEN_FILE}  (in project root)")
    print("  Add header:  Authorization: Bearer <token>")
    print("═" * 60 + "\n", flush=True)


# ── IP 白名单工具 ─────────────────────────────────────────────────────────────

def _client_ip(request: Request) -> str:
    """从 X-Forwarded-For 或直接连接中取客户端 IP。"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return ""


def _ip_allowed(ip: str, allowed: list[str]) -> bool:
    """简单前缀/精确匹配白名单（支持 CIDR 前缀，如 '192.168.'）。"""
    if not allowed:          # 空白名单 = 允许所有
        return True
    for rule in allowed:
        if ip == rule or ip.startswith(rule):
            return True
    return False


# ── 中间件 ────────────────────────────────────────────────────────────────────

# 不需要 token 的路径（精确匹配）
_EXEMPT_PATHS = {"/v1/health", "/"}

class AuthMiddleware(BaseHTTPMiddleware):
    """
    为所有 /v1/* 端点执行：
      1. IP 白名单检查
      2. Bearer token 验证
    """

    def __init__(self, app, token: str, allowed_ips: list[str]) -> None:
        super().__init__(app)
        self._token       = token
        self._allowed_ips = allowed_ips   # 空 = 不限制 IP

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # 豁免路径
        if path in _EXEMPT_PATHS:
            return await call_next(request)

        # IP 检查
        if self._allowed_ips:
            ip = _client_ip(request)
            if not _ip_allowed(ip, self._allowed_ips):
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": f"IP {ip!r} not in allowed list"},
                )

        # Token 检查：优先 header，其次 query param
        token = self._extract_token(request)
        if not token or not secrets.compare_digest(token, self._token):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Invalid or missing token"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        return await call_next(request)

    @staticmethod
    def _extract_token(request: Request) -> Optional[str]:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        # query param fallback（SSE / 文件下载等场景）
        return request.query_params.get("token")
