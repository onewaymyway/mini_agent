"""
api/user_store.py — 用户注册表与 token 角色系统

存储结构（<project_root>/.agent/users/）：
  users.json        — 用户列表（token_hash 不含明文）
  tokens/
    owner.key       — owner token 明文（0600）
    <user_id>.key   — 其他用户 token 明文（0600）

角色（role）：
  owner     — 主人，完全控制权，daemon 启动者
  family    — 家人/朋友，高信任，情感支持类对话
  colleague — 工作相关，专业交流，只读文件系统
  agent     — 其他 AI agent，结构化通信，沙箱工具
  public    — 公开访客，只读，受限对话轮数

每个用户有独立数据目录：.agent/users/<user_id>/
  profile.json      — 社交画像（由 RoleProfileManager 管理，agent 在对话中自动更新）
  memory.jsonl      — 与该用户的专属记忆
  preferences.json  — 用户偏好

注意：本文件里的 RoleProfileManager 和 mini_agent.profile.UserProfileManager 是两个
不同的东西，不要混淆：
  - RoleProfileManager（本文件）  → <project_root>/.agent/users/<user_id>/profile.json
    人工/agent 在对话中维护的社交画像（relation/trust_level/agent_notes 等）。
  - profile.py::UserProfileManager → ~/.agent/users/<user_id>/profile.json（全局，跨项目）
    LLM 自动总结的技术栈/习惯画像，单用户个性化功能，与角色系统无关。
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional
from mini_agent.time_utils import ts_to_str


# ── 角色定义 ──────────────────────────────────────────────────────────────────

VALID_ROLES = {"owner", "family", "colleague", "agent", "public"}

# 角色对应的资源预算（影响 ResourceArbiter）
ROLE_BUDGETS = {
    "owner":     {"max_tokens": 200000, "max_turns": 500, "max_tools": 100},
    "family":    {"max_tokens": 80000,  "max_turns": 200, "max_tools": 30},
    "colleague": {"max_tokens": 50000,  "max_turns": 100, "max_tools": 15},
    "agent":     {"max_tokens": 30000,  "max_turns": 50,  "max_tools": 10},
    "public":    {"max_tokens": 8000,   "max_turns": 20,  "max_tools": 0},
}

# 角色对应的工具权限组（空列表 = 无工具）
ROLE_TOOL_GROUPS = {
    "owner":     None,       # None = 不过滤，全部工具
    "family":    ["builtin", "search"],
    "colleague": ["builtin", "search"],
    "agent":     ["builtin"],
    "public":    [],
}

# 角色对应的对话风格注入（写入 session agent 的 system prompt）
ROLE_PERSONA_HINTS = {
    "owner": (
        "你在和你的主人对话。像对自己说话一样直接，可讨论任何话题，"
        "包括私人目标、未完成任务、批评和调整。"
    ),
    "family": (
        "你在和主人的家人或朋友对话。保持温暖、亲切、关心的语气，"
        "优先情感支持，不主动披露主人的工作细节或私人计划。"
    ),
    "colleague": (
        "你在和工作相关的人对话。保持专业简洁，聚焦工作事项，"
        "不讨论私人事务，文件访问只读。"
    ),
    "agent": (
        "你在和另一个 AI agent 对话。优先使用结构化格式，"
        "明确声明自己的能力边界，协议协商显式进行，不假设对方知道你的内部状态。"
    ),
    "public": (
        "你在和公开访客对话。礼貌但保守，不透露任何内部信息、"
        "主人信息或系统细节，对话范围受限。"
    ),
}


# ── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass
class UserRecord:
    """users.json 中的一条用户记录。"""
    user_id:     str
    name:        str
    role:        str
    token_hash:  str              # sha256(token)，不存明文
    created_at:  float
    last_seen:   float = 0.0
    trust_level: int = 5          # 1-10
    meta:        dict = field(default_factory=dict)   # 用户自定义字段

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "UserRecord":
        return UserRecord(
            user_id=d["user_id"],
            name=d.get("name", ""),
            role=d.get("role", "public"),
            token_hash=d.get("token_hash", ""),
            created_at=float(d.get("created_at", 0)),
            last_seen=float(d.get("last_seen", 0)),
            trust_level=int(d.get("trust_level", 5)),
            meta=d.get("meta", {}),
        )


@dataclass
class UserContext:
    """经过认证后注入 request.state 的用户上下文。"""
    user_id:     str
    name:        str
    role:        str
    trust_level: int
    is_loopback: bool   # 是否从本机连接（影响 owner 特权判断）

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"

    @property
    def budget(self) -> dict:
        return ROLE_BUDGETS.get(self.role, ROLE_BUDGETS["public"])

    @property
    def persona_hint(self) -> str:
        return ROLE_PERSONA_HINTS.get(self.role, ROLE_PERSONA_HINTS["public"])

    @property
    def tool_groups(self):
        return ROLE_TOOL_GROUPS.get(self.role, [])


# ── token 工具 ────────────────────────────────────────────────────────────────

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _generate_token() -> str:
    return secrets.token_hex(32)


def _write_key_file(path: Path, token: str) -> None:
    """原子写入 token 文件，设置 0600 权限。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(token + "\n", encoding="utf-8")
    try:
        tmp.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.user_store')
        pass
    os.replace(tmp, path)


