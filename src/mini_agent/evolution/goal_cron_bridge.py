"""
evolution/goal_cron_bridge.py — Goal ⇄ Cron 绑定桥接层
（next_doc/goal_cron_binding_plan.md Track B/C）

背景：`GoalBacklog`（perception/goal_backlog.py）和 `CronScheduler`
（evolution/cron_scheduler.py）此前是完全独立的两套体系——GoalNode 没有周期性字段，
CronJob 触发只会把 task_template 当一条裸消息塞进 InputQueue，互不感知。本模块
不改动两者各自的核心职责（GoalBacklog 管状态机，CronScheduler 管定时），只加一层
绑定/触发/回收逻辑，让一个 Goal 可以声明"我需要被周期性推进"。

对外入口三个：
  register_goal_cycle_handler(cron_scheduler, goal_backlog, objective_executor)
      — daemon 启动时调用一次，把触发逻辑接进 CronScheduler。
  make_goal_recurring(goal_backlog, cron_scheduler, goal_id, schedule, task_template)
      — 把一个已存在的 Goal 声明为周期性。
  stop_goal_recurrence(goal_backlog, cron_scheduler, goal_id)
      — 反向操作，停止自动续期（不删 Goal/cron job）。

以及一个供 AutonomousLoop 被动 tick 调用的收尾函数：
  reap_finished_cycles(goal_backlog) — 扫描所有 recurring Goal，把本轮已进入终态
      的子 Objective 计入 cycle_count/progress_notes。
"""

from __future__ import annotations

import time
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.perception.goal_backlog import GoalBacklog, GoalNode
    from mini_agent.evolution.cron_scheduler import CronScheduler, CronJob
    from mini_agent.evolution.objective_executor import ObjectiveExecutor


_TERMINAL_STATUSES = ("completed", "failed", "cancelled")


# ── 触发逻辑（Track B） ────────────────────────────────────────────────────────

def register_goal_cycle_handler(
    cron_scheduler: "CronScheduler",
    goal_backlog: "GoalBacklog",
    objective_executor: "ObjectiveExecutor",
) -> None:
    """把 goal_cycle 触发逻辑挂到 cron_scheduler。daemon 启动时（构建完
    GoalBacklog/CronScheduler/ObjectiveExecutor 三者之后）调用一次，见
    api/server.py::HttpServer._build_autonomous_loop()。
    """

    def _handler(job: "CronJob") -> bool:
        return _fire_goal_cycle(job, goal_backlog, objective_executor)

    cron_scheduler.set_goal_cycle_handler(_handler)


def _goal_has_active_cycle(goal: "GoalNode", goal_backlog: "GoalBacklog",
                            objective_executor: "ObjectiveExecutor") -> bool:
    """幂等检查：Goal 下是否已有一轮子 Objective 仍在跑。

    只看"仍是 active 状态、且 ObjectiveExecutor 认为它正在跑"的子节点——
    Objective 完成/失败/取消后，`ObjectiveExecutor._sync_goal_status()` 会把
    子节点自己的 status 改成终态，此时不再算"活跃"，允许开下一轮。
    """
    for child_id in goal.children_ids:
        child = goal_backlog.get(child_id)
        if child is None or not child.is_objective:
            continue
        if child.status == "active" and objective_executor.is_running(child.id):
            return True
    return False


def _autonomy_level(paths) -> str:
    """读取当前 autonomy_level，逻辑与 AutonomousLoop._get_autonomy_level() 保持
    一致（读取失败时保守降级为 passive）。之所以在这里重复一份小函数而不是
    直接依赖 AutonomousLoop 的方法，是为了不引入 goal_cron_bridge → autonomous_loop
    的反向依赖（后者本来就要 import 本模块）。
    """
    try:
        from mini_agent.perception.global_knowledge import load_self_profile
        profile = load_self_profile(paths)
        if profile:
            return profile.operating_state.autonomy_level
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.goal_cron_bridge._autonomy_level')
    return "passive"


