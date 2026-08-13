# -*- coding: utf-8 -*-
"""
HTTP级压力测试模块

基于 aiohttp / requests 实现并发压力测试，用于验证目标网站的
HTTP层面可访问性、响应时间分布、错误率等指标。

不依赖浏览器，纯HTTP层测试。
"""
import asyncio
import json
import logging
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class RequestResult:
    """单次HTTP请求结果"""
    url: str
    method: str
    status_code: int
    duration_ms: float
    content_length: int
    success: bool
    error_message: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "method": self.method,
            "status_code": self.status_code,
            "duration_ms": round(self.duration_ms, 2),
            "content_length": self.content_length,
            "success": self.success,
            "error_message": self.error_message,
            "headers": {k: v[:200] for k, v in self.headers.items()},
            "timestamp": self.timestamp,
        }


@dataclass
class LoadTestReport:
    """压力测试汇总报告"""
    test_name: str
    start_time: str
    end_time: str
    duration_seconds: float
    total_requests: int
    successful_requests: int
    failed_requests: int
    timeout_count: int
    error_count: int
    success_rate: float
    avg_duration_ms: float
    min_duration_ms: float
    max_duration_ms: float
    p50_duration_ms: float
    p90_duration_ms: float
    p95_duration_ms: float
    p99_duration_ms: float
    rps: float  # requests per second
    results: List[RequestResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "test_name": self.test_name,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_seconds": round(self.duration_seconds, 2),
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "timeout_count": self.timeout_count,
            "error_count": self.error_count,
            "success_rate": round(self.success_rate, 2),
            "avg_duration_ms": round(self.avg_duration_ms, 2),
            "min_duration_ms": round(self.min_duration_ms, 2),
            "max_duration_ms": round(self.max_duration_ms, 2),
            "p50_duration_ms": round(self.p50_duration_ms, 2),
            "p90_duration_ms": round(self.p90_duration_ms, 2),
            "p95_duration_ms": round(self.p95_duration_ms, 2),
            "p99_duration_ms": round(self.p99_duration_ms, 2),
            "rps": round(self.rps, 2),
            "results": [r.to_dict() for r in self.results],
        }


