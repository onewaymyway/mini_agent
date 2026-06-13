"""
profile.py — 用户画像（profile）存储与生成。

[SYS-PROFILE]

设计目标：
  - 当前为单用户模式：profile 存储于 ~/.agent/profile.json
    （路径由 AgentPaths.profile_path() 决定）。
  - 为后续多用户预留扩展点：
      1. UserProfile 自带 user_id 字段（默认 "default"）。
      2. UserProfileManager 接收可选的 user_id，传给
         AgentPaths.profile_path(user_id)；多用户场景下只需在
         构造 Agent 时传入实际 user_id，无需改动本模块的读写/生成逻辑。
      3. preferences（用户手动设置）与 derived（系统自动生成）分离存储，
         避免自动刷新覆盖用户的显式偏好。

Profile 文件结构：
{
  "user_id": "default",
  "display_name": null,
  "created_at": 1700000000.0,
  "updated_at": 1700000000.0,
  "preferences": {},          # 用户可通过命令显式设置，本模块不会自动覆盖
  "derived": {
    "summary": "...",          # 一段自然语言画像总结
    "tech_stack": [...],
    "habits": [...],
    "source_entry_count": 5,   # 生成时使用的记忆条目数（用于增量刷新判断）
    "updated_at": 1700000000.0
  }
}
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.llm.base import LLMClient
    from mini_agent.perception.memory_store import MemoryEntry


@dataclass
class UserProfile:
    user_id: str = "default"
    display_name: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # 用户显式设置的偏好（语言、模型、语气等），本模块不会自动修改
    preferences: dict = field(default_factory=dict)
    # 系统根据长期记忆自动生成/刷新的画像
    derived: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "UserProfile":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    @property
    def is_new(self) -> bool:
        """是否为尚未生成过画像的新用户。"""
        return not self.derived


class UserProfileManager:
    """
    用户 profile 的加载 / 保存 / 生成。

    Args:
        paths: AgentPaths 实例，用于解析存储路径。
        user_id: 预留参数。当前传 None 即可（单用户模式，
            对应 ~/.agent/profile.json）。多用户支持落地后，
            调用方传入实际 user_id 即可自动切换到
            ~/.agent/users/<user_id>/profile.json。
    """

    def __init__(self, paths, user_id: Optional[str] = None) -> None:
        self._paths = paths
        self._user_id = user_id
        self._path = paths.profile_path(user_id)
        self._profile: Optional[UserProfile] = None

    @property
    def path(self) -> Path:
        return self._path

    # ── 加载 / 保存 ────────────────────────────────────────────────────────

    def load(self) -> UserProfile:
        """加载 profile；不存在则返回一个新建的空 profile（不落盘）。"""
        if self._profile is not None:
            return self._profile

        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._profile = UserProfile.from_dict(data)
            except (json.JSONDecodeError, OSError, TypeError):
                self._profile = UserProfile(user_id=self._user_id or "default")
        else:
            self._profile = UserProfile(user_id=self._user_id or "default")

        return self._profile

    def save(self) -> None:
        """原子写回 profile 文件。"""
        if self._profile is None:
            return
        self._profile.updated_at = time.time()
        from mini_agent.session import _atomic_write_json
        _atomic_write_json(self._path, self._profile.to_dict())

    # ── 偏好（用户显式设置）──────────────────────────────────────────────────

    def set_preference(self, key: str, value) -> None:
        profile = self.load()
        profile.preferences[key] = value
        self.save()

    def set_display_name(self, name: str) -> None:
        profile = self.load()
        profile.display_name = name
        self.save()

    # ── 自动画像生成 ──────────────────────────────────────────────────────

    def should_refresh(self, current_entry_count: int, cfg) -> bool:
        """
        判断是否需要(重新)生成 derived 画像。

        触发条件：
          - 记忆条目数 >= cfg.profile_min_entries，且
          - 尚未生成过画像（is_new），或
          - 自上次生成以来新增的条目数 >= cfg.profile_refresh_interval_entries
        """
        if current_entry_count < cfg.profile_min_entries:
            return False

        profile = self.load()
        if profile.is_new:
            return True

        last_count = profile.derived.get("source_entry_count", 0)
        return (current_entry_count - last_count) >= cfg.profile_refresh_interval_entries

    def generate(self, llm_client: "LLMClient", entries: list["MemoryEntry"]) -> UserProfile:
        """
        基于最近的长期记忆条目，调用 LLM 生成/刷新 derived 画像。

        本方法只做 LLM 调用 + 解析 + 落盘，不做线程调度；
        调用方（agent.py）负责在后台线程中调用它。
        """
        from mini_agent.prompts import pm

        profile = self.load()

        memory_text = "\n".join(
            f"- {e.summary}" + (f"（标签: {', '.join(e.tags)}）" if e.tags else "")
            for e in entries
        )
        prompt = pm.render("user/profile_update_request", memory_text=memory_text)

        resp = llm_client.chat_with_retry(
            messages=[{"role": "user", "content": prompt}],
            system=pm.render("system/profile_summarizer"),
            tools=[],
            max_retries=10,
        )
        raw = (resp.text or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"summary": raw[:500]}

        derived = {
            "summary": str(parsed.get("summary", ""))[:1000],
            "tech_stack": list(parsed.get("tech_stack", []))[:20],
            "habits": list(parsed.get("habits", []))[:20],
            "source_entry_count": len(entries),
            "updated_at": time.time(),
        }
        profile.derived = derived
        self.save()
        return profile
