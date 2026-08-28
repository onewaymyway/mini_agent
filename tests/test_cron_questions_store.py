"""tests/test_cron_questions_store.py — 覆盖
notification/questions_store.py（cron 任务异步用户反馈的问答记录存储）。

设计背景见 next_doc/cron_async_user_feedback_mechanism_plan.md。
"""
from __future__ import annotations

import pytest

from mini_agent.notification.questions_store import (
    append_question,
    dismiss_question,
    find_pending_by_fingerprint,
    get_question,
    list_dismissed_questions,
    list_pending_questions,
    list_answered_questions,
    list_pending_question_texts_for_job,
    list_unconsumed_answers_for_job,
    mark_answers_consumed,
    submit_answer,
    STATUS_ANSWERED,
    STATUS_DISMISSED,
    STATUS_PENDING,
)
from mini_agent.storage.paths import AgentPaths


@pytest.fixture
def paths(tmp_path):
    return AgentPaths(tmp_path)


class TestAppendQuestion:
    def test_creates_pending_record_with_generated_id(self, paths):
        rec = append_question(paths, "user:job1", "要不要继续？", hint="超时会自动跳过")
        assert rec["job_id"] == "user:job1"
        assert rec["question"] == "要不要继续？"
        assert rec["hint"] == "超时会自动跳过"
        assert rec["status"] == STATUS_PENDING
        assert rec["answer"] == ""
        assert rec["answer_history"] == []
        assert rec["consumed"] is False
        assert rec["question_id"].startswith("cq:user:job1:")

    def test_persists_to_disk_and_readable_via_get(self, paths):
        rec = append_question(paths, "user:job1", "选哪个方案？", options=["A", "B"])
        fetched = get_question(paths, rec["question_id"])
        assert fetched is not None
        assert fetched["options"] == ["A", "B"]

    def test_multiple_questions_appended_independently(self, paths):
        append_question(paths, "user:job1", "问题1")
        append_question(paths, "user:job1", "问题2")
        pending = list_pending_questions(paths, job_id="user:job1")
        assert len(pending) == 2


class TestFindPendingByFingerprint:
    def test_finds_existing_pending_question_with_same_text(self, paths):
        rec = append_question(paths, "user:job1", "  要不要继续？  ".strip())
        found = find_pending_by_fingerprint(paths, "user:job1", "要不要继续？")
        assert found is not None
        assert found["question_id"] == rec["question_id"]

    def test_does_not_match_across_different_jobs(self, paths):
        append_question(paths, "user:job1", "同样的问题")
        found = find_pending_by_fingerprint(paths, "user:job2", "同样的问题")
        assert found is None

    def test_does_not_match_answered_questions(self, paths):
        rec = append_question(paths, "user:job1", "要不要继续？")
        submit_answer(paths, rec["question_id"], "要")
        found = find_pending_by_fingerprint(paths, "user:job1", "要不要继续？")
        assert found is None

    def test_returns_none_for_empty_question(self, paths):
        append_question(paths, "user:job1", "问题")
        assert find_pending_by_fingerprint(paths, "user:job1", "") is None

    def test_returns_none_when_nothing_matches(self, paths):
        assert find_pending_by_fingerprint(paths, "user:job1", "不存在的问题") is None


class TestSubmitAnswer:
    def test_first_submit_marks_answered_and_records_history(self, paths):
        rec = append_question(paths, "user:job1", "要不要继续？")
        updated = submit_answer(paths, rec["question_id"], "要，继续吧")
        assert updated["status"] == STATUS_ANSWERED
        assert updated["answer"] == "要，继续吧"
        assert len(updated["answer_history"]) == 1
        assert updated["answer_history"][0]["text"] == "要，继续吧"
        assert updated["consumed"] is False

    def test_second_submit_updates_answer_and_appends_history_not_overwrite(self, paths):
        rec = append_question(paths, "user:job1", "要不要继续？")
        submit_answer(paths, rec["question_id"], "要")
        updated = submit_answer(paths, rec["question_id"], "不要了，改主意了")
        assert updated["answer"] == "不要了，改主意了"
        assert len(updated["answer_history"]) == 2
        assert updated["answer_history"][0]["text"] == "要"
        assert updated["answer_history"][1]["text"] == "不要了，改主意了"

    def test_editing_answer_resets_consumed_flag(self, paths):
        rec = append_question(paths, "user:job1", "要不要继续？")
        submit_answer(paths, rec["question_id"], "要")
        mark_answers_consumed(paths, [rec["question_id"]])
        assert get_question(paths, rec["question_id"])["consumed"] is True

        submit_answer(paths, rec["question_id"], "不要了")
        assert get_question(paths, rec["question_id"])["consumed"] is False

    def test_unknown_question_id_returns_none(self, paths):
        assert submit_answer(paths, "cq:does-not-exist", "answer") is None

    def test_blank_answer_is_ignored(self, paths):
        rec = append_question(paths, "user:job1", "问题")
        assert submit_answer(paths, rec["question_id"], "   ") is None
        assert get_question(paths, rec["question_id"])["status"] == STATUS_PENDING