class LoadTester:
    """
    HTTP级压力测试器

    提供：
    - 单URL并发测试
    - 多URL轮转测试
    - 长时间稳定性测试
    - 结果统计与报告生成
    """

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        default_timeout: int = 15,
        default_concurrency: int = 10,
        use_aiohttp: bool = True,
    ):
        self.output_dir = output_dir or Path(__file__).parent.parent.parent / "tests" / "output" / "load_tests"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.default_timeout = default_timeout
        self.default_concurrency = default_concurrency
        self.use_aiohttp = use_aiohttp
        self._reports: List[LoadTestReport] = []

    # ==================== 核心测试方法 ====================

    async def stress_url(
        self,
        url: str,
        concurrency: int = 10,
        iterations: int = 3,
        timeout: int = 15,
        test_name: str = "",
    ) -> LoadTestReport:
        """
        对单个URL进行压力测试。

        Args:
            url: 目标URL
            concurrency: 并发数
            iterations: 每URL迭代次数
            timeout: 单次请求超时(秒)
            test_name: 测试名称（自动生成）
        """
        if not test_name:
            test_name = f"stress_{Path(url.split('/')[2]).stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        logger.info(f"Starting stress test: {test_name} | url={url} | concurrency={concurrency} | iterations={iterations}")

        total = concurrency * iterations
        report = LoadTestReport(
            test_name=test_name,
            start_time=datetime.now().isoformat(),
            end_time="",
            duration_seconds=0,
            total_requests=total,
            successful_requests=0,
            failed_requests=0,
            timeout_count=0,
            error_count=0,
            success_rate=0.0,
            avg_duration_ms=0.0,
            min_duration_ms=0.0,
            max_duration_ms=0.0,
            p50_duration_ms=0.0,
            p90_duration_ms=0.0,
            p95_duration_ms=0.0,
            p99_duration_ms=0.0,
            rps=0.0,
        )

        self._start_time = time.time()

        async def _run():
            semaphore = asyncio.Semaphore(concurrency)
            tasks = []
            for iteration in range(iterations):
                for req_id in range(concurrency):
                    tasks.append(
                        self._single_request(url, semaphore, timeout, iteration, req_id)
                    )
            return await asyncio.gather(*tasks, return_exceptions=True)

        raw_results = await _run()
        report.duration_seconds = time.time() - self._start_time
        report.rps = total / max(report.duration_seconds, 0.001)

        durations = []
        for r in raw_results:
            if isinstance(r, RequestResult):
                report.results.append(r)
                durations.append(r.duration_ms)
                if r.success:
                    report.successful_requests += 1
                else:
                    if "timeout" in (r.error_message or "").lower():
                        report.timeout_count += 1
                    else:
                        report.failed_requests += 1
                    if r.error_message and r.status_code == 0:
                        report.error_count += 1
            elif isinstance(r, Exception):
                report.error_count += 1
                logger.error(f"Unexpected exception: {r}")

        if durations:
            report.avg_duration_ms = statistics.mean(durations)
            report.min_duration_ms = min(durations)
            report.max_duration_ms = max(durations)
            report.p50_duration_ms = self._percentile(durations, 50)
            report.p90_duration_ms = self._percentile(durations, 90)
            report.p95_duration_ms = self._percentile(durations, 95)
            report.p99_duration_ms = self._percentile(durations, 99)

        report.success_rate = (report.successful_requests / max(total, 1)) * 100
        report.end_time = datetime.now().isoformat()

        self._reports.append(report)
        self._save_report(report)
        logger.info(
            f"Stress test done: {test_name} | "
            f"success={report.successful_requests}/{total} | "
            f"avg={report.avg_duration_ms:.0f}ms | "
            f"p95={report.p95_duration_ms:.0f}ms | "
            f"rps={report.rps:.1f}"
        )
        return report

    async def multi_url_stress(
        self,
        urls: List[Dict],
        concurrency: int = 5,
        iterations: int = 2,
        timeout: int = 15,
    ) -> List[LoadTestReport]:
        """
        对多个URL依次进行压力测试。

        Args:
            urls: [{"url": str, "name": str, ...}]
        """
        reports = []
        for url_info in urls:
            url = url_info.get("url", "")
            name = url_info.get("name", url)
            report = await self.stress_url(
                url=url,
                concurrency=concurrency,
                iterations=iterations,
                timeout=timeout,
                test_name=f"{name}_stress",
            )
            reports.append(report)
        return reports

    async def stability_test(
        self,
        url: str,
        duration_seconds: int = 60,
        interval_seconds: float = 1.0,
        concurrency: int = 3,
        test_name: str = "",
    ) -> LoadTestReport:
        """
        长时间稳定性测试：在指定时间段内持续发起请求。
        """
        if not test_name:
            test_name = f"stability_{Path(url.split('/')[2]).stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        logger.info(f"Starting stability test: {test_name} | url={url} | duration={duration_seconds}s")

        import aiohttp

        results: List[RequestResult] = []
        deadline = time.time() + duration_seconds
        concurrency_counter = 0

        while time.time() < deadline:
            tasks = []
            for _ in range(concurrency):
                tasks.append(self._aiohttp_get(url))
            batch = await asyncio.gather(*tasks, return_exceptions=True)
            for i, r in enumerate(batch):
                if isinstance(r, RequestResult):
                    results.append(r)
                elif isinstance(r, Exception):
                    results.append(RequestResult(
                        url=url, method="GET", status_code=0,
                        duration_ms=0, content_length=0, success=False,
                        error_message=str(r),
                    ))
            await asyncio.sleep(max(0, interval_seconds - 0.05))

        durations = [r.duration_ms for r in results if r.duration_ms > 0]
        successful = sum(1 for r in results if r.success)

        report = LoadTestReport(
            test_name=test_name,
            start_time=datetime.now().isoformat(),
            end_time=datetime.now().isoformat(),
            duration_seconds=duration_seconds,
            total_requests=len(results),
            successful_requests=successful,
            failed_requests=len(results) - successful,
            timeout_count=sum(1 for r in results if "timeout" in (r.error_message or "").lower()),
            error_count=sum(1 for r in results if r.status_code == 0),
            success_rate=(successful / max(len(results), 1)) * 100,
            avg_duration_ms=statistics.mean(durations) if durations else 0,
            min_duration_ms=min(durations) if durations else 0,
            max_duration_ms=max(durations) if durations else 0,
            p50_duration_ms=self._percentile(durations, 50) if durations else 0,
            p90_duration_ms=self._percentile(durations, 90) if durations else 0,
            p95_duration_ms=self._percentile(durations, 95) if durations else 0,
            p99_duration_ms=self._percentile(durations, 99) if durations else 0,
            rps=len(results) / max(duration_seconds, 1),
            results=results,
        )

        self._reports.append(report)
        self._save_report(report)
        return report

    # ==================== 内部方法 ====================

    async def _single_request(
        self,
        url: str,
        semaphore: asyncio.Semaphore,
        timeout: int,
        iteration: int,
        req_id: int,
    ) -> RequestResult:
        """执行单次HTTP请求"""
        async with semaphore:
            return await self._aiohttp_get(url, timeout=timeout)

    async def _aiohttp_get(
        self, url: str, timeout: int = 15
    ) -> RequestResult:
        """使用 aiohttp 发起GET请求"""
        try:
            import aiohttp
            conn_timeout = aiohttp.ClientTimeout(total=timeout)
            async with aiohttp.ClientSession(
                timeout=conn_timeout, trust_env=True
            ) as session:
                start = time.time()
                async with session.get(url, ssl=False, allow_redirects=True) as resp:
                    body = await resp.read()
                    duration_ms = (time.time() - start) * 1000
                    return RequestResult(
                        url=url,
                        method="GET",
                        status_code=resp.status,
                        duration_ms=duration_ms,
                        content_length=len(body),
                        success=200 <= resp.status < 400,
                        headers=dict(resp.headers),
                    )
        except asyncio.TimeoutError:
            return RequestResult(
                url=url, method="GET", status_code=0,
                duration_ms=(time.time() - start) * 1000 if 'start' in dir() else timeout * 1000,
                content_length=0, success=False,
                error_message="timeout",
            )
        except Exception as e:
            return RequestResult(
                url=url, method="GET", status_code=0,
                duration_ms=0, content_length=0, success=False,
                error_message=str(e),
            )

    @staticmethod
    def _percentile(data: List[float], p: int) -> float:
        """计算百分位数"""
        if not data:
            return 0.0
        sorted_data = sorted(data)
        k = (len(sorted_data) - 1) * (p / 100)
        f = int(k)
        c = f + 1
        if c >= len(sorted_data):
            return sorted_data[f]
        return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])

    def _save_report(self, report: LoadTestReport):
        """保存报告到文件"""
        path = self.output_dir / f"{report.test_name}.json"
        path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, default=str))
        logger.info(f"Report saved: {path}")

    def get_all_reports(self) -> List[LoadTestReport]:
        return list(self._reports)

    def generate_summary(self) -> Dict[str, Any]:
        """生成所有报告的汇总摘要"""
        if not self._reports:
            return {"message": "No reports generated"}

        total_reqs = sum(r.total_requests for r in self._reports)
        total_success = sum(r.successful_requests for r in self._reports)
        total_timeout = sum(r.timeout_count for r in self._reports)
        total_errors = sum(r.error_count for r in self._reports)
        all_durations = [
            r.avg_duration_ms for r in self._reports if r.avg_duration_ms > 0
        ]

        return {
            "generated_at": datetime.now().isoformat(),
            "total_reports": len(self._reports),
            "total_requests": total_reqs,
            "total_success": total_success,
            "total_failed": total_reqs - total_success,
            "total_timeout": total_timeout,
            "total_errors": total_errors,
            "overall_success_rate": round(total_success / max(total_reqs, 1) * 100, 2),
            "avg_duration_ms_all": round(statistics.mean(all_durations), 2) if all_durations else 0,
            "reports": [r.to_dict() for r in self._reports],
        }


