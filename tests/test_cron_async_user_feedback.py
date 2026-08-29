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
    def _call(self, paths, tmp_path, question, hint="", options=None, job_id="user:job1", urgency=""):
        from mini_agent.tools.ask_user_async import ask_user_async, set_project_root_provider

        set_project_root_provider(lambda: tmp_path)
        set_current_cron_job_id(job_id)
        try:
            raw = ask_user_async(question, hint=hint, options=options, urgency=urgency)
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

    def test_urgency_blocking_is_stored(self, paths, tmp_path):
        """[cron_async_feedback_further_improvements_plan.md F3]"""
        result = self._call(paths, tmp_path, "阻塞的问题", urgency="blocking")
        rec = questions_store.get_question(paths, result["question_id"])
        assert rec["urgency"] == "blocking"

    def test_urgency_defaults_to_normal_when_omitted(self, paths, tmp_path):
        result = self._call(paths, tmp_path, "普通问题")
        rec = questions_store.get_question(paths, result["question_id"])
        assert rec["urgency"] == "normal"


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
        # [cron_async_feedback_hardening_plan.md D2] 消费不再在 render_prompt()
        # 内部自动发生，要调用方确认这一步真正提交成功后显式调用。
        ws.consume_last_rendered_answers()

        second = ws.render_prompt("做点什么")
        assert "要不要继续？" not in second

    def test_pending_answers_not_consumed_if_never_explicitly_confirmed(self, paths):
        """[cron_async_feedback_hardening_plan.md D2] 渲染了但没有调用
        consume_last_rendered_answers()（模拟该步 submit_step_fn 失败、
        agent 从未真正看到这段 prompt）——答案应该还能在下次渲染里再次
        出现，不会静默丢失。"""
        ws = CronJobWorkspace(paths, "user:test_job")
        ws.ensure(default_task_template="{{task_description}}\n{{pending_answers}}\n")
        rec = questions_store.append_question(paths, "user:test_job", "要不要继续？")
        questions_store.submit_answer(paths, rec["question_id"], "要")

        first = ws.render_prompt("做点什么")
        assert "要不要继续？" in first
        # 故意不调用 consume_last_rendered_answers()

        second = ws.render_prompt("做点什么")
        assert "要不要继续？" in second

    def test_editing_answer_makes_it_reappear(self, paths):
        ws = CronJobWorkspace(paths, "user:test_job")
        ws.ensure(default_task_template="{{task_description}}\n{{pending_answers}}\n")
        rec = questions_store.append_question(paths, "user:test_job", "要不要继续？")
        questions_store.submit_answer(paths, rec["question_id"], "要")
        ws.render_prompt("做点什么")  # 第一次渲染
        ws.consume_last_rendered_answers()  # 模拟这一步成功提交给 agent

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


# ═══════════════════════════════════════════════════════════════════════
# 阶段5：端到端冒烟测试 —— 贯穿全部层次：
#   ask_user_async 工具（提问） → API 层 /v1/cron_questions/*（用户查看/
#   回答/修改） → CronJobWorkspace.render_prompt()（下次触发自动续接）
# 用来验证各阶段接口拼在一起确实能跑通完整的用户故事，而不只是各层各自
# 的单元测试都通过。
# ═══════════════════════════════════════════════════════════════════════

