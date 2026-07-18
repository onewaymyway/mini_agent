"""
tests/test_extraction_stats.py — wiki 提取层改进计划 E2 方案B 单元测试

覆盖：
  - history/compression.py::_log_extraction_stats 正确 append 写入
  - wiki/stats.py::compute_extraction_stats 均值计算正确性
  - last_n 截断行为
  - 空日志 / 文件不存在时的降级行为
"""

import sys
import json
from types import SimpleNamespace

import pytest

sys.path.insert(0, "src")

from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.stats import compute_extraction_stats
from mini_agent.history.compression import _log_extraction_stats


@pytest.fixture
def paths(tmp_path):
    return AgentPaths(tmp_path)


def test_compute_extraction_stats_empty(paths):
    stats = compute_extraction_stats(paths)
    assert stats.total_batches == 0
    assert stats.avg_entities_per_extraction == 0.0
    assert stats.avg_facts_per_extraction == 0.0


def test_log_extraction_stats_appends_and_averages(paths):
    cfg = SimpleNamespace(project_root=str(paths.project_root))

    _log_extraction_stats(cfg, num_decisions=1, num_entities=2, num_facts=4)
    _log_extraction_stats(cfg, num_decisions=0, num_entities=0, num_facts=0)
    _log_extraction_stats(cfg, num_decisions=2, num_entities=4, num_facts=2)

    log_path = paths.extraction_stats_log
    assert log_path.exists()
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    for line in lines:
        record = json.loads(line)
        assert set(record.keys()) >= {"ts", "decisions", "entities", "facts"}

    stats = compute_extraction_stats(paths)
    assert stats.total_batches == 3
    assert stats.avg_entities_per_extraction == pytest.approx((2 + 0 + 4) / 3)
    assert stats.avg_facts_per_extraction == pytest.approx((4 + 0 + 2) / 3)
    assert stats.avg_decisions_per_extraction == pytest.approx((1 + 0 + 2) / 3)
    # 只有第二条记录 entities 和 facts 同时为 0
    assert stats.zero_entities_and_facts_ratio == pytest.approx(1 / 3)


def test_compute_extraction_stats_last_n(paths):
    cfg = SimpleNamespace(project_root=str(paths.project_root))
    for i in range(5):
        _log_extraction_stats(cfg, num_decisions=0, num_entities=i, num_facts=0)

    stats_all = compute_extraction_stats(paths)
    assert stats_all.total_batches == 5

    stats_last2 = compute_extraction_stats(paths, last_n=2)
    assert stats_last2.total_batches == 2
    # 最后两条 entities 分别是 3, 4
    assert stats_last2.avg_entities_per_extraction == pytest.approx((3 + 4) / 2)


def test_compute_extraction_stats_tolerates_corrupt_lines(paths):
    log_path = paths.extraction_stats_log
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        '{"decisions": 1, "entities": 2, "facts": 3}\n'
        "not-json\n"
        '{"decisions": 0, "entities": 1, "facts": 1}\n',
        encoding="utf-8",
    )

    stats = compute_extraction_stats(paths)
    assert stats.total_batches == 2
    assert stats.avg_entities_per_extraction == pytest.approx((2 + 1) / 2)
