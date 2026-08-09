"""
tests/test_cron_job_workspace_and_executor.py

对应用户需求"cron 任务专属执行机制"（daemon 单任务超时/进度持久化/卡死
检测/看板可读的文件夹结构），覆盖：

  evolution/cron_job_workspace.py — CronJobWorkspace 的读写、prompt 渲染
  evolution/cron_job_executor.py  — CronJobExecutor.run_job() 的调度循环：
      正常完成 / 超时 / 触达最大步数 / 单步异常 / 卡死判定 GIVE_UP，
      以及跨次触发的进度恢复（render_prompt 拼接 progress_summary）

不依赖真实 LLM/Agent，submit_step_fn 全部用纯 Python 假实现。
cron_agent_bridge.py / cron_job_runner.py 依赖真实的 Agent/LLM client
构造和线程调度，这里不做覆盖（那部分更适合集成测试/手动验证，见
CHANGES 文档里"已知遗留"的说明）。
"""

from __future__ import annotations

import json
import time

import pytest

from mini_agent.evolution.cron_job_workspace import (
    CronJobWorkspace,
    CronJobConfig,
    CronJobState,
    STATUS_IDLE,
    STATUS_RUNNING,
    STATUS_NEEDS_REVIEW,
    STATUS_TIMED_OUT,
    DEFAULT_TIMEOUT_SECONDS,
    list_all_workspaces,
)
from mini_agent.evolution.cron_job_executor import (
    CronJobExecutor,
    StepResult,
)


class _FakePaths:
    """最小化的 AgentPaths 替身，只需要 project_root 属性。"""
    def __init__(self, root):
        self.project_root = str(root)


class _FakeJob:
    def __init__(self, job_id="user:test_job", name="测试任务", task_template="做一件测试任务"):
        self.id = job_id
        self.name = name
        self.task_template = task_template


# ═══════════════════════════════════════════════════════════════════════
# CronJobWorkspace
# ═══════════════════════════════════════════════════════════════════════

