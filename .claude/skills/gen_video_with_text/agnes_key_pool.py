"""
agnes_key_pool.py — Agnes API 多 Key 池（限流自动切换）

独立轻量实现，不依赖 mini_agent 主包，方便本 skill 单独运行（子进程调用）。
gen_image_with_text / gen_video_with_text / ask_image 三个 skill 各自持有
一份完全相同的拷贝，避免跨目录 import 在不同平台（尤其 Windows）下的路径问题。

Key 加载优先级：
  1. 环境变量 AGNES_API_KEYS（逗号分隔，可配多个 key，新增）
  2. providers.json 中 providers.agnes.api_keys（或 llm_fallback_chain 里
     provider == "agnes" 的 api_keys）——从当前脚本目录向上查找 providers.json
  3. 环境变量 AGNES_API_KEY（单个 key，向后兼容，行为与之前完全一致）

限流判定：HTTP 429，或响应文本中包含 rate limit / too many requests /
quota exceeded 等关键字（不区分大小写）。命中限流后立即切换下一把可用 key，
不占用原有的 max_retries 重试次数；非限流错误（参数错误、鉴权失败等）则
维持原来的同 key 重试逻辑，因为换 key 无法解决这类问题。

各 client 的默认行为完全不变：只配了一个 key（或没有 providers.json）时，
AgnesKeyPool 退化为"只有一把 key 可用"，等价于修改前的单 key 逻辑。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import List, Optional


_RATE_LIMIT_KEYWORDS = (
    "rate limit",
    "rate_limit",
    "too many requests",
    "quota exceeded",
    "quota_exceeded",
)


def is_rate_limit_error(status_code: Optional[int], text: str = "") -> bool:
    """判断一次请求失败是否属于限流错误。"""
    if status_code == 429:
        return True
    lowered = (text or "").lower()
    return any(kw in lowered for kw in _RATE_LIMIT_KEYWORDS)


def _find_providers_json(start: Path) -> Optional[Path]:
    """从 start 目录开始向上查找 providers.json（最多向上找 8 层）。"""
    current = start.resolve()
    for _ in range(8):
        candidate = current / "providers.json"
        if candidate.exists():
            return candidate
        if current.parent == current:
            break
        current = current.parent
    return None


def _extract_agnes_block(data: dict) -> dict:
    """从已解析的 providers.json 内容中提取 agnes 相关配置（keys / rotation / cooldown）。"""
    providers_block = data.get("providers", {}) or {}
    agnes_block = dict(providers_block.get("agnes", {}) or {})

    keys: List[str] = list(agnes_block.get("api_keys", []) or [])
    if not keys and agnes_block.get("api_key"):
        keys.append(agnes_block["api_key"])

    if not keys:
        for entry in data.get("llm_fallback_chain", []) or []:
            if entry.get("provider") == "agnes":
                keys = list(entry.get("api_keys", []) or [])
                if not keys and entry.get("api_key"):
                    keys.append(entry["api_key"])
                agnes_block.setdefault("key_rotation", entry.get("key_rotation"))
                agnes_block.setdefault("key_cooldown", entry.get("key_cooldown"))
                break

    # 去重保序
    seen = set()
    deduped = []
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            deduped.append(k)

    agnes_block["api_keys"] = deduped
    return agnes_block


def load_agnes_config(script_dir: Optional[str] = None) -> dict:
    """按优先级加载 Agnes key 及 rotation/cooldown 配置。

    Returns:
        {"keys": [...], "rotation": "passive"|"round_robin", "cooldown": float}
    """
    rotation = "passive"
    cooldown = 60.0

    # 1. AGNES_API_KEYS（逗号分隔）
    multi = os.environ.get("AGNES_API_KEYS", "")
    if multi.strip():
        keys = [k.strip() for k in multi.split(",") if k.strip()]
        if keys:
            return {"keys": keys, "rotation": rotation, "cooldown": cooldown}

    # 2. providers.json
    start = Path(script_dir) if script_dir else Path(__file__).parent
    providers_path = _find_providers_json(start)
    if providers_path:
        try:
            data = json.loads(providers_path.read_text(encoding="utf-8"))
            agnes_block = _extract_agnes_block(data)
            if agnes_block.get("api_keys"):
                return {
                    "keys": agnes_block["api_keys"],
                    "rotation": agnes_block.get("key_rotation") or rotation,
                    "cooldown": float(agnes_block.get("key_cooldown") or cooldown),
                }
        except Exception:
            pass  # providers.json 格式异常时不影响后续兜底

    # 3. 单个 AGNES_API_KEY（向后兼容）
    single = os.environ.get("AGNES_API_KEY")
    return {"keys": [single] if single else [], "rotation": rotation, "cooldown": cooldown}


class AgnesKeyPool:
    """极简多 Key 池：passive（遇限流才切换）/ round_robin（每次请求轮询）。"""

    def __init__(self, keys: List[str], rotation: str = "passive", cooldown: float = 60.0):
        if not keys:
            raise ValueError("AgnesKeyPool requires at least one key")
        self._keys: List[str] = list(dict.fromkeys(keys))  # 去重保序
        self._rotation = rotation if rotation in ("passive", "round_robin") else "passive"
        self._cooldown = cooldown
        self._cooldown_until = {k: 0.0 for k in self._keys}
        self._idx = 0

    def _is_available(self, key: str) -> bool:
        return time.monotonic() >= self._cooldown_until[key]

    def acquire(self) -> str:
        """获取当前应使用的 key；round_robin 模式下每次调用前进一格（跳过冷却中的）。"""
        if self._rotation == "round_robin":
            self._advance()
        return self._keys[self._idx]

    def report_rate_limit(self, key: str) -> Optional[str]:
        """报告 key 被限流：冷却该 key 并切换到下一把可用 key。
        全部 key 都在冷却中则返回 None（调用方应停止切换，走原有失败逻辑）。"""
        if key in self._cooldown_until:
            self._cooldown_until[key] = time.monotonic() + self._cooldown
        return self._find_next_available()

    def report_success(self, key: str) -> None:
        """请求成功，解除该 key 的冷却状态（如果有）。"""
        if key in self._cooldown_until:
            self._cooldown_until[key] = 0.0

    def all_exhausted(self) -> bool:
        return not any(self._is_available(k) for k in self._keys)

    def min_wait_seconds(self) -> float:
        now = time.monotonic()
        return min(max(0.0, self._cooldown_until[k] - now) for k in self._keys)

    def _advance(self) -> None:
        n = len(self._keys)
        for delta in range(1, n + 1):
            nxt = (self._idx + delta) % n
            if self._is_available(self._keys[nxt]):
                self._idx = nxt
                return

    def _find_next_available(self) -> Optional[str]:
        n = len(self._keys)
        for delta in range(1, n + 1):
            nxt = (self._idx + delta) % n
            if self._is_available(self._keys[nxt]):
                self._idx = nxt
                return self._keys[nxt]
        return None


def build_key_pool(script_dir: Optional[str] = None) -> Optional["AgnesKeyPool"]:
    """一步到位：加载配置并构建 AgnesKeyPool；没有任何 key 时返回 None。"""
    cfg = load_agnes_config(script_dir)
    if not cfg["keys"]:
        return None
    return AgnesKeyPool(keys=cfg["keys"], rotation=cfg["rotation"], cooldown=cfg["cooldown"])
