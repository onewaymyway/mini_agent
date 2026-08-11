# -*- coding: utf-8 -*-
"""
并发控制模块

提供异步并发控制功能：
- 信号量控制并发数
- 任务池管理
- 批量并发执行
- 并发结果收集

使用示例：
    from finance_toolkit.concurrency import (
        SemaphorePool,
        TaskPool,
        batch_fetch,
    )
    
    # 信号量控制
    pool = SemaphorePool(max_concurrent=10)
    async with pool.acquire():
        data = await fetch_data()
    
    # 批量并发
    results = await batch_fetch(fetchers, max_concurrent=5)
"""

import asyncio
import logging
import time
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
    TypeVar,
    Union,
)
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

T = TypeVar('T')


@dataclass
class TaskResult:
    """任务执行结果"""
    index: int
    success: bool
    result: Any = None
    error: Optional[Exception] = None
    elapsed: float = 0.0


class SemaphorePool:
    """
    信号量并发控制池
    
    限制同时执行的异步任务数量，防止过载。
    """
    
    def __init__(self, max_concurrent: int = 10):
        """
        初始化信号量池
        
        Args:
            max_concurrent: 最大并发数
        """
        self.max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active = 0
        self._lock = asyncio.Lock()
    
    async def acquire(self):
        """获取信号量"""
        await self._semaphore.acquire()
        async with self._lock:
            self._active += 1
        logger.debug(f"信号量已获取，当前活跃: {self._active}/{self.max_concurrent}")
    
    async def release(self):
        """释放信号量"""
        async with self._lock:
            self._active -= 1
        self._semaphore.release()
        logger.debug(f"信号量已释放，当前活跃: {self._active}/{self.max_concurrent}")
    
    async def __aenter__(self):
        await self.acquire()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.release()
    
    @property
    def active_count(self) -> int:
        """当前活跃任务数"""
        return self._active
    
    @property
    def available(self) -> int:
        """可用信号量数量"""
        return self.max_concurrent - self._active


class TaskPool:
    """
    任务池管理器
    
    管理异步任务的创建、执行和结果收集。
    """
    
    def __init__(self, max_concurrent: int = 10):
        """
        初始化任务池
        
        Args:
            max_concurrent: 最大并发数
        """
        self.max_concurrent = max_concurrent
        self._pool = SemaphorePool(max_concurrent)
        self._results: List[TaskResult] = []
        self._lock = asyncio.Lock()
    
    async def submit(
        self,
        func: Callable,
        *args,
        timeout: Optional[float] = None,
        **kwargs
    ) -> TaskResult:
        """
        提交任务到池中执行
        
        Args:
            func: 异步函数
            *args: 位置参数
            timeout: 超时时间（秒）
            **kwargs: 关键字参数
        
        Returns:
            TaskResult: 任务执行结果
        """
        start_time = time.time()
        index = len(self._results)
        
        async with self._pool:
            try:
                if timeout:
                    result = await asyncio.wait_for(func(*args, **kwargs), timeout=timeout)
                else:
                    result = await func(*args, **kwargs)
                
                elapsed = time.time() - start_time
                task_result = TaskResult(
                    index=index,
                    success=True,
                    result=result,
                    elapsed=elapsed
                )
                
            except Exception as e:
                elapsed = time.time() - start_time
                task_result = TaskResult(
                    index=index,
                    success=False,
                    error=e,
                    elapsed=elapsed
                )
                logger.error(f"任务 {index} 执行失败: {e}")
        
        async with self._lock:
            self._results.append(task_result)
        
        return task_result
    
    async def execute_batch(
        self,
        tasks: List[Tuple[Callable, tuple, dict]],
        timeout: Optional[float] = None
    ) -> List[TaskResult]:
        """
        批量执行任务
        
        Args:
            tasks: 任务列表，每个元素为 (func, args, kwargs)
            timeout: 单个任务超时时间
        
        Returns:
            任务结果列表
        """
        coroutines = []
        for i, (func, args, kwargs) in enumerate(tasks):
            coroutines.append(self.submit(func, *args, timeout=timeout, **kwargs))

        results = await asyncio.gather(*coroutines, return_exceptions=True)

        # 处理异常结果
        final_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                final_results.append(TaskResult(
                    index=i,
                    success=False,
                    error=result,
                    elapsed=0.0
                ))
            else:
                final_results.append(result)

        return final_results

    def get_results(self) -> List[TaskResult]:
        """获取所有任务结果"""
        return self._results.copy()

    def get_success_results(self) -> List[TaskResult]:
        """获取成功的任务结果"""
        return [r for r in self._results if r.success]

    def get_failed_results(self) -> List[TaskResult]:
        """获取失败的任务结果"""
        return [r for r in self._results if not r.success]

    def get_stats(self) -> Dict[str, Any]:
        """获取执行统计"""
        total = len(self._results)
        success = sum(1 for r in self._results if r.success)
        failed = total - success

        elapsed_times = [r.elapsed for r in self._results if r.elapsed > 0]
        avg_elapsed = sum(elapsed_times) / len(elapsed_times) if elapsed_times else 0
        max_elapsed = max(elapsed_times) if elapsed_times else 0
        min_elapsed = min(elapsed_times) if elapsed_times else 0

        return {
            'total': total,
            'success': success,
            'failed': failed,
            'success_rate': success / total if total > 0 else 0,
            'avg_elapsed': avg_elapsed,
            'max_elapsed': max_elapsed,
            'min_elapsed': min_elapsed,
            'max_concurrent': self.max_concurrent,
            'active': self._pool.active_count
        }


