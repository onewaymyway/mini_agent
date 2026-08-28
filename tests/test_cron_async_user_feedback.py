"""tests/test_cron_async_user_feedback.py — 覆盖
cron_async_user_feedback_mechanism_plan.md 阶段2 的执行链路集成：

  - tools/ask_user_async.py     — 异步提问工具，立刻返回、不阻塞、去重
  - evolution/cron_context.py   — 当前 cron job_id 的 thread-local 透传
  - evolution/cron_job_executor.py — STATUS_WAITING_FEEDBACK 状态机
  - evolution/cron_job_workspace.py — render_prompt() 的
    {{pending_answers}}/{{unanswered_questions}} 占位符

用真实 AgentPaths（而不是 tests/test_cron_job_workspace_and_executor.py
里的 `_FakePaths`），因为这里需要覆盖到 `paths.notification_cron_questions`
这条真实属性路径。
"""
from __future__ import annotations

import json

import pytest

from mini_agent.evolution.cron_context import (
    clear_current_cron_job_id,
    get_current_cron_job_id,
    set_current_cron_job_id,
)
from mini_agent.evolution.cron_job_executor import CronJobExecutor, StepResult
from mini_agent.evolution.cron_job_workspace import (
    CronJobWorkspace,
    STATUS_IDLE,
    STATUS_NEEDS_REVIEW,
    STATUS_WAITING_FEEDBACK,
)
from mini_agent.notification import questions_store
from mini_agent.storage.paths import AgentPaths


@pytest.fixture
def paths(tmp_path):
    return AgentPaths(tmp_path)


class _FakeJob:
    def __init__(self, job_id="user:test_job", name="测试任务", task_template="做一件测试任务"):
        self.id = job_id
        self.name = name
        self.task_template = task_template


# ═══════════════════════════════════════════════════════════════════════
# cron_context.py — thread-local job_id 透传
# ═══════════════════════════════════════════════════════════════════════

class TestCronContext:
    def test_default_is_adhoc_when_unset(self):
        clear_current_cron_job_id()
        assert get_current_cron_job_id() == "adhoc"

    def test_set_and_get_roundtrip(self):
        set_current_cron_job_id("user:job1")
        assert get_current_cron_job_id() == "user:job1"
        clear_current_cron_job_id()
        assert get_current_cron_job_id() == "adhoc"

    def test_empty_string_falls_back_to_default(self):
        set_current_cron_job_id("")
        assert get_current_cron_job_id() == "adhoc"
        clear_current_cron_job_id()


# ═══════════════════════════════════════════════════════════════════════
# tools/ask_user_async.py
# ═══════════════════════════════════════════════════════════════════════

class TestAskUserAsyncTool:
    def _call(self, paths, tmp_path, question, hint="", options=None, job_id="user:job1"):
        from mini_agent.tools.ask_user_async import ask_user_async, set_project_root_provider

        set_project_root_provider(lambda: tmp_path)
        set_current_cron_job_id(job_id)
        try:
            raw = ask_user_async(question, hint=hint, options=options)
        finally:
            clear_current_cron_job_id()
        return json.loads(raw)

    def test_returns_pending_status_without_blocking(self, paths, tmp_path):
        result = self._call(paths, tmp_path, "要不要继续？")
        assert result["status"] == "pending"
        assert result["question_id"]
        assert result["deduplicated"] is False

    def test_creates_record_in_questions_store_with_correct_job_id(self, paths, tmp_path):
        result = self._call(paths, tmp_path, "要不要继续？", job_id="user:job42")
        rec = questions_store.get_question(paths, result["question_id"])
        assert rec is not None
        assert rec["job_id"] == "user:job42"
        assert rec["question"] == "要不要继续？"

    def test_repeated_same_question_same_job_is_deduplicated(self, paths, tmp_path):
        first = self._call(paths, tmp_path, "要不要继续？", job_id="user:job1")
        second = self._call(paths, tmp_path, "要不要继续？", job_id="user:job1")
        assert second["deduplicated"] is True
        assert second["question_id"] == first["question_id"]
        # 只应该有一条记录，没有被重复创建
        assert len(questions_store.list_pending_questions(paths, job_id="user:job1")) == 1

    def test_same_question_different_job_not_deduplicated(self, paths, tmp_path):
        first = self._call(paths, tmp_path, "要不要继续？", job_id="user:job1")
        second = self._call(paths, tmp_path, "要不要继续？", job_id="user:job2")
        assert second["deduplicated"] is False
        assert second["question_id"] != first["question_id"]

    def test_answered_question_is_not_deduplicated_against(self, paths, tmp_path):
        first = self._call(paths, tmp_path, "要不要继续？", job_id="user:job1")
        questions_store.submit_answer(paths, first["question_id"], "要")
        second = self._call(paths, tmp_path, "要不要继续？", job_id="user:job1")
        assert second["deduplicated"] is False
        assert second["question_id"] != first["question_id"]

    def test_adhoc_job_id_used_when_not_in_cron_context(self, paths, tmp_path):
        from mini_agent.tools.ask_user_async import ask_user_async, set_project_root_provider

        set_project_root_provider(lambda: tmp_path)
        clear_current_cron_job_id()
        raw = ask_user_async("交互式问题")
        result = json.loads(raw)
        rec = questions_store.get_question(paths, result["question_id"])
        assert rec["job_id"] == "adhoc"

    def test_options_are_stored_but_not_enforced(self, paths, tmp_path):
        result = self._call(paths, tmp_path, "选哪个？", options=["A", "B"])
        rec = questions_store.get_question(paths, result["question_id"])
        assert rec["options"] == ["A", "B"]
        # 自由文本回答，不校验是否在 options 里
        updated = questions_store.submit_answer(paths, result["question_id"], "都不选，我要 C")
        assert updated["answer"] == "都不选，我要 C"