def _read_key_file(path: Path) -> Optional[str]:
    try:
        t = path.read_text(encoding="utf-8").strip()
        return t if len(t) >= 32 else None
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.api.user_store._read_key_file')
        return None


# ── UserStore ─────────────────────────────────────────────────────────────────

class UserStore:
    """
    用户注册表，管理多用户 token 与角色。

    目录结构：
      <users_dir>/
        users.json         — 用户列表
        tokens/
          owner.key        — owner token 明文（0600）
          <user_id>.key    — 其他用户 token 明文（0600）
        owner/             — owner 数据目录（见 RoleProfileManager）
        <user_id>/         — 其他用户数据目录
    """

    _USERS_FILE = "users.json"

    def __init__(self, users_dir: Path) -> None:
        self._dir = users_dir
        self._tokens_dir = users_dir / "tokens"
        self._users_file = users_dir / self._USERS_FILE
        self._users_dir = users_dir
        self._cache: dict[str, UserRecord] = {}   # user_id → record
        self._token_index: dict[str, str] = {}    # token_hash → user_id
        self._lock = __import__("threading").Lock()

        self._users_dir.mkdir(parents=True, exist_ok=True)
        self._tokens_dir.mkdir(parents=True, exist_ok=True)
        self._load()

    # ── 初始化：确保 owner 存在 ───────────────────────────────────────────────

    def ensure_owner(self, configured_token: str = "") -> str:
        """
        确保 owner 用户存在并返回其 token（明文）。
        优先级：configured_token > tokens/owner.key > 新生成
        """
        with self._lock:
            # 已有 owner？
            owner = self._cache.get("owner")
            key_path = self._tokens_dir / "owner.key"

            if configured_token:
                token = configured_token
            elif key_path.exists():
                token = _read_key_file(key_path) or _generate_token()
            else:
                token = _generate_token()

            _write_key_file(key_path, token)
            token_hash = _hash_token(token)

            if owner is None:
                owner = UserRecord(
                    user_id="owner",
                    name="Owner",
                    role="owner",
                    token_hash=token_hash,
                    created_at=time.time(),
                    trust_level=10,
                )
                self._cache["owner"] = owner
                self._token_index[token_hash] = "owner"
                self._save()
            elif owner.token_hash != token_hash:
                # token 更新（如配置变更）
                old_hash = owner.token_hash
                self._token_index.pop(old_hash, None)
                owner.token_hash = token_hash
                self._token_index[token_hash] = "owner"
                self._save()

            # 确保 owner 数据目录存在
            (self._users_dir / "owner").mkdir(exist_ok=True)
            return token

    # ── 认证 ─────────────────────────────────────────────────────────────────

    def authenticate(self, token: str) -> Optional[UserRecord]:
        """
        用 token 换取 UserRecord。
        使用恒时比较防止时序攻击。
        """
        if not token:
            return None
        token_hash = _hash_token(token)
        with self._lock:
            user_id = self._token_index.get(token_hash)
            if not user_id:
                return None
            record = self._cache.get(user_id)
            if record:
                record.last_seen = time.time()
                # 异步写回（不阻塞认证）
                self._schedule_save()
            return record

    # ── 用户 CRUD ────────────────────────────────────────────────────────────

    def add_user(
        self,
        name: str,
        role: str,
        trust_level: int = 5,
        meta: Optional[dict] = None,
    ) -> tuple[str, str]:
        """
        添加新用户，返回 (user_id, token)。
        token 明文写入 tokens/<user_id>.key，不存 users.json。
        """
        if role not in VALID_ROLES:
            raise ValueError(f"Invalid role: {role!r}. Must be one of {VALID_ROLES}")
        if role == "owner":
            raise ValueError("Cannot add another owner via add_user; use ensure_owner()")

        token = _generate_token()
        user_id = f"u_{secrets.token_hex(4)}"   # e.g. "u_a1b2c3d4"
        token_hash = _hash_token(token)

        record = UserRecord(
            user_id=user_id,
            name=name,
            role=role,
            token_hash=token_hash,
            created_at=time.time(),
            trust_level=trust_level,
            meta=meta or {},
        )

        with self._lock:
            self._cache[user_id] = record
            self._token_index[token_hash] = user_id
            _write_key_file(self._tokens_dir / f"{user_id}.key", token)
            # 创建用户数据目录
            (self._users_dir / user_id).mkdir(exist_ok=True)
            self._save()

        return user_id, token

    def rotate_token(self, user_id: str) -> Optional[str]:
        """重新生成 token，返回新 token 明文。"""
        with self._lock:
            record = self._cache.get(user_id)
            if not record:
                return None
            new_token = _generate_token()
            old_hash = record.token_hash
            self._token_index.pop(old_hash, None)
            record.token_hash = _hash_token(new_token)
            self._token_index[record.token_hash] = user_id
            key_path = self._tokens_dir / (
                "owner.key" if user_id == "owner" else f"{user_id}.key"
            )
            _write_key_file(key_path, new_token)
            self._save()
        return new_token

    def update_role(self, user_id: str, role: str) -> bool:
        if user_id == "owner":
            return False   # owner 角色不可变
        if role not in VALID_ROLES or role == "owner":
            return False
        with self._lock:
            record = self._cache.get(user_id)
            if not record:
                return False
            record.role = role
            self._save()
        return True

    def update_meta(self, user_id: str, meta: dict) -> bool:
        with self._lock:
            record = self._cache.get(user_id)
            if not record:
                return False
            record.meta.update(meta)
            self._save()
        return True

    def remove_user(self, user_id: str) -> bool:
        if user_id == "owner":
            return False
        with self._lock:
            record = self._cache.pop(user_id, None)
            if not record:
                return False
            self._token_index.pop(record.token_hash, None)
            (self._tokens_dir / f"{user_id}.key").unlink(missing_ok=True)
            self._save()
        return True

    def list_users(self) -> list[UserRecord]:
        with self._lock:
            return list(self._cache.values())

    def get_user(self, user_id: str) -> Optional[UserRecord]:
        with self._lock:
            return self._cache.get(user_id)

    def get_token(self, user_id: str) -> Optional[str]:
        """读取用户 token 明文（从 .key 文件），用于 CLI 打印。"""
        fname = "owner.key" if user_id == "owner" else f"{user_id}.key"
        return _read_key_file(self._tokens_dir / fname)

    def user_data_dir(self, user_id: str) -> Path:
        """返回用户数据目录（创建并确保存在）。"""
        d = self._users_dir / user_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── 持久化 ────────────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._users_file.exists():
            return
        try:
            data = json.loads(self._users_file.read_text(encoding="utf-8"))
            users = data.get("users", [])
            for u in users:
                record = UserRecord.from_dict(u)
                self._cache[record.user_id] = record
                self._token_index[record.token_hash] = record.user_id
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.api.user_store')
            pass

    def _save(self) -> None:
        """同步写入 users.json（调用方已持有锁）。"""
        try:
            data = {"users": [r.to_dict() for r in self._cache.values()]}
            import tempfile
            tmp = self._users_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self._users_file)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.api.user_store')
            pass

    def _schedule_save(self) -> None:
        """在后台线程异步写入（避免认证路径阻塞）。"""
        import threading
        threading.Thread(target=self._save_locked, daemon=True).start()

    def _save_locked(self) -> None:
        with self._lock:
            self._save()


