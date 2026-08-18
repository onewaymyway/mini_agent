"""
evolution/cron_agent_bridge.py — 为 cron 任务构建专用 Agent 实例

与 orchestrator/sub_agent.py::SubAgent._build_agent() 的关系：
  同样是"继承主 cfg，重新 load_config 一份独立配置"的模式，但 cron 任务
  不依赖 Task/TaskRecord（那是给"用户/主 Agent 显式派发的一次性任务"用的，
  带 manifest/事件落盘等一整套记录机制），cron 任务有自己的
  cron_job_workspace.py 记录进度/执行日志，所以这里是一个更轻量的独立函数。

  按用户要求：cron 任务全量继承主 Agent 的工具（不传 allowed_tools /
  allowed_tool_groups），构造时 registry 参数留空，Agent.__init__ 会回退
  到全局单例 get_default_registry()——与主 Agent、以及"未设置工具限制"的
  普通 SubAgent 用的是同一份全局工具注册表，这是本代码库里已经被
  SubAgent 并发验证过的既有模式（tools/orchestration.py 等模块的
  thread-local 状态按"构造 Agent 的线程"隔离，只要 Agent 在它将要运行的
  那条线程上构造，就是安全的——见 api/server.py AgentRunner 里 Phase 3
  的说明）。CronJobRunner 保证了这一点：Agent 在专属的 cron 执行线程内
  构造并运行，不跨线程。
"""

from __future__ import annotations

import json
import os
import re
from typing import Callable, Optional, TYPE_CHECKING

from mini_agent.config import AppConfig, load_config
from mini_agent.agent import Agent
from mini_agent.llm.base import LLMConfig
from mini_agent.llm.factory import create_client
from mini_agent.permissions import PermissionGuard
from mini_agent.evolution.cron_job_executor import StepResult

if TYPE_CHECKING:
    from mini_agent.evolution.cron_scheduler import CronJob


# cron 任务默认给较小的单次 run_turn() 内部预算（max_turns），配合
# CronJobExecutor 的外层 step 循环达成"内层限步数、外层限墙钟时间"的
# 双重兜底——单次 run_turn() 调用不会无限跑下去，即使某一步异常复杂，
# 也会先撞到这个内层预算，把控制权交还给外层循环，由外层判断是否已经
# 超时/超过 max_steps。
CRON_INNER_MAX_TURNS_DEFAULT = 15


def build_cron_agent(
    base_cfg: AppConfig,
    job: "CronJob",
    inner_max_turns: Optional[int] = None,
) -> Agent:
    """
    为一次 cron job 执行构建一个全新的、全量继承主 Agent 工具集的 Agent 实例。

    每次 job 触发都重新构建（不跨触发复用同一个 Agent/history），
    "上次做到哪了"的连续性通过 CronJobWorkspace.render_prompt() 拼接的
    progress_summary 文本实现，而不是靠保留 Agent 对象或 session 历史——
    这样可以避免 cron 任务的历史无限增长，也避免和用户会话的 session
    存储混在一起。

    inner_max_turns — 不传时回退到 base_cfg.cron.inner_max_turns（全局
    配置，config/models.py::CronConfig，默认 15），可以整机统一调整，
    不用逐处改代码常量。
    """
    if inner_max_turns is None:
        inner_max_turns = getattr(getattr(base_cfg, "cron", None), "inner_max_turns", CRON_INNER_MAX_TURNS_DEFAULT)

    cfg = load_config(
        project_root=base_cfg.project_root,
        verbose=False,
        sandbox=base_cfg.sandbox,
        auto_approve=True,               # cron 任务无人值守，必须自动批准工具调用
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
        f"\n\n[cron 任务] 你正在以 daemon 后台定时任务身份执行「{job.name}」"
        f"（job_id={job.id}）。这是无人值守执行，不会有人类实时回复你的问题——"
        f"如果信息不足，做出合理假设并在输出中说明，而不是等待澄清。"
        f"完成任务后请在最后一行输出 [CRON_DONE] 标记；如果任务本质上需要"
        f"跨多次触发才能完成（比如正在处理的内容量很大），在最后一行输出"
        f"[CRON_CONTINUE] 并简述下次应该从哪里继续。"
    )

    llm_cfg = LLMConfig.from_app_config(cfg)
    guard = PermissionGuard(
        auto_approve=True,
        sandbox=base_cfg.sandbox,
        project_root=base_cfg.project_root,
    )

    # registry 留空 → Agent.__init__ 回退到 get_default_registry()，
    # 即全量继承主 Agent 可用的工具集合（按用户明确要求）。
    return Agent(
        cfg=cfg,
        guard=guard,
        llm_client=create_client(llm_cfg),
        registry=None,
        skill_loader=None,
        tool_cache=None,
        is_subagent=True,
    )


