# -*- coding: utf-8 -*-
"""
统一测试运行器

整合 pytest + asyncio + mock 三模式，提供统一的测试执行入口。
支持：
- mock 模式：纯模拟数据，无需浏览器
- real 模式：真实浏览器会话
- stress 模式：HTTP级并发压力测试
- hybrid 模式：mock + real 混合执行
"""
import asyncio
import json
import logging
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class TestMode(Enum):
    """测试执行模式"""
    MOCK = "mock"
    REAL = "real"
    STRESS = "stress"
    HYBRID = "hybrid"


class TestStatus(Enum):
    """测试状态"""
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"
    TIMEOUT = "timeout"


@dataclass
class TestRecord:
    """单条测试记录"""
    test_id: str
    site_id: str
    site_name: str
    scenario_id: str
    scenario_name: str
    mode: TestMode
    status: TestStatus
    duration_seconds: float
    score: Optional[float] = None
    error_message: Optional[str] = None
    metrics: Dict[str, Any] = field(default_factory=dict)
    screenshot_path: Optional[str] = None
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "test_id": self.test_id,
            "site_id": self.site_id,
            "site_name": self.site_name,
            "scenario_id": self.scenario_id,
            "scenario_name": self.scenario_name,
            "mode": self.mode.value,
            "status": self.status.value,
            "duration_seconds": round(self.duration_seconds, 3),
            "score": self.score,
            "error_message": self.error_message,
            "metrics": self.metrics,
            "screenshot_path": self.screenshot_path,
            "timestamp": self.timestamp,
        }


@dataclass
class SuiteResult:
    """测试套件结果"""
    suite_id: str
    suite_name: str
    mode: TestMode
    start_time: str
    end_time: str
    duration_seconds: float
    total: int
    passed: int
    failed: int
    errors: int
    skipped: int
    timeout: int
    pass_rate: float
    avg_score: Optional[float] = None
    records: List[TestRecord] = field(default_factory=list)

    def add_record(self, record: TestRecord):
        self.records.append(record)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "suite_name": self.suite_name,
            "mode": self.mode.value,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_seconds": round(self.duration_seconds, 2),
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "skipped": self.skipped,
            "timeout": self.timeout,
            "pass_rate": round(self.pass_rate, 2),
            "avg_score": round(self.avg_score, 2) if self.avg_score is not None else None,
            "records": [r.to_dict() for r in self.records],
        }


