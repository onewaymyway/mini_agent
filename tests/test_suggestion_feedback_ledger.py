"""tests/test_suggestion_feedback_ledger.py — 建议反馈累积权重账本（F3）
专属单测。

补齐 next_doc/system_connectivity_gaps_and_missing_capabilities_plan.md
P0 建议里指出的技术债：此前该模块只跑通了周边集成测试
（test_improvement_backlog_merge.py），自己没有专属单测——尤其是"账本
文件损坏时 get_weight() 会不会崩"这类边界情况从未被验证过。

覆盖：
  1. 空类别 / 无历史记录 → get_weight 返回 1.0（中性，无影响）
  2. record_outcome 累加计数：多次 accepted/rejected 混合调用后
     get_entry 读到的计数正确
  3. rejected 达到阈值且 accepted=0 → get_weight 打七折（_DECAY_WEIGHT）
  4. accepted 达到阈值 → get_weight 加成（_BONUS_WEIGHT），即使同时有
     少量 rejected（未达阈值时不触发打折分支）
  5. rejected 达到阈值但 accepted 也 >0（不满足"accepted==0"条件）→
     不打折，走加成或中性分支
  6. category 为空字符串 → record_outcome 是 no-op，不创建账本文件；
     get_weight 直接返回 1.0
  7. 账本文件内容损坏（不是合法 JSON）→ get_weight/get_entry/
     all_categories 均不崩溃，退化为"无历史记录"
  8. 账本文件是合法 JSON 但顶层不是 dict（如是个 list）→ 同样不崩溃
  9. all_categories：返回全部类别，多类别互不干扰
  10. get_entry：不做衰减计算，原样返回累计数字
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.storage.paths import AgentPaths
from mini_agent.evolution.suggestion_feedback_ledger import (
    all_categories,
    get_entry,
    get_weight,
    record_outcome,
)


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


def _ledger_path(paths: AgentPaths) -> Path:
    return paths.workdir_dir / "suggestion_feedback_ledger.json"


class TestSuggestionFeedbackLedger(unittest.TestCase):
    def test_no_history_returns_neutral_weight(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            self.assertEqual(get_weight(paths, "some_category"), 1.0)

    def test_record_outcome_accumulates_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            record_outcome(paths, "cat_a", "accepted")
            record_outcome(paths, "cat_a", "accepted")
            record_outcome(paths, "cat_a", "rejected")

            entry = get_entry(paths, "cat_a")
            self.assertEqual(entry.accepted, 2)
            self.assertEqual(entry.rejected, 1)
            self.assertGreater(entry.last_outcome_ts, 0)

    def test_rejected_threshold_with_zero_accepted_triggers_decay(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            for _ in range(3):
                record_outcome(paths, "cat_b", "rejected")
            self.assertEqual(get_weight(paths, "cat_b"), 0.7)

    def test_accepted_threshold_triggers_bonus(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            record_outcome(paths, "cat_c", "accepted")
            record_outcome(paths, "cat_c", "accepted")
            self.assertEqual(get_weight(paths, "cat_c"), 1.15)

    def test_rejected_threshold_but_nonzero_accepted_does_not_decay(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            record_outcome(paths, "cat_d", "accepted")
            for _ in range(3):
                record_outcome(paths, "cat_d", "rejected")
            # accepted=1 < bonus threshold(2), rejected=3 但 accepted != 0
            # → 既不打折也不加成，走中性分支
            self.assertEqual(get_weight(paths, "cat_d"), 1.0)

    def test_empty_category_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            record_outcome(paths, "", "accepted")
            self.assertFalse(_ledger_path(paths).exists())
            self.assertEqual(get_weight(paths, ""), 1.0)

    def test_corrupted_ledger_file_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            p = _ledger_path(paths)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("{this is not valid json", encoding="utf-8")

            # 三个读取入口都不应该抛异常，应退化为"无历史记录"
            self.assertEqual(get_weight(paths, "cat_x"), 1.0)
            entry = get_entry(paths, "cat_x")
            self.assertEqual(entry.accepted, 0)
            self.assertEqual(entry.rejected, 0)
            self.assertEqual(all_categories(paths), {})

    def test_ledger_file_valid_json_but_not_a_dict_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            p = _ledger_path(paths)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("[1, 2, 3]", encoding="utf-8")

            # _load_ledger 会把这当成 dict 使用（.get），list 没有 .get，
            # 应该被内部 try/except 捕获并退化为空账本，而不是向上抛出。
            self.assertEqual(get_weight(paths, "cat_y"), 1.0)
            self.assertEqual(all_categories(paths), {})
            # record_outcome 也不应因为已有的损坏文件而崩溃
            record_outcome(paths, "cat_y", "accepted")

    def test_all_categories_isolated_across_categories(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            record_outcome(paths, "cat_e", "accepted")
            record_outcome(paths, "cat_f", "rejected")
            record_outcome(paths, "cat_f", "rejected")

            all_entries = all_categories(paths)
            self.assertEqual(set(all_entries.keys()), {"cat_e", "cat_f"})
            self.assertEqual(all_entries["cat_e"].accepted, 1)
            self.assertEqual(all_entries["cat_e"].rejected, 0)
            self.assertEqual(all_entries["cat_f"].rejected, 2)

    def test_get_entry_does_not_apply_decay(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            for _ in range(5):
                record_outcome(paths, "cat_g", "rejected")
            entry = get_entry(paths, "cat_g")
            # get_entry 只是原样返回计数，不做任何加权计算
            self.assertEqual(entry.rejected, 5)
            self.assertEqual(entry.accepted, 0)
            # 而 get_weight 对同样的数据会应用衰减
            self.assertEqual(get_weight(paths, "cat_g"), 0.7)


if __name__ == "__main__":
    unittest.main()