def _fire_goal_cycle(
    job: "CronJob",
    goal_backlog: "GoalBacklog",
    objective_executor: "ObjectiveExecutor",
) -> bool:
    """CronScheduler 到期触发一个 run_mode="goal_cycle" 的 job 时调用。

    返回 False 时 CronScheduler.tick() 不会推进 last_run_at/next_run_at，
    等于"这次没算数"，下次 tick 会再次尝试——用来实现"Goal 非 active 时挂起
    等待"和"上一轮还没跑完时跳过"两种场景，都不需要用户手动介入。

    档位边界：`AutonomousLoop._tick_passive()` 的既有约定是"方法体内不引用
    GoalBacklog 任何方法"，但 CronScheduler.tick() 恰好是在 passive 档位下
    被调用的（cron job 本身不分档位）。goal_cycle job 的触发逻辑本质上就是
    读写 GoalBacklog，为了不悄悄破坏这条边界，这里显式检查：autonomy_level
    为 "passive" 时直接跳过（不触碰 goal_backlog），等用户把档位调到
    maintenance/autonomous 才会真正生效。这也符合直觉——"周期性 Goal 自动
    续期"本来就是一种自主行为，不该在最保守的 passive 档位下发生。
    """
    if not job.goal_id:
        return False

    paths = getattr(goal_backlog, "_paths", None)
    if paths is not None and _autonomy_level(paths) == "passive":
        return False

    goal_backlog.load()
    goal = goal_backlog.get(job.goal_id)
    if goal is None or not goal.is_goal:
        # Goal 已经不存在了（比如被彻底删除，虽然当前 GoalBacklog 没有硬删除
        # 接口，但预留这个分支防御未来变化）——job 本身不自动删除，只是
        # 每次触发都跳过，用户可以用 /cron remove 清理。
        return False

    if goal.status != "active":
        # Goal 被暂停/放弃/取消：不触发，也不报错。这正是 P3 要解决的问题——
        # 用户只需要管 Goal 的状态，不需要额外记得去 disable 对应 cron job。
        return False

    if goal.skip_next_cycle:
        # [goal_cron_visibility_and_intervention_improvement_plan.md Track B]
        # 用户主动请求跳过这一轮，但保持 recurring=True——跟"Goal 未 active"
        # "上一轮未完成"两种系统级跳过不同，这是用户的主动决策，需要留痕在
        # progress_notes 里，方便回看"这一轮为什么没跑"。跳过后清零标记，
        # 只影响下一次触发这一次，不会一直跳过。
        goal_backlog.update_fields(goal.id, skip_next_cycle=False)
        goal_backlog.append_progress_note(goal.id, "本轮由用户手动跳过（跳过后周期性照常继续）")
        return False

    if _goal_has_active_cycle(goal, goal_backlog, objective_executor):
        # 上一轮还没跑完，本轮跳过，不叠加并发。
        return False

    cycle_no = goal.cycle_count + 1
    # [goal_cron_feedback_and_output_policy_plan.md P3] 原来是"二选一"
    # （job.task_template or goal.description），一旦 CronJob 配了
    # task_template，父 Goal 里的约束（含用户后续追加的意见）就不会出现在
    # 子任务里。改成拼接：父 Goal 说明在前，本轮具体任务模板在后，都保留。
    from mini_agent.perception.goal_backlog import compose_context
    description = compose_context(goal.description, job.task_template)
    description = _append_output_workspace_context(paths, goal.id, cycle_no, description)
    description = _append_execution_spec_context(paths, goal_backlog, goal, description)
    description = _append_growth_reorganize_hint(paths, goal, cycle_no, description)
    description = _append_growth_self_check_hint(paths, goal, cycle_no, description)
    _maybe_reclassify_growth_pursuit_style(paths, goal_backlog, goal, cycle_no)
    description = _append_growth_pursuit_style_hint(paths, goal, description)
    objective = goal_backlog.add_objective(
        title=f"{goal.title}（第 {cycle_no} 轮）",
        parent_id=goal.id,
        source="cron",
        description=description,
    )

    exec_id = objective_executor.start(objective)
    if exec_id is None:
        # 启动失败（拆解失败/第一步提交失败等）：把这个子节点标终态，避免
        # 留下一个 status="active" 但没有对应 execution 的幽灵节点，卡住
        # 下一次 tick 的幂等检查（_goal_has_active_cycle 会一直认为"活跃"，
        # 因为 objective_executor.is_running() 对它返回 False，反而不会拦住——
        # 但节点本身语义上应该反映"这轮没跑起来"，所以仍需显式标记）。
        goal_backlog.set_status(objective.id, "failed")
        goal_backlog.update_fields(objective.id, progress_notes="本轮启动失败：objective_executor.start() 返回 None")
        return False

    return True


