"""providers/keypool.py — 多 key 池（沿用 gen_image_with_text skill 的策略，独立实现）。

策略：
  * passive：只在遇到限流时切换到下一把可用 key；
  * 限流（HTTP 429 或响应体含限流关键字）→ 冷却该 key，立即换 key 重试，不消耗普通重试次数；
  * 全部 key 冷却中 → 返回 None，由调用方按普通失败处理；
  * 参数/鉴权类错误（400/401/403/404/422）→ 不重试，交回调用方修配置。

key 来源（只读环境变量，不写入项目文件）：
  AGNES_API_KEYS  逗号分隔的多个 key（优先）
  AGNES_API_KEY   单个 key
"""

from __future__ import annotations

import os
import time
from typing import Dict, List, Optional

RATE_LIMIT_KEYWORDS = ("rate limit", "quota exceeded", "too many requests", "限流", "频率")
NON_RETRYABLE_STATUS = (400, 401, 403, 404, 422)


def is_rate_limit(status: Optional[int], text: str = "") -> bool:
    if status == 429:
        return True
    lowered = (text or "").lower()
    return any(k in lowered for k in RATE_LIMIT_KEYWORDS)


def is_non_retryable(status: Optional[int], text: str = "") -> bool:
    if is_rate_limit(status, text):
        return False
    return status in NON_RETRYABLE_STATUS


class KeyPool:
    def __init__(self, keys: List[str], cooldown: float = 60.0):
        if not keys:
            raise ValueError("KeyPool 至少需要一把 key")
        self._keys = list(dict.fromkeys(keys))
        self._cooldown = cooldown
        self._until: Dict[str, float] = {k: 0.0 for k in self._keys}
        self._idx = 0

    def __len__(self) -> int:
        return len(self._keys)

    def _available(self, key: str) -> bool:
        return time.monotonic() >= self._until[key]

    def acquire(self) -> Optional[str]:
        """返回当前可用 key；全部冷却中返回 None。"""
        for step in range(len(self._keys)):
            key = self._keys[(self._idx + step) % len(self._keys)]
            if self._available(key):
                self._idx = (self._idx + step) % len(self._keys)
                return key
        return None

    def report_rate_limit(self, key: str) -> Optional[str]:
        """冷却该 key 并切换到下一把可用 key（没有则返回 None）。"""
        if key in self._until:
            self._until[key] = time.monotonic() + self._cooldown
        self._idx = (self._idx + 1) % len(self._keys)
        return self.acquire()

    def report_success(self, key: str) -> None:
        if key in self._until:
            self._until[key] = 0.0

    def min_wait(self) -> float:
        now = time.monotonic()
        return max(0.0, min(self._until.values()) - now)

    def key_index(self, key: Optional[str]) -> Optional[int]:
        """仅用于日志（不记录 key 本身）。"""
        return self._keys.index(key) if key in self._keys else None


def keys_from_env(env: Optional[Dict[str, str]] = None) -> List[str]:
    env = os.environ if env is None else env
    multi = env.get("AGNES_API_KEYS", "")
    candidates = [k.strip() for k in multi.split(",")] + [env.get("AGNES_API_KEY", "").strip()]
    return list(dict.fromkeys(k for k in candidates if k))


def pool_from_env(env: Optional[Dict[str, str]] = None) -> Optional[KeyPool]:
    keys = keys_from_env(env)
    return KeyPool(keys) if keys else None