class TestCronJobWorkspace:
    def test_ensure_creates_default_files(self, tmp_path):
        paths = _FakePaths(tmp_path)
        ws = CronJobWorkspace(paths, "user:abc123")
        ws.ensure(default_task_template="默认任务描述")

        assert ws.dir.is_dir()
        assert ws.runs_dir.is_dir()
        assert ws.prompt_path.exists()
        assert ws.config_path.exists()
        assert ws.state_path.exists()

        state = ws.read_state()
        assert state.status == STATUS_IDLE
        assert state.progress_summary == ""

        config = ws.read_config()
        assert config.timeout_seconds > 0
        assert config.max_steps > 0

    def test_job_id_with_colon_maps_to_safe_dirname(self, tmp_path):
        paths = _FakePaths(tmp_path)
        ws = CronJobWorkspace(paths, "sys:daily_digest")
        ws.ensure()
        assert ":" not in ws.dir.name
        assert ws.dir.name == "sys_daily_digest"

    def test_ensure_does_not_overwrite_existing_files(self, tmp_path):
        paths = _FakePaths(tmp_path)
        ws = CronJobWorkspace(paths, "user:abc123")
        ws.ensure(default_task_template="第一次的模板")
        ws.prompt_path.write_text("用户手动编辑过的内容", encoding="utf-8")

        # 第二次 ensure()（模拟下次触发）不应该覆盖用户编辑
        ws.ensure(default_task_template="第一次的模板")
        assert ws.read_prompt() == "用户手动编辑过的内容"

    def test_ensure_with_default_config_only_applies_on_first_creation(self, tmp_path):
        paths = _FakePaths(tmp_path)
        ws = CronJobWorkspace(paths, "user:abc123")
        custom_default = CronJobConfig(timeout_seconds=999, max_steps=5)
        ws.ensure(default_config=custom_default)

        config = ws.read_config()
        assert config.timeout_seconds == 999
        assert config.max_steps == 5

        # 已存在 config.json 后，再传不同的 default_config 不应生效
        ws.ensure(default_config=CronJobConfig(timeout_seconds=111, max_steps=1))
        config2 = ws.read_config()
        assert config2.timeout_seconds == 999

    def test_read_config_falls_back_to_default_for_missing_fields(self, tmp_path):
        """config.json 里没写的字段，read_config(default=...) 应跟随全局配置
        实时生效，不需要针对已存在的 job 做迁移。"""
        paths = _FakePaths(tmp_path)
        ws = CronJobWorkspace(paths, "user:abc123")
        ws.dir.mkdir(parents=True, exist_ok=True)
        # 手写一份只包含 max_steps 的残缺 config.json，模拟"旧版本 job 目录"
        ws.config_path.write_text(json.dumps({"max_steps": 7}), encoding="utf-8")

        global_default = CronJobConfig(timeout_seconds=1234, max_steps=999)
        config = ws.read_config(default=global_default)

        # max_steps 是 config.json 里显式写的，不受 default 影响
        assert config.max_steps == 7
        # timeout_seconds 是缺省字段，应该跟随传入的全局 default
        assert config.timeout_seconds == 1234

    def test_read_config_without_default_uses_hardcoded_default(self, tmp_path):
        paths = _FakePaths(tmp_path)
        ws = CronJobWorkspace(paths, "user:abc123")
        ws.dir.mkdir(parents=True, exist_ok=True)
        ws.config_path.write_text(json.dumps({"max_steps": 7}), encoding="utf-8")

        config = ws.read_config()
        assert config.max_steps == 7
        assert config.timeout_seconds == DEFAULT_TIMEOUT_SECONDS

    def test_read_config_missing_file_falls_back_to_default(self, tmp_path):
        paths = _FakePaths(tmp_path)
        ws = CronJobWorkspace(paths, "user:abc123")
        global_default = CronJobConfig(timeout_seconds=42, max_steps=3)
        config = ws.read_config(default=global_default)
        assert config.timeout_seconds == 42
        assert config.max_steps == 3

    def test_write_and_read_state_roundtrip(self, tmp_path):
        paths = _FakePaths(tmp_path)
        ws = CronJobWorkspace(paths, "user:abc123")
        ws.ensure()

        state = CronJobState(
            status=STATUS_NEEDS_REVIEW,
            progress_summary="卡在第 3 步",
            consecutive_failures=2,
            last_error="连续雷同",
        )
        ws.write_state(state)

        reloaded = ws.read_state()
        assert reloaded.status == STATUS_NEEDS_REVIEW
        assert reloaded.progress_summary == "卡在第 3 步"
        assert reloaded.consecutive_failures == 2
        assert reloaded.last_error == "连续雷同"

    def test_render_prompt_without_progress_strips_block(self, tmp_path):
        paths = _FakePaths(tmp_path)
        ws = CronJobWorkspace(paths, "user:abc123")
        ws.ensure()
        ws.prompt_path.write_text(
            "{{task_description}}\n"
            "{{#progress}}\n上次进度：{{progress}}\n{{/progress}}\n"
            "结束。",
            encoding="utf-8",
        )
        rendered = ws.render_prompt("去做点什么")
        assert "去做点什么" in rendered
        assert "上次进度" not in rendered  # progress 为空，整块应该被去掉
        assert "结束。" in rendered

    def test_render_prompt_with_progress_keeps_block(self, tmp_path):
        paths = _FakePaths(tmp_path)
        ws = CronJobWorkspace(paths, "user:abc123")
        ws.ensure()
        ws.prompt_path.write_text(
            "{{task_description}}\n"
            "{{#progress}}\n上次进度：{{progress}}\n{{/progress}}\n"
            "结束。",
            encoding="utf-8",
        )
        state = ws.read_state()
        state.progress_summary = "已经做完了前两步"
        ws.write_state(state)

        rendered = ws.render_prompt("去做点什么")
        assert "去做点什么" in rendered
        assert "已经做完了前两步" in rendered
        assert "结束。" in rendered

    def test_run_events_append_and_read(self, tmp_path):
        paths = _FakePaths(tmp_path)
        ws = CronJobWorkspace(paths, "user:abc123")
        ws.ensure()

        run_id = ws.new_run_id()
        ws.append_run_event(run_id, {"type": "run_started"})
        ws.append_run_event(run_id, {"type": "step", "step_index": 1})

        events = ws.read_run_events(run_id)
        assert len(events) == 2
        assert events[0]["type"] == "run_started"
        assert events[1]["step_index"] == 1
        assert "at" in events[0]  # 每条记录自动打时间戳

    def test_recent_runs_ordered_and_limited(self, tmp_path):
        paths = _FakePaths(tmp_path)
        ws = CronJobWorkspace(paths, "user:abc123")
        ws.ensure()

        run_ids = []
        for i in range(3):
            rid = f"2026-07-2{i}T00-00-00"
            ws.append_run_event(rid, {"type": "run_started"})
            run_ids.append(rid)
            time.sleep(0.01)  # 保证 mtime 递增，便于验证倒序

        recent = ws.recent_runs(limit=2)
        assert len(recent) == 2
        # 最近的（mtime 最大）应该排在最前面
        assert recent[0] == run_ids[-1]

    def test_list_all_workspaces(self, tmp_path):
        paths = _FakePaths(tmp_path)
        CronJobWorkspace(paths, "user:job1").ensure()
        CronJobWorkspace(paths, "sys:job2").ensure()

        names = list_all_workspaces(paths)
        assert set(names) == {"user_job1", "sys_job2"}

    def test_list_all_workspaces_empty_when_no_dir(self, tmp_path):
        paths = _FakePaths(tmp_path)
        assert list_all_workspaces(paths) == []


