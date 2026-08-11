"""
CrawlScheduler - 通用爬虫调度器

统一管理爬取任务：URL队列管理、并发控制、去重、重试、回调通知。
支持同步和异步两种模式，可与browser-cdp的CDP客户端集成。
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from .url_dedup import UrlDedupManager, UrlNormalizer
from .request_client import SyncRequestClient, AsyncRequestClient, RequestConfig, HttpResponse

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"  # 已去重跳过
    RETRYING = "retrying"


@dataclass
class CrawlTask:
    """爬取任务"""
    url: str
    task_id: str = ""
    priority: int = 0
    max_retries: int = 3
    current_retry: int = 0
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    started_at: float = 0.0
    completed_at: float = 0.0
    duration_ms: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.task_id:
            self.task_id = f"task_{int(time.time() * 1000)}_{hash(self.url) % 10000}"

    @property
    def is_done(self) -> bool:
        return self.status in (TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.SKIPPED)

    @property
    def can_retry(self) -> bool:
        return self.current_retry < self.max_retries and self.status == TaskStatus.FAILED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "url": self.url,
            "priority": self.priority,
            "status": self.status.value,
            "current_retry": self.current_retry,
            "max_retries": self.max_retries,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "metadata": self.metadata,
        }


class CrawlCallback:
    """爬取回调处理器"""

    def __init__(self):
        self._on_start: List[Callable] = []
        self._on_success: List[Callable] = []
        self._on_failure: List[Callable] = []
        self._on_complete: List[Callable] = []

    def on_start(self, fn: Callable) -> None:
        self._on_start.append(fn)

    def on_success(self, fn: Callable) -> None:
        self._on_success.append(fn)

    def on_failure(self, fn: Callable) -> None:
        self._on_failure.append(fn)

    def on_complete(self, fn: Callable) -> None:
        self._on_complete.append(fn)

    def fire_start(self, task: CrawlTask) -> None:
        for fn in self._on_start:
            try:
                fn(task)
            except Exception as e:
                logger.warning(f"on_start回调异常: {e}")

    def fire_success(self, task: CrawlTask, result: Any) -> None:
        for fn in self._on_success:
            try:
                fn(task, result)
            except Exception as e:
                logger.warning(f"on_success回调异常: {e}")

    def fire_failure(self, task: CrawlTask, error: str) -> None:
        for fn in self._on_failure:
            try:
                fn(task, error)
            except Exception as e:
                logger.warning(f"on_failure回调异常: {e}")

    def fire_complete(self, task: CrawlTask) -> None:
        for fn in self._on_complete:
            try:
                fn(task)
            except Exception as e:
                logger.warning(f"on_complete回调异常: {e}")


class PriorityQueue:
    """基于堆的优先级队列"""

    def __init__(self):
        self._heap: List[Tuple[int, float, str, CrawlTask]] = []
        self._counter = 0

    def push(self, task: CrawlTask) -> None:
        import heapq
        # 优先级取反（数值越大越优先），同时用时间戳作次要排序
        heapq.heappush(self._heap, (-task.priority, self._counter, task.task_id, task))
        self._counter += 1

    def pop(self) -> Optional[CrawlTask]:
        import heapq
        if not self._heap:
            return None
        _, _, _, task = heapq.heappop(self._heap)
        return task

    def peek(self) -> Optional[CrawlTask]:
        import heapq
        if not self._heap:
            return None
        return self._heap[0][3]

    def __len__(self) -> int:
        return len(self._heap)

    def is_empty(self) -> bool:
        return len(self._heap) == 0


class CrawlScheduler:
    """通用爬虫调度器"""

    def __init__(
        self,
        concurrency: int = 5,
        rate_limit_interval: float = 0.5,
        default_timeout: float = 30.0,
        default_max_retries: int = 3,
        dedup_storage_path: Optional[str] = None,
        dedup_retention_days: int = 7,
        auto_cleanup: bool = True,
    ):
        self.concurrency = concurrency
        self.default_timeout = default_timeout
        self.default_max_retries = default_max_retries
        self.auto_cleanup = auto_cleanup

        # 去重管理器
        self.dedup_manager = UrlDedupManager(
            storage_path=dedup_storage_path,
            retention_days=dedup_retention_days,
        )

        # 请求客户端（懒加载）
        self._sync_client: Optional[SyncRequestClient] = None
        self._async_client: Optional[AsyncRequestClient] = None

        # 任务队列
        self._task_queue = PriorityQueue()
        self._running_tasks: Dict[str, CrawlTask] = {}
        self._completed_tasks: Dict[str, CrawlTask] = {}
        self._task_lock = asyncio.Lock() if asyncio.get_event_loop().is_running() else None

        # 回调
        self.callbacks = CrawlCallback()

        # 统计
        self._stats = {
            "total_enqueued": 0,
            "total_started": 0,
            "total_success": 0,
            "total_failed": 0,
            "total_skipped": 0,
            "total_retries": 0,
            "total_time_ms": 0.0,
        }

        # 异步控制
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._running = False
        self._worker_task: Optional[asyncio.Task] = None

        logger.info(f"CrawlScheduler初始化完成: concurrency={concurrency}, dedup_path={dedup_storage_path}")

    # -------------------- 任务管理 --------------------

    def enqueue(self, url: str, priority: int = 0, max_retries: Optional[int] = None,
                metadata: Optional[Dict[str, Any]] = None) -> Optional[CrawlTask]:
        """添加爬取任务到队列"""
        # URL去重检查
        if self.dedup_manager.is_duplicate(url):
            logger.debug(f"URL已去重跳过: {url}")
            self._stats["total_skipped"] += 1
            return None

        task = CrawlTask(
            url=url,
            priority=priority,
            max_retries=max_retries or self.default_max_retries,
            metadata=metadata or {},
        )
        self._task_queue.push(task)
        self._stats["total_enqueued"] += 1
        logger.debug(f"任务入队: {task.task_id[:12]} | {url[:60]} | priority={priority}")
        return task

    def enqueue_batch(self, urls: List[str], priority: int = 0,
                      metadata: Optional[Dict[str, Any]] = None) -> List[CrawlTask]:
        """批量添加任务"""
        tasks = []
        for url in urls:
            task = self.enqueue(url, priority, metadata=metadata)
            if task:
                tasks.append(task)
        logger.info(f"批量入队: {len(tasks)}/{len(urls)} 个新任务")
        return tasks

    def get_pending_count(self) -> int:
        return len(self._task_queue)

    def get_completed_count(self) -> int:
        return len(self._completed_tasks)

    def get_running_count(self) -> int:
        return len([t for t in self._running_tasks.values() if not t.is_done])

    # -------------------- 同步执行 --------------------

    def run_sync(self, urls: Optional[List[str]] = None) -> Dict[str, Any]:
        """同步执行所有任务"""
        if urls:
            self.enqueue_batch(urls)

        start_time = time.time()
        results = []

        while not self._task_queue.is_empty():
            task = self._task_queue.pop()
            if task is None:
                break

            self._stats["total_started"] += 1
            task.status = TaskStatus.RUNNING
            task.started_at = time.time()
            self._running_tasks[task.task_id] = task
            self.callbacks.fire_start(task)

            success, result, error = self._execute_task_sync(task)
            task.completed_at = time.time()
            task.duration_ms = int((task.completed_at - task.started_at) * 1000)
            self._stats["total_time_ms"] += task.duration_ms

            if success:
                task.status = TaskStatus.SUCCESS
                task.result = result
                self._stats["total_success"] += 1
                self.dedup_manager.mark_crawled(task.url)
                self.callbacks.fire_success(task, result)
                results.append({"task_id": task.task_id, "url": task.url, "success": True, "duration_ms": task.duration_ms})
            else:
                if task.can_retry:
                    task.status = TaskStatus.RETRYING
                    task.current_retry += 1
                    self._stats["total_retries"] += 1
                    logger.warning(f"任务重试: {task.task_id[:12]} 第{task.current_retry}次, error={error}")
                    self._task_queue.push(task)  # 重新入队
                    continue
                task.status = TaskStatus.FAILED
                task.error = error
                self._stats["total_failed"] += 1
                self.callbacks.fire_failure(task, error)
                results.append({"task_id": task.task_id, "url": task.url, "success": False, "error": error})

            del self._running_tasks[task.task_id]
            self._completed_tasks[task.task_id] = task

        if self.auto_cleanup:
            self.dedup_manager.cleanup_old(days=1)  # 仅清理1天前的

        elapsed_s = time.time() - start_time
        logger.info(f"同步爬取完成: 总耗时={elapsed_s:.1f}s, 成功={self._stats['total_success']}, 失败={self._stats['total_failed']}, 跳过={self._stats['total_skipped']}")
        return self.get_stats(elapsed_s)

    def _execute_task_sync(self, task: CrawlTask) -> Tuple[bool, Any, Optional[str]]:
        """执行单个同步任务"""
        client = self._get_sync_client()
        config = RequestConfig(
            timeout=self.default_timeout,
            max_retries=task.max_retries,
            user_agent=self._get_user_agent(),
        )
        try:
            resp = client.get(task.url, config=config)
            if resp.is_success:
                return True, resp.text, None
            else:
                return False, None, resp.error or f"HTTP {resp.status_code}"
        except Exception as e:
            return False, None, str(e)

    # -------------------- 异步执行 --------------------

    async def run_async(self, urls: Optional[List[str]] = None) -> Dict[str, Any]:
        """异步执行所有任务"""
        if urls:
            self.enqueue_batch(urls)

        self._running = True
        start_time = time.time()

        # 创建信号量
        self._semaphore = asyncio.Semaphore(self.concurrency)

        # 启动worker
        worker_count = min(self.concurrency, max(1, len(self._task_queue)))
        worker_tasks = [asyncio.create_task(self._worker()) for _ in range(worker_count)]

        await asyncio.gather(*worker_tasks, return_exceptions=True)
        self._running = False

        if self.auto_cleanup:
            self.dedup_manager.cleanup_old(days=1)

        elapsed_s = time.time() - start_time
        logger.info(f"异步爬取完成: 总耗时={elapsed_s:.1f}s, 成功={self._stats['total_success']}, 失败={self._stats['total_failed']}, 跳过={self._stats['total_skipped']}")
        return self.get_stats(elapsed_s)

    async def _worker(self) -> None:
        """异步worker：从队列取任务并执行"""
        while self._running or not self._task_queue.is_empty():
            task = self._task_queue.pop()
            if task is None:
                break

            async with self._semaphore:
                await self._execute_task_async(task)

    async def _execute_task_async(self, task: CrawlTask) -> None:
        """执行单个异步任务"""
        self._stats["total_started"] += 1
        task.status = TaskStatus.RUNNING
        task.started_at = time.time()
        self._running_tasks[task.task_id] = task
        self.callbacks.fire_start(task)

        success, result, error = await self._execute_task_async_impl(task)
        task.completed_at = time.time()
        task.duration_ms = int((task.completed_at - task.started_at) * 1000)
        self._stats["total_time_ms"] += task.duration_ms

        if success:
            task.status = TaskStatus.SUCCESS
            task.result = result
            self._stats["total_success"] += 1
            self.dedup_manager.mark_crawled(task.url)
            self.callbacks.fire_success(task, result)
        else:
            if task.can_retry:
                task.status = TaskStatus.RETRYING
                task.current_retry += 1
                self._stats["total_retries"] += 1
                logger.warning(f"任务重试: {task.task_id[:12]} 第{task.current_retry}次, error={error}")
                self._task_queue.push(task)
                return
            task.status = TaskStatus.FAILED
            task.error = error
            self._stats["total_failed"] += 1
            self.callbacks.fire_failure(task, error)

        del self._running_tasks[task.task_id]
        self._completed_tasks[task.task_id] = task

    async def _execute_task_async_impl(self, task: CrawlTask) -> Tuple[bool, Any, Optional[str]]:
        """异步执行实现"""
        client = await self._get_async_client()
        config = RequestConfig(
            timeout=self.default_timeout,
            max_retries=task.max_retries,
            user_agent=self._get_user_agent(),
        )
        try:
            resp = await client.get(task.url, config=config)
            if resp.is_success:
                return True, resp.text, None
            else:
                return False, None, resp.error or f"HTTP {resp.status_code}"
        except Exception as e:
            return False, None, str(e)

    # -------------------- 客户端管理 --------------------

    def _get_sync_client(self) -> SyncRequestClient:
        if self._sync_client is None:
            self._sync_client = SyncRequestClient(
                default_timeout=self.default_timeout,
                rate_limiter=self._get_rate_limiter(),
            )
        return self._sync_client

    async def _get_async_client(self) -> AsyncRequestClient:
        if self._async_client is None:
            self._async_client = AsyncRequestClient(
                default_timeout=self.default_timeout,
                rate_limiter=RateLimiter(),
                concurrency=self.concurrency,
            )
            await self._async_client._ensure_session()
        return self._async_client

    def _get_rate_limiter(self):
        # 复用dedup_manager的同域限流逻辑
        from .request_client import RateLimiter
        return RateLimiter(default_interval=0.3)

    def _get_user_agent(self) -> str:
        from .request_client import UaRotator
        return UaRotator.random_ua()

    # -------------------- 统计 --------------------

    def get_stats(self, elapsed_s: float = 0.0) -> Dict[str, Any]:
        dedup_stats = self.dedup_manager.get_stats()
        sync_stats = self._sync_client.get_stats() if self._sync_client else {}
        return {
            **self._stats,
            "dedup": dedup_stats,
            "http_client": sync_stats,
            "elapsed_s": round(elapsed_s, 2),
            "qps": round(self._stats["total_started"] / elapsed_s, 2) if elapsed_s > 0 else 0,
        }

    def reset_stats(self) -> None:
        self._stats = {
            "total_enqueued": 0, "total_started": 0, "total_success": 0,
            "total_failed": 0, "total_skipped": 0, "total_retries": 0, "total_time_ms": 0.0,
        }

    # -------------------- 清理 --------------------

    def close(self) -> None:
        if self._sync_client:
            self._sync_client.close()
        if self._async_client:
            asyncio.get_event_loop().run_until_complete(self._async_client.close()) if not asyncio.get_event_loop().is_closed() else None
        self.dedup_manager.close()
        logger.info("CrawlScheduler已关闭")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


__all__ = ["CrawlScheduler", "CrawlTask", "TaskStatus", "CrawlCallback", "PriorityQueue", "UrlNormalizer"]