def _append_output_workspace_context(paths, goal_id: str, cycle_no: int, description: str) -> str:
    """[goal_cron_output_directory_convention_plan.md §3] recurring Goal
    一侧不经过 CronJobWorkspace.render_prompt()（那是 dedicated-execution
    cron 专属路径），这里补一段等价逻辑：分配本轮产出目录、读上一轮
    manifest，拼进子 Objective 描述末尾。

    paths 为 None（拿不到 AgentPaths，理论上不应发生，防御性处理）或任何
    环节异常时，静默跳过——不影响 Goal 触发主流程，退化为改造前的行为
    （agent 自己判断产出放哪）。
    """
    if paths is None:
        return description
    try:
        from mini_agent.evolution import output_workspace
        base_dir = output_workspace.goal_output_base_dir(paths, goal_id)
        cycle_dir = output_workspace.allocate_cycle_dir(paths, goal_id, cycle_no)

        parts = [description] if description and description.strip() else []

        prev_manifest = output_workspace.read_latest_manifest(base_dir)
        if prev_manifest:
            prev_text = output_workspace.format_manifest_for_prompt(prev_manifest)
            if prev_text:
                parts.append(f"--- 上一轮产出（{prev_manifest.get('_dir', '')}） ---\n{prev_text}")

        parts.append(f"本轮产出请写入：{cycle_dir}")
        return "\n\n".join(parts)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.goal_cron_bridge._append_output_workspace_context')
        return description


def _append_execution_spec_context(
    paths, goal_backlog: "GoalBacklog", goal: "GoalNode", description: str,
) -> str:
    """[goal_execution_spec_generation_plan.md §5] 如果这个 Goal 已确认执行
    规范，把 deliverables/sub_directories/per_cycle_criteria/
    special_constraints/handoff_fields 格式化后拼进本轮子 Objective
    description；同时做 §5.1 的轻量核对（纯文件名/key 字符串匹配），
    不匹配时追加软性提示，连续多轮不匹配则在 GoalNode 上留一条系统备注。

    未确认（`execution_spec_confirmed=False`）时完全不读规范文件，行为与
    引入本机制之前一致——不确认就不生效。任何环节异常都静默跳过，不影响
    Goal 触发主流程。
    """
    if paths is None or not getattr(goal, "execution_spec_confirmed", False):
        return description
    try:
        from mini_agent.config import load_config
        ges_cfg = getattr(load_config(), "goal_execution_spec", None)
        if ges_cfg is not None and not getattr(ges_cfg, "enabled", True):
            return description

        from mini_agent.perception import goal_execution_spec as ges
        spec = ges.load_spec(paths, goal.id)
        if spec is None or not spec.confirmed or spec.is_empty():
            return description

        block = spec.render_prompt_block()
        parts = [description] if description and description.strip() else []
        if block:
            parts.append(block)

        soft_check_enabled = bool(getattr(ges_cfg, "soft_check_enabled", True)) if ges_cfg else True
        if soft_check_enabled:
            hint = _soft_check_execution_spec(paths, goal_backlog, goal, spec, ges_cfg)
            if hint:
                parts.append(hint)

        return "\n\n".join(parts)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.goal_cron_bridge._append_execution_spec_context')
        return description


def _append_growth_reorganize_hint(paths, goal: "GoalNode", cycle_no: int, description: str) -> str:
    """[growth_advisor_autonomy_deepening_plan.md 方向 C1] 累计满
    `reorganize_every_n_cycles` 轮时，往本轮子 Objective description 里
    追加一段"顺带整理一下"的提示。只对打了 `growth_advisor` 标签的 Goal
    生效（`reorganize_hint_for_cycle` 内部判断），拿不到配置或任何环节
    异常都静默跳过，不影响 Goal 触发主流程。
    """
    if paths is None:
        return description
    try:
        from mini_agent.config import load_config
        cfg = getattr(load_config(), "growth_advisor", None)
        from mini_agent.evolution.growth_advisor import reorganize_hint_for_cycle
        hint = reorganize_hint_for_cycle(goal, cycle_no, cfg=cfg)
        if not hint:
            return description
        parts = [description] if description and description.strip() else []
        parts.append(hint)
        return "\n\n".join(parts)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.goal_cron_bridge._append_growth_reorganize_hint')
        return description


