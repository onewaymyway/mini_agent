"""
看板的登录账户模块 —— 已迁移为通用模块 mini_agent.web_auth.accounts（所有 Streamlit 应用共用）。

本文件只做转发，保留原有导入路径（app.py / manage_users.py 仍然 `from auth import ...`）。
设计见 next_doc/shared_web_login_plan.md。
"""

from mini_agent.web_auth.accounts import *  # noqa: F401,F403
from mini_agent.web_auth.accounts import (  # noqa: F401  显式列出，便于静态检查
    LastAdminError,
    LoginAttemptTracker,
    SessionStore,
    TOKEN_TTL_SECONDS,
    UserStore,
    get_or_create_secret,
    make_token,
    verify_token,
)
