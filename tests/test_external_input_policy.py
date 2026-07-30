"""tests/test_external_input_policy.py — IngestionPolicy 路由（P3 + P8）测试

覆盖：
  1. PolicyRule.matches：source_type/signal/fields.* 匹配维度，未识别维度判不匹配
  2. decide_action：首个匹配规则生效、都不匹配时回退 notify_only
  3. load_policies：文件缺失返回空列表；顶层结构错误报错；非法 action 跳过该条
  4. notify_only 落地：alerts.jsonl 写入、list_pending_alerts 只返回未 ack 的
  5. acknowledge_alert：标记后不再出现在 list_pending_alerts
  6. run_ingestion_policy_once：端到端跑通 gateway.publish_event → policy 路由
     → alerts.jsonl，游标正确推进（第二次调用不重复处理）
  7. enqueue_turn 命中时、未传 input_queue：不写 alert，只计入 skipped 计数
     （向后兼容 P3 阶段调用方式）
  8. [P5] enqueue_turn 命中且传入 input_queue：真正调用
     InputQueue.enqueue(initiator=...)，task_template 占位符渲染正确
  9. [P8] goal_candidate 已被移除：VALID_ACTIONS 不再包含它，配置里写
     `action: goal_candidate` 的规则在 load_policies() 里按"非法 action
     跳过该条"处理，不会导致外部输入被凭空建成新 Goal
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.external_input.gateway import publish_event
from mini_agent.external_input.policy import (
    VALID_ACTIONS,
    PoliciesConfigError,
    PolicyRule,
    acknowledge_alert,
    decide_action,
    list_pending_alerts,
    load_policies,
    run_ingestion_policy_once,
)
from mini_agent.external_input.source import ExternalInputEvent
from mini_agent.storage.paths import AgentPaths


class _StubInputQueue:
    """InputQueue 的最小 duck-typed 替身，只记录 enqueue() 的调用参数，
    不涉及真正的线程/队列语义（policy.py 只用得到 enqueue()）。"""

    def __init__(self):
        self.calls: list[dict] = []

    def enqueue(self, message, turn_id=None, initiator="user", meta=None):
        self.calls.append({"message": message, "initiator": initiator, "meta": meta or {}})
        return f"turn_{len(self.calls)}"


def _make_event(source_type="watch", signal="new_item", fields=None, event_id="e1"):
    return ExternalInputEvent(
        id=event_id, source_id="src1", source_type=source_type, signal=signal,
        title="标题", fields=fields or {},
    )


class TestPolicyRuleMatching(unittest.TestCase):
    def test_matches_source_type_and_signal(self):
        rule = PolicyRule(match={"source_type": "watch", "signal": "price_drop"}, action="notify_only")
        self.assertTrue(rule.matches(_make_event(source_type="watch", signal="price_drop")))
        self.assertFalse(rule.matches(_make_event(source_type="watch", signal="new_item")))
        self.assertFalse(rule.matches(_make_event(source_type="webhook", signal="price_drop")))

    def test_matches_fields_prefix(self):
        rule = PolicyRule(match={"fields.priority": "high"}, action="enqueue_turn")
        self.assertTrue(rule.matches(_make_event(fields={"priority": "high"})))
        self.assertFalse(rule.matches(_make_event(fields={"priority": "low"})))
        self.assertFalse(rule.matches(_make_event(fields={})))

    def test_empty_match_matches_everything(self):
        rule = PolicyRule(match={}, action="notify_only")
        self.assertTrue(rule.matches(_make_event()))

    def test_unrecognized_match_key_means_no_match(self):
        rule = PolicyRule(match={"nonsense_key": "x"}, action="notify_only")
        self.assertFalse(rule.matches(_make_event()))


class TestDecideAction(unittest.TestCase):
    def test_first_matching_rule_wins(self):
        rules = [
            PolicyRule(match={"source_type": "watch", "signal": "price_drop"}, action="notify_only"),
            PolicyRule(match={"source_type": "watch"}, action="enqueue_turn"),
        ]
        decided = decide_action(_make_event(signal="price_drop"), rules)
        self.assertEqual(decided.action, "notify_only")
        decided2 = decide_action(_make_event(signal="other_signal"), rules)
        self.assertEqual(decided2.action, "enqueue_turn")

    def test_no_match_falls_back_to_default(self):
        rules = [PolicyRule(match={"source_type": "webhook"}, action="enqueue_turn")]
        decided = decide_action(_make_event(source_type="watch"), rules)
        self.assertEqual(decided.action, "notify_only")

    def test_empty_rules_falls_back_to_default(self):
        decided = decide_action(_make_event(), [])
        self.assertEqual(decided.action, "notify_only")


class TestLoadPolicies(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write(self, content: str) -> None:
        p = self.paths.external_input_policies_config
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def test_missing_file_returns_empty(self):
        self.assertEqual(load_policies(self.paths), [])

    def test_load_rules(self):
        self._write(
            "- match:\n"
            "    source_type: watch\n"
            "    signal: price_drop\n"
            "  action: notify_only\n"
            "- match:\n"
            "    source_type: webhook\n"
            "    signal: urgent_message\n"
            "  action: enqueue_turn\n"
            "  enqueue:\n"
            "    initiator: external\n"
        )
        rules = load_policies(self.paths)
        self.assertEqual(len(rules), 2)
        self.assertEqual(rules[1].enqueue.get("initiator"), "external")

    def test_invalid_top_level_raises(self):
        self._write("match:\n  source_type: watch\naction: notify_only\n")
        with self.assertRaises(PoliciesConfigError):
            load_policies(self.paths)

    def test_invalid_action_skipped(self):
        self._write(
            "- match: {source_type: watch}\n  action: not_a_real_action\n"
            "- match: {source_type: webhook}\n  action: notify_only\n"
        )
        rules = load_policies(self.paths)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].match["source_type"], "webhook")


class TestNotifyOnlyAlerts(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmpdir.name))

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_run_policy_writes_alert_for_default_notify_only(self):
        publish_event(self.paths, _make_event(event_id="a1"))
        summary = run_ingestion_policy_once(self.paths, consumer_name="c1")
        self.assertEqual(summary.processed, 1)
        self.assertEqual(summary.notify_only, 1)

        pending = list_pending_alerts(self.paths)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["title"], "标题")
        self.assertFalse(pending[0]["acknowledged"])

    def test_acknowledge_removes_from_pending(self):
        publish_event(self.paths, _make_event(event_id="a2"))
        run_ingestion_policy_once(self.paths, consumer_name="c2")
        pending = list_pending_alerts(self.paths)
        alert_id = pending[0]["alert_id"]

        ok = acknowledge_alert(self.paths, alert_id)
        self.assertTrue(ok)
        self.assertEqual(list_pending_alerts(self.paths), [])

        # 重复 ack 同一条返回 False（已经不是"未确认"状态了）
        self.assertFalse(acknowledge_alert(self.paths, alert_id))

    def test_acknowledge_unknown_id_returns_false(self):
        self.assertFalse(acknowledge_alert(self.paths, "does-not-exist"))

    def test_cursor_advances_no_duplicate_processing(self):
        publish_event(self.paths, _make_event(event_id="a3"))
        first = run_ingestion_policy_once(self.paths, consumer_name="c3")
        second = run_ingestion_policy_once(self.paths, consumer_name="c3")
        self.assertEqual(first.processed, 1)
        self.assertEqual(second.processed, 0)
        self.assertEqual(len(list_pending_alerts(self.paths)), 1)

    def test_same_event_id_republished_does_not_duplicate_alert(self):
        """[BUGFIX] 复现并锁定修复：source 自身状态异常时可能对同一个
        event_id 重新 publish_event（这里直接模拟"网关层面又发布了一次
        携带同一个 event.id 的事件"，不依赖具体是哪个 source 出的问题），
        之前 `_notify_only()` 没有基于 alert_id 去重，会往 alerts.jsonl
        写入两条 alert_id 完全相同的记录——看板拿 alert_id 当 st.button
        的 key 用，直接触发 StreamlitDuplicateElementKey 崩溃。"""
        publish_event(self.paths, _make_event(event_id="dup1"))
        run_ingestion_policy_once(self.paths, consumer_name="cA")
        # 换一个新 consumer_name，模拟"游标层面认为这是全新事件"的场景——
        # 即便如此，_notify_only 也应该按 alert_id 去重，不会再追加一条。
        publish_event(self.paths, _make_event(event_id="dup1"))
        run_ingestion_policy_once(self.paths, consumer_name="cB")

        pending = list_pending_alerts(self.paths)
        self.assertEqual(len(pending), 1)
        alert_ids = [p["alert_id"] for p in pending]
        self.assertEqual(len(alert_ids), len(set(alert_ids)))

    def test_duplicate_not_reintroduced_after_acknowledge(self):
        """[BUGFIX] 同一个 alert_id 已经出现过（哪怕已经被 ack 掉），
        再次收到相同事件也不应该重新写入一条新的未确认记录。"""
        publish_event(self.paths, _make_event(event_id="dup2"))
        run_ingestion_policy_once(self.paths, consumer_name="cC")
        alert_id = list_pending_alerts(self.paths)[0]["alert_id"]
        acknowledge_alert(self.paths, alert_id)
        self.assertEqual(list_pending_alerts(self.paths), [])

        publish_event(self.paths, _make_event(event_id="dup2"))
        run_ingestion_policy_once(self.paths, consumer_name="cD")
        self.assertEqual(list_pending_alerts(self.paths), [])

    def test_enqueue_turn_does_not_create_alerts_when_skipped(self):
        policies_path = self.paths.external_input_policies_config
        policies_path.parent.mkdir(parents=True, exist_ok=True)
        policies_path.write_text(
            "- match: {signal: urgent_signal}\n  action: enqueue_turn\n",
            encoding="utf-8",
        )
        publish_event(self.paths, _make_event(event_id="g2", signal="urgent_signal"))

        summary = run_ingestion_policy_once(self.paths, consumer_name="c4")
        self.assertEqual(summary.processed, 1)
        self.assertEqual(summary.enqueue_turn_skipped, 1)
        self.assertEqual(summary.notify_only, 0)
        self.assertEqual(list_pending_alerts(self.paths), [])

    def test_goal_candidate_is_not_a_valid_action(self):
        """[P8] goal_candidate 落点已移除：外部输入与 Goal/Objective 的关联
        只能通过 goal_relevance.py::GoalRelevanceEngine 对已有 Goal 做
        attach/advance，IngestionPolicy 不应该再凭空创建新 Goal。"""
        self.assertNotIn("goal_candidate", VALID_ACTIONS)
        with self.assertRaises(ValueError):
            PolicyRule.from_dict({"match": {}, "action": "goal_candidate"})

    def test_legacy_goal_candidate_rule_in_yaml_is_skipped_not_fatal(self):
        """历史 policies.yaml 里残留 `action: goal_candidate` 的规则：
        按 load_policies() 既有的"单条规则非法即跳过"容错策略处理，不会
        导致整份配置报错，也不会被当成合法 action 命中。"""
        policies_path = self.paths.external_input_policies_config
        policies_path.parent.mkdir(parents=True, exist_ok=True)
        policies_path.write_text(
            "- match: {signal: hot_signal}\n  action: goal_candidate\n"
            "- match: {}\n  action: notify_only\n",
            encoding="utf-8",
        )
        rules = load_policies(self.paths)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].action, "notify_only")

    def test_enqueue_turn_calls_input_queue_when_provided(self):
        policies_path = self.paths.external_input_policies_config
        policies_path.parent.mkdir(parents=True, exist_ok=True)
        policies_path.write_text(
            "- match: {signal: urgent_signal}\n"
            "  action: enqueue_turn\n"
            "  enqueue:\n"
            "    initiator: external\n"
            "    task_template: \"紧急：{title} - {detail}\"\n",
            encoding="utf-8",
        )
        publish_event(
            self.paths,
            ExternalInputEvent(
                id="g5", source_id="src1", source_type="webhook", signal="urgent_signal",
                title="服务器告警", detail="磁盘使用率 95%",
            ),
        )
        iq = _StubInputQueue()
        summary = run_ingestion_policy_once(self.paths, consumer_name="c7", input_queue=iq)
        self.assertEqual(summary.enqueue_turn, 1)
        self.assertEqual(summary.enqueue_turn_skipped, 0)
        self.assertEqual(len(iq.calls), 1)
        self.assertEqual(iq.calls[0]["message"], "紧急：服务器告警 - 磁盘使用率 95%")
        self.assertEqual(iq.calls[0]["initiator"], "external")
        self.assertEqual(iq.calls[0]["meta"]["event_id"], "g5")

    def test_enqueue_turn_default_template_without_config(self):
        policies_path = self.paths.external_input_policies_config
        policies_path.parent.mkdir(parents=True, exist_ok=True)
        policies_path.write_text(
            "- match: {signal: urgent_signal}\n  action: enqueue_turn\n",
            encoding="utf-8",
        )
        publish_event(self.paths, _make_event(event_id="g6", signal="urgent_signal"))
        iq = _StubInputQueue()
        run_ingestion_policy_once(self.paths, consumer_name="c8", input_queue=iq)
        self.assertIn("标题", iq.calls[0]["message"])
        self.assertEqual(iq.calls[0]["initiator"], "external")


if __name__ == "__main__":
    unittest.main()
