# -*- coding: utf-8 -*-
"""
asyncio 适配器

将 asyncio 协程测试统一转换为 TestRecord 格式，提供：
- 超时保护（asyncio.wait_for）
- 并发控制（Semaphore）
- 结果聚合（gather）
- 错误分类（timeout/error/failure）
"""
import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# 避免循环导入
from tests.framework.unified_test_runner import TestRecord, TestStatus, TestMode


class AsyncAction(Enum):
    """异步操作类型"""
    NAVIGATE = "navigate"
    SEARCH = "search"
    CLICK = "click"
    EXTRACT = "extract"
    FORM_SUBMIT = "form_submit"
    PAGINATE = "paginate"
    SCREENSHOT = "screenshot"
    UPLOAD = "upload"
    CAPTCHA_SOLVE = "captcha_solve"
    SLEEP = "sleep"


@dataclass
class AsyncTestCase:
    """单条异步测试用例"""
    test_id: str
    site_id: str
    site_name: str
    scenario_id: str
    scenario_name: str
    action: AsyncAction
    coroutine_fn: Callable[..., Any]
    timeout: float = 30.0
    max_retries: int = 3
    retry_delay: float = 1.0
    expected_status: TestStatus = TestStatus.PASSED
    extra_params: Dict[str, Any] = field(default_factory=dict)


class AsyncTestAdapter:
    """
    asyncio 测试适配器

    封装异步测试的执行逻辑，提供统一的超时、重试、并发控制和结果收集。
    """

    def __init__(
        self,
        default_timeout: float = 30.0,
        default_max_retries: int = 3,
        default_retry_delay: float = 1.0,
        output_dir: Optional[Path] = None,
    ):
        self.default_timeout = default_timeout
        self.default_max_retries = default_max_retries
        self.default_retry_delay = default_retry_delay
        self.output_dir = output_dir or Path(__file__).parent.parent / "output" / "async_reports"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._records: List[TestRecord] = []

    async def run_single(
        self,
        test_case: AsyncTestCase,
    ) -> TestRecord:
        """
        执行单条异步测试用例（带重试）。
        """
        retries = test_case.max_retries if test_case.max_retries > 0 else self.default_max_retries
        timeout = test_case.timeout if test_case.timeout > 0 else self.default_timeout

        last_error = None
        for attempt in range(1, retries + 1):
            start = time.time()
            try:
                result = await asyncio.wait_for(
                    test_case.coroutine_fn(**test_case.extra_params),
                    timeout=timeout,
                )
                duration = time.time() - start
                # 解析协程返回值
                if isinstance(result, dict):
                    passed = result.get("passed", True)
                    score = result.get("score", 100.0 if passed else 0.0)
                    error = result.get("error")
                elif isinstance(result, bool):
                    passed = result
                    score = 100.0 if result else 0.0
                    error = None
                else:
                    passed = True
                    score = 80.0
                    error = None

                status = TestStatus.PASSED if passed else TestStatus.FAILED
                record = TestRecord(
                    test_id=test_case.test_id,
                    site_id=test_case.site_id,
                    site_name=test_case.site_name,
                    scenario_id=test_case.scenario_id,
                    scenario_name=test_case.scenario_name,
                    mode=TestMode.MOCK,
                    status=status,
                    duration_seconds=duration,
                    score=score,
                    error_message=error,
                    metrics={
                        "action": test_case.action.value,
                        "attempt": attempt,
                        "retries": retries,
                    },
                )
                self._records.append(record)
                return record

            except asyncio.TimeoutError:
                duration = time.time() - start
                last_error = f"Timeout after {timeout}s (attempt {attempt}/{retries})"
                logger.warning(f"{test_case.test_id}: {last_error}")
                if attempt < retries:
                    await asyncio.sleep(self.default_retry_delay * (2 ** (attempt - 1)))

            except Exception as e:
                duration = time.time() - start
                last_error = str(e)
                logger.warning(f"{test_case.test_id}: Attempt {attempt} failed: {e}")
                if attempt < retries:
                    await asyncio.sleep(self.default_retry_delay * (2 ** (attempt - 1)))

        # 所有重试失败
        record = TestRecord(
            test_id=test_case.test_id,
            site_id=test_case.site_id,
            site_name=test_case.site_name,
            scenario_id=test_case.scenario_id,
            scenario_name=test_case.scenario_name,
            mode=TestMode.MOCK,
            status=TestStatus.ERROR,
            duration_seconds=time.time() - start,
            error_message=last_error,
            metrics={"action": test_case.action.value, "retries_exhausted": True},
        )
        self._records.append(record)
        return record

    async def run_batch(
        self,
        test_cases: List[AsyncTestCase],
        max_concurrent: int = 5,
    ) -> List[TestRecord]:
        """
        批量执行异步测试用例（带并发控制）。
        """
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _run_with_semaphore(tc: AsyncTestCase) -> TestRecord:
            async with semaphore:
                return await self.run_single(tc)

        tasks = [self._run_with_semaphore(tc) for tc in test_cases]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        records = []
        for r in results:
            if isinstance(r, TestRecord):
                records.append(r)
            elif isinstance(r, Exception):
                logger.error(f"Batch task failed: {r}")
        return records

    async def run_stress_batch(
        self,
        test_cases: List[AsyncTestCase],
        concurrency: int = 20,
        iterations: int = 3,
    ) -> List[TestRecord]:
        """
        压力测试模式：对每条用例执行多次，控制总并发数。
        """
        all_cases = []
        for tc in test_cases:
            for i in range(iterations):
                new_tc = AsyncTestCase(
                    test_id=f"{tc.test_id}_iter{i+1}",
                    site_id=tc.site_id,
                    site_name=tc.site_name,
                    scenario_id=f"{tc.scenario_id}_i{i+1}",
                    scenario_name=tc.scenario_name,
                    action=tc.action,
                    coroutine_fn=tc.coroutine_fn,
                    timeout=tc.timeout,
                    max_retries=1,  # 压力测试不重试
                    extra_params=tc.extra_params,
                )
                all_cases.append(new_tc)

        return await self.run_batch(all_cases, max_concurrent=concurrency)

    def export_records(self, records: List[TestRecord], filename: str) -> str:
        """导出为 JSON"""
        passed = sum(1 for r in records if r.status == TestStatus.PASSED)
        failed = sum(1 for r in records if r.status == TestStatus.FAILED)
        errors = sum(1 for r in records if r.status == TestStatus.ERROR)
        skipped = sum(1 for r in records if r.status == TestStatus.SKIPPED)
        durations = [r.duration_seconds for r in records if r.duration_seconds > 0]

        data = {
            "export_time": datetime.now().isoformat(),
            "total": len(records),
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "skipped": skipped,
            "pass_rate": round(passed / max(len(records) - skipped, 1) * 100, 2),
            "avg_duration_ms": round(sum(durations) / max(len(durations), 1) * 1000, 2),
            "min_duration_ms": round(min(durations) * 1000, 2) if durations else 0,
            "max_duration_ms": round(max(durations) * 1000, 2) if durations else 0,
            "records": [r.to_dict() for r in records],
        }
        path = self.output_dir / f"{filename}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        logger.info(f"Async records exported: {path}")
        return str(path)

    def get_records(self) -> List[TestRecord]:
        return list(self._records)


