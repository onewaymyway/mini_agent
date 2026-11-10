"""
mini_agent.web_auth — 通用 Streamlit 登录（看板与外部项目共用一套账户）。

    from mini_agent.web_auth import require_login
    require_login("应用标题")

账户管理：python -m mini_agent.web_auth --help
设计见 next_doc/shared_web_login_plan.md。
"""

from mini_agent.web_auth.gate import (  # noqa: F401
    AUTH_COOKIE_NAME,
    ENV_AUTH_DIR,
    accounts_empty,
    auth_paths,
    logout,
    migrate_legacy_kanban_users,
    render_login_gate,
    require_login,
    shared_auth_dir,
)

__all__ = [
    "AUTH_COOKIE_NAME", "ENV_AUTH_DIR", "accounts_empty", "auth_paths", "logout",
    "migrate_legacy_kanban_users", "render_login_gate", "require_login", "shared_auth_dir",
]