# ═══════════════════════════════════════════════════════════════════════
# CronJobExecutor
# ═══════════════════════════════════════════════════════════════════════

class TestCronJobExecutor:
    def test_normal_completion_clears_progress(self, tmp_path):
        paths = _FakePaths(tmp_path)
        calls = {"n": 0}

        def step_fn(prompt):
            calls["n"] += 1
            return StepResult(text=f"第{calls['n']}步", done=(calls["n"] >= 2))

        outcome = CronJobExecutor(paths).run_job(_FakeJob(), step_fn)

        assert outcome.status == STATUS_IDLE
        assert outcome.steps_executed == 2

        ws = CronJobWorkspace(paths, "user:test_job")
        state = ws.read_state()
        assert state.status == STATUS_IDLE
        assert state.progress_summary == ""
        assert state.consecutive_failures == 0

    def test_single_step_first_call_receives_rendered_prompt(self, tmp_path):
        paths = _FakePaths(tmp_path)
        received = {}

        def step_fn(prompt):
            received["first"] = prompt
            return StepResult(text="done", done=True)

        CronJobExecutor(paths).run_job(_FakeJob(task_template="做 X 任务"), step_fn)
        assert "做 X 任务" in received["first"]

    def test_continuation_receives_simple_continue_marker(self, tmp_path):
        paths = _FakePaths(tmp_path)
        prompts_seen = []

        def step_fn(prompt):
            prompts_seen.append(prompt)
            return StepResult(text="x", done=(len(prompts_seen) >= 3))

        CronJobExecutor(paths).run_job(_FakeJob(), step_fn)
        assert len(prompts_seen) == 3
        assert prompts_seen[1] == "继续"
        assert prompts_seen[2] == "继续"

    def test_stuck_detector_triggers_needs_human_review(self, tmp_path):
        paths = _FakePaths(tmp_path)

        def step_fn(prompt):
            return StepResult(text="完全一模一样的重复输出内容", done=False)

        outcome = CronJobExecutor(paths).run_job(_FakeJob(), step_fn)

        assert outcome.status == STATUS_NEEDS_REVIEW
        ws = CronJobWorkspace(paths, "user:test_job")
        state = ws.read_state()
        assert state.status == STATUS_NEEDS_REVIEW
        assert state.consecutive_failures == 1
        assert state.progress_summary  # 保留最后一步输出供人工查看/续接
        assert "GIVE_UP" in state.last_error or "卡" in state.last_error

    def test_single_step_error_marks_needs_human_review(self, tmp_path):
        paths = _FakePaths(tmp_path)

        def step_fn(prompt):
            raise RuntimeError("模拟工具调用异常")

        outcome = CronJobExecutor(paths).run_job(_FakeJob(), step_fn)

        assert outcome.status == STATUS_NEEDS_REVIEW
        assert outcome.steps_executed == 0
        ws = CronJobWorkspace(paths, "user:test_job")
        state = ws.read_state()
        assert "模拟工具调用异常" in state.last_error

    def test_result_error_marks_needs_human_review(self, tmp_path):
        paths = _FakePaths(tmp_path)

        def step_fn(prompt):
            return StepResult(text="", done=False, error="内部工具报错")

        outcome = CronJobExecutor(paths).run_job(_FakeJob(), step_fn)
        assert outcome.status == STATUS_NEEDS_REVIEW

        ws = CronJobWorkspace(paths, "user:test_job")
        state = ws.read_state()
        assert state.last_error == "内部工具报错"

    def test_max_steps_reached_marks_timed_out_and_keeps_progress(self, tmp_path):
        paths = _FakePaths(tmp_path)
        ws = CronJobWorkspace(paths, "user:test_job")
        ws.ensure(default_config=CronJobConfig(timeout_seconds=9999, max_steps=3))

        call_count = {"n": 0}

        def step_fn(prompt):
            call_count["n"] += 1
            # 每步输出都不同，绕开卡死检测，纯粹测试步数上限
            return StepResult(text=f"进展第{call_count['n']}步，内容各不相同 {time.time()}", done=False)

        outcome = CronJobExecutor(paths).run_job(_FakeJob(), step_fn)

        assert outcome.status == STATUS_TIMED_OUT
        assert outcome.steps_executed == 3

        state = ws.read_state()
        assert state.status == STATUS_TIMED_OUT
        assert state.progress_summary  # 保留进度供下次续接
        assert state.consecutive_failures == 0  # timed_out 不计入"失败"

    def test_timeout_deadline_stops_loop(self, tmp_path):
        paths = _FakePaths(tmp_path)
        ws = CronJobWorkspace(paths, "user:test_job")
        # 超短超时（0 秒），保证第一次进入循环时 deadline 检查就直接命中
        ws.ensure(default_config=CronJobConfig(timeout_seconds=0, max_steps=999))

        called = {"n": 0}

        def step_fn(prompt):
            called["n"] += 1
            return StepResult(text="不应该被调用到很多次", done=False)

        outcome = CronJobExecutor(paths).run_job(_FakeJob(), step_fn)
        assert outcome.status == STATUS_TIMED_OUT
        assert outcome.steps_executed == 0
        assert called["n"] == 0

    def test_progress_resumes_across_separate_run_job_calls(self, tmp_path):
        """模拟"跨次触发"：第一次执行卡死/超时，第二次触发时 render_prompt()
        应该能读到上一次遗留的 progress_summary。"""
        paths = _FakePaths(tmp_path)
        ws = CronJobWorkspace(paths, "user:test_job")
        ws.ensure(default_config=CronJobConfig(timeout_seconds=9999, max_steps=2))
        ws.prompt_path.write_text(
            "{{task_description}}\n"
            "{{#progress}}\n[上次进度] {{progress}}\n{{/progress}}\n",
            encoding="utf-8",
        )

        def step_fn_first_run(prompt):
            return StepResult(text="做到一半，卡在第二步", done=False)

        outcome1 = CronJobExecutor(paths).run_job(_FakeJob(), step_fn_first_run)
        assert outcome1.status == STATUS_TIMED_OUT

        # 模拟下次触发：render_prompt 应该带上上次的进度
        second_prompt = ws.render_prompt("做一件测试任务")
        assert "做到一半，卡在第二步" in second_prompt

    def test_consecutive_failures_reset_after_success(self, tmp_path):
        paths = _FakePaths(tmp_path)
        ws = CronJobWorkspace(paths, "user:test_job")
        ws.ensure()

        # 第一次：卡死，consecutive_failures 变成 1
        def stuck_fn(prompt):
            return StepResult(text="重复重复重复重复的输出", done=False)
        CronJobExecutor(paths).run_job(_FakeJob(), stuck_fn)
        assert ws.read_state().consecutive_failures == 1

        # 第二次：正常完成，consecutive_failures 应该清零
        def ok_fn(prompt):
            return StepResult(text="正常完成", done=True)
        CronJobExecutor(paths).run_job(_FakeJob(), ok_fn)
        assert ws.read_state().consecutive_failures == 0
        assert ws.read_state().status == STATUS_IDLE

    def test_stale_running_status_increments_failure_and_still_executes(self, tmp_path):
        """模拟"上次异常退出、state 还停在 running"的僵尸状态：不应该阻止
        本次继续执行，只是多记一次 consecutive_failures。"""
        paths = _FakePaths(tmp_path)
        ws = CronJobWorkspace(paths, "user:test_job")
        ws.ensure()
        stale_state = ws.read_state()
        stale_state.status = STATUS_RUNNING
        ws.write_state(stale_state)

        def ok_fn(prompt):
            return StepResult(text="正常完成", done=True)

        outcome = CronJobExecutor(paths).run_job(_FakeJob(), ok_fn)
        assert outcome.status == STATUS_IDLE  # 本次正常执行完成

    def test_run_events_written_for_full_lifecycle(self, tmp_path):
        paths = _FakePaths(tmp_path)

        def step_fn(prompt):
            return StepResult(text="完成", done=True)

        outcome = CronJobExecutor(paths).run_job(_FakeJob(), step_fn)
        ws = CronJobWorkspace(paths, "user:test_job")
        events = ws.read_run_events(outcome.run_id)

        types = [e["type"] for e in events]
        assert "run_started" in types
        assert "step" in types
        assert "run_finished" in types


