"""
user_mapping.py
================
维护"微信 openid ↔ mini_agent 用户"的映射关系，本地 sqlite 持久化，
避免每次重启都重新调用 /v1/users 创建新用户。

角色规则：
    通过 RoleRules 从 config 里读取一份"哪些 openid 应该给什么角色"的
    简单规则（owner 名单 + 默认角色），create_user 时按此决定 role。
    规则本身很简单，故意不做成复杂的 DSL——如果以后需要更复杂的规则，
    再单独扩展 RoleRules。
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


#: mini_agent /v1/users 允许创建的角色（不含 "owner"，owner 只能是启动时
#: 配置好的全局 owner token 持有者，见 src/mini_agent/api/user_store.py::VALID_ROLES）。
#: 按信任等级从高到低：family > colleague > agent > public。
VALID_WEIXIN_ROLES = {"family", "colleague", "agent", "public"}


@dataclass
class RoleRules:
    """微信 openid → mini_agent 角色 的简单规则。"""

    owner_openids: set[str]
    default_role: str = "public"
    #: owner 名单里的微信用户创建出来的 mini_agent 用户角色（信任等级最高的非 owner 角色）
    owner_mapped_role: str = "family"

    def role_for(self, openid: str) -> str:
        if openid in self.owner_openids:
            return self.owner_mapped_role
        return self.default_role

    @classmethod
    def from_config(cls, cfg: dict) -> "RoleRules":
        owners = set(cfg.get("owner_openids", []) or [])
        default_role = cfg.get("default_role", "public")
        owner_mapped_role = cfg.get("owner_mapped_role", "family")
        if default_role not in VALID_WEIXIN_ROLES:
            raise ValueError(f"default_role 必须是 {VALID_WEIXIN_ROLES} 之一，收到: {default_role!r}")
        if owner_mapped_role not in VALID_WEIXIN_ROLES:
            raise ValueError(f"owner_mapped_role 必须是 {VALID_WEIXIN_ROLES} 之一，收到: {owner_mapped_role!r}")
        return cls(owner_openids=owners, default_role=default_role, owner_mapped_role=owner_mapped_role)


@dataclass
class UserMappingRecord:
    openid: str
    user_id: str
    token: str
    role: str
    created_at: float


class UserMappingStore:
    """openid ↔ mini_agent 用户映射的 sqlite 存储。"""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_mapping (
                openid     TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL,
                token      TEXT NOT NULL,
                role       TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_index (
                openid   TEXT NOT NULL,
                idx      INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                PRIMARY KEY (openid, idx)
            )
            """
        )
        self._conn.commit()

    def get(self, openid: str) -> Optional[UserMappingRecord]:
        cur = self._conn.execute(
            "SELECT openid, user_id, token, role, created_at FROM user_mapping WHERE openid = ?",
            (openid,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return UserMappingRecord(*row)

    def save(self, openid: str, user_id: str, token: str, role: str) -> UserMappingRecord:
        rec = UserMappingRecord(openid=openid, user_id=user_id, token=token, role=role, created_at=time.time())
        self._conn.execute(
            "INSERT OR REPLACE INTO user_mapping (openid, user_id, token, role, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (rec.openid, rec.user_id, rec.token, rec.role, rec.created_at),
        )
        self._conn.commit()
        return rec

    # ------------------------------------------------------------------
    # /sessions 列表的"序号 → session_id"映射，供 /session use <序号> 使用
    # ------------------------------------------------------------------

    def save_session_index(self, openid: str, sessions: list[str]) -> None:
        self._conn.execute("DELETE FROM session_index WHERE openid = ?", (openid,))
        self._conn.executemany(
            "INSERT INTO session_index (openid, idx, session_id) VALUES (?, ?, ?)",
            [(openid, i + 1, sid) for i, sid in enumerate(sessions)],
        )
        self._conn.commit()

    def resolve_session_ref(self, openid: str, ref: str) -> Optional[str]:
        """把用户输入的 '2' 这种序号解析成真实 session_id；非数字则原样返回（当作已经是 id）。"""
        ref = ref.strip()
        if ref.isdigit():
            cur = self._conn.execute(
                "SELECT session_id FROM session_index WHERE openid = ? AND idx = ?",
                (openid, int(ref)),
            )
            row = cur.fetchone()
            return row[0] if row else None
        return ref

    def close(self) -> None:
        self._conn.close()


async def get_or_create_user(
    store: UserMappingStore,
    client,  # MiniAgentClient，避免循环 import 用鸭子类型
    owner_token: str,
    openid: str,
    role_rules: RoleRules,
) -> UserMappingRecord:
    """已存在则直接返回映射；不存在则调 mini_agent /v1/users 建一个新用户。"""
    existing = store.get(openid)
    if existing is not None:
        return existing

    role = role_rules.role_for(openid)
    user_id, token = await client.create_user(
        owner_token=owner_token,
        name=f"wx_{openid}",
        role=role,
        meta={"source": "weixin", "openid": openid},
    )
    return store.save(openid, user_id, token, role)