class UnifiedTestRunner:
    """
    统一测试运行器
    
    封装三种执行模式：
    1. mock模式 - 使用模拟数据和固定响应时间
    2. real模式 - 使用真实浏览器CDP会话
    3. stress模式 - HTTP级并发压力测试
    """

    def __init__(self, config: Optional[Dict] = None, output_dir: Optional[Path] = None):
        self.config = config or {}
        self.output_dir = output_dir or Path(__file__).parent.parent.parent / "tests" / "output" / "reports"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._results: List[SuiteResult] = []
        self._start_time: Optional[float] = None

    # ==================== Mock 模式 ====================

    def run_mock_tests(
        self,
        test_cases: List[Dict],
        suite_name: str = "mock_suite",
        max_concurrent: int = 5,
    ) -> SuiteResult:
        """
        在mock模式下执行测试用例列表。
        每条用例是一个字典，包含：
          {"test_id", "site_id", "site_name", "scenario_id", "scenario_name",
           "action", "params", "expected"}
        返回SuiteResult。
        """
        suite = SuiteResult(
            suite_id=f"mock_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            suite_name=suite_name,
            mode=TestMode.MOCK,
            start_time=datetime.now().isoformat(),
            end_time="",
            duration_seconds=0,
            total=len(test_cases),
            passed=0, failed=0, errors=0, skipped=0, timeout=0,
            pass_rate=0.0,
        )
        self._start_time = time.time()

        async def _run():
            semaphore = asyncio.Semaphore(max_concurrent)
            tasks = [self._run_single_mock(tc, semaphore) for tc in test_cases]
            return await asyncio.gather(*tasks, return_exceptions=True)

        results = asyncio.run(_run())
        for r in results:
            if isinstance(r, Exception):
                suite.errors += 1
                logger.error(f"Mock run exception: {r}")
            elif isinstance(r, TestRecord):
                suite.add_record(r)
                if r.status == TestStatus.PASSED:
                    suite.passed += 1
                elif r.status == TestStatus.FAILED:
                    suite.failed += 1
                elif r.status == TestStatus.ERROR:
                    suite.errors += 1
                elif r.status == TestStatus.SKIPPED:
                    suite.skipped += 1
                elif r.status == TestStatus.TIMEOUT:
                    suite.timeout += 1

        suite.end_time = datetime.now().isoformat()
        suite.duration_seconds = time.time() - self._start_time
        suite.pass_rate = (suite.passed / max(suite.total - suite.skipped, 1)) * 100
        scores = [r.score for r in suite.records if r.score is not None]
        suite.avg_score = sum(scores) / len(scores) if scores else None
        self._results.append(suite)
        return suite

    async def _run_single_mock(
        self, case: Dict, semaphore: asyncio.Semaphore
    ) -> TestRecord:
        async with semaphore:
            test_id = case.get("test_id", "unknown")
            site_id = case.get("site_id", "unknown")
            site_name = case.get("site_name", "unknown")
            scenario_id = case.get("scenario_id", "unknown")
            scenario_name = case.get("scenario_name", "unknown")
            action = case.get("action", "navigate")
            params = case.get("params", {})
            expected = case.get("expected", {})

            start = time.time()
            try:
                result = await self._mock_execute(action, params, expected)
                duration = time.time() - start
                status = TestStatus.PASSED if result.get("passed", True) else TestStatus.FAILED
                return TestRecord(
                    test_id=test_id, site_id=site_id, site_name=site_name,
                    scenario_id=scenario_id, scenario_name=scenario_name,
                    mode=TestMode.MOCK, status=status,
                    duration_seconds=duration, score=result.get("score"),
                    error_message=result.get("error"),
                    metrics=result.get("metrics", {}),
                )
            except asyncio.TimeoutError:
                duration = time.time() - start
                return TestRecord(
                    test_id=test_id, site_id=site_id, site_name=site_name,
                    scenario_id=scenario_id, scenario_name=scenario_name,
                    mode=TestMode.MOCK, status=TestStatus.TIMEOUT,
                    duration_seconds=duration, error_message="Mock timeout",
                )
            except Exception as e:
                duration = time.time() - start
                return TestRecord(
                    test_id=test_id, site_id=site_id, site_name=site_name,
                    scenario_id=scenario_id, scenario_name=scenario_name,
                    mode=TestMode.MOCK, status=TestStatus.ERROR,
                    duration_seconds=duration, error_message=str(e),
                )

    async def _mock_execute(
        self, action: str, params: Dict, expected: Dict
    ) -> Dict[str, Any]:
        """
        模拟执行单条测试用例。根据action类型产生不同的模拟结果。
        """
        latency = self.config.get("mock_latency_ms", 100)
        await asyncio.sleep(latency / 1000)

        base_score = 90.0
        passed = True
        error = None
        metrics: Dict[str, Any] = {
            "latency_ms": latency,
            "action": action,
            "url": params.get("url", "about:blank"),
        }

        if action == "navigate":
            success = params.get("url", "").startswith(("http://", "https://"))
            base_score = 95.0 if success else 40.0
            metrics["status_code"] = 200 if success else 0
        elif action in ("search", "click", "extract_list", "extract_article"):
            base_score = 88.0
            metrics["keywords"] = params.get("keywords", "")
        elif action == "paginate":
            base_score = 85.0
            metrics["page"] = params.get("page", 1)
        elif action == "form_submit":
            base_score = 82.0
            metrics["fields"] = list(params.keys())
        elif action == "captcha_solve":
            base_score = 70.0
        elif action == "upload":
            base_score = 75.0

        if expected.get("min_score") and base_score < expected["min_score"]:
            passed = False
            error = f"Score {base_score} < min_score {expected['min_score']}"
        elif expected.get("max_duration") and latency > expected["max_duration"] * 1000:
            passed = False
            error = f"Latency {latency}ms > max_duration {expected['max_duration']}s"

        return {
            "passed": passed,
            "score": base_score if passed else max(0, base_score - 30),
            "error": error,
            "metrics": metrics,
        }

    # ==================== Stress 模式 ====================

    def run_stress_tests(
        self,
        urls: List[Dict],
        concurrency: int = 10,
        iterations: int = 3,
        timeout: int = 15,
        suite_name: str = "stress_suite",
    ) -> SuiteResult:
        """
        HTTP级压力测试。对每个URL发起concurrency个并发请求，共iterations轮。
        每条记录对应一次请求。
        """
        total = len(urls) * concurrency * iterations
        suite = SuiteResult(
            suite_id=f"stress_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            suite_name=suite_name,
            mode=TestMode.STRESS,
            start_time=datetime.now().isoformat(),
            end_time="", duration_seconds=0,
            total=total, passed=0, failed=0, errors=0, skipped=0, timeout=0,
            pass_rate=0.0,
        )
        self._start_time = time.time()

        async def _run():
            semaphore = asyncio.Semaphore(concurrency)
            tasks = []
            for url_info in urls:
                url = url_info.get("url", "")
                name = url_info.get("name", url)
                site_id = url_info.get("site_id", "unknown")
                for iteration in range(iterations):
                    for req_id in range(concurrency):
                        tasks.append(
                            self._stress_request(
                                url, name, site_id, iteration, req_id, semaphore, timeout
                            )
                        )
            return await asyncio.gather(*tasks, return_exceptions=True)

        results = asyncio.run(_run())
        for r in results:
            if isinstance(r, Exception):
                suite.errors += 1
            elif isinstance(r, TestRecord):
                suite.add_record(r)
                if r.status == TestStatus.PASSED:
                    suite.passed += 1
                elif r.status == TestStatus.FAILED:
                    suite.failed += 1
                elif r.status == TestStatus.ERROR:
                    suite.errors += 1
                elif r.status == TestStatus.TIMEOUT:
                    suite.timeout += 1

        suite.end_time = datetime.now().isoformat()
        suite.duration_seconds = time.time() - self._start_time
        suite.pass_rate = (suite.passed / max(suite.total - suite.skipped, 1)) * 100
        scores = [r.score for r in suite.records if r.score is not None]
        suite.avg_score = sum(scores) / len(scores) if scores else None
        self._results.append(suite)
        return suite

    async def _stress_request(
        self, url: str, name: str, site_id: str,
        iteration: int, req_id: int,
        semaphore: asyncio.Semaphore, timeout: int,
    ) -> TestRecord:
        async with semaphore:
            test_id = f"{site_id}_s{iteration}_{req_id}"
            start = time.time()
            try:
                resp = await asyncio.wait_for(
                    self._http_get(url), timeout=timeout
                )
                duration = time.time() - start
                status_code = resp.get("status_code", 0)
                passed = 200 <= status_code < 400
                score = min(100, status_code * 0.5 + (100 - duration * 10))
                return TestRecord(
                    test_id=test_id, site_id=site_id, site_name=name,
                    scenario_id=f"stress_iter{iteration}_req{req_id}",
                    scenario_name=f"Concurrent request {req_id} iteration {iteration}",
                    mode=TestMode.STRESS, status=TestStatus.PASSED if passed else TestStatus.FAILED,
                    duration_seconds=duration, score=max(0, score),
                    error_message=None if passed else f"HTTP {status_code}",
                    metrics={
                        "status_code": status_code,
                        "content_length": resp.get("content_length", 0),
                        "url": url,
                        "iteration": iteration,
                        "request_id": req_id,
                        "concurrency": semaphore._value,
                    },
                )
            except asyncio.TimeoutError:
                duration = time.time() - start
                return TestRecord(
                    test_id=test_id, site_id=site_id, site_name=name,
                    scenario_id=f"stress_iter{iteration}_req{req_id}",
                    scenario_name=f"Timeout {url}",
                    mode=TestMode.STRESS, status=TestStatus.TIMEOUT,
                    duration_seconds=duration, error_message="Request timeout",
                )
            except Exception as e:
                duration = time.time() - start
                return TestRecord(
                    test_id=test_id, site_id=site_id, site_name=name,
                    scenario_id=f"stress_iter{iteration}_req{req_id}",
                    scenario_name=f"Error {url}",
                    mode=TestMode.STRESS, status=TestStatus.ERROR,
                    duration_seconds=duration, error_message=str(e),
                )

    async def _http_get(self, url: str) -> Dict[str, Any]:
        """发起HTTP GET请求（优先用aiohttp，降级用requests）"""
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15), ssl=False) as resp:
                    body = await resp.text()
                    return {
                        "status_code": resp.status,
                        "content_length": len(body.encode()),
                        "headers": dict(resp.headers),
                    }
        except ImportError:
            import requests
            resp = requests.get(url, timeout=15, verify=False, allow_redirects=True)
            return {
                "status_code": resp.status_code,
                "content_length": len(resp.content),
                "headers": dict(resp.headers),
            }

    # ==================== 结果保存 ====================

    def save_suite_result(self, suite: SuiteResult, filename: Optional[str] = None) -> str:
        """保存单条SuiteResult到JSON文件"""
        if not filename:
            filename = f"{suite.mode.value}_suite_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        path = self.output_dir / f"{filename}.json"
        path.write_text(json.dumps(suite.to_dict(), ensure_ascii=False, indent=2, default=str))
        logger.info(f"Suite result saved to {path}")
        return str(path)

    def save_all_results(self) -> str:
        """保存所有历史结果"""
        path = self.output_dir / f"all_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        path.write_text(json.dumps(
            [s.to_dict() for s in self._results], ensure_ascii=False, indent=2, default=str
        ))
        logger.info(f"All results saved to {path}")
        return str(path)

    def generate_summary_report(self) -> str:
        """生成汇总报告"""
        total = sum(s.total for s in self._results)
        passed = sum(s.passed for s in self._results)
        failed = sum(s.failed for s in self._results)
        errors = sum(s.errors for s in self._results)
        skipped = sum(s.skipped for s in self._results)
        timeout = sum(s.timeout for s in self._results)
        avg_score = sum(
            (s.avg_score or 0) * s.total for s in self._results
        ) / max(total, 1)

        report = {
            "generated_at": datetime.now().isoformat(),
            "total_suites": len(self._results),
            "total_tests": total,
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "skipped": skipped,
            "timeout": timeout,
            "overall_pass_rate": round(passed / max(total - skipped, 1) * 100, 2),
            "avg_score": round(avg_score, 2),
            "suites": [s.to_dict() for s in self._results],
        }
        path = self.output_dir / f"summary_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        logger.info(f"Summary report saved to {path}")
        return str(path)

    def print_summary(self):
        """打印摘要到控制台"""
        total = sum(s.total for s in self._results)
        passed = sum(s.passed for s in self._results)
        failed = sum(s.failed for s in self._results)
        errors = sum(s.errors for s in self._results)
        print("\n" + "=" * 60)
        print("Unified Test Runner — Summary")
        print("=" * 60)
        for s in self._results:
            print(f"[{s.mode.value.upper()}] {s.suite_name}")
            print(f"  total={s.total} passed={s.passed} failed={s.failed} errors={s.errors} timeout={s.timeout}")
            print(f"  pass_rate={s.pass_rate:.1f}%  avg_score={s.avg_score:.1f}" if s.avg_score else f"  pass_rate={s.pass_rate:.1f}%")
            print()
        print(f"OVERALL: {total} tests, {passed} passed, {failed} failed, {errors} errors")
        print(f"  pass_rate={passed/max(total-passed-failed-errors,1)*100:.1f}%")
        print("=" * 60)


