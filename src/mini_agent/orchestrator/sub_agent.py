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

import json
import threading
import time
import traceback
from pathlib import Path
from typing import Callable, Optional

from mini_agent.config import AppConfig, load_config
from mini_agent.agent import Agent
from mini_agent.llm.base import LLMConfig
from mini_agent.llm.factory import create_client
from .task import Task, TaskRecord, TaskResult, TaskStatus
from .concurrency import get_task_sem
from mini_agent.permissions import PermissionGuard
from mini_agent.tools import get_default_registry
from mini_agent.storage.paths import AgentPaths
import io
from mini_agent.time_utils import ts_to_str


def _get_task_paths(base_cfg: AppConfig, session_id: Optional[str], task_id: str):
    """获取 task 级别的路径对象（session_id 可能为 None）。"""
    if not session_id:
        return None
    return AgentPaths(base_cfg.project_root), session_id, task_id


def _debug_log(
    task_id: str,
    event: str,
    details: dict | None = None,
    *,
    events_path: Optional[Path] = None,
) -> None:
    """
    写入 task 生命周期事件到 events.jsonl。
    路径：<project_root>/.agent/sessions/<session_id>/tasks/<task_id>/events.jsonl
    events_path 由 SubAgent 实例在获得 session_id 后传入。
    """
    if events_path is None:
        return  # session 还未建立时静默忽略
    try:
        events_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts":      time.time(),
            "ts_str":  ts_to_str(time.time()),
            "task_id": task_id,
            "event":   event,
            "details": details or {},
        }
        with open(events_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.orchestrator.sub_agent')
        pass


# 实时日志回调类型：(task_id, line) -> None
LogCallback = Callable[[str, str], None]

# 终端状态通知回调类型：(task_id, old_status, new_status) -> None
TerminalCallback = Callable[[str, TaskStatus, TaskStatus], None]


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
        on_terminal: Optional[TerminalCallback] = None,
        session_id: Optional[str] = None,
        shared_tool_cache=None,   # Optional[ToolResultCache]，Phase E / 3.3 跨 SubAgent 共享缓存
    ) -> None:
        self.record = record
        self.base_cfg = base_cfg
        # 保存主配置的 LLM 相关字段，避免子线程重新读取环境变量
        self._llm_provider = base_cfg.llm_provider
        self._llm_base_url = base_cfg.llm_base_url
        self._api_key = base_cfg.api_key  # 从主配置继承 API key
        self.on_log = on_log
        self.on_terminal = on_terminal
        self._shared_tool_cache = shared_tool_cache
        self._thread: Optional[threading.Thread] = None
        self._cancel_event = threading.Event()
        self._lock = threading.Lock()
        self._terminal_notified: set[TaskStatus] = set()  # 已通知的终态

        # Task 级路径（需要 session_id）
        self._session_id = session_id
        if session_id:
            _paths = AgentPaths(base_cfg.project_root)
            self._events_path: Optional[Path] = _paths.task_events(session_id, record.task_id)
            self._output_path: Optional[Path] = _paths.task_output(session_id, record.task_id)
            self._result_path: Optional[Path] = _paths.task_result(session_id, record.task_id)
            self._manifest_path: Optional[Path] = _paths.task_manifest(session_id, record.task_id)
            # 绑定 manifest 路径，并立即写一份初始 manifest（任务创建时落初始 manifest.json）
            record.bind_manifest_path(self._manifest_path)
            record.write_manifest()
        else:
            self._events_path = None
            self._output_path = None
            self._result_path = None
            self._manifest_path = None

    # ── 生命周期 ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """在后台线程中启动任务执行。"""
        with self._lock:
            if self.record.status != TaskStatus.PENDING:
                return
            # 注意：状态改为 RUNNING 在获得 semaphore 时才执行
            # 这里只启动线程，不立即改状态

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
        _debug_log(task.id, "sub_agent_start", {"task_name": task.name}, events_path=self._events_path)
        sem = get_task_sem()

        # 等待 task slot（可能排队）
        if sem.waiting_count > 0 or sem.active_count >= sem.limit:
            self._log(f"Queued (task slots full: {sem.active_count}/{sem.limit})")
            _debug_log(task.id, "queued", {"active": sem.active_count, "limit": sem.limit}, events_path=self._events_path)

        _debug_log(task.id, "acquiring_semaphore", {"label": task.id[:8] + " " + task.name[:16]}, events_path=self._events_path)
        with sem.acquire(label=task.id[:8] + " " + task.name[:16]):
            _debug_log(task.id, "semaphore_acquired", events_path=self._events_path)
            # 检查是否在排队期间被取消
            with self._lock:
                if self.record.status == TaskStatus.CANCELLED:
                    _debug_log(task.id, "cancelled_while_queued", events_path=self._events_path)
                    # 在排队期间被取消，直接返回
                    return
            self._run_body(task)

    def _run_body(self, task) -> None:
        # 【修复】在这里设置 RUNNING 状态（之前错误地在 task_manager._launch() 里设置）
        with self._lock:
            if self.record.status == TaskStatus.CANCELLED:
                return  # 获得信号量前已被取消
            self.record.status = TaskStatus.RUNNING
            self.record.started_at = time.time()

        # [SYS-HOOKS] SubagentStart：SubAgent 进入 RUNNING 状态时触发
        try:
            from mini_agent.hooks import get_hook_manager as _ghm_sa
            _hm_sa = _ghm_sa()
            if _hm_sa is not None:
                _hm_sa.run("SubagentStart", {
                    "task_id": task.id,
                    "task_name": task.name,
                    "prompt": task.prompt[:200],
                })
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.orchestrator.sub_agent')
            pass

        self._log(f"Starting task: {task.name}")
        self._log(f"Config: model={task.model or 'default'}, max_turns={task.max_turns}")
        _debug_log(task.id, "run_body_start", {"model": task.model, "max_turns": task.max_turns}, events_path=self._events_path)

        try:
            _debug_log(task.id, "building_agent", events_path=self._events_path)
            agent = self._build_agent(task)
            self._log(f"Agent built, running turn...")
            _debug_log(task.id, "agent_built", {"stats": str(agent.stats)}, events_path=self._events_path)

            _debug_log(task.id, "running_turn", events_path=self._events_path)
            output = self._run_with_capture(agent, task.prompt)
            _debug_log(task.id, "turn_completed", {"output_len": len(output) if output else 0}, events_path=self._events_path)
            self._log(f"Turn completed, output length: {len(output) if output else 0} chars")

            with self._lock:
                if self._cancel_event.is_set():
                    self.record.status = TaskStatus.CANCELLED
                    self.record.result = TaskResult(
                        output=output, error="Cancelled after completion"
                    )
                    _debug_log(task.id, "cancelled", events_path=self._events_path)
                else:
                    self.record.status = TaskStatus.DONE
                    self.record.result = TaskResult(
                        output=output,
                        input_tokens=agent.stats.input_tokens,
                        output_tokens=agent.stats.output_tokens,
                        tool_calls=agent.stats.tool_calls,
                        turns=agent.stats.turns,
                    )
                    self._write_result_json(output, agent)
                    _debug_log(task.id, "done", {
                        "input_tokens": agent.stats.input_tokens,
                        "output_tokens": agent.stats.output_tokens,
                        "tool_calls": agent.stats.tool_calls,
                        "turns": agent.stats.turns
                    })
            self._log(f"Done. Tokens: {agent.stats.input_tokens}↑ {agent.stats.output_tokens}↓, turns={agent.stats.turns}")

        except TimeoutError as exc:
            self._log(f"TIMEOUT: {exc}")
            _debug_log(task.id, "timeout", {"error": str(exc)}, events_path=self._events_path)
            with self._lock:
                self.record.status = TaskStatus.FAILED
                self.record.result = TaskResult(output="", error=f"Timeout: {exc}")

        except Exception as exc:
            from mini_agent.errors import log_exception
            log_exception(exc, where='mini_agent.orchestrator.sub_agent.SubAgent._run_body')
            tb = traceback.format_exc()
            self._log(f"ERROR: {exc}")
            # 完整 traceback 写入 debug jsonl，方便离线排查
            _debug_log(task.id, "error", {"error": str(exc), "traceback": tb}, events_path=self._events_path)
            with self._lock:
                self.record.status = TaskStatus.FAILED
                # error 字段保存完整 traceback，而不只是 str(exc)
                # 这样 get_task_status 返回的 error 字段包含完整堆栈
                self.record.result = TaskResult(output="", error=tb.strip())
            # traceback 同时逐行写入 log_lines
            for line in tb.splitlines():
                self.record.append_log(line)

        finally:
            with self._lock:
                self.record.finished_at = time.time()
                # 任务结束时补写 outcome 块到 manifest.json（DONE/FAILED/CANCELLED 三种终态统一处理）
                if self.record.is_terminal:
                    self.record.write_manifest()
                # 通知终态（只通知一次）
                if self.record.is_terminal and self.record.status not in self._terminal_notified:
                    self._terminal_notified.add(self.record.status)
                    old_status = TaskStatus.PENDING  # 近似值
                    # [SYS-HOOKS] SubagentStop：SubAgent 进入终态时触发
                    try:
                        from mini_agent.hooks import get_hook_manager as _ghm_sas
                        _hm_sas = _ghm_sas()
                        if _hm_sas is not None:
                            _hm_sas.run("SubagentStop", {
                                "task_id": self.record.task_id,
                                "status": self.record.status.value if hasattr(self.record.status, 'value') else str(self.record.status),
                                "error": (self.record.result.error if self.record.result else "") or "",
                            })
                    except Exception as _mini_agent_exc:
                        from mini_agent.errors import log_exception
                        log_exception(_mini_agent_exc, where='mini_agent.orchestrator.sub_agent')
                        pass
                    if self.on_terminal:
                        try:
                            self.on_terminal(self.record.task_id, old_status, self.record.status)
                        except Exception as _mini_agent_exc:
                            from mini_agent.errors import log_exception
                            log_exception(_mini_agent_exc, where='mini_agent.orchestrator.sub_agent')
                            pass

    # SubAgent 层的重试配置
    _RETRY_MAX_ATTEMPTS = 8    # 最多尝试 3 次（首次 + 2 次重试）
    _RETRY_DELAY = 5.0         # 每次重试前等待 2 秒，给 NIM 服务端缓冲时间

    def _is_retryable_error(self, err_str: str) -> bool:
        """
        判断错误是否值得重试。
        可重试：HTTP 5xx（服务端临时错误）、超时。
        不重试：HTTP 4xx（鉴权失败、参数错误等客户端问题）。
        """
        err_lower = err_str.lower()
        if "http 5" in err_lower:       # 500 / 502 / 503 / 504
            return True
        if "timeout" in err_lower:
            return True
        if "timed out" in err_lower:
            return True
        return False

    def _run_with_capture(self, agent: Agent, prompt: str) -> str:
        """
        运行 agent.run_turn()，对可恢复的 LLM 错误（5xx / 超时）自动重试。

        【修复】原来的实现替换了全局 sys.stdout/sys.stderr，这在多线程环境下
        是不安全的：多个 SubAgent 并发时会互相覆盖对方的 stdout，也会破坏
        主线程的 rich/terminal 渲染（状态栏、REPL 输出等）。

        正确做法：直接运行 run_turn()，SubAgent 的输出本来就应该通过
        on_log 回调（写入 TaskRecord.log_lines）而不是 stdout。
        agent.run_turn() 内部的 rich Console 输出在 cfg.stream=False 时
        会静默（因为 SubAgent 构建时设置了 cfg.stream=False）。

        【止血补丁】NVIDIA NIM 等服务端在高并发时会偶发 HTTP 500（serde
        反序列化错误），该错误与请求内容无关，重试大概率成功。此处在
        SubAgent 层捕获可重试异常，最多尝试 _RETRY_MAX_ATTEMPTS 次，每次
        间隔 _RETRY_DELAY 秒。4xx 等客户端错误不重试，直接向上抛出。
        """
        last_exc: Optional[Exception] = None

        for attempt in range(1, self._RETRY_MAX_ATTEMPTS + 1):
            try:
                return agent.run_turn(prompt)
            except Exception as exc:
                err_str = str(exc)
                if not self._is_retryable_error(err_str):
                    # 不可重试的错误（如 4xx），直接抛出
                    raise

                last_exc = exc
                if attempt < self._RETRY_MAX_ATTEMPTS:
                    self._log(
                        f"[Retry {attempt}/{self._RETRY_MAX_ATTEMPTS - 1}] "
                        f"LLM transient error, retrying in {self._RETRY_DELAY}s: {exc}"
                    )
                    _debug_log(self.record.task_id, "llm_retry", {
                        "attempt": attempt,
                        "max_attempts": self._RETRY_MAX_ATTEMPTS,
                        "error": err_str,
                        "delay_s": self._RETRY_DELAY,
                    })
                    time.sleep(self._RETRY_DELAY)
                else:
                    self._log(f"[Retry exhausted] All {self._RETRY_MAX_ATTEMPTS} attempts failed: {exc}")
                    _debug_log(self.record.task_id, "llm_retry_exhausted", {
                        "attempts": self._RETRY_MAX_ATTEMPTS,
                        "error": err_str,
                    })

        # 所有重试耗尽，抛出最后一次异常
        raise last_exc

    def _build_agent(self, task: Task) -> Agent:
        """为本次任务构建独立的 Agent 实例。"""
        # 继承主 cfg，但允许 task 覆盖部分字段
        # 关键：显式传递 API key 和 LLM 配置，避免子线程重新读取环境变量
        cfg = load_config(
            project_root=self.base_cfg.project_root,
            verbose=False,
            sandbox=self.base_cfg.sandbox,
            auto_approve=task.auto_approve,
            model=task.model or self.base_cfg.model,
            llm_provider=task.provider or self._llm_provider,
            llm_base_url=self._llm_base_url,
            use_system_tool_call=self.base_cfg.use_system_tool_call,
            debug_llm=self.base_cfg.debug_llm,
            # [Phase E / 3.3 顺带修复] load_config() 默认从磁盘/环境变量重新加载，
            # 不会自动继承调用方内存中 base_cfg 的 perception 配置；之前这里没有
            # 显式传 tool_cache_enabled，导致即使主 agent 开启了 tool_cache，
            # 没有 shared_tool_cache 注入的 SubAgent 也会静默拿到一个 None
            # （未启用）的私有缓存，而不是按预期各自新建一份私有缓存。
            tool_cache_enabled=self.base_cfg.tool_cache_enabled,
        )
        # [session 嵌套] 若有主 session id，让本 SubAgent（task）的 session
        # 落在 <project_root>/.agent/sessions/<main_session_id>/<自己的 session_id>/
        # 下，而不是与主 session 平级散落在 sessions_dir 根目录。
        if self._session_id:
            cfg.session_dir = AgentPaths(self.base_cfg.project_root).session_dir(self._session_id)
        # 显式设置 API key（从主配置继承）
        if not cfg.api_key and self._api_key:
            cfg.api_key = self._api_key
        # 如果还是空的，从环境变量再试一次，并记录 debug 信息
        if not cfg.api_key:
            from mini_agent.config import os
            env_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if env_key:
                cfg.api_key = env_key
                _debug_log(self.record.task_id, "api_key_from_env", {}, events_path=self._events_path)
            else:
                _debug_log(self.record.task_id, "api_key_missing", {
                    "from_config": bool(self._api_key),
                    "from_env": bool(env_key)
                })

        cfg.max_turns = task.max_turns
        cfg.stream = False   # SubAgent 不流式输出（输出被捕获）
        if task.system_extra:
            cfg.system_extra = task.system_extra

        llm_cfg = LLMConfig.from_app_config(cfg)
        guard = PermissionGuard(
            auto_approve=task.auto_approve,
            sandbox=self.base_cfg.sandbox,   # 【修复】继承主进程的 sandbox 配置
            project_root=self.base_cfg.project_root,
        )

        # 自定义子 agent 工具限制（来自 AgentProfile.tools / tool_groups）
        registry = None
        if task.allowed_tools or task.allowed_tool_groups:
            registry = get_default_registry().filtered(
                names=task.allowed_tools,
                groups=task.allowed_tool_groups,
            )

        # [Phase E / 3.3] SubAgent 信息继承：按名称激活主 agent 当前激活的 skill。
        # 只有 task.active_skills 非空时才构造 SkillLoader（避免没有继承需求的
        # 普通任务也付出一次技能目录扫描的开销）。目录解析逻辑与
        # cli/app.py 构造主 Agent 的 SkillLoader 时完全一致，保证子 agent
        # 能发现同一批 skill 定义（否则"按名称激活"会因为根本没扫描到该 skill
        # 而静默失败——SkillLoader.activate() 对未知名称直接返回 False）。
        skill_loader = None
        if task.active_skills:
            from mini_agent.skills import SkillLoader
            skill_dirs = []
            if cfg.skills_dir:
                skill_dirs.append(cfg.skills_dir)
            skill_loader = SkillLoader(
                skill_dirs,
                per_skill_tokens=getattr(cfg, "skill_compact_per_skill", 5_000),
                total_budget=getattr(cfg, "skill_compact_budget", 25_000),
            )
            for name in task.active_skills:
                skill_loader.activate(name)

            # 【关键修复】Agent.__init__ 在 self.skill_loader 非空时会调用
            # register_skill_tools()/register_compact_tool()/register_skill_stats_tool()，
            # 把 skill_list/skill_activate/compact_skill_context 等工具注册到
            # self.registry——若此处 registry 仍是 None（未设置 allowed_tools 时
            # 的默认情况），Agent.__init__ 会回退到全局单例 get_default_registry()。
            # 主 agent 启动时已经在那个全局单例上注册过同名工具，重复注册会直接
            # 抛 ValueError 崩溃任务（生产环境复现：spawn 一个继承了 active_skills
            # 的 SubAgent 即触发）。
            #
            # 不能简单加 override=True 了事：这些工具函数通过闭包捕获
            # skill_loader/agent 参数，override 会把全局 registry 里 skill_list
            # 等工具的实现直接替换成指向【这个 SubAgent】的 skill_loader——
            # 之后主 agent 或其他并发 SubAgent 调用 skill_list 时，实际执行的
            # 会是这个已经结束的 SubAgent 的闭包，造成跨 agent 串台，比崩溃更隐蔽、
            # 更危险。正确做法是给这种"持有自己 skill_loader"的 SubAgent 一份
            # 独立的 registry 副本（filtered() 返回的是新对象，不是引用），
            # 工具注册各自隔离，互不影响。
            if registry is None:
                registry = get_default_registry().filtered()

        return Agent(
            cfg=cfg, guard=guard, llm_client=create_client(llm_cfg),
            registry=registry, skill_loader=skill_loader,
            tool_cache=self._shared_tool_cache,
            is_subagent=True,
        )

    def _write_result_json(self, output: str, agent) -> None:
        """任务完成时将结果写入 result.json。"""
        if not self._result_path:
            return
        try:
            import json as _json
            self._result_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "task_id":       self.record.task_id,
                "status":        "done",
                "started_at":    self.record.started_at,
                "finished_at":   time.time(),
                "input_tokens":  agent.stats.input_tokens,
                "output_tokens": agent.stats.output_tokens,
                "tool_calls":    agent.stats.tool_calls,
                "turns":         agent.stats.turns,
                "output_len":    len(output) if output else 0,
            }
            self._result_path.write_text(
                _json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.orchestrator.sub_agent')
            pass

    def _log(self, line: str) -> None:
        self.record.append_log(line)
        # 写入 output.log（tab 切换功能的数据源）
        if self._output_path:
            try:
                self._output_path.parent.mkdir(parents=True, exist_ok=True)
                ts = time.strftime("%H:%M:%S")
                with open(self._output_path, "a", encoding="utf-8") as f:
                    f.write(f"[{ts}] {line}\n")
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.orchestrator.sub_agent')
                pass
        if self.on_log:
            try:
                self.on_log(self.record.task_id, line)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.orchestrator.sub_agent')
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