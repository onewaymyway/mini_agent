"""
orchestrator/sub_agent.py — Sub-Agent 执行单元

每个 Task 对应一个 SubAgent 实例。
SubAgent 是对 Agent 的轻量包装，在独立线程中运行，
完成后将结果写入 TaskRecord。

设计原则：
  - SubAgent 与主 Agent 完全隔离（独立的对话历史、独立的统计）
  - SubAgent 继承主 Agent 的 LLMConfig（provider/model）但可覆盖
  - SubAgent 的 stdout 不直接打印，改为写入 TaskRecord.log_lines
    （可选：通过回调实时转发给主界面）
  - SubAgent 线程安全：状态写入通过 TaskRecord 的 lock 保护
"""

from __future__ import annotations

import io
import sys
import threading
import time
import traceback
from typing import Callable, Optional

from mini_agent.config import AppConfig, load_config
from mini_agent.agent import Agent
from mini_agent.llm.base import LLMConfig
from mini_agent.llm.factory import create_client
from .task import Task, TaskRecord, TaskResult, TaskStatus
from .concurrency import get_task_sem
from mini_agent.permissions import PermissionGuard
from mini_agent.tools import get_default_registry


# 实时日志回调类型：(task_id, line) -> None
LogCallback = Callable[[str, str], None]


class SubAgent:
    """
    在独立线程中运行单个 Task 的 Agent 包装器。

    使用方式：
        sub = SubAgent(task_record, base_cfg, on_log=my_callback)
        sub.start()       # 非阻塞，立即返回
        sub.join()        # 等待完成（可选）
        sub.cancel()      # 发送取消信号
    """

    def __init__(
        self,
        record: TaskRecord,
        base_cfg: AppConfig,
        on_log: Optional[LogCallback] = None,
    ) -> None:
        self.record = record
        self.base_cfg = base_cfg
        self.on_log = on_log
        self._thread: Optional[threading.Thread] = None
        self._cancel_event = threading.Event()
        self._lock = threading.Lock()

    # ── 生命周期 ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """在后台线程中启动任务执行。"""
        with self._lock:
            if self.record.status != TaskStatus.PENDING:
                return
            self.record.status = TaskStatus.RUNNING
            self.record.started_at = time.time()

        self._thread = threading.Thread(
            target=self._run,
            name=f"sub-agent-{self.record.task_id}",
            daemon=True,
        )
        self._thread.start()

    def join(self, timeout: Optional[float] = None) -> None:
        """阻塞等待线程结束。"""
        if self._thread:
            self._thread.join(timeout=timeout)

    def cancel(self) -> None:
        """发送取消信号（当前轮次完成后生效）。"""
        self._cancel_event.set()
        with self._lock:
            if self.record.status == TaskStatus.PENDING:
                self.record.status = TaskStatus.CANCELLED
                self.record.finished_at = time.time()

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── 执行 ──────────────────────────────────────────────────────────────────

    def _run(self) -> None:
        task = self.record.task
        sem = get_task_sem()

        # 等待 task slot（可能排队）
        if sem.waiting_count > 0 or sem.active_count >= sem.limit:
            self._log(f"Queued (task slots full: {sem.active_count}/{sem.limit})")

        with sem.acquire(label=task.id[:8] + " " + task.name[:16]):
            self._run_body(task)

    def _run_body(self, task) -> None:
        self._log(f"Starting task: {task.name}")

        try:
            agent = self._build_agent(task)
            output = self._run_with_capture(agent, task.prompt)

            with self._lock:
                if self._cancel_event.is_set():
                    self.record.status = TaskStatus.CANCELLED
                    self.record.result = TaskResult(
                        output=output, error="Cancelled after completion"
                    )
                else:
                    self.record.status = TaskStatus.DONE
                    self.record.result = TaskResult(
                        output=output,
                        input_tokens=agent.stats.input_tokens,
                        output_tokens=agent.stats.output_tokens,
                        tool_calls=agent.stats.tool_calls,
                        turns=agent.stats.turns,
                    )
            self._log(f"Done. Tokens: {agent.stats.input_tokens}↑ {agent.stats.output_tokens}↓")

        except Exception as exc:
            tb = traceback.format_exc()
            self._log(f"ERROR: {exc}")
            with self._lock:
                self.record.status = TaskStatus.FAILED
                self.record.result = TaskResult(output="", error=str(exc))
            # 详细 traceback 只写日志，不打印到控制台
            for line in tb.splitlines():
                self.record.append_log(line)

        finally:
            with self._lock:
                self.record.finished_at = time.time()

    def _run_with_capture(self, agent: Agent, prompt: str) -> str:
        """
        运行 agent.run_turn()，将打印输出重定向到日志。
        SubAgent 不应直接向 stdout 写入（会与主 REPL 输出混合）。
        """
        buf = io.StringIO()
        old_stdout, old_stderr = sys.stdout, sys.stderr

        try:
            # 捕获 stdout/stderr，逐行写入日志
            sys.stdout = _LineCapture(buf, self._log)
            sys.stderr = _LineCapture(buf, self._log)
            result = agent.run_turn(prompt)
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

        return result

    def _build_agent(self, task: Task) -> Agent:
        """为本次任务构建独立的 Agent 实例。"""
        # 继承主 cfg，但允许 task 覆盖部分字段
        cfg = load_config(
            project_root=self.base_cfg.project_root,
            verbose=False,
            sandbox=self.base_cfg.sandbox,
            auto_approve=task.auto_approve,
            model=task.model or self.base_cfg.model,
            llm_provider=task.provider or self.base_cfg.llm_provider,
            llm_base_url=self.base_cfg.llm_base_url,
            use_system_tool_call=self.base_cfg.use_system_tool_call,
            debug_llm=self.base_cfg.debug_llm,
        )
        cfg.max_turns = task.max_turns
        cfg.stream = False   # SubAgent 不流式输出（输出被捕获）
        if task.system_extra:
            cfg.system_extra = task.system_extra

        llm_cfg = LLMConfig.from_app_config(cfg)
        guard = PermissionGuard(
            auto_approve=task.auto_approve,
            sandbox=self.base_cfg.sandbox,
            project_root=self.base_cfg.project_root,
        )
        return Agent(cfg=cfg, guard=guard, llm_client=create_client(llm_cfg))

    def _log(self, line: str) -> None:
        self.record.append_log(line)
        if self.on_log:
            try:
                self.on_log(self.record.task_id, line)
            except Exception:
                pass


# ── 输出捕获辅助类 ────────────────────────────────────────────────────────────

class _LineCapture:
    """将写入的字符串逐行转发给日志回调。"""

    def __init__(self, buf: io.StringIO, log_fn: Callable[[str], None]) -> None:
        self._buf = buf
        self._log = log_fn
        self._pending = ""

    def write(self, s: str) -> int:
        self._pending += s
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            clean = _strip_ansi(line)
            if clean.strip():
                self._log(clean)
        return len(s)

    def flush(self) -> None:
        if self._pending.strip():
            self._log(_strip_ansi(self._pending))
            self._pending = ""

    def fileno(self) -> int:
        raise io.UnsupportedOperation("fileno")


def _strip_ansi(text: str) -> str:
    """移除 ANSI 转义序列（rich 输出会包含这些）。"""
    import re
    return re.sub(r"\x1b\[[0-9;]*[mGKHF]", "", text)