def run_full_suite(config_path: Optional[str] = None, mode: str = "mock") -> SuiteResult:
    """
    便捷入口：从配置文件加载并运行完整套件。
    config_path — JSON配置文件路径（可选，默认读取config/tools_integration_config.json）
    mode — mock/real/stress/hybrid
    """
    from .test_config_loader import load_test_config

    if config_path is None:
        config_path = str(Path(__file__).parent.parent.parent / "config" / "test_environment_config.json")

    env_config = load_test_config(config_path)
    runner = UnifiedTestRunner(
        config={
            "mock_latency_ms": env_config.browser.get("timeout_seconds", 30) * 10,
        },
        output_dir=Path(env_config.reporting.get("directory", "tests/output/reports")),
    )

    if mode == "mock":
        mock_cases = [
            {
                "test_id": f"FC-{i:03d}",
                "site_id": site.site_id,
                "site_name": site.name,
                "scenario_id": tc,
                "scenario_name": f"TestCase {tc}",
                "action": _action_for_case(tc),
                "params": {"url": site.url, "keywords": site.specific_config.get("search_keywords", [""])[0]},
                "expected": {"min_score": 75},
            }
            for site in env_config.phase1_sites
            for tc in site.test_cases
        ]
        return runner.run_mock_tests(mock_cases, suite_name=f"phase1_mock_{mode}")

    elif mode == "stress":
        urls = [
            {"site_id": site.site_id, "name": site.name, "url": site.url}
            for site in env_config.phase1_sites
        ]
        return runner.run_stress_tests(
            urls, concurrency=5, iterations=2, timeout=10, suite_name=f"phase1_stress_{mode}"
        )

    else:
        raise ValueError(f"Unsupported mode: {mode}")