# ═══════════════════════════════════════════════════════════════════════
# CronJobExecutor — STATUS_WAITING_FEEDBACK 状态机
# ═══════════════════════════════════════════════════════════════════════

class TestWaitingFeedbackStateMachine:
    def test_step_that_asks_question_then_finishes_marks_waiting_feedback(self, paths):
        """本步调用 ask_user_async 提问，agent 判定本次触发可以收尾
        （done=True），但问题仍未被回答 → 应该记成 waiting_feedback，
        不是 idle。"""
        def step_fn(prompt):
            questions_store.append_question(paths, "user:test_job", "要不要继续？")
            return StepResult(text="已经问了用户，先收尾", done=True)

        outcome = CronJobExecutor(paths).run_job(_FakeJob(), step_fn)

        assert outcome.status == STATUS_WAITING_FEEDBACK
        ws = CronJobWorkspace(paths, "user:test_job")
        state = ws.read_state()
        assert state.status == STATUS_WAITING_FEEDBACK
        # 不算失败
        assert state.consecutive_failures == 0
        # 保留最后一步输出作为进度，而不是像 idle 那样清空
        assert state.progress_summary != ""

    def test_no_pending_question_stays_idle(self, paths):
        def step_fn(prompt):
            return StepResult(text="正常完成，没有提问", done=True)

        outcome = CronJobExecutor(paths).run_job(_FakeJob(), step_fn)
        assert outcome.status == STATUS_IDLE

        ws = CronJobWorkspace(paths, "user:test_job")
        state = ws.read_state()
        assert state.progress_summary == ""

    def test_already_answered_question_does_not_trigger_waiting_feedback(self, paths):
        """提问的同时已经有了答案（极端时序，比如上次触发问的、这次触发前
        用户就已经答了）：不应该被判定为 waiting_feedback。"""
        rec = questions_store.append_question(paths, "user:test_job", "要不要继续？")
        questions_store.submit_answer(paths, rec["question_id"], "要")

        def step_fn(prompt):
            return StepResult(text="正常完成", done=True)

        outcome = CronJobExecutor(paths).run_job(_FakeJob(), step_fn)
        assert outcome.status == STATUS_IDLE

    def test_needs_review_takes_priority_over_waiting_feedback(self, paths):
        """卡死判定 GIVE_UP 的紧急程度高于"还在等一个问题的答案"，不应该
        被 waiting_feedback 覆盖掉。"""
        def step_fn(prompt):
            questions_store.append_question(paths, "user:test_job", "要不要继续？")
            return StepResult(text="完全一模一样的重复输出内容", done=False)

        outcome = CronJobExecutor(paths).run_job(_FakeJob(), step_fn)
        assert outcome.status == STATUS_NEEDS_REVIEW

    def test_job_id_context_available_inside_step_fn(self, paths):
        """验证 run_job() 确实把 job.id 写进了 thread-local，
        step_fn（模拟 agent 工具调用发生的位置）里能读到正确的值。"""
        seen = {}

        def step_fn(prompt):
            seen["job_id"] = get_current_cron_job_id()
            return StepResult(text="done", done=True)

        CronJobExecutor(paths).run_job(_FakeJob(job_id="user:specific_job"), step_fn)
        assert seen["job_id"] == "user:specific_job"

    def test_context_cleared_after_run_job_returns(self, paths):
        def step_fn(prompt):
            return StepResult(text="done", done=True)

        CronJobExecutor(paths).run_job(_FakeJob(job_id="user:specific_job"), step_fn)
        assert get_current_cron_job_id() == "adhoc"