# ==================== 便捷工厂函数 ====================

def make_navigate_coroutine(url: str, mock_latency_ms: float = 100) -> Callable:
    """创建模拟页面导航的协程工厂"""
    async def _navigate(**kwargs):
        await asyncio.sleep(mock_latency_ms / 1000)
        return {"passed": url.startswith(("http://", "https://")), "score": 95.0 if url.startswith("http") else 40.0}
    return _navigate


def make_search_coroutine(base_url: str, keyword: str, mock_latency_ms: float = 200) -> Callable:
    """创建模拟搜索的协程工厂"""
    async def _search(**kwargs):
        await asyncio.sleep(mock_latency_ms / 1000)
        has_results = bool(keyword and len(keyword) >= 1)
        return {"passed": has_results, "score": 88.0 if has_results else 50.0, "result_count": 10 if has_results else 0}
    return _search


def make_extract_coroutine(url: str, selector: str, mock_latency_ms: float = 150) -> Callable:
    """创建模拟数据提取的协程工厂"""
    async def _extract(**kwargs):
        await asyncio.sleep(mock_latency_ms / 1000)
        valid_selector = bool(selector and "." in selector or "#" in selector)
        return {"passed": valid_selector, "score": 85.0 if valid_selector else 60.0, "fields_found": 5}
    return _extract


def create_async_test_cases(
    site_id: str,
    site_name: str,
    url: str,
    case_definitions: List[Dict],
    mock_latency_ms: float = 100,
) -> List[AsyncTestCase]:
    """
    根据用例定义列表批量创建 AsyncTestCase。

    case_definitions: [{"scenario_id", "scenario_name", "action", "params", "timeout", "expected"}]
    """
    cases = []
    for i, defn in enumerate(case_definitions):
        action_str = defn.get("action", "navigate")
        params = defn.get("params", {})
        timeout = defn.get("timeout", 30)

        # 根据 action 类型选择对应的协程工厂
        if action_str == "navigate":
            coro_fn = make_navigate_coroutine(url, mock_latency_ms)
        elif action_str == "search":
            keyword = params.get("keyword", params.get("keywords", ""))
            coro_fn = make_search_coroutine(url, keyword, mock_latency_ms)
        elif action_str == "extract":
            selector = params.get("selector", ".result")
            coro_fn = make_extract_coroutine(url, selector, mock_latency_ms)
        else:
            # 默认：延迟后返回成功
            async def _default(**kwargs):
                await asyncio.sleep(mock_latency_ms / 1000)
                return {"passed": True, "score": 80.0}
            coro_fn = _default

        case = AsyncTestCase(
            test_id=f"ASYNC_{site_id}_{i:03d}",
            site_id=site_id,
            site_name=site_name,
            scenario_id=defn.get("scenario_id", f"SC-{i}"),
            scenario_name=defn.get("scenario_name", action_str),
            action=AsyncAction(action_str),
            coroutine_fn=coro_fn,
            timeout=timeout,
            extra_params=params,
        )
        cases.append(case)
    return cases


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')

    async def demo():
        adapter = AsyncTestAdapter(default_timeout=10)

        test_cases = create_async_test_cases(
            site_id="demo_site",
            site_name="Demo Site",
            url="https://example.com",
            case_definitions=[
                {"scenario_id": "NAV-01", "scenario_name": "Navigate home", "action": "navigate", "params": {"url": "https://example.com"}},
                {"scenario_id": "SRCH-01", "scenario_name": "Search query", "action": "search", "params": {"keyword": "test"}},
                {"scenario_id": "EXT-01", "scenario_name": "Extract data", "action": "extract", "params": {"selector": ".content"}},
            ],
            mock_latency_ms=50,
        )

        records = await adapter.run_batch(test_cases, max_concurrent=3)
        adapter.export_records(records, "demo_async_report")

        print(f"\nResults: {len(records)} tests")
        print(f"Passed: {sum(1 for r in records if r.status == TestStatus.PASSED)}")
        print(f"Failed: {sum(1 for r in records if r.status == TestStatus.FAILED)}")
        print(f"Errors: {sum(1 for r in records if r.status == TestStatus.ERROR)}")

    asyncio.run(demo())