"""
测试执行器

提供统一的测试执行框架，支持 Mock 模式和真实浏览器模式。
"""
import pytest
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
import json

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))


class TestExecutor:
    """测试执行器"""
    
    def __init__(self, mode: str = "mock"):
        """
        初始化测试执行器
        
        Args:
            mode: 执行模式，"mock" 或 "real"
        """
        self.mode = mode
        self._results = []
    
    def run_tests(
        self,
        test_paths: List[str],
        markers: Optional[List[str]] = None,
        verbose: bool = False,
        generate_report: bool = True
    ) -> Dict[str, Any]:
        """
        运行测试
        
        Args:
            test_paths: 测试文件路径列表
            markers: pytest markers 过滤
            verbose: 是否详细输出
            generate_report: 是否生成报告
        
        Returns:
            测试结果字典
        """
        pytest_args = [
            "-v" if verbose else "",
            "--tb=short",
        ]
        
        if markers:
            pytest_args.append(f"-k {' or '.join(markers)}")
        
        pytest_args.extend(test_paths)
        
        # 运行 pytest
        exit_code = pytest.main(pytest_args)
        
        # 生成报告
        if generate_report:
            report = self._generate_report(exit_code)
            return report
        
        return {"exit_code": exit_code}
    
    def _generate_report(self, exit_code: int) -> Dict[str, Any]:
        """生成测试报告"""
        report = {
            "generated_at": datetime.now().isoformat(),
            "mode": self.mode,
            "exit_code": exit_code,
            "summary": {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "skipped": 0,
                "error": 0,
            },
            "details": []
        }
        
        return report
    
    def run_by_category(
        self,
        category: str,
        verbose: bool = False
    ) -> Dict[str, Any]:
        """
        按类别运行测试
        
        Args:
            category: 测试类别（ecommerce, news, social, gov, etc.）
            verbose: 是否详细输出
        
        Returns:
            测试结果字典
        """
        test_patterns = {
            "ecommerce": ["tests/compatibility/test_ecommerce.py"],
            "news": ["tests/compatibility/test_news.py"],
            "social": ["tests/compatibility/test_social.py"],
            "gov": ["tests/compatibility/test_gov.py"],
            "job": ["tests/compatibility/test_job.py"],
            "finance": ["tests/compatibility/test_finance.py"],
            "unit": ["tests/unit/"],
            "integration": ["tests/integration/"],
            "e2e": ["tests/e2e/"],
        }
        
        test_paths = test_patterns.get(category, [])
        if not test_paths:
            return {"error": f"Unknown category: {category}"}
        
        return self.run_tests(test_paths, verbose=verbose)
    
    def run_all(self, verbose: bool = False) -> Dict[str, Any]:
        """运行所有测试"""
        test_paths = [
            "tests/unit/",
            "tests/compatibility/",
            "tests/integration/",
            "tests/e2e/",
            "tests/evaluation/",
        ]
        return self.run_tests(test_paths, verbose=verbose)


# 全局执行器实例
_executor = TestExecutor()


def get_executor() -> TestExecutor:
    """获取全局测试执行器实例"""
    return _executor


def pytest_collection_modifyitems(config, items):
    """pytest hook：为测试项添加标记"""
    for item in items:
        # 根据文件路径添加标记
        path = str(item.fspath)
        if "unit" in path:
            item.add_marker(pytest.mark.unit)
        elif "integration" in path:
            item.add_marker(pytest.mark.integration)
        elif "e2e" in path:
            item.add_marker(pytest.mark.e2e)
        elif "compatibility" in path:
            item.add_marker(pytest.mark.browser)
