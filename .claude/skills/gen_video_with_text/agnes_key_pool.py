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


# 明确"重试没有意义，必须先由 Agent 修好配置再重跑"的一类错误：请求参数
# 错误 / 鉴权失败 / 资源不存在 / 内容被拒绝。这些状态码在非限流场景下，
# 同一个请求重试多少次结果都一样（比如 mode 参数写错了，不会因为多试几次
# 就变成合法值），必须立即停止重试，把结构化错误交回调用方处理，而不是
# 陪着走满 max_retries。
_NON_RETRYABLE_STATUS_CODES = (400, 401, 403, 404, 422)

# 账号级限制：服务端返回 403 并提示“账号异常 / 访问受限”。这不是参数或配置错误，
# 而是这一把 key（这个账号）被限制，换一把 key 通常可以继续。
_KEY_RESTRICTED_KEYWORDS = (
    "unusual activity",
    "temporarily restricted",
    "access has been temporarily restricted",
    "账号异常",
    "访问受限",
    "账户异常",
)
# 受限的 key 冷却时间（秒）。受限通常持续较久，冷却期内不会再被选中。
RESTRICTED_COOLDOWN_SEC = 3600.0


def is_key_restricted_error(status_code: Optional[int], text: str = "") -> bool:
    """判断一次失败是否属于“这把 key 所在账号被限制”（应切换 key 重试）。"""
    if status_code not in (403, None):
        return False
    lowered = (text or "").lower()
    return any(kw in lowered for kw in _KEY_RESTRICTED_KEYWORDS)


def is_non_retryable_error(status_code: Optional[int], text: str = "") -> bool:
    """判断一次请求失败是否属于"重试无意义，需要先修配置"的错误。

    注意：先排除限流错误（限流也可能带 429 之外的状态码文案，但限流本身
    应该走换 key 重试，不应该被这里误判为不可重试）；账号受限同理，应换 key。
    """
    if is_rate_limit_error(status_code, text) or is_key_restricted_error(status_code, text):
        return False
    return status_code in _NON_RETRYABLE_STATUS_CODES


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
            return {"keys": keys, "rotation": rotation, "cooldown": cooldown,
                    "source": "环境变量 AGNES_API_KEYS", "path": None}

    # 2. providers.json：AGNES_PROVIDERS_JSON 指定的文件优先；否则从 script_dir 向上查找
    explicit = os.environ.get("AGNES_PROVIDERS_JSON", "").strip()
    start = Path(script_dir) if script_dir else Path(__file__).parent
    providers_path = Path(explicit).expanduser() if explicit else _find_providers_json(start)
    if providers_path and providers_path.is_file():
        try:
            data = json.loads(providers_path.read_text(encoding="utf-8"))
            agnes_block = _extract_agnes_block(data)
            if agnes_block.get("api_keys"):
                return {
                    "keys": agnes_block["api_keys"],
                    "rotation": agnes_block.get("key_rotation") or rotation,
                    "cooldown": float(agnes_block.get("key_cooldown") or cooldown),
                    "source": "AGNES_PROVIDERS_JSON 指定的 providers.json" if explicit else "providers.json",
                    "path": str(providers_path),
                }
        except Exception:
            pass  # providers.json 格式异常时不影响后续兜底

    # 3. 单个 AGNES_API_KEY（向后兼容）
    single = os.environ.get("AGNES_API_KEY")
    return {"keys": [single] if single else [], "rotation": rotation, "cooldown": cooldown,
            "source": "环境变量 AGNES_API_KEY" if single else "未找到", "path": None}


def mask_key(key: Optional[str]) -> str:
    """日志用的脱敏 key：只保留前 4 位和后 4 位，例如 sk-a…9f2c。完整 key 不进入日志。"""
    if not key:
        return "-"
    return key if len(key) <= 10 else f"{key[:4]}…{key[-4:]}"


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

    def current_key(self) -> str:
        """读取当前指针指向的 key，不推进轮转（仅用于初始化/展示，真正发请求前要用 acquire）。"""
        return self._keys[self._idx]

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
        return self._find_next_available(after=key)

    def __len__(self) -> int:
        return len(self._keys)

    def index_of(self, key: Optional[str]) -> Optional[int]:
        """key 在池中的编号（从 1 开始），仅用于日志。"""
        return self._keys.index(key) + 1 if key in self._keys else None

    def describe(self, key: Optional[str]) -> str:
        """日志用描述：编号 + 脱敏 key，例如 #2 sk-a…9f2c。"""
        idx = self.index_of(key)
        return f"#{idx} {mask_key(key)}" if idx else mask_key(key)

    def cooldown_left(self, key: str) -> float:
        """该 key 还需冷却多少秒（0 表示可用）。"""
        return max(0.0, self._cooldown_until.get(key, 0.0) - time.monotonic())

    def config_summary(self) -> str:
        """配置来源与 key 数量（不含完整 key），例如：providers.json（/path/providers.json），共 3 把。"""
        src = getattr(self, "source", "未知")
        path = getattr(self, "source_path", None)
        where = f"{src}（{path}）" if path else src
        return f"key 配置来源：{where}，共 {len(self._keys)} 把"

    def status_lines(self) -> List[str]:
        """全部 key 的状态（脱敏），用于全部受限时的提示。"""
        out = []
        for k in self._keys:
            left = self.cooldown_left(k)
            state = f"冷却中，约 {int(left // 60)} 分钟后可用" if left > 0 else "可用"
            out.append(f"{self.describe(k)} {state}")
        return out

    def report_restricted(self, key: str) -> Optional[str]:
        """报告 key 所在账号受限：长冷却该 key 并切换到下一把可用 key（没有则返回 None）。"""
        if key in self._cooldown_until:
            self._cooldown_until[key] = time.monotonic() + RESTRICTED_COOLDOWN_SEC
        return self._find_next_available(after=key)

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

    def _find_next_available(self, after: Optional[str] = None) -> Optional[str]:
        """从 after（失败的 key）所在位置往后找第一把可用 key；after 为 None 时从当前指针开始。
        失败的 key 自己已经被冷却，所以不会被选回来。"""
        n = len(self._keys)
        base = self._keys.index(after) if after in self._keys else self._idx
        for delta in range(1, n + 1):
            nxt = (base + delta) % n
            if self._is_available(self._keys[nxt]):
                self._idx = nxt
                return self._keys[nxt]
        return None


def build_key_pool(script_dir: Optional[str] = None) -> Optional["AgnesKeyPool"]:
    """一步到位：加载配置并构建 AgnesKeyPool；没有任何 key 时返回 None。"""
    cfg = load_agnes_config(script_dir)
    if not cfg["keys"]:
        return None
    pool = AgnesKeyPool(keys=cfg["keys"], rotation=cfg["rotation"], cooldown=cfg["cooldown"])
    pool.source = cfg.get("source") or "未知"
    pool.source_path = cfg.get("path")
    return pool
