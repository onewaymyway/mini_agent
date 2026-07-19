"""
perception/system_events.py — 跨子系统事件总线（记忆 / 自我进化 / 具身感知之间的信号桥接）

设计背景（见 next_doc 讨论记录，本模块是"事件总线"讨论的落地）：

现有代码里已经有多处"某模块写一个状态文件，另一个模块按自己的节奏去读"的信号
桥接模式（proprioception_snapshot.json → ResourceArbiter._check_frustration()、
rhythm.json → consolidation.rhythm_is_allowed()、self_maintenance_state.json →
should_run_self_maintenance()）。这些都是"覆盖写、只存最近一次状态"的**快照**模式，
适合"我想知道当前是什么状态"，但不适合"我想知道刚刚发生了什么事、不想错过"——
后者需要**日志**语义（追加写、每条独立、可被多个消费者各自按自己的进度读取）。

本模块补的就是这条日志语义的通用基础设施，供后续任意"A 模块的状态变化需要
通知 B 模块"场景复用，不用每次都新造一个状态文件 + 手写轮询判断。

三条硬约束（都是讨论中明确要求的，不是可选项）：

1. **文件优先，内存不是事实来源**。publish() 必须先落盘成功才算发布成功；
   内存队列（如果调用方选择用）只是同进程内的低延迟缓存，丢了不影响正确性，
   因为任何消费者都可以从 events.jsonl 里按游标重新读到。

2. **不同事件允许不同的响应粒度，由事件自己声明，不是消费者猜**。`tier` 字段
   显式标注该事件期望被多快处理："instant"（应该在下一次 0.5s 主循环里被看到）、
   "tick"（下一次 AutonomousLoop.tick()，默认 60s，看到即可）、"cron"（下一次
   日级/小时级周期任务看到即可，典型是给人看的汇总类事件）。本模块不负责调度
   ——它只负责让"消费者在自己已有的调度节拍里查一下有没有新事件"这件事变得
   便宜、统一、不用每个消费者各写一遍游标管理代码。

3. **不新增线程、不做真正的进程间推送**。整个代码库的调度风格是"轮询 + 状态
   文件"（dequeue(timeout=0.5)、should_tick()、rhythm_is_allowed() 全是这个
   模式），本模块延续这个风格：所谓"即时"，实际是"下一次已经存在的 0.5s
   循环体顺带查一下"，不是异步中断。这样接入成本低、行为可预测、不引入新的
   并发复杂度。

Windows 兼容性说明：goal_backlog.py 目前对无 fcntl 平台（Windows）是"不加锁、
尽力而为"。events.jsonl 是高频追加场景（每次 frustration 边沿、每次 outcome
判定都可能写），比 goal_backlog 更容易撞上并发交错，因此这里没有沿用"Windows
就不锁"的旧模式，而是用 msvcrt.locking 补全了 Windows 分支。
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths

try:
    import fcntl  # POSIX only（Linux / macOS）
    _PLATFORM_LOCK = "posix"
except ImportError:  # Windows 等无 fcntl 平台
    fcntl = None  # type: ignore
    try:
        import msvcrt
        _PLATFORM_LOCK = "windows"
    except ImportError:  # pragma: no cover - 理论上不会出现（非 POSIX 非 Windows）
        msvcrt = None  # type: ignore
        _PLATFORM_LOCK = "none"


# 合法 tier 取值，写死枚举而不是自由字符串，避免消费者拿到拼写不一致的值。
VALID_TIERS = frozenset({"instant", "tick", "cron"})

# 单文件超过这个大小就在下次 publish 时触发滚动归档，避免无限增长。
_ROTATE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB


# ── 跨平台文件锁（供 append 使用；读操作不加锁，允许读到"稍旧一点"的内容） ──

class _LockedFile:
    """`with _LockedFile(path) as f:` 独占锁定后再写入，跨进程/跨平台安全。
    只用于写路径（publish / rotate），读路径不加锁——jsonl 追加写在两大平台上
    对"已写入的完整行"都不会产生读到半行的问题，读不加锁换取更低的读取成本。"""

    def __init__(self, path: Path):
        self._path = path
        self._f = None

    def __enter__(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(self._path, "a", encoding="utf-8")
        if _PLATFORM_LOCK == "posix":
            fcntl.flock(self._f.fileno(), fcntl.LOCK_EX)
        elif _PLATFORM_LOCK == "windows":
            # msvcrt.locking 锁的是"从当前文件指针开始的 N 字节"，对追加写场景
            # 用一个固定的 1 字节哨兵锁在文件开头即可实现互斥，不依赖文件长度。
            self._f.seek(0)
            _msvcrt_lock_retry(self._f)
        # _PLATFORM_LOCK == "none"：无法加锁，尽力而为（理论上不会走到这个分支）
        return self._f

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


def _msvcrt_lock_retry(f, retries: int = 20, delay: float = 0.05) -> None:
    """msvcrt.locking 拿不到锁时抛 OSError 而不是阻塞等待，需要自己重试。"""
    last_exc: Optional[OSError] = None
    for _ in range(retries):
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            return
        except OSError as exc:
            last_exc = exc
            time.sleep(delay)
    # 重试耗尽：放弃加锁，尽力而为写入（宁可偶尔交错，也不无限阻塞主循环）
    if last_exc is not None:
        pass


# ── 事件数据结构 ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SystemEvent:
    """一条跨子系统事件。event_type 建议用 "领域.具体信号" 的命名（点分两段），
    比如 "proprioception.frustration_spike"、"memory.sparse_region_detected"，
    第一段用于后续按领域过滤/归档，第二段是具体信号名。"""

    event_id: str
    ts: float
    source: str          # 产生者标识，如 "session:xxxx" / "consolidation" / "outcome_tracker"
    event_type: str
    tier: str            # "instant" | "tick" | "cron"
    payload: dict = field(default_factory=dict)

    def to_json_line(self) -> str:
        return json.dumps(
            {
                "event_id": self.event_id,
                "ts": self.ts,
                "source": self.source,
                "event_type": self.event_type,
                "tier": self.tier,
                "payload": self.payload,
            },
            ensure_ascii=False,
        )

    @staticmethod
    def from_dict(d: dict) -> "SystemEvent":
        return SystemEvent(
            event_id=d.get("event_id", ""),
            ts=float(d.get("ts", 0.0)),
            source=d.get("source", ""),
            event_type=d.get("event_type", ""),
            tier=d.get("tier", "tick"),
            payload=d.get("payload") or {},
        )


# ── 发布 ──────────────────────────────────────────────────────────────────

def publish(
    paths: "AgentPaths",
    *,
    source: str,
    event_type: str,
    tier: str,
    payload: Optional[dict] = None,
) -> SystemEvent:
    """发布一条事件：追加写入 events.jsonl。这是唯一的"发布成功"判据——
    没有额外的内存广播步骤，消费者统一走 poll_since() 从文件读。

    调用方应当在"状态边沿"而非"每次采样"时调用（比如 frustration 越过阈值
    的那一次，而不是每个 turn 都调用），避免事件日志被高频采样信号刷屏。
    这个去重/边沿判断由调用方负责（不同信号源的"有意义变化"定义不同，
    本模块不替调用方做假设）。
    """
    if tier not in VALID_TIERS:
        raise ValueError(f"非法 tier: {tier!r}，必须是 {sorted(VALID_TIERS)} 之一")

    evt = SystemEvent(
        event_id=secrets.token_hex(4),
        ts=time.time(),
        source=source,
        event_type=event_type,
        tier=tier,
        payload=payload or {},
    )

    events_path = paths.system_events
    try:
        _maybe_rotate(paths)
        with _LockedFile(events_path) as f:
            f.write(evt.to_json_line() + "\n")
    except Exception as _mini_agent_exc:
        # 发布失败不应该拖垮调用方的主流程（proprioception 写入、outcome 判定
        # 等都是"顺带产出事件"，不是这些流程本身的核心职责）。调用方如果需要
        # 感知发布失败，可以自行 try/except 包一层再调用 publish()。
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.system_events.publish')
        pass
    return evt


def _maybe_rotate(paths: "AgentPaths") -> None:
    """events.jsonl 超过阈值大小时，整体挪到归档目录（按当天日期命名），
    主文件清空重新开始。归档文件只在 /diagnostics、/digest 之类人工查看
    场景里按需读取，不参与游标轮询。"""
    events_path = paths.system_events
    try:
        if not events_path.exists() or events_path.stat().st_size < _ROTATE_SIZE_BYTES:
            return
        archive_dir = paths.system_events_archive_dir
        archive_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d_%H%M%S")
        archive_path = archive_dir / f"events_{stamp}.jsonl"
        with _LockedFile(events_path):
            # 锁住主文件期间做 rename，避免和正在写入的另一个进程交错。
            # rename 是同一文件系统内的原子操作（POSIX 和 Windows 均如此）。
            events_path.replace(archive_path)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.system_events._maybe_rotate')
        pass  # 归档失败不影响主流程，下次 publish 时会再尝试


# ── 消费（按游标读取） ─────────────────────────────────────────────────────

def _cursor_path(paths: "AgentPaths", consumer_name: str) -> Path:
    return paths.system_events_cursors_dir / f"{consumer_name}.json"


def _load_cursor(paths: "AgentPaths", consumer_name: str) -> dict:
    p = _cursor_path(paths, consumer_name)
    if not p.exists():
        return {"last_event_id": "", "last_ts": 0.0}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.system_events._load_cursor')
        return {"last_event_id": "", "last_ts": 0.0}


def _save_cursor(paths: "AgentPaths", consumer_name: str, evt: SystemEvent) -> None:
    p = _cursor_path(paths, consumer_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(
            json.dumps({"last_event_id": evt.event_id, "last_ts": evt.ts}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.system_events._save_cursor')
        pass


def poll_since(
    paths: "AgentPaths",
    *,
    consumer_name: str,
    tiers: Optional[list[str]] = None,
    event_types: Optional[list[str]] = None,
    advance_cursor: bool = True,
) -> list[SystemEvent]:
    """消费者按自己的调度节拍调用（即时层消费者放在 0.5s 主循环里、节拍层放在
    tick() 里、周期层放在 cron/digest 里），读取自上次游标之后的新事件。

    consumer_name 用于隔离不同消费者各自的游标，同一个消费者多次调用会自动
    推进游标（除非显式传 advance_cursor=False，用于"只想看看有没有、暂不消费"
    的场景，比如 /diagnostics 命令查看最新事件不应该影响真实消费者的进度）。

    tiers / event_types 均为可选过滤，不传则返回该消费者游标之后的全部事件——
    消费者自己决定要不要过滤，模块本身不强制"一个消费者只能订阅一种 tier"。
    """
    events_path = paths.system_events
    if not events_path.exists():
        return []

    cursor = _load_cursor(paths, consumer_name)
    last_ts = float(cursor.get("last_ts", 0.0))
    last_id = cursor.get("last_event_id", "")

    result: list[SystemEvent] = []
    try:
        with open(events_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.perception.system_events.poll_since')
                    continue
                evt = SystemEvent.from_dict(d)
                # ts 相同时用 event_id 兜底去重（同一时刻可能有多条事件）
                if evt.ts < last_ts or (evt.ts == last_ts and evt.event_id == last_id):
                    continue
                if tiers is not None and evt.tier not in tiers:
                    continue
                if event_types is not None and evt.event_type not in event_types:
                    continue
                result.append(evt)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.system_events.poll_since')
        return []

    if advance_cursor and result:
        _save_cursor(paths, consumer_name, result[-1])

    return result