# ═══════════════════════════════════════════════════════════════════════
# CronJobWorkspace.render_prompt() — 新占位符
# ═══════════════════════════════════════════════════════════════════════

class TestRenderPromptPlaceholders:
    def test_pending_answers_placeholder_injects_answered_qa(self, paths):
        ws = CronJobWorkspace(paths, "user:test_job")
        ws.ensure(default_task_template="{{task_description}}\n{{pending_answers}}\n")
        rec = questions_store.append_question(paths, "user:test_job", "要不要继续？")
        questions_store.submit_answer(paths, rec["question_id"], "要，继续吧")

        rendered = ws.render_prompt("做点什么")
        assert "要不要继续？" in rendered
        assert "要，继续吧" in rendered

    def test_pending_answers_consumed_after_render_not_shown_again(self, paths):
        ws = CronJobWorkspace(paths, "user:test_job")
        ws.ensure(default_task_template="{{task_description}}\n{{pending_answers}}\n")
        rec = questions_store.append_question(paths, "user:test_job", "要不要继续？")
        questions_store.submit_answer(paths, rec["question_id"], "要")

        first = ws.render_prompt("做点什么")
        assert "要不要继续？" in first

        second = ws.render_prompt("做点什么")
        assert "要不要继续？" not in second

    def test_editing_answer_makes_it_reappear(self, paths):
        ws = CronJobWorkspace(paths, "user:test_job")
        ws.ensure(default_task_template="{{task_description}}\n{{pending_answers}}\n")
        rec = questions_store.append_question(paths, "user:test_job", "要不要继续？")
        questions_store.submit_answer(paths, rec["question_id"], "要")
        ws.render_prompt("做点什么")  # 第一次渲染，标记 consumed

        questions_store.submit_answer(paths, rec["question_id"], "不要了，改主意了")
        rendered = ws.render_prompt("做点什么")
        assert "不要了，改主意了" in rendered

    def test_unanswered_questions_placeholder_lists_pending(self, paths):
        ws = CronJobWorkspace(paths, "user:test_job")
        ws.ensure(default_task_template="{{task_description}}\n{{unanswered_questions}}\n")
        questions_store.append_question(paths, "user:test_job", "还没回答的问题")

        rendered = ws.render_prompt("做点什么")
        assert "还没回答的问题" in rendered

    def test_conditional_blocks_hide_when_no_questions(self, paths):
        ws = CronJobWorkspace(paths, "user:test_job")
        ws.ensure(default_task_template=(
            "{{task_description}}\n"
            "{{#pending_answers}}\nHAS_ANSWERS\n{{pending_answers}}\n{{/pending_answers}}\n"
            "{{#unanswered_questions}}\nHAS_PENDING\n{{unanswered_questions}}\n{{/unanswered_questions}}\n"
        ))
        rendered = ws.render_prompt("做点什么")
        assert "HAS_ANSWERS" not in rendered
        assert "HAS_PENDING" not in rendered

    def test_scoped_to_this_job_only(self, paths):
        ws1 = CronJobWorkspace(paths, "user:job1")
        ws1.ensure(default_task_template="{{task_description}}\n{{pending_answers}}\n")
        ws2 = CronJobWorkspace(paths, "user:job2")
        ws2.ensure(default_task_template="{{task_description}}\n{{pending_answers}}\n")

        rec = questions_store.append_question(paths, "user:job2", "job2 的问题")
        questions_store.submit_answer(paths, rec["question_id"], "job2 的答案")

        assert "job2 的问题" not in ws1.render_prompt("任务1")
        assert "job2 的问题" in ws2.render_prompt("任务2")

    def test_missing_placeholder_in_custom_template_is_noop(self, paths):
        """用户自定义的 prompt.md 没有新占位符时不受影响（向后兼容），
        跟既有 {{output_policy}} 的处理方式一致。"""
        ws = CronJobWorkspace(paths, "user:test_job")
        ws.ensure(default_task_template="就是一个普通的自定义模板，没有任何占位符")
        rec = questions_store.append_question(paths, "user:test_job", "问题")
        questions_store.submit_answer(paths, rec["question_id"], "答案")

        rendered = ws.render_prompt("做点什么")
        assert rendered == "就是一个普通的自定义模板，没有任何占位符"