# ── 角色社交画像管理 ──────────────────────────────────────────────────────────
#
# 注意：这不是 mini_agent.profile.UserProfileManager（那个是单用户、跨项目的
# LLM 自动生成技术栈/习惯画像，存在 ~/.agent/users/<user_id>/profile.json）。
# 这里管的是"这个人是谁、关系如何、对话中要注意什么"的社交画像，
# 存在 <project_root>/.agent/users/<user_id>/profile.json（项目本地）。
# 两者路径形状相似但 scope 不同，故意分成两个类，避免谁覆盖谁。

class RoleProfileManager:
    """
    管理 .agent/users/<user_id>/profile.json。
    由 SessionAgent 在对话中增量写入，由 Self 的 巩固循环 tick 汇总。
    """

    def __init__(self, users_dir: Path) -> None:
        self._users_dir = users_dir

    def get_profile(self, user_id: str) -> dict:
        path = self._users_dir / user_id / "profile.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.api.user_store.RoleProfileManager.get_profile')
            return {}

    def update_profile(self, user_id: str, updates: dict) -> None:
        """增量更新画像（深度合并）。"""
        path = self._users_dir / user_id / "profile.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        current = self.get_profile(user_id)
        merged = _deep_merge(current, updates)
        merged["last_updated"] = time.time()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    def add_agent_note(self, user_id: str, note: str) -> None:
        """追加 agent 观察备注（用于 agent 在对话中记录关于用户的洞察）。"""
        profile = self.get_profile(user_id)
        notes = profile.get("agent_notes", [])
        notes.append({"ts": time.time(), "ts_str": ts_to_str(time.time()), "note": note})
        profile["agent_notes"] = notes[-50:]  # 保留最近 50 条
        self.update_profile(user_id, profile)

    def build_system_context(self, user_id: str, role: str) -> str:
        """
        从 profile 生成注入 SessionAgent system prompt 的用户上下文片段。
        格式化为人类可读的 markdown，让 agent 快速了解对话对象。
        """
        profile = self.get_profile(user_id)
        persona = profile.get("persona", {})
        lines: list[str] = []

        lines.append(f"## 对话用户信息")
        lines.append(f"- 用户 ID：{user_id}")
        if profile.get("name") or persona.get("relation"):
            name_str = profile.get("name", "")
            rel = persona.get("relation", "")
            if name_str and rel:
                lines.append(f"- 称呼：{name_str}（{rel}）")
            elif name_str:
                lines.append(f"- 称呼：{name_str}")

        if persona.get("personalities"):
            lines.append(f"- 性格特点：{', '.join(persona['personalities'])}")
        if persona.get("interests"):
            lines.append(f"- 兴趣：{', '.join(persona['interests'])}")
        if persona.get("communication_style"):
            lines.append(f"- 沟通风格：{persona['communication_style']}")
        if persona.get("sensitive_topics"):
            lines.append(f"- 敏感话题（避免主动提及）：{', '.join(persona['sensitive_topics'])}")

        # 最近的 agent 备注
        notes = profile.get("agent_notes", [])
        if notes:
            recent = notes[-3:]
            lines.append(f"- 近期备注：")
            for n in recent:
                lines.append(f"  - {n['note']}")

        # 角色提示
        lines.append(f"\n{ROLE_PERSONA_HINTS.get(role, '')}")

        return "\n".join(lines)


def _deep_merge(base: dict, updates: dict) -> dict:
    result = dict(base)
    for k, v in updates.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result