def run_load_test(
    urls: List[str],
    concurrency: int = 5,
    iterations: int = 2,
    timeout: int = 15,
    output_dir: Optional[Path] = None,
) -> List[LoadTestReport]:
    """便捷函数：批量对URL列表执行压力测试"""
    tester = LoadTester(output_dir=output_dir)
    url_dicts = [{"url": u, "name": u.split("//")[-1].split("/")[0]} for u in urls]
    return asyncio.run(tester.multi_url_stress(url_dicts, concurrency, iterations, timeout))


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')

    test_urls = [
        "https://www.gov.cn",
        "https://www.bing.com",
        "https://github.com",
        "https://www.zhihu.com",
    ]

    results = asyncio.run(
        LoadTester().multi_url_stress(
            [{"url": u, "name": u} for u in test_urls],
            concurrency=3,
            iterations=2,
            timeout=10,
        )
    )

    for r in results:
        print(f"\n{'='*50}")
        print(f"{r.test_name}")
        print(f"  requests: {r.total_requests}  success: {r.successful_requests}  rate: {r.success_rate:.1f}%")
        print(f"  avg: {r.avg_duration_ms:.0f}ms  p95: {r.p95_duration_ms:.0f}ms  max: {r.max_duration_ms:.0f}ms")
        print(f"  rps: {r.rps:.1f}")
