"""tests/test_failure_pattern_store.py — 统一失败模式库（F2）专属单测。

补齐 next_doc/system_connectivity_gaps_and_missing_capabilities_plan.md
P0 建议里指出的技术债：此前该模块只跑通了周边集成测试
（GoalRunner/TurnJudge stuck 端到端手工验证），自己没有专属单测。

覆盖：
  1. _normalize_category：纯标点/空白 → "unknown"；纯中文标题正确取词
     （中文没有空格分词，验证不会因为 split() 对中文失效而整句变成一个
     "词"，也不会因为正则误删中文字符导致内容丢失）
  2. _root_cause_tag：命中 timeout/permission/tool_missing/rate_limit
     四类规则 + 都不命中时落到 other
  3. 三路数据源各自为空时，run_failure_pattern_aggregation_once 不报错，
     返回空 patterns
  4. objective_executions.json 数据源：同类失败正确聚合为一个 pattern
     （occurrence_count 累加，而不是 3 条孤立记录）
  5. goal_state dead_ends 数据源：dict 结构的 dead_end 优先用 reason 字段
     做根因匹配（不会被 round/progress 之类的数字噪音干扰）
  6. turn_judge_stuck_events 数据源：record_turn_judge_stuck_event 写入
     后能被聚合读到
  7. 多次运行聚合：occurrence_count 正确累加而不是覆盖，first_seen 保留
     最早、last_seen 更新为最新
  8. 已有 pattern 但本轮扫描窗口未命中时，不会被静默丢弃
  9. get_patterns_for_category：只返回达到 min_occurrence 阈值的 pattern
  10. store 文件损坏（非法 JSON / 合法 JSON 但非 dict）不崩溃，退化为空
  11. objective_executions.json 文件损坏不影响其余两路数据源正常聚合
      （单一数据源异常的隔离性）
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from mini_agent.storage.paths import AgentPaths
from mini_agent.evolution.failure_pattern_store import (
    _normalize_category,
    _root_cause_tag,
    get_patterns_for_category,
    load_failure_patterns,
    record_turn_judge_stuck_event,
    run_failure_pattern_aggregation_once,
)


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


def _write_objective_executions(paths: AgentPaths, executions: list[dict]) -> None:
    p = paths.workdir_dir / "objective_executions.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"executions": executions}, ensure_ascii=False), encoding="utf-8")


def _write_goal_state(paths: AgentPaths, session_id: str, goal_text: str, dead_ends: list) -> None:
    sd = paths.sessions_dir / session_id
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "goal_state.json").write_text(
        json.dumps({"goal_text": goal_text, "dead_ends": dead_ends}, ensure_ascii=False),
        encoding="utf-8",
    )


class TestNormalizeCategory(unittest.TestCase):
    def test_empty_text_is_unknown(self):
        self.assertEqual(_normalize_category(""), "unknown")

    def test_all_punctuation_is_unknown(self):
        self.assertEqual(_normalize_category("！！！ 。。。 ,,, ???"), "unknown")

    def test_pure_chinese_title_normalizes_without_losing_content(self):
        result = _normalize_category("部署脚本反复超时失败排查")
        self.assertNotEqual(result, "unknown")
        self.assertIn("部署脚本反复超时失败排查", result.replace(" ", ""))

    def test_mixed_english_and_punctuation(self):
        result = _normalize_category("Deploy failed: timeout!! retry???")
        self.assertNotEqual(result, "unknown")
        self.assertNotIn("!", result)
        self.assertNotIn(":", result)


class TestRootCauseTag(unittest.TestCase):
    def test_timeout(self):
        self.assertEqual(_root_cause_tag("connection timed out after 30s"), "timeout")
        self.assertEqual(_root_cause_tag("请求超时"), "timeout")

    def test_permission(self):
        self.assertEqual(_root_cause_tag("permission denied"), "permission")
        self.assertEqual(_root_cause_tag("没有权限访问该文件"), "permission")

    def test_tool_missing(self):
        self.assertEqual(_root_cause_tag("command not found: foo"), "tool_missing")
        self.assertEqual(_root_cause_tag("工具不存在"), "tool_missing")

    def test_rate_limit(self):
        self.assertEqual(_root_cause_tag("HTTP 429 rate limit exceeded"), "rate_limit")

    def test_other_fallback(self):
        self.assertEqual(_root_cause_tag("一些完全无法归类的奇怪错误"), "other")

    def test_empty_string_falls_back_to_other(self):
        self.assertEqual(_root_cause_tag(""), "other")


class TestAggregationEmptySources(unittest.TestCase):
    def test_all_sources_empty_returns_empty_patterns(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            summary = run_failure_pattern_aggregation_once(paths)
            self.assertTrue(summary.ok)
            self.assertEqual(summary.patterns, [])


class TestObjectiveExecutionsSource(unittest.TestCase):
    def test_same_category_failures_merge_into_one_pattern(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_objective_executions(paths, [
                {
                    "objective_title": "部署脚本升级",
                    "finished_at": time.time(),
                    "steps": [
                        {"error_msg": "连接超时 timeout"},
                        {"error_msg": "again timeout while connecting"},
                        {"error_msg": ""},  # 空 error_msg 不计入
                    ],
                },
                {
                    "objective_title": "部署脚本升级",
                    "finished_at": time.time(),
                    "steps": [{"error_msg": "third timeout occurrence"}],
                },
            ])
            summary = run_failure_pattern_aggregation_once(paths)
            self.assertTrue(summary.ok)
            # 三次同类超时失败应聚合为 1 个 pattern，而不是 3 条孤立记录
            objective_patterns = [p for p in summary.patterns if p.source == "objective"]
            self.assertEqual(len(objective_patterns), 1)
            self.assertEqual(objective_patterns[0].occurrence_count, 3)
            self.assertEqual(objective_patterns[0].root_cause_tag, "timeout")


class TestDeadEndSource(unittest.TestCase):
    def test_dict_dead_end_uses_reason_field_not_full_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_goal_state(
                paths, "sess1", "修复权限问题",
                dead_ends=[{"round": 3, "progress": 0.1, "reason": "permission denied on /etc"}],
            )
            summary = run_failure_pattern_aggregation_once(paths)
            dead_end_patterns = [p for p in summary.patterns if p.source == "dead_end"]
            self.assertEqual(len(dead_end_patterns), 1)
            self.assertEqual(dead_end_patterns[0].root_cause_tag, "permission")

    def test_string_dead_end_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_goal_state(paths, "sess2", "重试连接", dead_ends=["connection timed out repeatedly"])
            summary = run_failure_pattern_aggregation_once(paths)
            dead_end_patterns = [p for p in summary.patterns if p.source == "dead_end"]
            self.assertEqual(len(dead_end_patterns), 1)
            self.assertEqual(dead_end_patterns[0].root_cause_tag, "timeout")


class TestTurnJudgeStuckSource(unittest.TestCase):
    def test_recorded_events_get_aggregated(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            for _ in range(3):
                record_turn_judge_stuck_event(
                    paths, task_hint="用户反复要求同一个格式转换任务",
                    reason="连续多轮无实质进展",
                )
            summary = run_failure_pattern_aggregation_once(paths)
            stuck_patterns = [p for p in summary.patterns if p.source == "turn_judge_stuck"]
            self.assertEqual(len(stuck_patterns), 1)
            self.assertEqual(stuck_patterns[0].occurrence_count, 3)


class TestMergeAcrossRuns(unittest.TestCase):
    def test_occurrence_count_accumulates_across_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_objective_executions(paths, [{
                "objective_title": "同步数据",
                "finished_at": 1000.0,
                "steps": [{"error_msg": "timeout syncing"}],
            }])
            run_failure_pattern_aggregation_once(paths)

            _write_objective_executions(paths, [{
                "objective_title": "同步数据",
                "finished_at": 2000.0,
                "steps": [{"error_msg": "timeout syncing again"}],
            }])
            summary2 = run_failure_pattern_aggregation_once(paths)

            objective_patterns = [p for p in summary2.patterns if p.source == "objective"]
            self.assertEqual(len(objective_patterns), 1)
            self.assertEqual(objective_patterns[0].occurrence_count, 2)
            self.assertEqual(objective_patterns[0].first_seen, 1000.0)
            self.assertEqual(objective_patterns[0].last_seen, 2000.0)

    def test_existing_pattern_not_dropped_when_not_rehit(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_objective_executions(paths, [{
                "objective_title": "一次性任务",
                "finished_at": time.time(),
                "steps": [{"error_msg": "timeout once"}],
            }])
            run_failure_pattern_aggregation_once(paths)

            # 第二轮扫描窗口里这个任务不再出现（objective_executions 清空）
            _write_objective_executions(paths, [])
            summary2 = run_failure_pattern_aggregation_once(paths)

            all_patterns = load_failure_patterns(paths)
            self.assertEqual(len(all_patterns), 1)
            self.assertEqual(all_patterns[0]["occurrence_count"], 1)


class TestGetPatternsForCategory(unittest.TestCase):
    def test_below_threshold_not_returned(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_objective_executions(paths, [{
                "objective_title": "低频失败任务",
                "finished_at": time.time(),
                "steps": [{"error_msg": "timeout only once"}],
            }])
            run_failure_pattern_aggregation_once(paths)
            hits = get_patterns_for_category(paths, "低频失败任务", min_occurrence=3)
            self.assertEqual(hits, [])

    def test_at_or_above_threshold_returned(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            _write_objective_executions(paths, [{
                "objective_title": "高频失败任务",
                "finished_at": time.time(),
                "steps": [
                    {"error_msg": "timeout a"},
                    {"error_msg": "timeout b"},
                    {"error_msg": "timeout c"},
                ],
            }])
            run_failure_pattern_aggregation_once(paths)
            hits = get_patterns_for_category(paths, "高频失败任务", min_occurrence=3)
            self.assertEqual(len(hits), 1)


class TestStoreCorruption(unittest.TestCase):
    def test_invalid_json_store_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            store_path = paths.workdir_dir / "failure_pattern_store.json"
            store_path.parent.mkdir(parents=True, exist_ok=True)
            store_path.write_text("{not valid json", encoding="utf-8")

            self.assertEqual(load_failure_patterns(paths), [])
            self.assertEqual(get_patterns_for_category(paths, "任意类别"), [])
            # 聚合本身也不应因为已损坏的旧 store 而崩溃
            summary = run_failure_pattern_aggregation_once(paths)
            self.assertTrue(summary.ok)

    def test_valid_json_but_non_dict_store_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            store_path = paths.workdir_dir / "failure_pattern_store.json"
            store_path.parent.mkdir(parents=True, exist_ok=True)
            store_path.write_text("[1, 2, 3]", encoding="utf-8")

            self.assertEqual(load_failure_patterns(paths), [])
            summary = run_failure_pattern_aggregation_once(paths)
            self.assertTrue(summary.ok)


class TestSourceIsolation(unittest.TestCase):
    def test_corrupted_objective_executions_does_not_block_other_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            exec_path = paths.workdir_dir / "objective_executions.json"
            exec_path.parent.mkdir(parents=True, exist_ok=True)
            exec_path.write_text("{not valid json at all", encoding="utf-8")

            _write_goal_state(
                paths, "sess_iso", "隔离性验证任务",
                dead_ends=[{"reason": "permission denied here"}],
            )

            summary = run_failure_pattern_aggregation_once(paths)
            # objective 源读取失败被吞掉（内部函数捕获异常返回空列表），
            # dead_end 源仍然正常产出结果
            dead_end_patterns = [p for p in summary.patterns if p.source == "dead_end"]
            self.assertEqual(len(dead_end_patterns), 1)


if __name__ == "__main__":
    unittest.main()