# ═══════════════════════════════════════════════════════════════════════
# CronJobExecutor — [growth_advisor_improvement_plan_v4.md 方向一 M3]
# 收尾时顺带产出记忆（`memory_backfill_cfg`/`memory_backend`/`llm_client`
# 属性赋值接入，跟 circuit_breaker 走同样的模式）
# ═══════════════════════════════════════════════════════════════════════

class _FakeMemoryBackfillCfg:
    def __init__(self, enabled=True, cron_run_backfill_enabled=True):
        self.enabled = enabled
        self.cron_run_backfill_enabled = cron_run_backfill_enabled


class _FakeMemoryBackend:
    def __init__(self):
        self.entries = []

    def upsert(self, entry):
        self.entries.append(entry)

    def all_entries(self):
        return self.entries


class _FakeSummaryResp:
    def __init__(self, text):
        self.text = text


class _FakeLLMClient:
    def __init__(self, summary_text="cron 任务已完成"):
        self._summary_text = summary_text

    def chat_with_retry(self, **kwargs):
        return _FakeSummaryResp(self._summary_text)


def _make_backfill_executor(paths, *, cfg=None, backend=None, llm=None):
    executor = CronJobExecutor(paths)
    executor.memory_backfill_cfg = cfg if cfg is not None else _FakeMemoryBackfillCfg()
    executor.memory_backend = backend if backend is not None else _FakeMemoryBackend()
    executor.llm_client = llm if llm is not None else _FakeLLMClient()
    return executor


