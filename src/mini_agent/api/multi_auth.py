"""
api/multi_auth.py — daemon 多用户架构 Phase 1：多用户认证中间件

与 api/auth.py::AuthMiddleware（单 token）的关系：
  - 两者互斥，由 create_app() 按 cfg.http_multi_user_enabled 二选一挂载，
    默认仍是 AuthMiddleware（单 token），完全不影响现有部署。
  - MultiUserAuthMiddleware 不重新实现 IP 白名单/token 提取逻辑，
    直接复用 auth.py 里已经写好且经过验证的 _client_ip/_ip_allowed/
    AuthMiddleware._extract_token，避免两份相似但不同步的实现。

认证成功后，会在 request.state 上注入：
  request.state.user_ctx  — UserContext 实例（见 user_store.py）
供下游路由（routes.py）和 SessionAgentPool（Phase 3）使用。

owner 判定：
  - token 对应的 UserRecord.role == "owner"，与"是否本机连接"无关——
    owner 是身份概念（谁持有 owner token），不是网络位置概念。
    is_loopback 字段单独记录"是否从 127.0.0.1/::1 连接"，仅供需要额外
    本机特权判断的场景使用（目前没有端点用到，留作扩展点）。
"""

from __future__ import annotations

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .auth import _client_ip, _ip_allowed, AuthMiddleware, _EXEMPT_PATHS
from .user_store import UserStore, UserContext


_LOOPBACK_IPS = {"127.0.0.1", "::1", "localhost"}


class MultiUserAuthMiddleware(BaseHTTPMiddleware):
    """
    为所有 /v1/* 端点执行：
      1. IP 白名单检查（逻辑与 AuthMiddleware 完全一致，直接复用）
      2. token → UserStore.authenticate() → UserRecord
      3. 注入 request.state.user_ctx，供路由层取用

    与单 token 模式的行为差异仅在"认证"这一步：
    找不到该 token 对应的用户 → 401（消息文案和单 token 模式保持一致的风格，
    避免给攻击者提供"到底是 token 错还是多用户没配"这类额外信息）。
    """

    def __init__(self, app, role_store: UserStore, allowed_ips: list[str]) -> None:
        super().__init__(app)
        self._role_store  = role_store
        self._allowed_ips  = allowed_ips  # 空 = 不限制 IP

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # 豁免路径（与 AuthMiddleware 共用同一份列表，保持两种模式行为一致）
        if path in _EXEMPT_PATHS:
            return await call_next(request)

        # IP 检查（逻辑复用 auth.py，不重新实现）
        ip = _client_ip(request)
        if self._allowed_ips and not _ip_allowed(ip, self._allowed_ips):
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": f"IP {ip!r} not in allowed list"},
            )

        # Token → 用户身份
        token = AuthMiddleware._extract_token(request)
        record = self._role_store.authenticate(token) if token else None
        if record is None:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Invalid or missing token"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        request.state.user_ctx = UserContext(
            user_id=record.user_id,
            name=record.name,
            role=record.role,
            trust_level=record.trust_level,
            is_loopback=(ip in _LOOPBACK_IPS),
        )

        return await call_next(request)


def require_owner(request: Request) -> bool:
    """
    供 owner-only 端点调用的权限检查。
    单用户模式（没有 user_ctx）下默认放行——和现状行为一致：
    单 token 模式下，能通过 AuthMiddleware 认证的就是唯一的那个使用者，
    本来就等同于 owner，不应该因为新增了这个检查就把现有单用户部署挡在外面。
    """
    user_ctx = getattr(request.state, "user_ctx", None)
    if user_ctx is None:
        return True
    return user_ctx.is_owner
