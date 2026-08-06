"""
测试调度器

负责管理测试任务，控制执行顺序和并发。
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Dict, List, Optional

from .models import Priority, TestCase, TestResult, TestRun, WebsiteConfig
from .executor import TestCaseExecutor

logger = logging.getLogger(__name__)


class TestScheduler:
    """测试调度器"""

    def __init__(self, max_concurrency: int = 3):
        self.max_concurrency = max_concurrency
        self._runs: Dict[str, TestRun] = {}
        self._website_configs: Dict[str, WebsiteConfig] = {}

    def register_website(self, config: WebsiteConfig) -> None:
        """
        注册网站配置

        Args:
            config: 网站配置
        """
        self._website_configs[config.name] = config
        logger.info(f"注册网站: {config.name} ({config.url})")

    def get_website_config(self, name: str) -> Optional[WebsiteConfig]:
        """
        获取网站配置

        Args:
            name: 网站名称

        Returns:
            网站配置，不存在则返回 None
        """
        return self._website_configs.get(name)

    def get_websites_by_category(self, category: str) -> List[WebsiteConfig]:
        """
        按分类获取网站列表

        Args:
            category: 分类代码

        Returns:
            网站配置列表
        """
        return [
            config for config in self._website_configs.values()
            if config.category.value == category
        ]

    def get_websites_by_priority(self, priority: Priority) -> List[WebsiteConfig]:
        """
        按优先级获取网站列表

        Args:
            priority: 优先级

        Returns:
            网站配置列表
        """
        return [
            config for config in self._website_configs.values()
            if config.priority == priority
        ]

    async def run_test(
        self,
        website_name: str,
        test_cases: List[TestCase],
    ) -> TestRun:
        """
        执行网站测试

        Args:
            website_name: 网站名称
            test_cases: 测试用例列表

        Returns:
            TestRun: 测试执行记录
        """
        config = self._website_configs.get(website_name)
        if not config:
            raise ValueError(f"未找到网站配置: {website_name}")

        run_id = f"{website_name}_{int(time.time())}"
        run = TestRun(run_id=run_id, website_name=website_name)
        self._runs[run_id] = run

        logger.info(f"开始测试网站: {website_name}, 用例数: {len(test_cases)}")

        executor = TestCaseExecutor(config)

        # 按优先级排序
        sorted_cases = sorted(
            test_cases,
            key=lambda x: x.case_id,
        )

        # 并发执行测试用例
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def run_single_case(case: TestCase) -> TestResult:
            async with semaphore:
                return await executor.execute(case)

        # 执行所有用例
        tasks = [run_single_case(case) for case in sorted_cases]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 收集结果
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                # 创建失败结果
                fail_result = TestResult(
                    run_id=run_id,
                    website_name=website_name,
                    case_id=sorted_cases[i].case_id,
                    status=TestStatus.FAIL,
                    error_message=str(result),
                )
                run.add_result(fail_result)
            else:
                run.add_result(result)

        run.total_cases = len(test_cases)
        run.completed_at = datetime.now()

        logger.info(
            f"测试完成: {website_name}, "
            f"通过: {run.passed_cases}/{run.total_cases}, "
            f"成功率: {run.success_rate:.1%}"
        )

        return run

    def get_run(self, run_id: str) -> Optional[TestRun]:
        """
        获取测试执行记录

        Args:
            run_id: 执行 ID

        Returns:
            测试执行记录，不存在则返回 None
        """
        return self._runs.get(run_id)

    def get_all_runs(self) -> List[TestRun]:
        """获取所有测试执行记录"""
        return list(self._runs.values())

    def get_runs_by_website(self, website_name: str) -> List[TestRun]:
        """
        按网站名称获取测试执行记录

        Args:
            website_name: 网站名称

        Returns:
            测试执行记录列表
        """
        return [
            run for run in self._runs.values()
            if run.website_name == website_name
        ]

    def clear_old_runs(self, days: int = 7) -> int:
        """
        清理旧的测试执行记录

        Args:
            days: 保留天数

        Returns:
            清理的记录数量
        """
        from datetime import timedelta

        cutoff = datetime.now() - timedelta(days=days)
        old_runs = [
            run_id for run_id, run in self._runs.items()
            if run.completed_at and run.completed_at < cutoff
        ]

        for run_id in old_runs:
            del self._runs[run_id]

        logger.info(f"清理了 {len(old_runs)} 条旧的测试记录")
        return len(old_runs)