def _append_growth_self_check_hint(paths, goal: "GoalNode", cycle_no: int, description: str) -> str:
    """[growth_advisor_ideal_advisor_gap_and_roadmap_plan.md 方向 5]
    累计满 `pursuit_self_check_every_n_cycles` 轮时，往本轮子 Objective
    description 里追加一段"顺带生成自测小节"的提示，跟 C1 的整理提示是
    同一种"按累计轮次追加 prompt 指令"模式、同一个位置串联调用，不产生
    额外的执行循环或 LLM 调用点。只对打了 `growth_advisor` 标签的 Goal
    生效（`self_check_hint_for_cycle` 内部判断），拿不到配置或任何环节
    异常都静默跳过，不影响 Goal 触发主流程。
    """
    if paths is None:
        return description
    try:
        from mini_agent.config import load_config
        cfg = getattr(load_config(), "growth_advisor", None)
        from mini_agent.evolution.growth_advisor import self_check_hint_for_cycle
        hint = self_check_hint_for_cycle(goal, cycle_no, cfg=cfg)
        if not hint:
            return description
        parts = [description] if description and description.strip() else []
        parts.append(hint)
        return "\n\n".join(parts)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.goal_cron_bridge._append_growth_self_check_hint')
        return description


def _append_growth_pursuit_style_hint(paths, goal: "GoalNode", description: str) -> str:
    """[growth_advisor_ideal_advisor_gap_and_roadmap_plan.md 方向 6]
    每一轮都追加一次调研风格提示（不像 C1/方向 5 那样按轮次取模触发——
    风格是这个方向的持续属性，不是某一轮才需要的提醒）。只对打了
    `growth_advisor` 标签、且已经在 `auto_pursue_candidate()` 落地时被
    分类过（`goal.growth_pursuit_style` 非空）的 Goal 生效
    （`pursuit_style_hint` 内部判断），拿不到配置或任何环节异常都静默
    跳过，不影响 Goal 触发主流程。
    """
    if paths is None:
        return description
    try:
        from mini_agent.config import load_config
        cfg = getattr(load_config(), "growth_advisor", None)
        from mini_agent.evolution.growth_advisor import pursuit_style_hint
        hint = pursuit_style_hint(goal, cfg=cfg)
        if not hint:
            return description
        parts = [description] if description and description.strip() else []
        parts.append(hint)
        return "\n\n".join(parts)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.goal_cron_bridge._append_growth_pursuit_style_hint')
        return description


def _maybe_reclassify_growth_pursuit_style(paths, goal_backlog, goal: "GoalNode", cycle_no: int) -> None:
    """[growth_advisor_ideal_advisor_gap_and_roadmap_plan.md 方向 6
    动态修正] 累计满 `pursuit_style_reclassify_every_n_cycles` 轮时，
    用该方向最近几轮实际产出的内容重新判定一次调研风格，可能改写
    `goal.growth_pursuit_style`（不同于其它 `_append_growth_*` 系列
    函数只追加提示文字，这里是一次状态更新）。调用点在
    `_append_growth_pursuit_style_hint()` 之前，保证同一轮里如果
    风格被修正了，紧接着追加的风格提示用的是修正后的新值。拿不到
    配置、`llm_helper` 或任何环节异常都静默跳过，不影响 Goal 触发
    主流程；这一步本身不产生 LLM 调用（规则式路径），只有开启
    `pursuit_style_llm_enabled` 且 `llm_helper_provider` 可用时才会。
    """
    if paths is None:
        return
    try:
        from mini_agent.config import load_config
        cfg = getattr(load_config(), "growth_advisor", None)
        from mini_agent.evolution.growth_advisor import maybe_reclassify_pursuit_style
        maybe_reclassify_pursuit_style(paths, goal_backlog, goal, cycle_no, cfg=cfg, llm_helper=None)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.goal_cron_bridge._maybe_reclassify_growth_pursuit_style')