class TestListPendingAndAnswered:
    def test_list_pending_excludes_answered(self, paths):
        p1 = append_question(paths, "user:job1", "问题1")
        p2 = append_question(paths, "user:job1", "问题2")
        submit_answer(paths, p1["question_id"], "答案1")
        pending = list_pending_questions(paths, job_id="user:job1")
        assert [d["question_id"] for d in pending] == [p2["question_id"]]

    def test_list_answered_excludes_pending(self, paths):
        p1 = append_question(paths, "user:job1", "问题1")
        append_question(paths, "user:job1", "问题2")
        submit_answer(paths, p1["question_id"], "答案1")
        answered = list_answered_questions(paths, job_id="user:job1")
        assert [d["question_id"] for d in answered] == [p1["question_id"]]

    def test_list_pending_sorted_newest_first(self, paths):
        p1 = append_question(paths, "user:job1", "问题1")
        p1["created_at"] = 100.0
        p2 = append_question(paths, "user:job1", "问题2")
        p2["created_at"] = 200.0
        # 直接追加的记录已经按调用顺序落盘（真实时间戳递增），无需手工改时间戳
        pending = list_pending_questions(paths, job_id="user:job1")
        assert pending[0]["question_id"] == p2["question_id"]

    def test_filter_by_job_id(self, paths):
        append_question(paths, "user:job1", "问题A")
        append_question(paths, "user:job2", "问题B")
        pending_job1 = list_pending_questions(paths, job_id="user:job1")
        assert len(pending_job1) == 1
        assert pending_job1[0]["question"] == "问题A"

    def test_pagination_limit_and_offset(self, paths):
        for i in range(5):
            append_question(paths, "user:job1", f"问题{i}")
        page = list_pending_questions(paths, job_id="user:job1", limit=2, offset=1)
        assert len(page) == 2

    def test_no_job_id_returns_across_all_jobs(self, paths):
        append_question(paths, "user:job1", "问题A")
        append_question(paths, "user:job2", "问题B")
        assert len(list_pending_questions(paths)) == 2

    def test_history_includes_full_answer_history_regardless_of_consumed(self, paths):
        rec = append_question(paths, "user:job1", "要不要继续？")
        submit_answer(paths, rec["question_id"], "要")
        mark_answers_consumed(paths, [rec["question_id"]])
        answered = list_answered_questions(paths, job_id="user:job1")
        assert len(answered) == 1
        assert answered[0]["consumed"] is True
        assert len(answered[0]["answer_history"]) == 1


class TestUnconsumedAnswersForRenderPrompt:
    def test_unconsumed_answers_returned_for_job(self, paths):
        rec = append_question(paths, "user:job1", "要不要继续？")
        submit_answer(paths, rec["question_id"], "要")
        unconsumed = list_unconsumed_answers_for_job(paths, "user:job1")
        assert len(unconsumed) == 1
        assert unconsumed[0]["question_id"] == rec["question_id"]

    def test_consumed_answers_excluded_after_marking(self, paths):
        rec = append_question(paths, "user:job1", "要不要继续？")
        submit_answer(paths, rec["question_id"], "要")
        mark_answers_consumed(paths, [rec["question_id"]])
        assert list_unconsumed_answers_for_job(paths, "user:job1") == []

    def test_pending_questions_not_included(self, paths):
        append_question(paths, "user:job1", "还没回答的问题")
        assert list_unconsumed_answers_for_job(paths, "user:job1") == []

    def test_scoped_to_job_id(self, paths):
        rec1 = append_question(paths, "user:job1", "问题1")
        submit_answer(paths, rec1["question_id"], "答案1")
        rec2 = append_question(paths, "user:job2", "问题2")
        submit_answer(paths, rec2["question_id"], "答案2")
        assert len(list_unconsumed_answers_for_job(paths, "user:job1")) == 1
        assert len(list_unconsumed_answers_for_job(paths, "user:job2")) == 1

    def test_mark_answers_consumed_returns_count_and_skips_unknown(self, paths):
        rec = append_question(paths, "user:job1", "问题")
        submit_answer(paths, rec["question_id"], "答案")
        count = mark_answers_consumed(paths, [rec["question_id"], "cq:does-not-exist"])
        assert count == 1
        # 再次标记同一条，已经是 consumed，不重复计数
        assert mark_answers_consumed(paths, [rec["question_id"]]) == 0

    def test_mark_answers_consumed_empty_input_is_noop(self, paths):
        assert mark_answers_consumed(paths, []) == 0


