"""providers/keypool.py — 多 key 池（沿用 gen_image_with_text skill 的策略，独立实现）。

策略：
  * passive：只在遇到限流时切换到下一把可用 key；
  * 限流（HTTP 429 或响应体含限流关键字）→ 冷却该 key，立即换 key 重试，不消耗普通重试次数；
  * 全部 key 冷却中 → 返回 None，由调用方按普通失败处理；
  * 参数/鉴权类错误（400/401/403/404/422）→ 不重试，交回调用方修配置。

key 来源（与 mini_agent 的生图 skill 一致，不写入项目文件）：
  AGNES_API_KEYS → providers.json（AGNES_PROVIDERS_JSON 或从 skill 目录向上查找）→ AGNES_API_KEY
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
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


SKILL_DIR_REL = Path(".claude") / "skills" / "gen_image_with_text"   # mini_agent 的生图 skill（key 的权威来源）


def find_image_skill_dir(start: Optional[Path] = None) -> Optional[Path]:
    """从本文件向上查找 mini_agent 仓库内的生图 skill 目录（与 novel_to_video 的 skill 查找一致）。"""
    here = (start or Path(__file__)).resolve()
    for base in [here, *here.parents]:
        cand = base / SKILL_DIR_REL
        if (cand / "agnes_key_pool.py").is_file():
            return cand
    return None


def _find_providers_json(start: Path) -> Optional[Path]:
    """从 start 向上查找 providers.json（最多 8 层），与 skill 的 _find_providers_json 相同。"""
    current = Path(start).resolve()
    for _ in range(8):
        cand = current / "providers.json"
        if cand.is_file():
            return cand
        if current.parent == current:
            break
        current = current.parent
    return None


def _extract_agnes_keys(data: dict) -> List[str]:
    """从 providers.json 中取 agnes 的 key：providers.agnes.api_keys / api_key，其次 llm_fallback_chain 中 provider=agnes。"""
    block = (data.get("providers", {}) or {}).get("agnes", {}) or {}
    keys = list(block.get("api_keys", []) or [])
    if not keys and block.get("api_key"):
        keys.append(block["api_key"])
    if not keys:
        for entry in data.get("llm_fallback_chain", []) or []:
            if entry.get("provider") == "agnes":
                keys = list(entry.get("api_keys", []) or [])
                if not keys and entry.get("api_key"):
                    keys.append(entry["api_key"])
                break
    return list(dict.fromkeys(k.strip() for k in keys if k and k.strip()))


def resolve_keys(env: Optional[Dict[str, str]] = None, start: Optional[Path] = None) -> Dict[str, object]:
    """按 skill 的顺序解析 Agnes key（只返回来源与数量，不返回 key 内容）：
      1. 环境变量 AGNES_API_KEYS（逗号分隔）
      2. providers.json：AGNES_PROVIDERS_JSON 指定的文件；否则从生图 skill 目录向上查找
      3. 环境变量 AGNES_API_KEY
    env 为 None 时读取真实环境与文件；传入字典时（测试用）只读该字典，不读文件。
    返回 {keys, source, path, searched}：searched 为检查过的位置（用于日志排错）。"""
    use_files = env is None
    env = os.environ if env is None else env
    searched: List[str] = []
    multi = [k.strip() for k in env.get("AGNES_API_KEYS", "").split(",") if k.strip()]
    if multi:
        return {"keys": list(dict.fromkeys(multi)), "source": "环境变量 AGNES_API_KEYS", "path": None,
                "searched": ["环境变量 AGNES_API_KEYS"]}
    searched.append("环境变量 AGNES_API_KEYS（未设置）")
    if use_files:
        explicit = env.get("AGNES_PROVIDERS_JSON", "").strip()
        if explicit:
            path = Path(explicit).expanduser()
            searched.append(f"AGNES_PROVIDERS_JSON 指定的 {path}")
        else:
            skill = find_image_skill_dir(start)
            base = skill if skill else Path(__file__).parent
            searched.append(f"从 {base} 向上查找 providers.json")
            path = _find_providers_json(base)
        if path and path.is_file():
            try:
                keys = _extract_agnes_keys(json.loads(path.read_text(encoding="utf-8")))
                if keys:
                    return {"keys": keys, "source": f"providers.json（{path}）", "path": str(path),
                            "searched": searched}
                searched.append(f"{path}：没有 agnes 的 api_keys")
            except Exception as exc:  # noqa: BLE001 - 文件格式异常不影响后续兜底
                searched.append(f"{path}：解析失败（{type(exc).__name__}）")
        else:
            searched.append("providers.json：未找到")
    single = env.get("AGNES_API_KEY", "").strip()
    if single:
        return {"keys": [single], "source": "环境变量 AGNES_API_KEY", "path": None, "searched": searched}
    searched.append("环境变量 AGNES_API_KEY（未设置）")
    return {"keys": [], "source": "未找到", "path": None, "searched": searched}


def keys_from_env(env: Optional[Dict[str, str]] = None) -> List[str]:
    return list(resolve_keys(env)["keys"])


def pool_from_env(env: Optional[Dict[str, str]] = None) -> Optional[KeyPool]:
    info = resolve_keys(env)
    if not info["keys"]:
        return None
    pool = KeyPool(info["keys"])
    pool.source = info["source"]
    return pool