def _soft_check_execution_spec(paths, goal_backlog, goal, spec, ges_cfg) -> str:
    """核对上一轮（若存在）的 manifest 是否满足规范要求的 file_check 类
    deliverables 和 handoff_fields。不做语义判断，只做字符串匹配。

    匹配不上：①返回一句拼进下一轮 prompt 的软提示；②连续
    `soft_check_alert_after_cycles` 轮都没匹配上时，在 GoalNode 上追加一条
    系统备注（`goal_backlog.append_progress_note` 是覆盖式写入，这里改用
    直接更新 progress_notes 追加，避免覆盖 agent 自己的进展记录——见下方
    实现，用简单字符串拼接而不是调用 append_progress_note）。

    返回空字符串表示"本轮没有可核对的上一轮数据，或全部匹配上"。
    """
    from mini_agent.evolution import output_workspace
    from mini_agent.perception import goal_execution_spec as ges

    base_dir = output_workspace.goal_output_base_dir(paths, goal.id)
    prev_manifest = output_workspace.read_latest_manifest(base_dir)
    if not prev_manifest:
        return ""

    result = ges.soft_check_manifest(spec, prev_manifest)
    if result["ok"]:
        if spec.soft_check_miss_streak or spec.soft_check_alerted:
            spec.soft_check_miss_streak = 0
            spec.soft_check_alerted = False
            ges.save_spec(paths, goal.id, spec)
        return ""

    spec.soft_check_miss_streak += 1
    alert_after = int(getattr(ges_cfg, "soft_check_alert_after_cycles", 3) or 3) if ges_cfg else 3

    missing_bits = []
    if result["missing_deliverables"]:
        missing_bits.append("产出物：" + "、".join(result["missing_deliverables"]))
    if result["missing_handoff_keys"]:
        missing_bits.append("handoff 字段：" + "、".join(result["missing_handoff_keys"]))
    missing_text = "；".join(missing_bits)

    if spec.soft_check_miss_streak >= alert_after and not spec.soft_check_alerted:
        spec.soft_check_alerted = True
        try:
            note = (
                f"⚠️ 建议复查执行规范：连续 {spec.soft_check_miss_streak} 轮未见规范要求的"
                f"产出/字段（{missing_text}），规范可能已不再贴合实际执行情况。"
            )
            current = goal.progress_notes or ""
            merged = (current + "\n" + note).strip() if current else note
            goal_backlog.update_fields(goal.id, progress_notes=merged)
        except Exception:
            pass

    ges.save_spec(paths, goal.id, spec)
    return f"上一轮执行规范要求的以下内容未见产出，请注意补上：{missing_text}"


# ── 绑定/解绑（用户操作入口） ───────────────────────────────────────────────────

def make_goal_recurring(
    goal_backlog: "GoalBacklog",
    cron_scheduler: "CronScheduler",
    goal_id: str,
    schedule: str,
    task_template: Optional[str] = None,
) -> "CronJob":
    """把一个已存在的 Goal 声明为周期性。

    - Goal 不存在或不是 level="goal" 的节点 → 抛 ValueError（调用方通常是
      CLI/REST，交由上层转成用户可读的错误提示）。
      注：为什么这里选择抛异常而不是返回 None——`add_job()` 本身没有"失败"
      语义（总是成功建 job），如果本函数也吞掉错误静默返回 None，用户会看到
      "命令执行了但什么都没发生"，比报错更难排查。
    - 已经绑定过 → 复用旧 job（更新 schedule/task_template），不会重复创建
      多个 job 绑在同一个 Goal 上（对应 plan 里"一对一绑定"的约束）。
    """
    goal_backlog.load()
    goal = goal_backlog.get(goal_id)
    if goal is None or not goal.is_goal:
        raise ValueError(f"Goal 不存在或不是有效的 goal 节点：{goal_id}")

    template = task_template or goal.description or goal.title

    if goal.recurring and goal.recurrence_cron_job_id:
        existing = cron_scheduler.get(goal.recurrence_cron_job_id)
        if existing is not None:
            existing.schedule = schedule
            existing.task_template = template
            existing.enabled = True
            from mini_agent.evolution.cron_scheduler import compute_next_run
            existing.next_run_at = compute_next_run(schedule, 0.0)
            cron_scheduler.save()
            return existing

    job = cron_scheduler.add_job(
        name=f"goal-cycle:{goal.title[:24]}",
        schedule=schedule,
        task_template=template,
        description=f"周期性推进 Goal {goal.id}：{goal.title}",
        tags=["goal_cycle"],
        enabled=True,
        goal_id=goal.id,
        run_mode="goal_cycle",
    )
    goal_backlog.set_recurrence(goal.id, recurring=True, cron_job_id=job.id)
    return job


