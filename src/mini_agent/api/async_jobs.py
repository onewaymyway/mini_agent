"""
async_jobs.py — 看板"点击 → 后台跑 → 轮询拿结果"的通用异步任务机制。

背景见 next_doc/kanban_async_job_mechanism_plan.md。核心问题：看板上凡是涉及
LLM 调用的按钮（生成执行规范草稿 / 补充意见重新生成 / 手动重判整体关闭 /
成长顾问扫描 / 候选采纳 / 候选报告刷新……），过去都是"HTTP handler 里同步调用
LLM，前端设一个超时时间等结果"——这个模式治标不治本：
  1. LLM/Agent 探索路径耗时是分钟级的，任何固定超时早晚会不够用；
  2. 更严重的是，`async def` 路由里直接同步调用会整段阻塞事件循环，拖垮
     daemon 对其它请求的响应能力（`blocking_guard.run_blocking` 解决了"卡住
     事件循环"这半个问题，但它内部的 `asyncio.wait_for` 超时之后请求仍然会
     失败，只是不再堵事件循环，用户体验上还是"点了按钮等一会儿失败"）。

这个模块提供的是"提交后立即返回 job_id，任务在后台跑到底，前端轮询状态"这一层：
- `AsyncJobRegistry.start(fn, *args, key=..., **kwargs)`：把同步函数 `fn` 丢进
  `asyncio.to_thread` 后台执行，立即返回 job_id，不阻塞这次 HTTP 响应，也不用
  `run_blocking` 那种超时截断（后台任务允许跑到底，无论多久）。
- 任务状态落盘到 `paths.async_job_record(job_id)`，同时维护一个按业务 `key`
  （比如 `"execution_spec_generate:{goal_id}"`）指向"最近一次 job_id"的指针
  文件，这样即使用户刷新了整个看板页面、丢了 `st.session_state`，重新打开时
  依然能通过 key 查到"上一次这个操作是不是还在跑/已经跑完/失败了"，而不是
  "看不到结果就以为丢了"——任务本身从不因为前端有没有在看而受影响。
- 已完成的任务记录不会无限堆积：`start()` 每次调用时顺带清理超过
  `RETENTION_SECONDS` 的旧记录（best-effort，不引入额外的后台线程/定时器）。

所有新增的 LLM/长耗时调用点都应该走这套机制，不要再手写"同步调用 + 前端猜
一个超时时间"的端点。用法示例见 routes.py 里 `generate_goal_execution_spec`
等 6 个改造过的端点。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from mini_agent.storage.paths import AgentPaths

# 已完成/失败的任务记录（内存缓存 + 磁盘文件）保留多久，超过的在下次 start() 时
# 顺手清理掉，避免 async_jobs/ 目录无限增长。仍在跑的任务不受影响。
RETENTION_SECONDS = 3600.0


def _now() -> float:
    return time.time()


@dataclass
class _JobRecord:
    job_id: str
    key: Optional[str] = None
    status: str = "running"  # running | done | error
    started_at: float = field(default_factory=_now)
    finished_at: Optional[float] = None
    result: Any = None
    error: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "key": self.key,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error": self.error,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "_JobRecord":
        return cls(
            job_id=d["job_id"], key=d.get("key"), status=d.get("status", "running"),
            started_at=d.get("started_at", 0.0), finished_at=d.get("finished_at"),
            result=d.get("result"), error=d.get("error"), meta=d.get("meta") or {},
        )


class AsyncJobRegistry:
    """挂在 `app.state.async_jobs` 上的进程内单例，daemon 生命周期内常驻。

    内存态是权威来源（读写都优先走内存），磁盘落盘只是为了"进程重启/前端
    session 丢失后仍能查到最近状态"——所以磁盘写入失败不影响任务本身的执行
    结果（best-effort，异常吞掉记日志，不向上抛）。
    """

    def __init__(self, paths: AgentPaths) -> None:
        self._paths = paths
        self._lock = threading.Lock()
        self._jobs: Dict[str, _JobRecord] = {}

    # ── 提交任务 ──────────────────────────────────────────────────────────

    def start(
        self,
        fn: Callable[..., Any],
        *args: Any,
        key: Optional[str] = None,
        meta: Optional[dict] = None,
        **kwargs: Any,
    ) -> str:
        """把同步函数 `fn(*args, **kwargs)` 丢进后台线程跑，立即返回 job_id。

        `key` 是业务操作的稳定标识（如 `"execution_spec_generate:{goal_id}"`），
        用于 `get_latest_by_key()` 找回"这个操作最近一次任务的状态"——不传的话
        仍然可以正常跑，只是没法通过 key 查，只能凭 job_id 本身查。
        """
        self._gc_expired()
        job_id = uuid.uuid4().hex
        record = _JobRecord(job_id=job_id, key=key, meta=meta or {})
        with self._lock:
            self._jobs[job_id] = record
        self._persist(record)
        if key:
            self._write_latest_pointer(key, job_id)

        async def _run() -> None:
            try:
                result = await asyncio.to_thread(fn, *args, **kwargs)
            except Exception as e:  # noqa: BLE001 — 故意捕获全部异常，转成任务状态而不是让后台任务默默丢失
                from mini_agent.errors import log_exception
                log_exception(e, where=f"mini_agent.api.async_jobs.AsyncJobRegistry.run.{key or 'unkeyed'}")
                with self._lock:
                    rec = self._jobs.get(job_id)
                    if rec is not None:
                        rec.status = "error"
                        rec.error = str(e)
                        rec.finished_at = _now()
                        self._persist(rec)
            else:
                with self._lock:
                    rec = self._jobs.get(job_id)
                    if rec is not None:
                        rec.status = "done"
                        rec.result = result
                        rec.finished_at = _now()
                        self._persist(rec)

        # 用 create_task 而不是 await：调用方（HTTP 路由）要立即拿到 job_id
        # 返回给前端，任务本身在事件循环里继续跑（`asyncio.to_thread` 部分在
        # 线程池，不阻塞事件循环）。
        asyncio.create_task(_run())
        return job_id

    # ── 查询 ──────────────────────────────────────────────────────────────

    def get(self, job_id: str) -> Optional[dict]:
        with self._lock:
            rec = self._jobs.get(job_id)
        if rec is not None:
            return rec.to_dict()
        # 内存里没有（比如 daemon 重启过），尝试从磁盘找回。
        loaded = self._load_from_disk(job_id)
        if loaded is not None:
            with self._lock:
                self._jobs.setdefault(job_id, loaded)
            return loaded.to_dict()
        return None

    def get_latest_by_key(self, key: str) -> Optional[dict]:
        """给前端"重新打开看板/丢了 session_state 之后，找回上一次这个操作
        跑到哪了"用。没有任何历史记录时返回 None（不是错误，是合法的初始态）。
        """
        pointer_path = self._paths.async_job_latest_pointer(key)
        try:
            if not pointer_path.exists():
                return None
            data = json.loads(pointer_path.read_text(encoding="utf-8"))
            job_id = data.get("job_id")
        except Exception:
            return None
        if not job_id:
            return None
        return self.get(job_id)

    # ── 内部：落盘 / 清理 ─────────────────────────────────────────────────

    def _persist(self, record: _JobRecord) -> None:
        try:
            path = self._paths.async_job_record(record.job_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(record.to_dict(), ensure_ascii=False, default=str), encoding="utf-8")
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where="mini_agent.api.async_jobs.AsyncJobRegistry._persist")

    def _write_latest_pointer(self, key: str, job_id: str) -> None:
        try:
            path = self._paths.async_job_latest_pointer(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"key": key, "job_id": job_id}, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where="mini_agent.api.async_jobs.AsyncJobRegistry._write_latest_pointer")

    def _load_from_disk(self, job_id: str) -> Optional[_JobRecord]:
        try:
            path = self._paths.async_job_record(job_id)
            if not path.exists():
                return None
            return _JobRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return None

    def _gc_expired(self) -> None:
        """best-effort 清理超过 RETENTION_SECONDS 的已完成/失败任务记录
        （内存 + 磁盘）。只在 start() 时顺带跑一次，不引入额外的后台线程。"""
        cutoff = _now() - RETENTION_SECONDS
        with self._lock:
            expired = [
                jid for jid, rec in self._jobs.items()
                if rec.status != "running" and rec.finished_at and rec.finished_at < cutoff
            ]
            for jid in expired:
                self._jobs.pop(jid, None)
        for jid in expired:
            try:
                self._paths.async_job_record(jid).unlink(missing_ok=True)
            except Exception:
                pass
