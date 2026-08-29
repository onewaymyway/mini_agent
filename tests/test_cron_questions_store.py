"""tests/test_cron_questions_store.py — 覆盖
notification/questions_store.py（cron 任务异步用户反馈的问答记录存储）。

设计背景见 next_doc/cron_async_user_feedback_mechanism_plan.md。
"""
from __future__ import annotations

import pytest

from mini_agent.notification.questions_store import (
    append_question,
    count_questions,
    dismiss_question,
    find_pending_by_fingerprint,
    find_or_create_question,
    get_question,
    list_dismissed_questions,
    list_pending_questions,
    list_answered_questions,
    list_pending_question_texts_for_job,
    list_unconsumed_answers_for_job,
    mark_answers_consumed,
    normalize_urgency,
    submit_answer,
    STATUS_ANSWERED,
    STATUS_DISMISSED,
    STATUS_PENDING,
    URGENCY_BLOCKING,
    URGENCY_NORMAL,
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

    def test_dismiss_with_note_records_dismiss_note(self, paths):
        """[cron_async_feedback_further_improvements_plan.md F2]"""
        rec = append_question(paths, "user:job1", "还需要回答吗？")
        updated = dismiss_question(paths, rec["question_id"], note="不再需要这个信息了")
        assert updated["dismiss_note"] == "不再需要这个信息了"
        stored = get_question(paths, rec["question_id"])
        assert stored["dismiss_note"] == "不再需要这个信息了"

    def test_dismiss_without_note_leaves_dismiss_note_absent(self, paths):
        """[cron_async_feedback_further_improvements_plan.md F2] 不传 note
        时不写入该字段，跟改动前的行为完全一致（向后兼容）。"""
        rec = append_question(paths, "user:job1", "还需要回答吗？")
        updated = dismiss_question(paths, rec["question_id"])
        assert "dismiss_note" not in updated

    def test_dismiss_with_empty_note_leaves_dismiss_note_absent(self, paths):
        """[F2] 空字符串等价于不传——不写入空的 dismiss_note。"""
        rec = append_question(paths, "user:job1", "还需要回答吗？")
        updated = dismiss_question(paths, rec["question_id"], note="")
        assert "dismiss_note" not in updated

    def test_dismiss_note_not_overwritten_on_idempotent_repeat_call(self, paths):
        """[F2] 已经是 dismissed 的问题重复调用是幂等的，不应该用第二次
        调用传入的（可能为空的）note 覆盖第一次记录的原因。"""
        rec = append_question(paths, "user:job1", "还需要回答吗？")
        dismiss_question(paths, rec["question_id"], note="第一次的原因")
        second = dismiss_question(paths, rec["question_id"])  # 不传 note
        assert second["dismiss_note"] == "第一次的原因"


class TestFindOrCreateQuestionConcurrency:
    """[cron_async_feedback_hardening_plan.md D1] 并发安全回归测试。"""

    def test_concurrent_calls_same_question_only_create_one_record(self, paths):
        from mini_agent.notification.questions_store import find_or_create_question
        import threading

        results = []
        lock = threading.Lock()

        def worker():
            record, is_new = find_or_create_question(
                paths, "job-x", "同一个问题", fuzzy_threshold=None,
            )
            with lock:
                results.append((record["question_id"], is_new))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        pending = list_pending_questions(paths, job_id="job-x")
        assert len(pending) == 1
        ids = {r[0] for r in results}
        assert len(ids) == 1

    def test_concurrent_submit_answer_and_dismiss_do_not_corrupt_file(self, paths):
        from mini_agent.notification.questions_store import find_or_create_question
        import threading

        records = []
        for i in range(20):
            record, _ = find_or_create_question(
                paths, "job-y", f"问题{i}", fuzzy_threshold=None,
            )
            records.append(record)

        def answer_worker(qid):
            submit_answer(paths, qid, "答案")

        def dismiss_worker(qid):
            dismiss_question(paths, qid)

        threads = []
        for i, r in enumerate(records):
            fn = answer_worker if i % 2 == 0 else dismiss_worker
            threads.append(threading.Thread(target=fn, args=(r["question_id"],)))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        answered = list_answered_questions(paths, job_id="job-y")
        dismissed = list_dismissed_questions(paths, job_id="job-y")
        assert len(answered) == 10
        assert len(dismissed) == 10
        # 文件本身仍是合法 jsonl，且总条数不丢不重
        assert len(answered) + len(dismissed) == 20


class TestArchiveOldRecords:
    """[cron_async_feedback_hardening_plan.md D5] 数据归档：超过保留期的
    answered/dismissed 记录挪到 archive 文件，pending 记录永不归档。"""

    def test_old_answered_and_dismissed_are_archived(self, paths):
        import time as _time
        from mini_agent.notification.questions_store import archive_old_records

        rec1 = append_question(paths, "job-a", "问题1")
        submit_answer(paths, rec1["question_id"], "答案1")
        rec2 = append_question(paths, "job-a", "问题2")
        dismiss_question(paths, rec2["question_id"])
        rec3 = append_question(paths, "job-a", "问题3")  # 仍 pending

        # 手动把前两条的 updated_at 改到 100 天前，模拟"很久以前的记录"。
        old_ts = _time.time() - 100 * 86400
        raw = paths.notification_cron_questions.read_text(encoding="utf-8").splitlines()
        import json as _json
        rewritten = []
        for line in raw:
            d = _json.loads(line)
            if d["question_id"] in (rec1["question_id"], rec2["question_id"]):
                d["updated_at"] = old_ts
            rewritten.append(_json.dumps(d, ensure_ascii=False))
        paths.notification_cron_questions.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

        archived_count = archive_old_records(paths, retention_days=90)
        assert archived_count == 2

        # 主文件只剩 pending 的那条。
        remaining = list_pending_questions(paths, job_id="job-a")
        assert len(remaining) == 1
        assert remaining[0]["question_id"] == rec3["question_id"]
        assert get_question(paths, rec1["question_id"]) is None
        assert get_question(paths, rec2["question_id"]) is None

        # archive 文件里能找到被归档的两条。
        archive_path = paths.notification_cron_questions.parent / "cron_questions.archive.jsonl"
        assert archive_path.exists()
        archived_ids = {
            _json.loads(line)["question_id"]
            for line in archive_path.read_text(encoding="utf-8").splitlines()
        }
        assert archived_ids == {rec1["question_id"], rec2["question_id"]}

    def test_recent_records_are_not_archived(self, paths):
        from mini_agent.notification.questions_store import archive_old_records

        rec = append_question(paths, "job-a", "最近的问题")
        submit_answer(paths, rec["question_id"], "答案")

        archived_count = archive_old_records(paths, retention_days=90)
        assert archived_count == 0
        assert get_question(paths, rec["question_id"]) is not None

    def test_pending_records_never_archived_regardless_of_age(self, paths):
        import time as _time, json as _json
        from mini_agent.notification.questions_store import archive_old_records

        rec = append_question(paths, "job-a", "一直没人回答的老问题")
        old_ts = _time.time() - 365 * 86400
        raw = paths.notification_cron_questions.read_text(encoding="utf-8")
        d = _json.loads(raw.strip())
        d["created_at"] = old_ts
        d["updated_at"] = old_ts
        paths.notification_cron_questions.write_text(_json.dumps(d, ensure_ascii=False) + "\n", encoding="utf-8")

        archived_count = archive_old_records(paths, retention_days=90)
        assert archived_count == 0
        assert get_question(paths, rec["question_id"]) is not None


class TestPurgeQuestionsForJob:
    """[cron_async_feedback_hardening_plan.md D5] job 被删除时清掉其名下
    所有问答记录，避免永久遗留的孤儿数据。"""

    def test_purge_removes_all_statuses_for_job_only(self, paths):
        from mini_agent.notification.questions_store import purge_questions_for_job

        r1 = append_question(paths, "job-x", "问题A")
        submit_answer(paths, r1["question_id"], "答案A")
        r2 = append_question(paths, "job-x", "问题B")
        dismiss_question(paths, r2["question_id"])
        r3 = append_question(paths, "job-x", "问题C")  # pending
        other = append_question(paths, "job-y", "别的 job 的问题")

        removed = purge_questions_for_job(paths, "job-x")
        assert removed == 3
        assert get_question(paths, r1["question_id"]) is None
        assert get_question(paths, r2["question_id"]) is None
        assert get_question(paths, r3["question_id"]) is None
        # 别的 job 不受影响。
        assert get_question(paths, other["question_id"]) is not None

    def test_purge_nonexistent_job_is_noop(self, paths):
        from mini_agent.notification.questions_store import purge_questions_for_job
        assert purge_questions_for_job(paths, "no-such-job") == 0


class TestOrphanedPendingQuestions:
    """[cron_async_feedback_hardening_plan.md D6] run_id 记录 + 事后识别
    "孤儿线程迟到写入"的问题。"""

    def test_question_run_id_matching_current_state_is_not_orphaned(self, paths):
        from mini_agent.notification.questions_store import (
            find_or_create_question, list_orphaned_pending_questions,
        )
        from mini_agent.evolution.cron_job_workspace import CronJobWorkspace

        ws = CronJobWorkspace(paths, "user:job1")
        ws.ensure()
        state = ws.read_state()
        state.last_run_id = "run-current"
        ws.write_state(state)

        find_or_create_question(paths, "user:job1", "问题A", run_id="run-current")

        assert list_orphaned_pending_questions(paths) == []

    def test_question_run_id_not_matching_current_state_is_orphaned(self, paths):
        from mini_agent.notification.questions_store import (
            find_or_create_question, list_orphaned_pending_questions,
        )
        from mini_agent.evolution.cron_job_workspace import CronJobWorkspace

        ws = CronJobWorkspace(paths, "user:job1")
        ws.ensure()
        state = ws.read_state()
        state.last_run_id = "run-newer"
        ws.write_state(state)

        # 模拟孤儿线程用一个已经不是最新的 run_id 迟到写入。
        rec, _ = find_or_create_question(paths, "user:job1", "问题A", run_id="run-stale")

        orphaned = list_orphaned_pending_questions(paths)
        assert len(orphaned) == 1
        assert orphaned[0]["question_id"] == rec["question_id"]

    def test_adhoc_and_missing_run_id_never_flagged_as_orphaned(self, paths):
        from mini_agent.notification.questions_store import (
            append_question, list_orphaned_pending_questions,
        )
        append_question(paths, "adhoc", "交互式问题")  # 没有 run_id 字段
        assert list_orphaned_pending_questions(paths) == []


# [cron_async_feedback_lifecycle_and_usability_plan.md E1] 长期无人回答的
# 问题自动关闭机制。
class TestExpireStalePendingQuestions:
    def test_expires_pending_older_than_threshold(self, paths):
        import time
        from mini_agent.notification.questions_store import (
            expire_stale_pending_questions,
            DISMISS_REASON_STALE_TIMEOUT,
        )

        rec = append_question(paths, "user:job1", "要不要继续？")
        # 直接改写 created_at 模拟"15 天前提出"，不依赖真的等待。
        _backdate(paths, rec["question_id"], days_ago=15)

        expired = expire_stale_pending_questions(paths, stale_after_days=14)
        assert len(expired) == 1
        assert expired[0]["question_id"] == rec["question_id"]
        assert expired[0]["status"] == STATUS_DISMISSED
        assert expired[0]["dismiss_reason"] == DISMISS_REASON_STALE_TIMEOUT

        stored = get_question(paths, rec["question_id"])
        assert stored["status"] == STATUS_DISMISSED
        assert stored["dismiss_reason"] == DISMISS_REASON_STALE_TIMEOUT

    def test_does_not_expire_recent_pending(self, paths):
        from mini_agent.notification.questions_store import expire_stale_pending_questions

        rec = append_question(paths, "user:job1", "要不要继续？")
        _backdate(paths, rec["question_id"], days_ago=3)

        expired = expire_stale_pending_questions(paths, stale_after_days=14)
        assert expired == []
        assert get_question(paths, rec["question_id"])["status"] == STATUS_PENDING

    def test_expired_question_disappears_from_pending_and_unanswered(self, paths):
        from mini_agent.notification.questions_store import (
            expire_stale_pending_questions,
            list_pending_question_texts_for_job,
        )

        rec = append_question(paths, "user:job1", "要不要继续？")
        _backdate(paths, rec["question_id"], days_ago=30)
        expire_stale_pending_questions(paths, stale_after_days=14)

        assert list_pending_questions(paths) == []
        assert list_pending_question_texts_for_job(paths, "user:job1") == []
        # 不会被误当作"已回答"混进历史面板。
        assert list_answered_questions(paths) == []

    def test_expired_question_appears_in_dismissed_list_with_reason(self, paths):
        from mini_agent.notification.questions_store import (
            expire_stale_pending_questions,
            DISMISS_REASON_STALE_TIMEOUT,
        )

        rec = append_question(paths, "user:job1", "要不要继续？")
        _backdate(paths, rec["question_id"], days_ago=30)
        expire_stale_pending_questions(paths, stale_after_days=14)

        dismissed = list_dismissed_questions(paths)
        assert len(dismissed) == 1
        assert dismissed[0]["dismiss_reason"] == DISMISS_REASON_STALE_TIMEOUT

    def test_filters_by_job_id(self, paths):
        from mini_agent.notification.questions_store import expire_stale_pending_questions

        rec1 = append_question(paths, "user:job1", "问题A")
        rec2 = append_question(paths, "user:job2", "问题B")
        _backdate(paths, rec1["question_id"], days_ago=30)
        _backdate(paths, rec2["question_id"], days_ago=30)

        expired = expire_stale_pending_questions(paths, stale_after_days=14, job_id="user:job1")
        assert len(expired) == 1
        assert expired[0]["question_id"] == rec1["question_id"]
        # job2 的问题不受影响，仍是 pending。
        assert get_question(paths, rec2["question_id"])["status"] == STATUS_PENDING

    def test_answered_questions_never_expired(self, paths):
        from mini_agent.notification.questions_store import expire_stale_pending_questions

        rec = append_question(paths, "user:job1", "要不要继续？")
        submit_answer(paths, rec["question_id"], "继续")
        _backdate(paths, rec["question_id"], days_ago=30)

        expired = expire_stale_pending_questions(paths, stale_after_days=14)
        assert expired == []
        assert get_question(paths, rec["question_id"])["status"] == STATUS_ANSWERED

    def test_zero_or_negative_threshold_still_requires_explicit_call(self, paths):
        # 阈值本身允许调用方传入非正数（调用方——autonomous_loop——是在
        # <=0 时直接不调这个函数；store 层不强行拦截，语义上传 0 表示
        # "立刻过期所有 pending"，是合法输入，不是错误。
        from mini_agent.notification.questions_store import expire_stale_pending_questions

        rec = append_question(paths, "user:job1", "要不要继续？")
        expired = expire_stale_pending_questions(paths, stale_after_days=0)
        assert len(expired) == 1
        assert expired[0]["question_id"] == rec["question_id"]


class TestDismissReason:
    def test_manual_dismiss_records_manual_reason(self, paths):
        from mini_agent.notification.questions_store import DISMISS_REASON_MANUAL

        rec = append_question(paths, "user:job1", "要不要继续？")
        updated = dismiss_question(paths, rec["question_id"])
        assert updated["dismiss_reason"] == DISMISS_REASON_MANUAL

    def test_idempotent_dismiss_does_not_overwrite_existing_reason(self, paths):
        from mini_agent.notification.questions_store import (
            DISMISS_REASON_MANUAL,
            DISMISS_REASON_STALE_TIMEOUT,
            expire_stale_pending_questions,
        )

        rec = append_question(paths, "user:job1", "要不要继续？")
        _backdate(paths, rec["question_id"], days_ago=30)
        expire_stale_pending_questions(paths, stale_after_days=14)

        # 已经因超时被关闭的问题，再被用户手动点一次"忽略"（比如看板还没
        # 刷新，用户没看到它其实已经关了）——幂等，理由不应该被覆盖成
        # manual，因为它确实是超时关的，不是用户手动关的。
        again = dismiss_question(paths, rec["question_id"])
        assert again["dismiss_reason"] == DISMISS_REASON_STALE_TIMEOUT


def _backdate(paths, question_id: str, *, days_ago: int) -> None:
    """测试辅助：把某条记录的 created_at 往前拨，模拟"提出已经很久了"，
    不依赖真实等待。"""
    import time
    from mini_agent.notification.questions_store import _load_all, _write_all

    records = _load_all(paths)
    for d in records:
        if d.get("question_id") == question_id:
            d["created_at"] = time.time() - days_ago * 86400
    _write_all(paths, records)


class TestCountQuestions:
    """[cron_async_feedback_further_improvements_plan.md F1]"""

    def test_counts_by_status(self, paths):
        append_question(paths, "user:job1", "问题A")
        rec_b = append_question(paths, "user:job1", "问题B")
        rec_c = append_question(paths, "user:job1", "问题C")
        submit_answer(paths, rec_b["question_id"], "答案")
        dismiss_question(paths, rec_c["question_id"])

        assert count_questions(paths, status=STATUS_PENDING) == 1
        assert count_questions(paths, status=STATUS_ANSWERED) == 1
        assert count_questions(paths, status=STATUS_DISMISSED) == 1
        assert count_questions(paths) == 3

    def test_counts_filtered_by_job_id(self, paths):
        append_question(paths, "user:job1", "问题A")
        append_question(paths, "user:job1", "问题B")
        append_question(paths, "user:job2", "问题C")

        assert count_questions(paths, status=STATUS_PENDING, job_id="user:job1") == 2
        assert count_questions(paths, status=STATUS_PENDING, job_id="user:job2") == 1
        assert count_questions(paths, status=STATUS_PENDING, job_id="user:job3") == 0

    def test_count_zero_when_no_records(self, paths):
        assert count_questions(paths, status=STATUS_PENDING) == 0
        assert count_questions(paths) == 0


class TestUrgency:
    """[cron_async_feedback_further_improvements_plan.md F3]"""

    def test_normalize_urgency_valid_values_passthrough(self):
        assert normalize_urgency("blocking") == URGENCY_BLOCKING
        assert normalize_urgency("normal") == URGENCY_NORMAL

    def test_normalize_urgency_invalid_or_missing_falls_back_to_normal(self):
        assert normalize_urgency(None) == URGENCY_NORMAL
        assert normalize_urgency("") == URGENCY_NORMAL
        assert normalize_urgency("urgent!!!") == URGENCY_NORMAL

    def test_append_question_stores_urgency(self, paths):
        rec = append_question(paths, "user:job1", "问题", urgency="blocking")
        assert rec["urgency"] == URGENCY_BLOCKING
        stored = get_question(paths, rec["question_id"])
        assert stored["urgency"] == URGENCY_BLOCKING

    def test_append_question_defaults_to_normal(self, paths):
        rec = append_question(paths, "user:job1", "问题")
        assert rec["urgency"] == URGENCY_NORMAL

    def test_append_question_invalid_urgency_falls_back_to_normal(self, paths):
        rec = append_question(paths, "user:job1", "问题", urgency="超级紧急")
        assert rec["urgency"] == URGENCY_NORMAL

    def test_find_or_create_question_stores_urgency_on_new_record(self, paths):
        rec, is_new = find_or_create_question(paths, "user:job1", "问题", urgency="blocking")
        assert is_new is True
        assert rec["urgency"] == URGENCY_BLOCKING

    def test_find_or_create_question_dedup_does_not_overwrite_urgency(self, paths):
        """去重命中已存在的问题时，不应该用这次调用的 urgency 覆盖第一次
        记录的值——语义上这是"同一个问题"，紧急程度以第一次判断为准。"""
        first, _ = find_or_create_question(paths, "user:job1", "还需要回答吗？", urgency="blocking")
        second, is_new = find_or_create_question(paths, "user:job1", "还需要回答吗？", urgency="normal")
        assert is_new is False
        assert second["question_id"] == first["question_id"]
        assert second["urgency"] == URGENCY_BLOCKING

    def test_unanswered_texts_sorts_blocking_first(self, paths):
        append_question(paths, "user:job1", "普通问题1", urgency="normal")
        blocking = append_question(paths, "user:job1", "阻塞问题", urgency="blocking")
        append_question(paths, "user:job1", "普通问题2", urgency="normal")

        rows = list_pending_question_texts_for_job(paths, "user:job1")
        assert rows[0]["question_id"] == blocking["question_id"]

    def test_unanswered_texts_normal_group_sorted_by_created_at(self, paths):
        """同一 urgency 分组内部仍按 created_at 正序（等得最久的排最前），
        不因为叠加 urgency 排序就丢了原有的时间序。"""
        q1 = append_question(paths, "user:job1", "问题1")
        q2 = append_question(paths, "user:job1", "问题2")
        rows = list_pending_question_texts_for_job(paths, "user:job1")
        ids = [r["question_id"] for r in rows]
        assert ids.index(q1["question_id"]) < ids.index(q2["question_id"])
