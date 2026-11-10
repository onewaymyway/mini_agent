"""
mini_agent.web_auth.gate — 通用 Streamlit 登录门禁（看板与外部项目共用）。

用法（外部项目通常不需要自己调用，由看板启动时的包装入口自动调用；
项目如果想自己控制登录，可以在页面顶部调用）：

    from mini_agent.web_auth import require_login
    require_login("我的应用")      # 未登录：渲染登录页并停止；已登录：返回用户名

账户目录默认 ~/.agent/web_auth/，可用环境变量 MINI_AGENT_WEB_AUTH_DIR 覆盖，
所有应用读写同一份账户与会话数据（见 next_doc/shared_web_login_plan.md）。

Cookie 免登录依赖 extra-streamlit-components；未安装时退化为仅会话登录，
仍然有效拦截，只是刷新页面需要重新输入密码。
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from mini_agent.web_auth.accounts import (
    LoginAttemptTracker,
    SessionStore,
    UserStore,
    get_or_create_secret,
    make_token,
    verify_token,
)

ENV_AUTH_DIR = "MINI_AGENT_WEB_AUTH_DIR"
AUTH_COOKIE_NAME = "mini_agent_web_auth"
_SESSION_MANAGER_KEY = "_cookie_manager"


@dataclass(frozen=True)
class AuthPaths:
    users: Path
    secret: Path
    attempts: Path
    sessions: Path


def shared_auth_dir() -> Path:
    """共享账户目录：环境变量优先，否则 ~/.agent/web_auth/。"""
    env = os.environ.get(ENV_AUTH_DIR, "").strip()
    if env:
        return Path(env).expanduser()
    return Path.home() / ".agent" / "web_auth"


def auth_paths(auth_dir: Optional[Path] = None) -> AuthPaths:
    base = Path(auth_dir).expanduser() if auth_dir else shared_auth_dir()
    return AuthPaths(
        users=base / "users.json",
        secret=base / "web_auth_secret",
        attempts=base / "login_attempts.json",
        sessions=base / "sessions.json",
    )


def migrate_legacy_kanban_users(project_root: Path, auth_dir: Optional[Path] = None) -> bool:
    """看板升级：把旧的 <项目>/.agent/kanban_users.json 一次性复制到共享目录。

    只在共享账户文件不存在时复制，旧文件保留不删。返回是否发生了复制。
    """
    target = auth_paths(auth_dir).users
    legacy = Path(project_root) / ".agent" / "kanban_users.json"
    if target.exists() or not legacy.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(legacy, target)
    return True


def accounts_empty(auth_dir: Optional[Path] = None) -> bool:
    return UserStore(auth_paths(auth_dir).users).is_empty()


def _st():
    import streamlit as st
    return st


def _client_id() -> str:
    """登录限流分桶用的客户端标识（取不到则退化为只按用户名限流）。"""
    try:
        headers = _st().context.headers
        fwd = headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip()
        return headers.get("Host", "") or ""
    except Exception:
        return ""


# ── Cookie（可选） ───────────────────────────────────────────────────────

def _cookie_manager():
    """本次 rerun 的 CookieManager（每次 rerun 只建一个；依赖缺失返回 None）。

    看板的 main() 会预先把自己的 CookieManager 存进 session_state["_cookie_manager"]，
    这里优先复用它，避免同一次 rerun 里出现两个各自持有快照的实例。
    """
    st = _st()
    mgr = st.session_state.get(_SESSION_MANAGER_KEY)
    if mgr is not None:
        return mgr
    try:
        import extra_streamlit_components as stx
    except ImportError:
        return None
    mgr = stx.CookieManager(key="mini_agent_web_auth_cookie_manager")
    st.session_state[_SESSION_MANAGER_KEY] = mgr
    return mgr


def _cookie_get() -> str:
    mgr = _cookie_manager()
    if mgr is None:
        return ""
    try:
        return mgr.get(AUTH_COOKIE_NAME) or ""
    except Exception:
        return ""


def _cookie_set(token: str, exp_ts: float) -> None:
    mgr = _cookie_manager()
    if mgr is None:
        return
    mgr.set(
        AUTH_COOKIE_NAME, token,
        key="mini_agent_web_auth_cookie_set",
        expires_at=datetime.fromtimestamp(exp_ts, tz=timezone.utc),
        same_site="lax",
    )


def _cookie_clear() -> None:
    mgr = _cookie_manager()
    if mgr is None:
        return
    try:
        if mgr.get(AUTH_COOKIE_NAME):
            mgr.delete(AUTH_COOKIE_NAME, key="mini_agent_web_auth_cookie_delete")
    except KeyError:
        pass


# ── 门禁 ────────────────────────────────────────────────────────────────

def _fail_count(tracker: LoginAttemptTracker, username: str, client_id: str) -> int:
    entry = tracker._load().get(tracker._key(username, client_id))  # noqa: SLF001
    return entry.get("count", 0) if entry else 0


def render_login_gate(app_title: str = "mini-agent",
                      auth_dir: Optional[Path] = None,
                      *, paths: Optional[AuthPaths] = None) -> Tuple[bool, str]:
    """返回 (是否已通过验证, 用户名)。未通过时已经画好登录页，调用方应停止渲染。

    paths 可整体指定（看板的 --users-file 等场景）；否则用 auth_dir 或共享目录。
    """
    st = _st()
    paths = paths or auth_paths(auth_dir)
    store = UserStore(paths.users)
    tracker = LoginAttemptTracker(paths.attempts)
    session_store = SessionStore(paths.sessions)

    if st.session_state.get("authenticated"):
        sid = st.session_state.get("session_id", "")
        username = st.session_state.get("username", "")
        if session_store.is_valid(sid, username):
            session_store.touch(sid)
            return True, username
        st.session_state.authenticated = False
        st.session_state.pop("username", None)
        st.session_state.pop("session_id", None)
        _cookie_clear()
        st.warning("⚠️ 当前会话已失效（可能已被撤销或过期），请重新登录。")

    secret = get_or_create_secret(paths.secret)

    cookie_token = _cookie_get()
    if cookie_token:
        result = verify_token(cookie_token, secret)
        if result:
            username, sid = result
            if session_store.is_valid(sid, username):
                session_store.touch(sid)
                st.session_state.authenticated = True
                st.session_state.username = username
                st.session_state.session_id = sid
                return True, username
        _cookie_clear()

    # 整个登录页放进占位容器：登录成功的那一轮要把它清空，否则表单会残留在项目内容上方
    page = st.empty()
    with page.container():
        st.markdown(f"## 🔐 {app_title} 登录")

        if store.is_empty():
            st.warning(
                "尚未创建任何账户，无法登录。请先在服务器上执行：\n\n"
                f"```\npython -m mini_agent.web_auth add <用户名> --admin\n```\n\n"
                f"账户目录：`{paths.users.parent}`（所有应用共用）"
            )
            return False, ""

        with st.form("mini_agent_web_auth_login_form"):
            username = st.text_input("用户名")
            password = st.text_input("密码", type="password")
            submitted = st.form_submit_button("登录", use_container_width=True)

        if submitted:
            client_id = _client_id()
            remaining = tracker.seconds_until_unlocked(username, client_id)
            if remaining > 0:
                mins = int(remaining // 60) + 1
                st.error(f"🔒 该账户登录失败次数过多，已被临时锁定，请约 {mins} 分钟后再试。")
            elif store.verify(username, password):
                tracker.record_success(username, client_id)
                sid, exp = session_store.create(username, client_id)
                st.session_state.authenticated = True
                st.session_state.username = username
                st.session_state.session_id = sid
                _cookie_set(make_token(username, sid, exp, secret), exp)
                page.empty()  # 登录成功：清掉登录页，本轮直接放行
                return True, username
            else:
                tracker.record_failure(username, client_id)
                left = max(tracker.max_attempts - _fail_count(tracker, username, client_id), 0)
                st.error(f"用户名或密码错误。（连续失败达到上限会临时锁定该账户，剩余尝试次数：{left}）")

    return False, ""


def logout(auth_dir: Optional[Path] = None) -> None:
    """退出登录：撤销当前会话、清 Cookie、清本地登录态。"""
    st = _st()
    sid = st.session_state.get("session_id", "")
    if sid:
        SessionStore(auth_paths(auth_dir).sessions).revoke(sid)
    _cookie_clear()
    for key in ("authenticated", "username", "session_id"):
        st.session_state.pop(key, None)


def require_login(app_title: str = "mini-agent", *, auth_dir: Optional[Path] = None) -> str:
    """页面入口门禁：未登录则渲染登录页并 st.stop()（之后的代码不会执行）；已登录返回用户名。

    注意：如果需要调用 st.set_page_config，必须在本函数之前调用（Streamlit 要求它是第一个命令）。
    看板启动的包装入口已经自行设置页面配置，外部项目无需关心。
    """
    st = _st()
    ok, username = render_login_gate(app_title, auth_dir)
    if not ok:
        st.stop()
    st.sidebar.caption(f"👤 {username}")
    if st.sidebar.button("退出登录", key="mini_agent_web_auth_logout"):
        logout(auth_dir)
        st.rerun()
    return username


__all__ = [
    "AUTH_COOKIE_NAME", "ENV_AUTH_DIR", "AuthPaths",
    "accounts_empty", "auth_paths", "logout", "migrate_legacy_kanban_users",
    "render_login_gate", "require_login", "shared_auth_dir",
]