class TestPendingQuestionTextsForJob:
    def test_returns_only_pending_for_job_sorted_oldest_first(self, paths):
        append_question(paths, "user:job1", "问题1")
        append_question(paths, "user:job1", "问题2")
        rec3 = append_question(paths, "user:job1", "问题3")
        submit_answer(paths, rec3["question_id"], "已回答，不应出现")
        texts = list_pending_question_texts_for_job(paths, "user:job1")
        assert [d["question"] for d in texts] == ["问题1", "问题2"]

    def test_empty_when_no_pending(self, paths):
        assert list_pending_question_texts_for_job(paths, "user:job1") == []


class TestDismissQuestion:
    def test_dismisses_pending_question(self, paths):
        rec = append_question(paths, "user:job1", "还需要回答吗？")
        updated = dismiss_question(paths, rec["question_id"])
        assert updated["status"] == STATUS_DISMISSED
        stored = get_question(paths, rec["question_id"])
        assert stored["status"] == STATUS_DISMISSED

    def test_dismissed_question_disappears_from_pending_list(self, paths):
        rec = append_question(paths, "user:job1", "还需要回答吗？")
        dismiss_question(paths, rec["question_id"])
        assert list_pending_questions(paths, job_id="user:job1") == []

    def test_dismissed_question_not_in_unanswered_prompt_texts(self, paths):
        rec = append_question(paths, "user:job1", "还需要回答吗？")
        dismiss_question(paths, rec["question_id"])
        assert list_pending_question_texts_for_job(paths, "user:job1") == []

    def test_dismissed_question_does_not_appear_in_answered_list(self, paths):
        rec = append_question(paths, "user:job1", "还需要回答吗？")
        dismiss_question(paths, rec["question_id"])
        assert list_answered_questions(paths, job_id="user:job1") == []

    def test_dismissed_question_appears_in_dismissed_list(self, paths):
        rec = append_question(paths, "user:job1", "还需要回答吗？")
        dismiss_question(paths, rec["question_id"])
        dismissed = list_dismissed_questions(paths, job_id="user:job1")
        assert len(dismissed) == 1
        assert dismissed[0]["question_id"] == rec["question_id"]

    def test_dismissing_answered_question_returns_none(self, paths):
        rec = append_question(paths, "user:job1", "问题")
        submit_answer(paths, rec["question_id"], "答案")
        assert dismiss_question(paths, rec["question_id"]) is None
        # 状态不受影响，仍然是 answered
        assert get_question(paths, rec["question_id"])["status"] == STATUS_ANSWERED

    def test_dismissing_unknown_question_returns_none(self, paths):
        assert dismiss_question(paths, "cq:nope:xxxx") is None

    def test_dismissing_already_dismissed_is_idempotent(self, paths):
        rec = append_question(paths, "user:job1", "问题")
        first = dismiss_question(paths, rec["question_id"])
        second = dismiss_question(paths, rec["question_id"])
        assert first["status"] == STATUS_DISMISSED
        assert second["status"] == STATUS_DISMISSED
        # 没有产生第二条记录
        assert len(list_dismissed_questions(paths, job_id="user:job1")) == 1

    def test_dismissed_question_does_not_block_reasking_same_text(self, paths):
        """去重只匹配 STATUS_PENDING，忽略后同一问题文本应该被当作
        全新问题重新创建，不会被这条已忽略的记录挡住。"""
        rec = append_question(paths, "user:job1", "还需要回答吗？")
        dismiss_question(paths, rec["question_id"])
        assert find_pending_by_fingerprint(paths, "user:job1", "还需要回答吗？") is None