class TestCronJobExecutorMemoryBackfill:
    def test_normal_completion_writes_cron_memory_entry(self, tmp_path):
        paths = _FakePaths(tmp_path)
        backend = _FakeMemoryBackend()
        executor = _make_backfill_executor(paths, backend=backend)

        def step_fn(prompt):
            return StepResult(text="今天的日报已生成完毕。", done=True)

        outcome = executor.run_job(_FakeJob(job_id="sys:daily_report"), step_fn)

        assert outcome.status == STATUS_IDLE
        assert len(backend.entries) == 1
        assert backend.entries[0].session_id == f"cron:sys:daily_report:{outcome.run_id}"

    def test_timed_out_run_does_not_write_memory(self, tmp_path):
        paths = _FakePaths(tmp_path)
        backend = _FakeMemoryBackend()
        executor = _make_backfill_executor(paths, backend=backend)
        ws = CronJobWorkspace(paths, "user:test_job")
        ws.ensure(default_config=CronJobConfig(timeout_seconds=0, max_steps=999))

        def step_fn(prompt):
            return StepResult(text="还在跑第一步")

        outcome = executor.run_job(_FakeJob(), step_fn)

        assert outcome.status == STATUS_TIMED_OUT
        assert len(backend.entries) == 0

    def test_needs_human_review_run_does_not_write_memory(self, tmp_path):
        paths = _FakePaths(tmp_path)
        backend = _FakeMemoryBackend()
        executor = _make_backfill_executor(paths, backend=backend)

        def step_fn(prompt):
            raise RuntimeError("步执行异常")

        outcome = executor.run_job(_FakeJob(), step_fn)

        assert outcome.status == STATUS_NEEDS_REVIEW
        assert len(backend.entries) == 0

    def test_empty_last_text_does_not_write_memory(self, tmp_path):
        paths = _FakePaths(tmp_path)
        backend = _FakeMemoryBackend()
        executor = _make_backfill_executor(paths, backend=backend)

        def step_fn(prompt):
            return StepResult(text="   ", done=True)

        outcome = executor.run_job(_FakeJob(), step_fn)

        assert outcome.status == STATUS_IDLE
        assert len(backend.entries) == 0

    def test_missing_memory_backfill_cfg_skips_silently(self, tmp_path):
        """未升级的调用方（属性保持默认 None）不应该受影响，`run_job()`
        主流程行为跟改造前完全一致。"""
        paths = _FakePaths(tmp_path)

        def step_fn(prompt):
            return StepResult(text="完成", done=True)

        outcome = CronJobExecutor(paths).run_job(_FakeJob(), step_fn)
        assert outcome.status == STATUS_IDLE  # 没有因为缺依赖而报错

    def test_disabled_cron_run_backfill_flag_skips(self, tmp_path):
        paths = _FakePaths(tmp_path)
        backend = _FakeMemoryBackend()
        executor = _make_backfill_executor(
            paths, backend=backend,
            cfg=_FakeMemoryBackfillCfg(cron_run_backfill_enabled=False),
        )

        def step_fn(prompt):
            return StepResult(text="完成", done=True)

        executor.run_job(_FakeJob(), step_fn)
        assert len(backend.entries) == 0

    def test_missing_memory_backend_or_llm_client_skips_silently(self, tmp_path):
        paths = _FakePaths(tmp_path)
        executor = CronJobExecutor(paths)
        executor.memory_backfill_cfg = _FakeMemoryBackfillCfg()
        # memory_backend / llm_client 都保持默认 None

        def step_fn(prompt):
            return StepResult(text="完成", done=True)

        outcome = executor.run_job(_FakeJob(), step_fn)
        assert outcome.status == STATUS_IDLE  # 没有报错，静默跳过

    def test_backfill_exception_does_not_break_run_job_result(self, tmp_path):
        """记忆生成内部抛异常时，不能反过来影响 run_job() 已经产出的
        outcome/状态落盘结果。"""
        paths = _FakePaths(tmp_path)

        class _BrokenLLMClient:
            def chat_with_retry(self, **kwargs):
                raise RuntimeError("LLM 调用失败")

        executor = _make_backfill_executor(paths, llm=_BrokenLLMClient())

        def step_fn(prompt):
            return StepResult(text="完成", done=True)

        outcome = executor.run_job(_FakeJob(), step_fn)
        assert outcome.status == STATUS_IDLE

        ws = CronJobWorkspace(paths, "user:test_job")
        state = ws.read_state()
        assert state.status == STATUS_IDLE
