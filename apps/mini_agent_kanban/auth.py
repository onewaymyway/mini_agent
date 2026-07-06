"""
auth.py —— 看板登录鉴权。

这是 Streamlit 看板 UI 自身的登录门禁，和 mini-agent HTTP API 的
Bearer Token 鉴权（/v1/chat 等接口用的那个 token）是两回事，互不替代：
    - API token  → 看板用它去调 daemon 的 HTTP 接口（谁能操作 Agent）
    - 看板账户   → 谁能打开这个 Streamlit 页面（本文件负责）

不依赖数据库或第三方服务：账户信息是一个本地 JSON 文件（明文用户名 +
salt + PBKDF2 密码哈希，不存明文密码）；登录态用一个签名 token 持久化
到 URL query param 里，避免用户刷新页面就被踢出登录。

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


class UserStore:
    """管理看板账户。文件内容形如：
        {"alice": {"salt": "<hex>", "hash": "<hex>"}, ...}
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

    def add_user(self, username: str, password: str) -> None:
        data = self._load()
        salt = os.urandom(16)
        data[username] = {"salt": salt.hex(), "hash": _hash_password(password, salt)}
        self._save(data)

    def remove_user(self, username: str) -> bool:
        data = self._load()
        if username in data:
            del data[username]
            self._save(data)
            return True
        return False

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


# ── 免登录 token：跨页面刷新保持登录态 ────────────────────────────────────

def get_or_create_secret(secret_file: Path) -> bytes:
    """签名密钥第一次用时自动生成并落盘（0600 权限），之后复用。
    密钥丢失/更换会让所有已签发的 token 失效（相当于强制所有人重新登录），
    这是预期行为，不是 bug。"""
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


def make_token(username: str, secret: bytes, ttl_seconds: int = TOKEN_TTL_SECONDS) -> str:
    exp = int(time.time()) + ttl_seconds
    payload = f"{username}:{exp}"
    sig = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def verify_token(token: str, secret: bytes) -> Optional[str]:
    """校验通过返回 username；过期/篡改/格式错误统一返回 None。"""
    try:
        username, exp_str, sig = token.split(":")
        exp = int(exp_str)
    except Exception:
        return None
    if time.time() > exp:
        return None
    payload = f"{username}:{exp}"
    expected_sig = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_sig, sig):
        return None
    return username