async def batch_fetch(
    fetchers: List[Callable],
    max_concurrent: int = 10,
    timeout: Optional[float] = None,
    return_exceptions: bool = False
) -> List[Any]:
    """
    批量并发执行抓取函数
    
    Args:
        fetchers: 抓取函数列表
        max_concurrent: 最大并发数
        timeout: 单个任务超时时间
        return_exceptions: 是否返回异常而非抛出
    
    Returns:
        结果列表
    """
    pool = SemaphorePool(max_concurrent)
    results = []
    
    async def fetch_with_semaphore(i, fetcher):
        async with pool:
            try:
                if timeout:
                    return await asyncio.wait_for(fetcher(), timeout=timeout)
                return await fetcher()
            except Exception as e:
                if return_exceptions:
                    return e
                raise
    
    tasks = [fetch_with_semaphore(i, f) for i, f in enumerate(fetchers)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    return list(results)


async def concurrent_map(
    func: Callable,
    items: List[Any],
    max_concurrent: int = 10,
    timeout: Optional[float] = None
) -> List[Any]:
    """
    并发映射函数
    
    Args:
        func: 处理函数
        items: 输入列表
        max_concurrent: 最大并发数
        timeout: 单个任务超时时间
    
    Returns:
        处理结果列表
    """
    pool = SemaphorePool(max_concurrent)
    
    async def process_with_semaphore(i, item):
        async with pool:
            try:
                if timeout:
                    return await asyncio.wait_for(func(item), timeout=timeout)
                return await func(item)
            except Exception as e:
                logger.error(f"处理项 {i} 失败: {e}")
                raise
    
    tasks = [process_with_semaphore(i, item) for i, item in enumerate(items)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    return list(results)


async def with_timeout(
    coro,
    timeout: float,
    default: Any = None
) -> Any:
    """
    带超时的协程执行
    
    Args:
        coro: 协程
        timeout: 超时时间（秒）
        default: 超时时的默认返回值
    
    Returns:
        协程结果或默认值
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(f"协程执行超时 ({timeout}s)")
        return default


def get_event_loop() -> asyncio.AbstractEventLoop:
    """
    获取事件循环
    
    Returns:
        事件循环实例
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            return loop
    except RuntimeError:
        pass
    return asyncio.new_event_loop()


if __name__ == '__main__':
    # 测试
    async def test_task_pool():
        async def dummy_task(i):
            await asyncio.sleep(0.1)
            return f"result_{i}"
        
        pool = TaskPool(max_concurrent=3)
        tasks = [(dummy_task, (i,), {}) for i in range(10)]
        results = await pool.execute_batch(tasks)
        
        print(f"总任务数: {len(results)}")
        print(f"成功: {sum(1 for r in results if r.success)}")
        print(f"失败: {sum(1 for r in results if not r.success)}")
        print(f"统计: {pool.get_stats()}")
    
    asyncio.run(test_task_pool())