_TOOL_RESULT_BLOCK_RE = re.compile(r"<tool_result>\s*(.*?)\s*</tool_result>", re.DOTALL)


def _extract_tool_calls_from_history(new_entries: list[dict]) -> list[dict]:
    """[cron_run_debug_detail_improvement_plan.md ①] 从 agent._hist 里本步
    新增的一段 history 条目中提取工具调用轨迹，组装成
    `{"name": ..., "input": ..., "output": ...}` 三元组列表。

    这些信息本来就存在于 history 里（agent.run_turn() 内部已经把
    assistant 回复的 tool_use content block 和工具结果回注都写了进去），
    这里只是纯读取 + 按出现顺序配对，不修改 history、不影响主流程。

    配对依据：assistant_reply 条目里按顺序出现的 tool_use block 先进
    "待配对"队列；随后出现的 tool_result 条目里，<tool_result>...
    </tool_result> 包裹的 JSON 块也按顺序解析——顺序保证见
    history_manager.append_tool_results() 内部 zip(tool_calls, results)
    的写入方式，两边天然一一对应。

    任何单条解析失败都静默跳过（防御性处理，调试功能本身不能拖垮 cron
    执行）；整体异常兜底返回已提取的部分结果。
    """
    tool_calls: list[dict] = []
    pending: list[dict] = []
    try:
        for entry in new_entries:
            entry_type = str(entry.get("_type") or "")
            if entry_type == "assistant_reply":
                content = entry.get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use":
                        pending.append({
                            "name": block.get("name"),
                            "input": block.get("input"),
                        })
            elif entry_type == "tool_result":
                content = entry.get("content")
                if not isinstance(content, str):
                    continue
                for match in _TOOL_RESULT_BLOCK_RE.finditer(content):
                    try:
                        parsed = json.loads(match.group(1))
                    except Exception:  # noqa: BLE001 — 单条解析失败跳过，不影响其它
                        continue
                    call = pending.pop(0) if pending else {
                        "name": parsed.get("name"), "input": None,
                    }
                    tool_calls.append({
                        "name": call.get("name"),
                        "input": call.get("input"),
                        "output": parsed.get("output"),
                    })
    except Exception:  # noqa: BLE001 — 调试信息提取失败不能影响 cron 主流程
        pass
    return tool_calls


def make_submit_step_fn(agent: Agent) -> Callable[[str], StepResult]:
    """
    把一个已构建好的 Agent 包装成 CronJobExecutor.run_job() 需要的
    submit_step_fn：每次调用做一次完整的 agent.run_turn()（内部可能是
    多轮工具调用，由 cfg.max_turns 兜底），返回本步文本 + 是否完成。

    完成判定优先级：
      1. 输出末尾出现 [CRON_DONE] 标记 → 明确完成
      2. 输出末尾出现 [CRON_CONTINUE] 标记 → 明确未完成，继续下一步
      3. 都没出现时，用 agent._last_turn_hit_max_turns 兜底判断：
         若本次 run_turn() 是自然结束（没有触达内层 max_turns 预算），
         说明模型自己认为已经把当前这轮该做的事做完了，判定为完成；
         若是被内层预算打断的，判定为未完成，继续下一步。
    """
    def _submit(prompt_text: str) -> StepResult:
        # [cron_run_debug_detail_improvement_plan.md ①] 调用前记下 history
        # 长度，调用后只对本步新增的一段做工具调用提取——不需要改
        # Agent 核心执行逻辑，纯读 agent._hist。history 属性不存在（极端
        # 情况下的替身/mock agent）时静默跳过，不影响主流程。
        hist = getattr(agent, "_hist", None)
        before_len = len(hist.history) if hist is not None else 0

        try:
            text = agent.run_turn(prompt_text)
        except Exception as e:  # noqa: BLE001 — 交给 CronJobExecutor 统一记录/降级
            return StepResult(text="", done=False, error=str(e))

        tool_calls: list[dict] = []
        if hist is not None:
            try:
                tool_calls = _extract_tool_calls_from_history(hist.history[before_len:])
            except Exception:  # noqa: BLE001 — 提取失败不能影响本步正常返回
                tool_calls = []

        stripped = (text or "").strip()
        if "[CRON_DONE]" in stripped:
            return StepResult(text=stripped, done=True, tool_calls=tool_calls)
        if "[CRON_CONTINUE]" in stripped:
            return StepResult(text=stripped, done=False, tool_calls=tool_calls)

        hit_budget = bool(getattr(agent, "_last_turn_hit_max_turns", False))
        return StepResult(text=stripped, done=(not hit_budget), tool_calls=tool_calls)

    return _submit


__all__ = [
    "build_cron_agent", "make_submit_step_fn", "CRON_INNER_MAX_TURNS_DEFAULT",
    "_extract_tool_calls_from_history",
]
