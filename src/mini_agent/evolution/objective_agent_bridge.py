"""
evolution/objective_agent_bridge.py — [daemon_autonomous_state_recovery_plan.md
阶段三 / P1] 为 autonomous Objective 的每个 step 构建独立上下文的 Agent 实例。

背景：在这个模块出现之前，`autonomous`/`cron` 的 turn 都通过
`ObjectiveExecutor._submit_fn` → `bridge.input_queue.enqueue()` 提交，最终跑在
Self 共用的那一个 `bridge.agent` 上——与真人交互、与其它自主任务共享同一段
对话历史，容易互相污染上下文（见计划文档"根因回顾"第 4 点）。cron 任务已经
在更早的改造里通过 `cron_agent_bridge.py` + `CronJobExecutor` 拿到了独立的
Agent 实例；这个模块把同样的模式补给 `autonomous` Objective 的 step。

设计要点（与 cron_agent_bridge.py 保持同构，便于对照阅读）：
  - 每次 step 提交都重新构建一个全新 Agent（不复用、不保留跨 step 的对话
    历史）。"上一步做到哪了"完全靠 ObjectiveExecutor 自己在 prompt 里拼接的
    结构化摘要（`[前序步骤结果]`/`[前序步骤产出文件]`）传递，这一点在
    `_submit_step()` 里已经实现，本模块不需要、也不应该额外做什么来"记住"
    上一个 Agent 实例的状态。
  - 全量继承主 Agent 的工具（registry 留空 → 回退到全局默认 registry），
    与 cron 任务、以及未设置工具限制的普通 SubAgent 是同一套已验证过的
    "thread-local 状态按构造 Agent 的线程隔离"模式——只要 Agent 在它将要
    运行的那条线程上构造，就是安全的。`ObjectiveIsolatedRunner` 保证了这
    一点：每个 step 都在专属的后台线程里构造 + 运行，不跨线程。
  - `auto_approve=True`：自主任务无人值守，必须自动批准工具调用（与 cron
    一致）。
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import threading
import time
import uuid
from typing import Any, Callable, Optional

from mini_agent.config import AppConfig, load_config
from mini_agent.agent import Agent
from mini_agent.llm.base import LLMConfig
from mini_agent.llm.factory import create_client
from mini_agent.permissions import PermissionGuard

log = logging.getLogger("mini_agent.objective_agent_bridge")

# 与 cron_agent_bridge.CRON_INNER_MAX_TURNS_DEFAULT 同一档位；实际生效值
# 由 cfg.autonomy.objective_isolated_inner_max_turns 决定，这里只是兜底。
OBJECTIVE_INNER_MAX_TURNS_DEFAULT = 15


def build_objective_agent(
    base_cfg: AppConfig,
    objective_title: str,
    execution_id: str,
    inner_max_turns: Optional[int] = None,
) -> Agent:
    """
    为一次 Objective step 执行构建一个全新的、全量继承主 Agent 工具集的独立
    Agent 实例。不携带真人交互或其它 Objective 的历史，只携带任务描述本身
    （由调用方传入 run_turn() 的 message 参数）。

    与 cron_agent_bridge.build_cron_agent() 的结构保持一致，便于对照维护。
    """
    if inner_max_turns is None:
        inner_max_turns = getattr(
            getattr(base_cfg, "autonomy", None),
            "objective_isolated_inner_max_turns",
            OBJECTIVE_INNER_MAX_TURNS_DEFAULT,
        )

    cfg = load_config(
        project_root=base_cfg.project_root,
        verbose=False,
        sandbox=base_cfg.sandbox,
        auto_approve=True,               # 自主任务无人值守，必须自动批准工具调用
        model=base_cfg.model,
        llm_provider=base_cfg.llm_provider,
        llm_base_url=base_cfg.llm_base_url,
        use_system_tool_call=base_cfg.use_system_tool_call,
        debug_llm=base_cfg.debug_llm,
        tool_cache_enabled=base_cfg.tool_cache_enabled,
    )
    if not cfg.api_key:
        cfg.api_key = base_cfg.api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    cfg.max_turns = inner_max_turns
    cfg.stream = False
    cfg.system_extra = (
        (base_cfg.system_extra or "") +
        f"\n\n[自主任务 - 独立上下文] 你正在以 daemon 后台自主任务身份执行"
        f"「{objective_title}」（execution_id={execution_id}）。这是无人值守"
        f"执行，本次 turn 使用一个专属的、不携带其它对话历史的独立会话——"
        f"如果需要了解此前步骤的进展，请以本条消息里附带的"
        f"「前序步骤结果」/「前序步骤产出文件」为准，不要假设自己记得任何"
        f"未在本条消息中出现的内容。如果信息不足，做出合理假设并在输出中"
        f"说明，而不是等待澄清。"
    )

    llm_cfg = LLMConfig.from_app_config(cfg)
    guard = PermissionGuard(
        auto_approve=True,
        sandbox=base_cfg.sandbox,
        project_root=base_cfg.project_root,
    )

    # registry 留空 → Agent.__init__ 回退到 get_default_registry()，
    # 即全量继承主 Agent 可用的工具集合，与 cron 任务同一模式。
    return Agent(
        cfg=cfg,
        guard=guard,
        llm_client=create_client(llm_cfg),
        registry=None,
        skill_loader=None,
        tool_cache=None,
        is_subagent=True,
    )


class ObjectiveIsolatedRunner:
    """
    [P1] 可以直接替换 `ObjectiveExecutor._submit_fn` 的独立上下文 runner。

    与共享 bridge.input_queue 的默认提交路径相比：每次 `submit()` 调用都在
    一个专属的后台线程里构建全新 Agent + 执行 `run_turn()`，执行完毕后立即
    丢弃这个 Agent 实例（不保留、不复用），并通过 `on_done`/`on_failed`
    回调把结果交回给 ObjectiveExecutor——回调签名与
    `ObjectiveExecutor.on_turn_done(turn_id, result_summary, valid)` /
    `on_turn_failed(turn_id, error)` 完全一致，可以直接传方法引用。

    结果健全性校验（P0-A）在这里同样生效：复用
    `perception/format_correction_detector.py::is_valid_final_result()`，
    与 api/server.py 里共享路径的判定逻辑保持一致，不需要
    ObjectiveExecutor/on_turn_done 关心"这个 turn 是不是隔离上下文跑的"。
    """

    def __init__(
        self,
        base_cfg: AppConfig,
        on_done: Callable[..., Any],
        on_failed: Callable[[str, str], Any],
        max_workers: Optional[int] = None,
        inner_max_turns: Optional[int] = None,
    ) -> None:
        self._base_cfg = base_cfg
        self._on_done = on_done
        self._on_failed = on_failed
        self._inner_max_turns = inner_max_turns
        if max_workers is None:
            max_workers = getattr(
                getattr(base_cfg, "autonomy", None), "objective_isolated_max_workers", 4
            )
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, int(max_workers)),
            thread_name_prefix="obj-isolated",
        )
        self._lock = threading.Lock()
        self._stopped = False

    def submit(self, message: str, initiator: str, meta: dict) -> Optional[str]:
        """与 `submit_fn(message, initiator, meta) -> turn_id` 签名一致，
        可直接赋给 `ObjectiveExecutor._submit_fn`。"""
        with self._lock:
            if self._stopped:
                return None
        turn_id = f"obj-iso-{uuid.uuid4().hex[:12]}"
        try:
            self._executor.submit(self._run_step, turn_id, message, meta)
        except RuntimeError:
            # executor 已 shutdown（daemon 正在退出），当作提交失败处理，
            # ObjectiveExecutor 会按现有"submit 返回 None"的既有路径降级。
            return None
        return turn_id

    def _run_step(self, turn_id: str, message: str, meta: dict) -> None:
        from mini_agent.perception.format_correction_detector import is_valid_final_result

        objective_title = meta.get("objective_id", "") or "(unknown)"
        execution_id = meta.get("execution_id", "") or "(unknown)"

        try:
            agent = build_objective_agent(
                self._base_cfg, objective_title, execution_id,
                inner_max_turns=self._inner_max_turns,
            )
        except Exception as exc:
            log.warning("build_objective_agent failed for turn_id=%s: %s", turn_id, exc)
            self._safe_on_failed(turn_id, f"独立上下文 Agent 构建失败: {exc}")
            return

        try:
            result = agent.run_turn(message)
        except Exception as exc:
            log.warning("isolated run_turn failed for turn_id=%s: %s", turn_id, exc)
            self._safe_on_failed(turn_id, str(exc))
            return

        summary = (result or "").strip()
        summary = summary.split("\n")[0][:200]
        result_valid = not getattr(agent, "_last_turn_result_invalid", False)
        if not result_valid and not is_valid_final_result(result or ""):
            # 双重确认（agent 自身已经在 run_turn 末尾判过一次，这里再校验
            # 一遍是防御性的，避免未来接入不经过 run_turn() 内部校验路径的
            # 场景时静默漏判）——两者任一判定无效就认为无效。
            result_valid = False

        self._safe_on_done(turn_id, summary, result_valid)

    def _safe_on_done(self, turn_id: str, summary: str, valid: bool) -> None:
        try:
            self._on_done(turn_id, summary, valid=valid)
        except Exception as exc:
            log.warning("on_done callback failed for turn_id=%s: %s", turn_id, exc)

    def _safe_on_failed(self, turn_id: str, error: str) -> None:
        try:
            self._on_failed(turn_id, error)
        except Exception as exc:
            log.warning("on_failed callback failed for turn_id=%s: %s", turn_id, exc)

    def shutdown(self, wait: bool = False) -> None:
        """daemon 退出时调用：停止接受新 step，不强行打断正在跑的线程
        （wait=False 时不阻塞退出流程，与其它子系统的关停风格一致）。"""
        with self._lock:
            self._stopped = True
        self._executor.shutdown(wait=wait, cancel_futures=not wait)