def stop_goal_recurrence(
    goal_backlog: "GoalBacklog",
    cron_scheduler: "CronScheduler",
    goal_id: str,
) -> bool:
    """停止周期性推进：disable 绑定的 cron job，goal.recurring 置回 False。
    不删除 Goal，也不删除 cron job（用户随时可以再次 make_goal_recurring 复用
    同一个 job，或手动 /cron remove 彻底清掉）。
    """
    goal_backlog.load()
    goal = goal_backlog.get(goal_id)
    if goal is None or not goal.is_goal:
        return False

    job_id = goal.recurrence_cron_job_id
    if job_id:
        cron_scheduler.disable(job_id)

    return goal_backlog.set_recurrence(goal.id, recurring=False, cron_job_id=None)


# ── 完成计数回收（Track C） ────────────────────────────────────────────────────

def reap_finished_cycles(goal_backlog: "GoalBacklog", *, llm_helper_provider=None) -> int:
    """扫描所有 recurring=True 的 Goal，把本轮新出现的终态子 Objective 计入
    cycle_count/progress_notes。由 AutonomousLoop 被动 tick 周期性调用
    （只读遍历 + 命中才写，开销可控，不需要单独起线程/订阅机制）。

    `llm_helper_provider`：[growth_advisor_autonomy_deepening_plan_v2.md
    方向 1] 可选的惰性 `Callable[[], Any]`，每次命中 B1/B2 检查点时才
    取一次当前 `llm_helper`（跟 `tech_radar_search.py` 等 cron job 的
    `llm_helper_provider` 约定一致），供 `_check_pursuit_saturation()`
    在 `cfg.pursuit_increment_llm_review_enabled=True` 时触发一次可选的
    LLM 复核。不传（默认 `None`）时行为与改动前完全一致——`evaluate_
    cycle_increment()` 拿不到 `llm_helper` 会自动跳过复核这一步。

    返回本次新计数的子节点数量（用于日志/测试断言，正常 tick 大多数时候是 0）。
    """
    goal_backlog.load()
    reaped = 0
    for goal in goal_backlog.all_nodes():
        if not goal.is_goal or not goal.recurring:
            continue
        for child_id in goal.children_ids:
            child = goal_backlog.get(child_id)
            if child is None or not child.is_objective:
                continue
            if child.status not in _TERMINAL_STATUSES:
                continue
            if child.id in goal.reaped_cycle_child_ids:
                continue
            note = child.progress_notes or f"状态：{child.status}"
            if goal_backlog.record_cycle_completed(goal.id, child.id, note=note):
                reaped += 1
                if child.status == "failed":
                    # [goal_cron_visibility_and_intervention_improvement_plan.md
                    # Track C] 只在失败时推通知——completed/cancelled 不打扰
                    # 用户，正常跑完的周期性任务不需要每轮都推送。发送失败
                    # （比如渠道配置有问题）不影响本次计数结果，只是记录到
                    # dispatcher 自己的日志里。
                    _notify_cycle_failed(goal_backlog, goal, note)
                elif child.status == "completed":
                    # [growth_advisor_autonomy_deepening_plan.md 方向 B1/B2]
                    # 只对成长顾问自主推进的 Goal（打了 growth_advisor 标签）
                    # 做增量质量判断；一轮成功完成时顺带算一次饱和度信号，
                    # 刚跨过阈值才推一次通知（同一次饱和状态不重复打扰）。
                    # 诊断增强，任何异常都吞掉，不影响 reap 主流程的计数。
                    _check_pursuit_saturation(goal_backlog, goal, llm_helper_provider=llm_helper_provider)
                    # [方向 C2] 本轮新增摘要暂存，等下一次真正推送时打包
                    # 带出，不单独消耗推送额度。同样只是诊断/展示增强。
                    _record_pursuit_digest(goal_backlog, goal)
        # [Track D] 归档已跑过多轮、已经计过数的旧子节点，避免 goals.json
        # 随轮数无限增长。只读遍历+命中才写，跟本函数其余部分同一种开销
        # 可控的设计哲学。
        try:
            goal_backlog.archive_finished_cycle_children(goal.id)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.goal_cron_bridge.reap_finished_cycles.archive')
    return reaped


