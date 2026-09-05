"""
auth.py —— 看板登录鉴权。

这是 Streamlit 看板 UI 自身的登录门禁，和 mini-agent HTTP API 的
Bearer Token 鉴权（/v1/chat 等接口用的那个 token）是两回事，互不替代：
    - API token  → 看板用它去调 daemon 的 HTTP 接口（谁能操作 Agent）
    - 看板账户   → 谁能打开这个 Streamlit 页面（本文件负责）

不依赖数据库或第三方服务：账户信息是一个本地 JSON 文件（明文用户名 +
salt + PBKDF2 密码哈希，不存明文密码）；登录态用一个签名 token 持久化
到浏览器 Cookie 里，避免用户刷新页面就被踢出登录。

[kanban_auth_cookie_migration_plan.md] token 早期版本是存在 URL
query param（`?auth=...`）里的，会随浏览器历史记录、反向代理/服务器
访问日志、Referer 头、用户手动复制分享链接等途径泄露，这些泄露途径都
是"URL 本身会被记录/转发"这个载体决定的，跟签名算法是否安全无关。
现在改成存 Cookie（见 app.py::`_cookie_get_auth`/`_cookie_set_auth`/
`_cookie_clear_auth`），本文件里 `make_token`/`verify_token` 签名和
校验的逻辑完全不受影响——变的只是"签好的字符串放哪儿"，不是"怎么签"。
注意这不是 HttpOnly Cookie，页面自身的 JS 依然能读到它，不能防 XSS
窃取；能防的是"token 出现在 URL 里"这一类泄露面。

[kanban_session_management_plan.md] token 本身的签名一旦签发，在过期
之前签名算法自己没法"撤销"——如果这个 token 意外泄露（比如浏览器被
共享、设备丢失），撤销手段本来只有"轮换 `kanban_session_secret` 签名
密钥"这种会连累所有人重新登录的核选项。
现在加了 `SessionStore`：每次登录都会在这张登记表里留一条记录（会话
id、所属用户、签发/过期/最近活跃时间、客户端标识），`verify_token`
校验签名通过之后还要再查一下这条记录是否还在表里，不在就当作已撤销、
强制重新登录——这样"退出登录"、"退出所有其他会话"（自助）、"踢掉某个
会话"（管理员）都能只影响目标会话，不用动签名密钥。

账户管理走 manage_users.py 命令行脚本，不在 Streamlit UI 里做"注册"
功能——看板本身没有用户自助注册的需求，账户应该由管理员在服务器上创建。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Optional

_PBKDF2_ITER = 200_000
TOKEN_TTL_SECONDS = 12 * 3600  # 免登录 token 有效期：12 小时

# ── 登录失败限流 ──────────────────────────────────────────────────────────

DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_WINDOW_SECONDS = 15 * 60     # 15 分钟内累计失败次数
DEFAULT_LOCKOUT_SECONDS = 15 * 60    # 锁定 15 分钟


class LoginAttemptTracker:
    """
    简单的登录失败限流器：按"用户名 + 客户端标识（通常是 IP）"为 key，
    记到本地 JSON 文件里。同一个 key 在 window_seconds 内失败满
    max_attempts 次就锁定 lockout_seconds，期间直接拒绝、不再校验密码
    （防止暴力破解，也避免密码校验本身被当成放大攻击的手段）。

    只按用户名限流会被"永远失败同一个真实账户"绕过节流窗口重置逻辑吗？
    不会——每次失败都会检查是否超过 window，超过就重新计数，所以效果
    等价于滑动窗口下的固定次数限制，足够挡住脚本化的暴力枚举，但不是
    企业级防护。真正面向公网建议再加一层 nginx/fail2ban 按 IP 限流，
    这里只能拿到 Streamlit 转发的 X-Forwarded-For（不一定可靠，取决于
    你是否在反向代理后面、代理有没有正确设置这个头）。
    """

    def __init__(
        self,
        path: Path,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
        lockout_seconds: int = DEFAULT_LOCKOUT_SECONDS,
    ):
        self.path = Path(path)
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
        try:
            os.chmod(self.path, 0o600)
        except Exception:
            pass

    @staticmethod
    def _key(username: str, client_id: str = "") -> str:
        return f"{(username or '').strip().lower()}|{client_id or ''}"

    def seconds_until_unlocked(self, username: str, client_id: str = "") -> float:
        """返回还需要等待多少秒才能再次尝试；0 表示现在就可以尝试。"""
        data = self._load()
        entry = data.get(self._key(username, client_id))
        if not entry:
            return 0.0
        locked_until = entry.get("locked_until", 0)
        remaining = locked_until - time.time()
        return remaining if remaining > 0 else 0.0

    def record_failure(self, username: str, client_id: str = "") -> None:
        data = self._load()
        key = self._key(username, client_id)
        now = time.time()
        entry = data.get(key) or {"count": 0, "first_ts": now, "locked_until": 0}
        # 超出统计窗口就当作一次新的计数周期，避免"很久以前的一次失败"
        # 一直悬在那里影响判断。
        if now - entry.get("first_ts", now) > self.window_seconds:
            entry = {"count": 0, "first_ts": now, "locked_until": 0}
        entry["count"] += 1
        if entry["count"] >= self.max_attempts:
            entry["locked_until"] = now + self.lockout_seconds
        data[key] = entry
        self._save(data)

    def record_success(self, username: str, client_id: str = "") -> None:
        """登录成功后清掉这个 key 的失败记录，不让下次无辜用户/正常操作
        因为历史失败次数被误伤。"""
        data = self._load()
        key = self._key(username, client_id)
        if key in data:
            del data[key]
            self._save(data)


def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITER).hex()


class LastAdminError(Exception):
    """尝试把最后一个管理员降级 / 删除时抛出——不允许操作把所有人锁在
    账户管理门外（这之后就没有任何人能再把权限加回来，只能回到服务器
    敲 manage_users.py，违背了这整个功能"不用登服务器"的初衷）。"""


class UserStore:
    """管理看板账户。文件内容形如：
        {"alice": {"salt": "<hex>", "hash": "<hex>",
                    "is_admin": true, "created_at": 1234567890.0}, ...}

    `is_admin` / `created_at` 是后加的字段——旧文件里没有这两个字段的
    账户，读取时分别按 `False` / `None` 处理（`dict.get` 默认值），不需要
    额外的文件迁移脚本，旧账户下次被 `add_user`/`set_admin` 写一次之后
    才会补上这两个字段。
    """

    def __init__(self, users_file: Path):
        self.path = Path(users_file)

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
        try:
            os.chmod(self.path, 0o600)
        except Exception:
            pass

    def is_empty(self) -> bool:
        return len(self._load()) == 0

    def add_user(self, username: str, password: str, is_admin: bool = False) -> None:
        """新增账户，或者对已有账户做"重置密码"（同一个 username 再调一次
        就是 upsert 语义，历史上一直如此，这次没有改变）。

        `is_admin` 默认 `False`，不传时和改动前的行为完全一致——调用方
        `manage_users.py add`（不带 `--admin`）、以及"改自己密码"路径
        （显式传入当前 `is_admin` 值）都依赖这个默认值/显式传参组合来
        保证不会误把已有账户的管理员身份重置掉。
        """
        data = self._load()
        salt = os.urandom(16)
        existing = data.get(username) or {}
        entry = {
            "salt": salt.hex(),
            "hash": _hash_password(password, salt),
            "is_admin": bool(is_admin),
            "created_at": existing.get("created_at") or time.time(),
        }
        data[username] = entry
        self._save(data)

    def remove_user(self, username: str) -> bool:
        """删除账户；如果目标是"最后一个管理员"则拒绝并抛出
        `LastAdminError`，不做任何修改（和 `set_admin` 共享同一条保护
        逻辑，命令行 `remove` 子命令和页面"删除账户"按钮都会走到这里，
        不用在两处各自判断一遍）。"""
        data = self._load()
        if username not in data:
            return False
        if data[username].get("is_admin") and self._admin_count(data) <= 1:
            raise LastAdminError(f"{username!r} 是最后一个管理员，不能删除")
        del data[username]
        self._save(data)
        return True

    def list_users(self) -> list:
        return sorted(self._load().keys())

    def verify(self, username: str, password: str) -> bool:
        entry = self._load().get(username)
        if not entry:
            return False
        try:
            salt = bytes.fromhex(entry["salt"])
        except Exception:
            return False
        actual = _hash_password(password, salt)
        return hmac.compare_digest(entry.get("hash", ""), actual)

    # ── 管理员身份 ────────────────────────────────────────────────────

    def is_admin(self, username: str) -> bool:
        entry = self._load().get(username)
        return bool(entry and entry.get("is_admin", False))

    @staticmethod
    def _admin_count(data: dict) -> int:
        return sum(1 for entry in data.values() if entry.get("is_admin"))

    def admin_count(self) -> int:
        return self._admin_count(self._load())

    def set_admin(self, username: str, is_admin: bool) -> bool:
        """设置/取消管理员身份。目标账户不存在返回 `False`。

        "最后一个管理员不能被降级"的保护：只在 `is_admin=False`（降级）
        且目标账户当前确实是管理员、且降级后会变成 0 个管理员时生效
        （即 `_admin_count(data) <= 1`）——`admin_count() == 0` 的兜底期
        不受影响，因为压根没有"当前是管理员"这个前提，走不到这条分支；
        升级成管理员（`is_admin=True`）永远允许，不需要这层保护。
        """
        data = self._load()
        entry = data.get(username)
        if not entry:
            return False
        if not is_admin and entry.get("is_admin") and self._admin_count(data) <= 1:
            raise LastAdminError(f"{username!r} 是最后一个管理员，不能取消管理员身份")
        entry["is_admin"] = bool(is_admin)
        self._save(data)
        return True

    def list_users_detailed(self) -> list:
        """返回 `[{"username":, "is_admin":, "created_at":}, ...]`，按用户名
        排序，供账户管理 tab 的表格用。`created_at` 是旧账户可能没有的
        字段，缺失时给 `None`（UI 侧显示"未知"）。`list_users()` 原方法
        保留不动，`manage_users.py list` 继续用它。"""
        data = self._load()
        return [
            {
                "username": username,
                "is_admin": bool(entry.get("is_admin", False)),
                "created_at": entry.get("created_at"),
            }
            for username, entry in sorted(data.items())
        ]


# ── 免登录 token：跨页面刷新保持登录态 + 会话管理（可撤销） ──────────────

def get_or_create_secret(secret_file: Path) -> bytes:
    """签名密钥第一次用时自动生成并落盘（0600 权限），之后复用。
    密钥丢失/更换会让所有已签发的 token 全部失效（相当于强制所有人重新
    登录）——这是"核弹级"的一次性踢下线所有人的手段；日常场景更细粒度的
    需求（"只踢掉我自己另一个设备的登录"、"管理员踢掉某个人"）见下面
    `SessionStore`，不需要动这个密钥文件。"""
    secret_file = Path(secret_file)
    if secret_file.exists():
        try:
            hexed = secret_file.read_text(encoding="utf-8").strip()
            if hexed:
                return bytes.fromhex(hexed)
        except Exception:
            pass
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    secret = os.urandom(32)
    secret_file.write_text(secret.hex(), encoding="utf-8")
    try:
        os.chmod(secret_file, 0o600)
    except Exception:
        pass
    return secret


class SessionStore:
    """登录会话登记表——用于回答"谁正在用看板/用的哪个会话"这个问题，
    以及让"退出登录"、"退出所有其他会话"、管理员"踢掉某个会话"这几个
    操作真正对已签发的免登录 token 生效（不用像轮换签名密钥那样把所有人
    一次性全部踢掉）。

    文件内容形如：
        {"<session_id 十六进制>": {
            "username": "alice",
            "issued_at": 1234567890.0,
            "expires_at": 1234567890.0,
            "client_id": "1.2.3.4",   # 见 app.py::_client_id()，拿不到时是空串
            "last_seen": 1234567890.0,
        }, ...}

    这是 Cookie 免登录 token 方案（见 `make_token`/`verify_token`）的必要
    配套：signature 本身只能证明"这个 token 确实是服务器签发的、没被
    篡改、没过期"，签名算法自己没有"撤销"这个概念——一旦签出去，在过期
    之前永远有效。加上这张登记表之后，`verify_token` 校验通过只表示
    "签名合法"，还要再查一下 `session_id` 是否还在这张表里，才能确认
    "这个会话没有被撤销"。
    """

    def __init__(self, sessions_file: Path):
        self.path = Path(sessions_file)

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
        try:
            os.chmod(self.path, 0o600)
        except Exception:
            pass

    @staticmethod
    def _prune(data: dict) -> bool:
        """原地删掉已过期的条目，返回是否有删除发生（调用方据此决定要不要
        落盘，避免"读一次就白白写一次"）。过期只是从这张登记表里消失，
        不影响 `verify_token` 本身对签名/时间戳的校验——两边都会各自判定
        为"无效"，双重保险。"""
        now = time.time()
        expired = [sid for sid, entry in data.items() if entry.get("expires_at", 0) < now]
        for sid in expired:
            del data[sid]
        return bool(expired)

    def create(self, username: str, client_id: str = "", ttl_seconds: int = TOKEN_TTL_SECONDS) -> tuple:
        """新登记一个会话，返回 `(session_id, exp)`；`exp` 是整数时间戳，
        调用方拿去签 `make_token`，保证 token 里的过期时间和这张表里记的
        完全一致。"""
        data = self._load()
        self._prune(data)
        session_id = os.urandom(16).hex()
        now = time.time()
        exp = now + ttl_seconds
        data[session_id] = {
            "username": username,
            "issued_at": now,
            "expires_at": exp,
            "client_id": client_id or "",
            "last_seen": now,
        }
        self._save(data)
        return session_id, int(exp)

    def is_valid(self, session_id: str, username: Optional[str] = None) -> bool:
        """会话是否还"活着"：存在、没过期，且（如果传了 username）确实
        属于这个用户——最后一条是防止理论上的会话 id 被复用/伪造后冒充
        成别的用户（虽然 session_id 是 16 字节随机数，实际碰撞概率可以
        忽略，这里只是多一层防御）。"""
        if not session_id:
            return False
        entry = self._load().get(session_id)
        if not entry:
            return False
        if entry.get("expires_at", 0) < time.time():
            return False
        if username is not None and entry.get("username") != username:
            return False
        return True

    def touch(self, session_id: str, min_interval: float = 300.0) -> None:
        """更新"最近活跃时间"，节流写盘：距离上次记录的 `last_seen` 不满
        `min_interval` 秒就跳过，不然 Streamlit 几乎每次交互都会重跑到
        登录门禁这里，会变成"每次点击都写一次磁盘文件"。"""
        data = self._load()
        entry = data.get(session_id)
        if not entry:
            return
        now = time.time()
        if now - entry.get("last_seen", 0) < min_interval:
            return
        entry["last_seen"] = now
        self._save(data)

    def revoke(self, session_id: str) -> bool:
        """撤销单个会话。撤销之后，即使拿着那个 token 的人还没刷新页面，
        下一次和页面有任何交互（点按钮、切 tab……）触发 Streamlit rerun
        时，登录门禁重新核对会话表就会发现它已经不在了，直接被退回登录页
        ——不需要等 token 自然过期，也不需要轮换全局签名密钥连累其他人。"""
        data = self._load()
        if session_id in data:
            del data[session_id]
            self._save(data)
            return True
        return False

    def revoke_all_for_user(self, username: str, except_session_id: Optional[str] = None) -> int:
        """撤销某个用户的所有会话，`except_session_id` 可以排除"当前正在
        用的这一个"（"退出所有其他会话"场景：不想把自己也顺手踢下线）。
        返回实际撤销的数量。"""
        data = self._load()
        targets = [
            sid for sid, entry in data.items()
            if entry.get("username") == username and sid != except_session_id
        ]
        for sid in targets:
            del data[sid]
        if targets:
            self._save(data)
        return len(targets)

    def revoke_all(self) -> int:
        """撤销全部用户的全部会话（管理员的"核选项"，效果类似轮换签名
        密钥，但不需要真的去改密钥文件）。返回撤销前的会话总数。"""
        data = self._load()
        count = len(data)
        if data:
            self._save({})
        return count

    def list_sessions(self, username: Optional[str] = None) -> list:
        """列出未过期的会话（供页面表格展示），可选按用户名过滤。只读，
        不会顺手把过期条目落盘删掉——真正的清理发生在 `create`/`revoke`
        等本来就要写盘的操作里，避免"仅仅是打开一下列表页"就触发一次
        磁盘写入。按 `issued_at` 倒序（最近登录的排前面）。"""
        data = self._load()
        now = time.time()
        items = [
            {"session_id": sid, **entry}
            for sid, entry in data.items()
            if entry.get("expires_at", 0) >= now and (username is None or entry.get("username") == username)
        ]
        items.sort(key=lambda item: item.get("issued_at", 0), reverse=True)
        return items


def make_token(username: str, session_id: str, exp: int, secret: bytes) -> str:
    """签发免登录 token。`session_id`/`exp` 由 `SessionStore.create()`
    产出，这里只负责签名，不自己算过期时间——保证 token 里的 `exp` 和
    `SessionStore` 登记表里的 `expires_at` 永远是同一个值，不会出现"token
    还没过期但会话表已经判定过期"这种两边打架的情况。"""
    payload = f"{username}:{session_id}:{exp}"
    sig = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def verify_token(token: str, secret: bytes) -> Optional[tuple]:
    """校验签名和过期时间，通过则返回 `(username, session_id)`；格式
    错误/过期/篡改统一返回 `None`。

    注意：这个函数只验证"签名是否合法"，**不**查询 `SessionStore`——
    一个签名合法但已被撤销的会话，这里仍然会返回非 None。调用方
    （`render_login_gate`）必须在拿到返回值之后，再用
    `SessionStore.is_valid(session_id, username)` 补一道"这个会话是否
    还活着"的检查，两步都通过才能算真正登录成功。之所以不把这道检查
    合并进来，是因为 `verify_token` 是不做 IO 的纯函数（方便单测），
    `SessionStore` 需要读文件。

    历史兼容性：升级前签发的旧格式 token（`username:exp:sig`，3 段）在
    这里会因为 `split(":")` 解包成 3 个值而不是 4 个而抛异常、返回
    `None`，效果等同于"这些旧 token 全部失效，需要重新登录"——和当年
    引入这套机制、以及后续任何一次改变 token 内部格式的升级都是同样的
    预期行为，不是需要修的 bug。
    """
    try:
        username, session_id, exp_str, sig = token.split(":")
        exp = int(exp_str)
    except Exception:
        return None
    if time.time() > exp:
        return None
    payload = f"{username}:{session_id}:{exp}"
    expected_sig = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_sig, sig):
        return None
    return username, session_id
