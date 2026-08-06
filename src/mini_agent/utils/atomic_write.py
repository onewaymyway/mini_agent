"""
utils/atomic_write.py — 通用原子写入工具（Windows 兼容）

提供带指数退避重试的原子写入功能，解决 Windows 上 os.replace
因文件被其他进程短暂锁定导致的 PermissionError (WinError 5) 问题。

所有需要原子写入的模块都应导入并使用本模块的函数，
避免在各处重复实现相同的重试逻辑。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Optional

from mini_agent.errors import log_exception


# Windows 上 os.replace 可能因文件被其他进程短暂锁定而失败 (WinError 5)
# 使用指数退避重试机制提高成功率
_ATOMIC_WRITE_MAX_RETRIES = 5
_ATOMIC_WRITE_BASE_DELAY = 0.05  # 50ms


def _atomic_replace_with_retry(tmp: str, path: Path) -> None:
    """带重试的原子替换（tmp -> path）。
    
    Windows 上会因文件被杀毒软件/编辑器/索引服务短暂锁定导致
    os.replace 失败 (PermissionError: WinError 5)，采用指数退避重试。
    
    Args:
        tmp: 临时文件路径
        path: 目标文件路径
    
    Raises:
        PermissionError: 重试耗尽后仍失败
        OSError: 其他文件系统错误
    """
    last_exc = None
    for attempt in range(_ATOMIC_WRITE_MAX_RETRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as exc:
            last_exc = exc
            if attempt < _ATOMIC_WRITE_MAX_RETRIES - 1:
                delay = _ATOMIC_WRITE_BASE_DELAY * (2 ** attempt)
                time.sleep(delay)
            else:
                # 最后一次尝试失败，清理临时文件并抛出
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                log_exception(exc, where="utils.atomic_write._atomic_replace_with_retry",
                              extra={"path": str(path), "attempts": _ATOMIC_WRITE_MAX_RETRIES})
                raise
        except OSError as exc:
            # 其他 OSError (如文件不存在等) 直接抛出
            try:
                os.unlink(tmp)
            except OSError:
                pass
            log_exception(exc, where="utils.atomic_write._atomic_replace_with_retry",
                          extra={"path": str(path)})
            raise
    # 理论上不会到达这里，但为了类型检查器满意
    if last_exc:
        raise last_exc


def _flock(f) -> None:
    """跨平台文件锁（尽力而为）。

    部分文件系统（典型如 Android Termux 的 FUSE/SD 卡挂载路径）不支持
    flock，调用会抛出 OSError(38)（ENOSYS - Function not implemented），
    而不是 ImportError（fcntl 模块本身是存在的，只是该文件系统不支持这个
    系统调用）。这里必须把 OSError 也纳入捕获范围，否则异常会向上传播到
    atomic_write_json，导致文件写入整体失败、session 无法持久化——
    而文件锁定本身只是"尽力而为"，锁不上不应该阻断核心写入逻辑。
    """
    try:
        import fcntl
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        return
    except (ImportError, OSError):
        pass
    try:
        import msvcrt
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 512)
    except Exception:
        pass


def atomic_write_text(path: Path, text: str, *, flock: bool = False) -> None:
    """原子写入文本文件（tmp + rename），避免读端看到半截内容。
    
    Windows 上会因文件被杀毒软件/编辑器/索引服务短暂锁定导致
    os.replace 失败 (PermissionError: WinError 5)，采用指数退避重试。
    
    Args:
        path: 目标文件路径
        text: 要写入的文本内容
        flock: 是否在写入时加文件锁（默认 False，session.py 需要开启）
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            if flock:
                _flock(f)
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
    except Exception as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        log_exception(exc, where="utils.atomic_write.atomic_write_text",
                      extra={"path": str(path)})
        raise
    
    _atomic_replace_with_retry(tmp, path)


def atomic_write_json(path: Path, data: Any, *, flock: bool = False) -> None:
    """原子写入 JSON 文件（tmp + rename），避免读端看到半截 JSON。
    
    Windows 上会因文件被杀毒软件/编辑器/索引服务短暂锁定导致
    os.replace 失败 (PermissionError: WinError 5)，采用指数退避重试。
    
    Args:
        path: 目标文件路径
        data: 要序列化写入的数据（需可 JSON 序列化）
        flock: 是否在写入时加文件锁（默认 False，session.py 需要开启）
    """
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2), flock=flock)


def atomic_write_jsonl(path: Path, records: list[dict]) -> None:
    """原子写入 JSONL 文件（每行一个 JSON 对象）。
    
    Args:
        path: 目标文件路径
        records: 字典列表，每个字典将写为一行 JSON
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".jsonl.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        log_exception(exc, where="utils.atomic_write.atomic_write_jsonl",
                      extra={"path": str(path), "record_count": len(records)})
        raise
    
    _atomic_replace_with_retry(tmp, path)


def atomic_append_jsonl(path: Path, record: dict) -> None:
    """原子追加一行 JSONL——先读已有内容+新行一起写临时文件再 replace。
    
    单机场景下 append 本身已经是安全的，这里额外走一次 tmp+replace
    只是为了在同一目录下与其它落盘路径保持一致的失败语义
    （写一半崩溃不会破坏已有内容）。
    
    Args:
        path: 目标文件路径
        record: 要追加的字典记录
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8")
        except OSError as exc:
            log_exception(exc, where="utils.atomic_write.atomic_append_jsonl.read_existing",
                          extra={"path": str(path)})
            existing = ""
    line = json.dumps(record, ensure_ascii=False)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(existing)
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        log_exception(exc, where="utils.atomic_write.atomic_append_jsonl",
                      extra={"path": str(path)})
        raise
    
    _atomic_replace_with_retry(tmp, path)