def _notify_cycle_failed(goal_backlog: "GoalBacklog", goal: "GoalNode", note: str) -> None:
    """周期性 Goal 某一轮以 failed 收尾时推一条通知。复用已有的通知网关
    （notification/dispatcher.py，见 watchlist_notification_goal_design.md），
    不新增渠道实现；kanban 渠道恒真兜底，用户至少能在看板"全局待办中心"/
    通知记录里看到。异常整体吞掉——通知是感知增强，不能反过来影响
    reap_finished_cycles() 的计数主流程。
    """
    try:
        paths = getattr(goal_backlog, "_paths", None)
        if paths is None:
            return
        from mini_agent.notification.dispatcher import NotificationDispatcher, NotificationMessage
        NotificationDispatcher(paths).dispatch(NotificationMessage(
            title=f"周期性目标「{goal.title}」第 {goal.cycle_count} 轮执行失败",
            body=(note or "")[:200],
            source="goal_cycle",
            meta={"goal_id": goal.id, "cycle": goal.cycle_count},
        ))
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.goal_cron_bridge._notify_cycle_failed')


def _check_pursuit_saturation(goal_backlog: "GoalBacklog", goal: "GoalNode", *, llm_helper_provider=None) -> None:
    """[growth_advisor_autonomy_deepening_plan.md 方向 B1/B2；
    growth_advisor_autonomy_deepening_plan_v2.md 方向 1] 一轮成功
    完成时，对成长顾问自主推进的 Goal 算一次增量质量/饱和度信号，刚
    跨过阈值就推一条"要不要降频"的通知。纯诊断增强：不判断失败、不
    自动停止/降低周期性执行（是否降频仍由用户在通知/看板里决定），
    任何异常整体吞掉，不影响 reap_finished_cycles() 的计数主流程。
    """
    try:
        paths = getattr(goal_backlog, "_paths", None)
        if paths is None:
            return
        from mini_agent.config.loader import load_config
        from mini_agent.evolution.growth_advisor import process_pursuit_cycle_completion
        cfg = getattr(load_config(), "growth_advisor", None)
        llm_helper = llm_helper_provider() if llm_helper_provider else None
        hint = process_pursuit_cycle_completion(paths, goal, llm_helper=llm_helper, cfg=cfg)
        if hint is None:
            return
        from mini_agent.notification.dispatcher import NotificationDispatcher, NotificationMessage
        NotificationDispatcher(paths).dispatch(NotificationMessage(
            title=f"「{goal.title}」最近几轮新增内容不多了",
            body=hint["message"],
            source="growth_advisor_pursuit_saturation",
            meta={"goal_id": goal.id, "streak": hint["streak"]},
        ))
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.goal_cron_bridge._check_pursuit_saturation')


def _record_pursuit_digest(goal_backlog: "GoalBacklog", goal: "GoalNode") -> None:
    """[growth_advisor_autonomy_deepening_plan.md 方向 C2] 一轮成功完成
    时，把这一轮新增的 covered_subtopics 存进待推送摘要队列。纯诊断/
    展示增强，任何异常都整体吞掉，不影响 reap_finished_cycles() 的计数
    主流程。"""
    try:
        paths = getattr(goal_backlog, "_paths", None)
        if paths is None:
            return
        from mini_agent.config import load_config
        cfg = getattr(load_config(), "growth_advisor", None)
        from mini_agent.evolution.growth_advisor import record_pursuit_cycle_digest
        record_pursuit_cycle_digest(paths, goal, cfg=cfg)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.goal_cron_bridge._record_pursuit_digest')


__all__ = [
    "register_goal_cycle_handler",
    "make_goal_recurring",
    "stop_goal_recurrence",
    "reap_finished_cycles",
]
