"""
基础测试用例类

提供统一的测试用例基类，封装浏览器会话管理、断言方法和结果记录。
"""
import time
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class TestResult:
    """测试结果数据结构"""
    test_id: str
    website: str
    scenario_id: str
    scenario_name: str
    status: str  # passed, failed, skipped, error
    duration_seconds: float
    score: Optional[float] = None
    error_message: Optional[str] = None
    metrics: Dict[str, Any] = field(default_factory=dict)
    screenshot_path: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "test_id": self.test_id,
            "website": self.website,
            "scenario_id": self.scenario_id,
            "scenario_name": self.scenario_name,
            "status": self.status,
            "duration_seconds": round(self.duration_seconds, 3),
            "score": self.score,
            "error_message": self.error_message,
            "metrics": self.metrics,
            "screenshot_path": self.screenshot_path,
            "timestamp": self.timestamp,
        }


@dataclass
class TestConfig:
    """测试配置"""
    timeout_seconds: int = 30
    max_retries: int = 3
    headless: bool = True
    stealth_mode: bool = True
    screenshot_on_fail: bool = True
    pause_on_fail: bool = False


class BaseTestCase(ABC):
    """测试用例基类"""

    def __init__(self, config: Optional[TestConfig] = None):
        self.config = config or TestConfig()
        self.session = None
        self.results: List[TestResult] = []
        self._start_time: float = 0
        self._screenshot_dir: Path = Path(__file__).parent.parent / "output" / "screenshots"
        self._results_dir: Path = Path(__file__).parent.parent / "output" / "results"
        self._ensure_directories()

    def _ensure_directories(self):
        """确保输出目录存在"""
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)
        self._results_dir.mkdir(parents=True, exist_ok=True)

    async def setup(self):
        """测试前准备 - 由子类实现"""
        pass

    async def teardown(self):
        """测试后清理 - 由子类实现"""
        pass

    def record_result(self, result: TestResult):
        """记录测试结果"""
        self.results.append(result)
        if result.status == "failed":
            logger.error(f"Test {result.test_id} failed: {result.error_message}")
        elif result.status == "passed":
            logger.debug(f"Test {result.test_id} passed in {result.duration_seconds:.2f}s")

    def save_results(self, filename: str) -> str:
        """保存测试结果到JSON文件"""
        data = {
            "run_timestamp": datetime.now().isoformat(),
            "total_tests": len(self.results),
            "passed": sum(1 for r in self.results if r.status == "passed"),
            "failed": sum(1 for r in self.results if r.status == "failed"),
            "skipped": sum(1 for r in self.results if r.status == "skipped"),
            "results": [r.to_dict() for r in self.results],
        }
        path = self._results_dir / f"{filename}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        logger.info(f"Results saved to {path}")
        return str(path)

    @abstractmethod
    async def run_test(self) -> TestResult:
        """执行测试用例，由子类实现"""
        pass

    async def run_with_retry(self) -> TestResult:
        """带重试的测试执行"""
        last_error = None
        for attempt in range(1, self.config.max_retries + 1):
            self._start_time = time.time()
            try:
                result = await self.run_test()
                result.duration_seconds = time.time() - self._start_time
                self.record_result(result)
                return result
            except Exception as e:
                last_error = e
                logger.warning(f"Attempt {attempt}/{self.config.max_retries} failed: {e}")
                if attempt < self.config.max_retries:
                    await self._retry_cleanup()

        result = TestResult(
            test_id=self.__class__.__name__,
            website=getattr(self, "website", "unknown"),
            scenario_id="N/A",
            scenario_name="N/A",
            status="error",
            duration_seconds=time.time() - self._start_time,
            error_message=str(last_error),
        )
        self.record_result(result)
        return result

    async def _retry_cleanup(self):
        """重试前的清理工作"""
        if self.session:
            try:
                await self.session.close()
            except Exception:
                pass
            await self.setup()