class TestEndToEndAcrossApiAndPromptLayers:
    def _make_api_client(self, project_root):
        from types import SimpleNamespace

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from mini_agent.api.routes import router

        app = FastAPI()
        app.include_router(router)
        cfg = SimpleNamespace(project_root=project_root)
        bridge = SimpleNamespace(agent=SimpleNamespace(cfg=cfg))
        app.state.http_server = SimpleNamespace(bridge=bridge)
        return TestClient(app)

    def test_full_round_trip_tool_then_api_answer_then_prompt_injection(self, paths, tmp_path):
        from mini_agent.tools.ask_user_async import ask_user_async, set_project_root_provider

        job_id = "user:e2e_job"

        # 1) cron 任务执行时调用 ask_user_async 提问，立刻拿到 pending。
        set_project_root_provider(lambda: tmp_path)
        set_current_cron_job_id(job_id)
        try:
            raw = ask_user_async("要不要把预算提到 8000？", hint="当前是 5000")
        finally:
            clear_current_cron_job_id()
        result = json.loads(raw)
        assert result["status"] == "pending"
        question_id = result["question_id"]

        # 提问当下 render_prompt() 应该在 unanswered_questions 里列出它，
        # pending_answers 应为空（还没人回答）。
        ws = CronJobWorkspace(paths, job_id)
        ws.ensure()
        rendered_before = ws.render_prompt("继续做预算相关的任务")
        assert "要不要把预算提到 8000" in rendered_before

        # 2) 用户在看板上通过 REST API 回答（模拟前端调用）。
        client = self._make_api_client(tmp_path)
        resp = client.post(f"/v1/cron_questions/{question_id}/answer", json={"answer": "同意提到 8000"})
        assert resp.status_code == 200
        assert resp.json()["question"]["status"] == "answered"

        # API 的 pending 列表应该不再包含它，history 里能看到。
        pending = client.get("/v1/cron_questions/pending", params={"job_id": job_id}).json()["questions"]
        assert all(q["question_id"] != question_id for q in pending)
        history = client.get("/v1/cron_questions/history", params={"job_id": job_id}).json()["questions"]
        assert any(q["question_id"] == question_id for q in history)

        # 3) 下次该 job 被调度触发，render_prompt() 应该自动把答案注入。
        rendered_after = ws.render_prompt("继续做预算相关的任务")
        assert "同意提到 8000" in rendered_after
        # [cron_async_feedback_hardening_plan.md D2] 消费不再在渲染时自动
        # 发生，模拟 CronJobExecutor 确认这一步真正提交成功后才消费。
        ws.consume_last_rendered_answers()
        # 已消费过一次之后，再次渲染不应该重复注入同一个答案。
        rendered_third = ws.render_prompt("继续做预算相关的任务")
        assert "同意提到 8000" not in rendered_third

        # 4) 用户后来改主意，通过 API 修改答案——应该让答案重新出现在
        #    下一次渲染里（哪怕已经被消费过）。
        resp2 = client.post(f"/v1/cron_questions/{question_id}/answer", json={"answer": "改主意了，维持 5000"})
        assert resp2.status_code == 200
        rendered_fourth = ws.render_prompt("继续做预算相关的任务")
        assert "改主意了，维持 5000" in rendered_fourth

        # history 应该保留两条修改记录，旧答案不丢失。
        history_after_edit = client.get("/v1/cron_questions/history", params={"job_id": job_id}).json()["questions"]
        record = next(q for q in history_after_edit if q["question_id"] == question_id)
        answer_texts = [a["text"] for a in record["answer_history"]]
        assert answer_texts == ["同意提到 8000", "改主意了，维持 5000"]

    def test_deduplicated_question_across_multiple_triggers_only_notified_once(self, paths, tmp_path):
        """同一个 job 连续两次触发都问了同一个问题（比如 agent 每次都
        判断需要确认同一件事）时，第二次应该被去重，不产生第二条待办。"""
        from mini_agent.tools.ask_user_async import ask_user_async, set_project_root_provider

        set_project_root_provider(lambda: tmp_path)
        set_current_cron_job_id("user:e2e_dedup_job")
        try:
            first = json.loads(ask_user_async("需要审批吗？"))
            second = json.loads(ask_user_async("需要审批吗？"))
        finally:
            clear_current_cron_job_id()

        assert first["question_id"] == second["question_id"]
        client = self._make_api_client(tmp_path)
        pending = client.get(
            "/v1/cron_questions/pending", params={"job_id": "user:e2e_dedup_job"}
        ).json()["questions"]
        assert len(pending) == 1


