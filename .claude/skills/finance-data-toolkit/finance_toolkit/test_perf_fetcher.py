"""
测试文件: test_perf_fetcher.py
"""

import pytest


def test_example():
    """测试示例"""
    assert True


def test_deliverable_naming():
    """测试产出物命名规范"""
    filename = "test_perf_fetcher.py"
    assert filename.startswith("test_")
    assert filename.endswith(".py")
