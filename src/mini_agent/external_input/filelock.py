"""external_input/filelock.py — 跨平台文件独占锁（P2/P3 新增）。

背景（评审记录，见 next_doc/watchlist_notification_goal_design.md §9.1 #1）：
  pending_hits.jsonl 会被 WatchlistMatcher（追加写）和 N 个
  sys:watchlist_report_<tier> cron job（读取整个文件 → 标记 consumed →
  整体重写）并发访问。如果两者时间上重叠，"读整个文件 → 改 → 整体覆盖写"
  会把并发写入的新记录连带丢掉。

这里提供一个跟 perception/system_events.py::_LockedFile 同款风格的独占锁，
但泛化成"锁住这个路径对应的一把互斥锁文件，在 with 块内做任意读写"，
而不是只服务于追加写场景——report_tiers 消费 pending_hits 时需要的是
"读 + 改 + 整体重写"这个复合操作的互斥性，不是单次 open(mode="a")。

实现上锁在一个独立的 `<path>.lock` 哨兵文件上（不是锁数据文件本身），
这样"读 + 改 + 覆盖写数据文件"整个过程都在锁的保护窗口内，不受
"覆盖写会创建新文件描述符导致锁失效"这类细节影响。
"""

from __future__ import annotations

from pathlib import Path

try:
    import fcntl  # POSIX only（Linux / macOS）
    _PLATFORM_LOCK = "posix"
except ImportError:  # Windows 等无 fcntl 平台
    fcntl = None  # type: ignore
    try:
        import msvcrt
        _PLATFORM_LOCK = "windows"
    except ImportError:  # pragma: no cover
        msvcrt = None  # type: ignore
        _PLATFORM_LOCK = "none"


class ExclusiveFileLock:
    """`with ExclusiveFileLock(data_path):` 期间独占持有 `<data_path>.lock`，
    跨进程/跨平台互斥。用于保护"读整个 jsonl → 改 → 整体重写"这类复合操作，
    不只是单次 append。锁文件本身内容无意义，只借助操作系统文件锁语义。"""

    def __init__(self, data_path: Path):
        self._lock_path = data_path.parent / (data_path.name + ".lock")
        self._f = None

    def __enter__(self):
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(self._lock_path, "a+")
        if _PLATFORM_LOCK == "posix":
            fcntl.flock(self._f.fileno(), fcntl.LOCK_EX)
        elif _PLATFORM_LOCK == "windows":
            self._f.seek(0)
            _msvcrt_lock_retry(self._f)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if _PLATFORM_LOCK == "posix":
                fcntl.flock(self._f.fileno(), fcntl.LOCK_UN)
            elif _PLATFORM_LOCK == "windows":
                self._f.seek(0)
                try:
                    msvcrt.locking(self._f.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        finally:
            self._f.close()
        return False


def _msvcrt_lock_retry(f, retries: int = 50, delay: float = 0.05) -> None:
    import time
    last_exc: Optional[OSError] = None  # type: ignore[name-defined]
    for _ in range(retries):
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            return
        except OSError as exc:
            last_exc = exc
            time.sleep(delay)
    if last_exc is not None:
        raise last_exc