class TestDismissedQuestionsPlaceholder:
    """[cron_async_feedback_hardening_plan.md D3] {{dismissed_questions}}
    占位符：忽略过的问题应该在下次渲染时提醒 agent 不要再问。"""

    def test_dismissed_question_appears_in_placeholder(self, paths):
        ws = CronJobWorkspace(
            paths, "user:test_job",
        )
        ws.ensure(default_task_template="{{task_description}}\n{{dismissed_questions}}\n")
        rec = questions_store.append_question(paths, "user:test_job", "要不要重构模块 A？")
        questions_store.dismiss_question(paths, rec["question_id"])

        rendered = ws.render_prompt("做点什么")
        assert "要不要重构模块 A？" in rendered
        assert "不要再问" in rendered

    def test_dismissed_conditional_block_hidden_when_none(self, paths):
        ws = CronJobWorkspace(paths, "user:test_job")
        ws.ensure(default_task_template=(
            "{{task_description}}\n"
            "{{#dismissed_questions}}\nHAS_DISMISSED\n{{dismissed_questions}}\n{{/dismissed_questions}}\n"
        ))
        rendered = ws.render_prompt("做点什么")
        assert "HAS_DISMISSED" not in rendered

    def test_dismissed_question_never_reappears_as_answered_or_unanswered(self, paths):
        ws = CronJobWorkspace(paths, "user:test_job")
        ws.ensure(default_task_template=(
            "{{task_description}}\n"
            "{{#unanswered_questions}}\n{{unanswered_questions}}\n{{/unanswered_questions}}\n"
            "{{#pending_answers}}\n{{pending_answers}}\n{{/pending_answers}}\n"
        ))
        rec = questions_store.append_question(paths, "user:test_job", "要不要重构模块 A？")
        questions_store.dismiss_question(paths, rec["question_id"])
        rendered = ws.render_prompt("做点什么")
        assert "要不要重构模块 A？" not in rendered

    def test_stale_timeout_dismissed_question_gets_different_note_than_manual(self, paths):
        """[cron_async_feedback_lifecycle_and_usability_plan.md E1] 系统
        因超时自动关闭的问题，跟用户手动忽略的问题在 prompt 里应该给
        agent 不同的措辞——前者不是"用户拒绝"，不该说"不要再问"。"""
        ws = CronJobWorkspace(paths, "user:test_job")
        ws.ensure(default_task_template="{{task_description}}\n{{dismissed_questions}}\n")

        manual_rec = questions_store.append_question(paths, "user:test_job", "手动忽略的问题")
        questions_store.dismiss_question(paths, manual_rec["question_id"])

        stale_rec = questions_store.append_question(paths, "user:test_job", "超时自动关闭的问题")
        questions_store.expire_stale_pending_questions(paths, stale_after_days=0)

        rendered = ws.render_prompt("做点什么")
        assert "手动忽略的问题" in rendered
        assert "超时自动关闭的问题" in rendered
        # 手动忽略的那条附带"不要再问"的强提示。
        manual_line = next(line for line in rendered.splitlines() if "手动忽略的问题" in line)
        assert "不要再问" in manual_line
        # 超时自动关闭的那条不应该说"不要再问"，语气应该更委婉。
        stale_line = next(line for line in rendered.splitlines() if "超时自动关闭的问题" in line)
        assert "自动关闭" in stale_line
        assert "不要再问" not in stale_line


