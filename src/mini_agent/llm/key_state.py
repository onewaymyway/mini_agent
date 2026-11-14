"""
llm/key_state.py — least_loaded 轮转策略的共享状态存储（支持跨进程）

每把 key 的运行时状态：
  leases       在途请求租约 {lease_id: 过期时间戳}
  last_used    最近一次被选中的时间戳（同负载时用于打散）
  cooldown_until / fail_count  冷却与失败计数

daemon、外部项目、LLMHelper 等调用方可能分属不同进程，因此状态写入
<主项目根>/.agent/llm_key_state.json，并用文件锁串行化读改写。
文件里只保存 key 的 SHA-256 摘要，不落明文 key。

租约带过期时间：进程崩溃或调用卡死时，租约会在 LEASE_TTL 后自动失效，
不会永久占用某把 key 的负载计数。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Optional, TypeVar

try:  # POSIX
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover
    fcntl = None
try:  # Windows
    import msvcrt  # type: ignore
except ImportError:  # pragma: no cover
    msvcrt = None

STATE_FILENAME = "llm_key_state.json"
LEASE_TTL = 900.0          # 租约最长有效期（秒），应大于单次 LLM 调用的最长耗时
PRUNE_AFTER = 86400.0      # 空闲超过该时长且无租约、无冷却的记录会被清理

_T = TypeVar("_T")


@dataclass(frozen=True)
class KeyLease:
    """一次 checkout 的结果：所用的 key 与租约 id（非 least_loaded 模式下为 None）。"""
    key: str
    lease_id: Optional[str] = None


def key_id(key: str) -> str:
    """key 的非可逆标识，用作状态文件中的键。"""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _empty_state() -> dict:
    return {"version": 1, "keys": {}}


def key_record(data: dict, kid: str) -> dict:
    return data["keys"].setdefault(
        kid,
        {"leases": {}, "last_used": 0.0, "cooldown_until": 0.0, "fail_count": 0},
    )


def prune_leases(rec: dict, now: float) -> None:
    rec["leases"] = {lid: exp for lid, exp in rec["leases"].items() if exp > now}


def gc_key_state(data: dict, now: float) -> None:
    for kid in list(data["keys"]):
        rec = data["keys"][kid]
        prune_leases(rec, now)
        if (
            not rec["leases"]
            and rec["cooldown_until"] <= now
            and now - rec["last_used"] > PRUNE_AFTER
        ):
            del data["keys"][kid]


def pick_least_loaded(
    data: dict,
    keys: list[str],
    now: float,
    exclude: Optional[str] = None,
) -> Optional[str]:
    """在未冷却的 key 中选在途数最少的；并列时选最久未使用的。无可用 key 返回 None。"""
    best: Optional[str] = None
    best_score: Optional[tuple] = None
    for k in keys:
        if k == exclude:
            continue
        rec = key_record(data, key_id(k))
        prune_leases(rec, now)
        if rec["cooldown_until"] > now:
            continue
        score = (len(rec["leases"]), rec["last_used"])
        if best_score is None or score < best_score:
            best, best_score = k, score
    return best


def checkout_least_loaded(data: dict, keys: list[str], now: float) -> tuple[str, str]:
    """登记一个租约并返回 (key, lease_id)。

    全部 key 都在冷却中时，仍选择冷却最快结束的那一把（与原有“总要试一把”的行为一致）。
    """
    chosen = pick_least_loaded(data, keys, now)
    if chosen is None:
        chosen = min(keys, key=lambda k: key_record(data, key_id(k))["cooldown_until"])
    rec = key_record(data, key_id(chosen))
    lease_id = uuid.uuid4().hex
    rec["leases"][lease_id] = now + LEASE_TTL
    rec["last_used"] = now
    return chosen, lease_id


# ─────────────────────────────────────────────────────────────────────────────
# 存储后端
# ─────────────────────────────────────────────────────────────────────────────

class KeyStateBackend:
    """对共享状态做一次原子的读-改-写。fn(data) 的返回值原样返回。"""

    def transact(self, fn: Callable[[dict], _T]) -> _T:
        raise NotImplementedError


class MemoryKeyState(KeyStateBackend):
    """进程内后端：无项目根、测试场景使用。"""

    def __init__(self) -> None:
        self._data = _empty_state()
        self._lock = threading.Lock()

    def transact(self, fn: Callable[[dict], _T]) -> _T:
        with self._lock:
            return fn(self._data)


def _lock_file(f) -> None:
    if fcntl is not None:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        return
    if msvcrt is not None:  # pragma: no cover - Windows
        f.seek(0)
        while True:
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                return
            except OSError:
                time.sleep(0.05)


def _unlock_file(f) -> None:
    if fcntl is not None:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        return
    if msvcrt is not None:  # pragma: no cover - Windows
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


class FileKeyState(KeyStateBackend):
    """文件后端：多进程共享。读改写期间持有独占文件锁。"""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock_path = self._path.with_name(self._path.name + ".lock")
        self._tlock = threading.Lock()

    def transact(self, fn: Callable[[dict], _T]) -> _T:
        with self._tlock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._lock_path, "a+b") as lf:
                _lock_file(lf)
                try:
                    data = self._read()
                    before = json.dumps(data, sort_keys=True)
                    result = fn(data)
                    if json.dumps(data, sort_keys=True) != before:
                        self._write(data)
                    return result
                finally:
                    _unlock_file(lf)

    def _read(self) -> dict:
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("keys"), dict):
                return data
        except (OSError, ValueError):
            pass
        return _empty_state()

    def _write(self, data: dict) -> None:
        tmp = self._path.with_name(f"{self._path.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, self._path)


_FILE_BACKENDS: dict[str, FileKeyState] = {}
_FILE_BACKENDS_LOCK = threading.Lock()


def get_file_backend(path: Path) -> FileKeyState:
    """同一路径复用同一个后端实例（同进程内线程锁共享）。"""
    key = str(Path(path).resolve())
    with _FILE_BACKENDS_LOCK:
        backend = _FILE_BACKENDS.get(key)
        if backend is None:
            backend = FileKeyState(Path(key))
            _FILE_BACKENDS[key] = backend
        return backend