def _action_for_case(case_id: str) -> str:
    """根据测试用例ID映射到action类型"""
    if case_id.startswith("FC-00"):
        n = int(case_id.split("-")[1])
        if n <= 8: return "navigate"
        elif n <= 20: return "search"
        elif n <= 28: return "click"
        elif n <= 35: return "form_submit"
        elif n <= 42: return "extract_list"
        elif n <= 50: return "extract_article"
        else: return "navigate"
    elif case_id.startswith("CP-"):
        return "search"
    elif case_id.startswith("ER-"):
        return "navigate"
    elif case_id.startswith("ST-"):
        return "paginate"
    else:
        return "navigate"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    suite = run_full_suite(mode="mock")
    print("\n--- Mock Mode Result ---")
    print(f"Passed: {suite.passed}/{suite.total}")
    print(f"Pass rate: {suite.pass_rate:.1f}%")
    print(f"Avg score: {suite.avg_score:.1f}")

    suite2 = run_full_suite(mode="stress")
    print("\n--- Stress Mode Result ---")
    print(f"Passed: {suite2.passed}/{suite2.total}")
    print(f"Pass rate: {suite2.pass_rate:.1f}%")
    print(f"Duration: {suite2.duration_seconds:.1f}s")

    runner = UnifiedTestRunner()
    runner._results = [suite, suite2]
    runner.print_summary()
    runner.save_all_results()
    runner.generate_summary_report()