class TestUnansweredQuestionsAgeHint:
    """[cron_async_feedback_lifecycle_and_usability_plan.md E1]
    {{unanswered_questions}} 里每条附带等待天数，提示 agent 越拖越久的
    问题更应该优先自己拿主意或想别的办法绕过去。"""

    def test_recent_question_shows_no_specific_day_count(self, paths):
        ws = CronJobWorkspace(paths, "user:test_job")
        ws.ensure(default_task_template="{{task_description}}\n{{unanswered_questions}}\n")
        questions_store.append_question(paths, "user:test_job", "刚问的问题")

        rendered = ws.render_prompt("做点什么")
        assert "刚问的问题" in rendered
        assert "尚未回答" in rendered

    def test_old_question_shows_days_waited(self, paths):
        import time
        ws = CronJobWorkspace(paths, "user:test_job")
        ws.ensure(default_task_template="{{task_description}}\n{{unanswered_questions}}\n")
        rec = questions_store.append_question(paths, "user:test_job", "拖了很久的问题")

        records = questions_store._load_all(paths)
        for d in records:
            if d.get("question_id") == rec["question_id"]:
                d["created_at"] = time.time() - 5 * 86400
        questions_store._write_all(paths, records)

        rendered = ws.render_prompt("做点什么")
        assert "拖了很久的问题" in rendered
        assert "已等待 5 天" in rendered

    def test_blocking_question_gets_marker_and_sorts_first(self, paths):
        """[cron_async_feedback_further_improvements_plan.md F3]"""
        ws = CronJobWorkspace(paths, "user:test_job")
        ws.ensure(default_task_template="{{task_description}}\n{{unanswered_questions}}\n")
        questions_store.append_question(paths, "user:test_job", "普通问题", urgency="normal")
        questions_store.append_question(paths, "user:test_job", "阻塞问题", urgency="blocking")

        rendered = ws.render_prompt("做点什么")
        assert "（阻塞）「阻塞问题」" in rendered
        # blocking 排在 normal 前面
        assert rendered.index("阻塞问题") < rendered.index("普通问题")


class TestFuzzyDeduplication:
    """[cron_async_feedback_hardening_plan.md D4] ask_user_async 默认开启
    模糊去重：LLM 换个措辞问同一个语义问题应该被合并，不产生新通知。"""

    def test_semantically_similar_rephrasing_is_deduplicated(self, paths, tmp_path):
        from mini_agent.tools.ask_user_async import ask_user_async, set_project_root_provider

        set_project_root_provider(lambda: tmp_path)
        set_current_cron_job_id("user:fuzzy_job")
        try:
            r1 = json.loads(ask_user_async("你希望预算提高到多少？"))
            r2 = json.loads(ask_user_async("你希望把预算提高到多少呢？"))
        finally:
            clear_current_cron_job_id()

        assert r1["question_id"] == r2["question_id"]
        assert r2["deduplicated"] is True
        pending = questions_store.list_pending_questions(paths, job_id="user:fuzzy_job")
        assert len(pending) == 1

    def test_genuinely_different_questions_are_not_merged(self, paths, tmp_path):
        from mini_agent.tools.ask_user_async import ask_user_async, set_project_root_provider

        set_project_root_provider(lambda: tmp_path)
        set_current_cron_job_id("user:fuzzy_job2")
        try:
            r1 = json.loads(ask_user_async("要不要提高预算？"))
            r2 = json.loads(ask_user_async("周报要不要发给张三？"))
        finally:
            clear_current_cron_job_id()

        assert r1["question_id"] != r2["question_id"]
        pending = questions_store.list_pending_questions(paths, job_id="user:fuzzy_job2")
        assert len(pending) == 2


class TestRunIdPropagationForOrphanDetection:
    """[cron_async_feedback_hardening_plan.md D6] CronJobExecutor.run_job()
    执行期间调用 ask_user_async 应该把当次 run_id 记进问题记录。"""

    def test_run_job_sets_run_id_visible_to_ask_user_async(self, paths, tmp_path):
        from mini_agent.evolution.cron_job_executor import CronJobExecutor, StepResult
        from mini_agent.tools.ask_user_async import ask_user_async, set_project_root_provider

        class _FakeJob:
            id = "user:e2e_run_id_job"
            name = "测试"
            task_template = "做点什么"

        set_project_root_provider(lambda: tmp_path)
        captured = {}

        def step_fn(prompt):
            raw = ask_user_async("要不要继续？")
            captured["result"] = json.loads(raw)
            return StepResult(text="已提问", done=True)

        CronJobExecutor(paths).run_job(_FakeJob(), step_fn)

        rec = questions_store.get_question(paths, captured["result"]["question_id"])
        ws = CronJobWorkspace(paths, "user:e2e_run_id_job")
        assert rec["run_id"] == ws.read_state().last_run_id
        assert rec["run_id"] != ""
        assert questions_store.list_orphaned_pending_questions(paths) == []
