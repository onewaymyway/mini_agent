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
    *, llm_helper_provider=None,
) -> None:
    """把 goal_cycle 触发逻辑挂到 cron_scheduler。daemon 启动时（构建完
    GoalBacklog/CronScheduler/ObjectiveExecutor 三者之后）调用一次，见
    api/server.py::HttpServer._build_autonomous_loop()。

    `llm_helper_provider`：[goal_stuck_stats_and_llm_progress_judge_plan.md
    §2] 可选、惰性获取的 `Callable[[], Optional[LLMHelper]]`，daemon 启动
    时 agent 可能还没就绪也不影响注册本身（与 `ensure_goal_relevance_judge_job`
    等既有 P5 机制同款写法）。传 None（默认）时 execution phase 的进展趋势
    信号维持纯 difflib 判断，行为与引入本参数之前完全一致。
    """

    def _handler(job: "CronJob") -> bool:
        return _fire_goal_cycle(job, goal_backlog, objective_executor, llm_helper_provider=llm_helper_provider)

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
    *, llm_helper_provider=None,
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
        # [kanban_goal_delete_and_bulk_delete_plan.md] Goal 已经不存在了。
        # 正常情况下这个分支不该被走到——`DELETE /v1/goals/{goal_id}`（及
        # `DELETE /v1/goals` 一键删除）在硬删除 Goal 节点的同时，会扫描
        # 所有 job.goal_id 命中被删节点的 cron job 一并 remove_job()，
        # 不会留下这种"引用已删 Goal"的僵尸绑定。这里保留兜底分支，是为
        # 了防御：① 更早版本（该功能上线前）遗留下来、从未被清理过的旧
        # 绑定；② 未来出现其它硬删除路径（比如手动改 goals.json）时不
        # 至于直接抛异常——job 本身不自动删除，只是每次触发都跳过，用户
        # 可以在"⏰ Cron 任务"tab 或 `/cron remove` 手动清理。
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
    phase_info = _resolve_execution_phase(
        paths, goal, cycle_no, goal_backlog=goal_backlog, llm_helper_provider=llm_helper_provider,
    )
    # [goal_output_directory_and_execution_phase_redesign_plan.md §4 Stage 4]
    # 纯粹的旁路副作用（可能生成一份未确认的 spec 草稿 + 发通知），不影响
    # description 拼接，放在 phase_info 算出来之后、其它 prompt 拼接之前
    # 都可以，这里紧跟在 phase 判定之后，逻辑上离得最近。
    _maybe_auto_generate_converge_spec_draft(paths, goal_backlog, goal, cycle_no, phase_info)
    description = _append_output_workspace_context(paths, goal, cycle_no, description, phase_info=phase_info)
    description = _append_execution_phase_context(paths, goal, description, phase_info)
    # [goal_cron_task_optimization_holistic_plan.md 方向 C] "下一轮降级执行"
    # 与 execution phase 提示是叠加关系，不互斥——不管当前判定处于哪个阶段，
    # 都在描述末尾追加一段"从简执行"约束，并在消费后立即清零标记，只影响
    # 这一次触发。放在 execution phase 片段之后，避免被阶段提示的措辞盖过。
    if goal.next_cycle_lightweight:
        goal_backlog.update_fields(goal.id, next_cycle_lightweight=False)
        goal_backlog.append_progress_note(goal.id, f"第 {cycle_no} 轮由用户标记为降级执行（从简）")
        description = compose_context(
            description,
            "【本轮降级执行】用户临时要求这一轮从简处理：只做最小限度的同步/"
            "巡检，不要引入新方案、不要做结构性变更、不要扩大任务范围，"
            "有明显异常再如实汇报，其余按现状简要确认即可。",
        )
    description = _append_legacy_migration_directive(paths, goal_backlog, goal, cycle_no, description)
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


def _resolve_execution_phase(paths, goal: "GoalNode", cycle_no: int,
                              *, goal_backlog=None, llm_helper_provider=None) -> dict:
    """[goal_execution_phase_improvement_plan.md §4 / Stage D /
    goal_stuck_stats_and_llm_progress_judge_plan.md §2 /
    goal_output_directory_and_execution_phase_redesign_plan.md Stage 3]
    读取/推进这个 Goal 的 ExecutionPhaseState，计算本轮 effective_mode，
    并落盘/发健康告警——这部分是纯粹的"判定 + 副作用"，从原来的
    `_append_execution_phase_context()` 拆出来，是因为 Stage 3 需要在
    构造 prompt 的产出目录约束段落时（`_append_output_workspace_context`）
    就知道 effective_mode（explore/converge 只能写 scratch/，running 只能
    写 output/），而不是等到阶段文案段落才知道。

    返回 dict：{"effective_mode": str|None, "spec_confirmed": bool,
    "spec": GoalExecutionSpec|None, "state": ExecutionPhaseState|None}。
    任何环节异常都吞掉，返回 effective_mode=None（调用方据此按最保守的
    "running"（只写 output/）兜底，不影响 Goal 触发主流程）。
    """
    result = {"effective_mode": None, "spec_confirmed": False, "spec": None, "state": None}
    if paths is None:
        return result
    try:
        from mini_agent.perception import execution_phase as ep
        from mini_agent.perception import goal_execution_spec as ges

        state = ep.load_phase(paths, goal.id)

        spec_confirmed = bool(getattr(goal, "execution_spec_confirmed", False))
        spec_recently_revised = not spec_confirmed
        miss_streak = 0
        spec = None
        if spec_confirmed:
            spec = ges.load_spec(paths, goal.id)
            if spec is not None:
                miss_streak = int(getattr(spec, "soft_check_miss_streak", 0))
                confirmed_at = getattr(spec, "confirmed_at", None)
                if confirmed_at is not None and (time.time() - confirmed_at) < 86400:
                    spec_recently_revised = True

        llm_helper = None
        if llm_helper_provider is not None:
            try:
                from mini_agent.config import load_config
                if getattr(load_config().execution_phase, "progress_trend_llm_enabled", False):
                    helper = llm_helper_provider()
                    if helper is not None:
                        llm_helper = lambda prompt, _h=helper: _h.ask(prompt)
            except Exception:
                llm_helper = None

        # [Stage 8c] new_topic_discovery=="intrinsic" 的 Goal（wiki/股票报告
        # 等累积/双轨型）内容层天然每轮都不同，"跨轮进展文本雷同"信号对它们
        # 没有意义（见 resolve_effective_mode 文档约定），此时直接不计算/
        # 不传该信号，而不是计算出来又被下游忽略——省一次可能的 LLM 调用。
        spec_new_topic_discovery = getattr(spec, "new_topic_discovery", "none") if spec is not None else "none"
        if spec_new_topic_discovery == "intrinsic":
            progress_trend_stuck = None
        else:
            progress_trend_stuck = ep.compute_progress_trend_signal(goal_backlog, goal.id, llm_helper=llm_helper)

        # [Stage 8c] 组装 routine_texts：取 spec/history/ 里最近几个历史版本
        # （已按时间倒序，取最新的几条后反转为正序）的 execution_routine +
        # 当前版本，各自序列化成一段文本，交给
        # compute_routine_stability_signal() 判断"规范层标准动作是否已
        # 收敛"。spec 为空、或 execution_routine 从未使用过（全部序列化后
        # 为空串）时，routine_texts 不足两条，信号自然返回 None（关闭），
        # 不影响任何未采用 Stage 8 新字段的存量 Goal。
        routine_stability = None
        if spec is not None:
            try:
                history = ges.list_spec_history(paths, goal.id)[:3]
                routine_texts = [
                    ges.serialize_routine_steps(h.get("execution_routine") or [])
                    for h in reversed(history)
                ]
                current_routine_text = ges.serialize_routine_steps(spec.execution_routine)
                if current_routine_text:
                    routine_texts.append(current_routine_text)
                routine_texts = [t for t in routine_texts if t]
                routine_stability = ep.compute_routine_stability_signal(routine_texts, llm_helper=llm_helper)
            except Exception:
                routine_stability = None

        effective_mode, state = ep.resolve_effective_mode(
            state,
            cycle_no=cycle_no,
            spec_confirmed=spec_confirmed,
            spec_recently_revised=spec_recently_revised,
            miss_streak=miss_streak,
            progress_trend_stuck=progress_trend_stuck,
            routine_stability=routine_stability,
        )
        try:
            health_reason = ep.check_phase_health(state, effective_mode)
            if health_reason:
                kind = "stuck_explore" if effective_mode == "explore" else "phase_flapping"
                state.last_health_alert_kind = kind
                state.last_health_alert_at = time.time()
                _notify_phase_health_issue(paths, goal, health_reason)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.goal_cron_bridge._resolve_execution_phase.health_check')

        ep.save_phase(paths, state)

        result["effective_mode"] = effective_mode
        result["spec_confirmed"] = spec_confirmed
        result["spec"] = spec
        result["state"] = state
        # [Stage 4] 把已经构造好的 llm_helper 闭包一并带出去，供
        # `_maybe_auto_generate_converge_spec_draft()` 复用，避免同一轮内
        # 重复读配置/重新构造一次 helper。
        result["llm_helper"] = llm_helper
        return result
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.goal_cron_bridge._resolve_execution_phase')
        return result


def _maybe_auto_generate_converge_spec_draft(
    paths, goal_backlog: "GoalBacklog", goal: "GoalNode", cycle_no: int, phase_info: dict,
) -> None:
    """[goal_output_directory_and_execution_phase_redesign_plan.md §4 Stage 4]
    converge 阶段收尾时，如果最近两轮的"方案对比说明"结论一致，自动生成一份
    GoalExecutionSpec 草稿——不自动确认，仍需用户手动 `/agent goals spec
    confirm`（或看板对应操作）确认，只是降低"卡在 converge 没人管、忘记
    手动跑一次 `spec generate`"的概率（方案 §4 提到的具体反馈）。

    "结论一致"复用 `execution_phase.compute_progress_trend_signal()` 已有的
    difflib/LLM 双模式判断基础设施（`progress_trend_stuck` 信号本身语义是
    "最近几轮进展文本高度相似"，这里只是换一个更小的 window=2 并把"相似"
    解读为"收敛结论一致"而不是"卡住"——两种解读在这个函数的上下文里其实
    是同一件事：文本层面高度雷同）。`llm_helper` 直接复用
    `_resolve_execution_phase()` 已经构造好的那份（挂在 phase_info 里），
    避免同一轮内重复读配置。

    只在这个 Goal 目前完全没有任何 spec（草稿或已确认）时触发一次：一旦
    生成过草稿，`ges.load_spec()` 就会返回非 None，之后自动不再重复生成/
    覆盖，避免打断用户可能正在手动编辑的草稿。任何异常整体吞掉，不影响
    Goal 触发主流程。
    """
    try:
        if paths is None or phase_info.get("effective_mode") != "converge":
            return
        if bool(getattr(goal, "execution_spec_confirmed", False)):
            return

        from mini_agent.perception import goal_execution_spec as ges
        if ges.load_spec(paths, goal.id) is not None:
            return  # 已经有草稿/规范了（不管是否确认），不重复自动生成

        from mini_agent.perception import execution_phase as ep
        consensus = ep.compute_progress_trend_signal(
            goal_backlog, goal.id, window=2, llm_helper=phase_info.get("llm_helper"),
        )
        if consensus is not True:
            return

        from mini_agent.config import load_config
        builder = ges.GoalExecutionSpecBuilder(load_config())
        spec = builder.build_draft(goal.id, goal.title, goal.description)
        ges.save_spec(paths, goal.id, spec)

        try:
            goal_backlog.append_progress_note(
                goal.id,
                f"第 {cycle_no} 轮（converge）检测到最近两轮方案对比说明结论一致，"
                "已自动生成执行规范草稿（尚未确认），请查看 spec/SPEC.md 或用 "
                "`/agent goals spec show` 核对后，用 `/agent goals spec confirm` 确认。",
            )
        except Exception:
            pass

        try:
            from mini_agent.notification.dispatcher import NotificationDispatcher, NotificationMessage
            NotificationDispatcher(paths).dispatch(NotificationMessage(
                title=f"周期性目标「{goal.title}」已自动生成执行规范草稿",
                body="最近两轮收敛期的方案对比说明结论一致，系统已自动生成一份"
                     "执行规范草稿（未确认），请查看后决定是否确认或修订。",
                source="goal_cycle_converge_spec_draft",
                meta={"goal_id": goal.id, "cycle_no": cycle_no},
            ))
        except Exception:
            pass
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(
            _mini_agent_exc,
            where='mini_agent.evolution.goal_cron_bridge._maybe_auto_generate_converge_spec_draft',
        )


def _append_output_workspace_context(paths, goal: "GoalNode", cycle_no: int, description: str,
                                      *, phase_info: Optional[dict] = None) -> str:
    """[goal_output_directory_and_execution_phase_redesign_plan.md Stage 3]
    recurring Goal 一侧改用新的四目录模型（output/notes/spec/scratch，方案
    §1~§6），取代原来"每轮一个新目录 cycle_NNNN/"的旧模型：
      - 幂等确保 output/ 固定骨架存在（README.md/_misc/_archive/scripts/…）
      - 把最近几轮 notes/cycle_NNNN.md 原文拼进 prompt（取代原来只取最后
        一轮、内容格式相对简陋的 manifest 机制）
      - 依据 effective_mode 拼一段"本轮可以写哪里、不可以写哪里"的约束：
          explore/converge → 只能写 scratch/（脚本草稿走
            output/scripts/_experiments/），converge 额外要求"搬运+清理"
          running           → 只能写 output/，并附上已确认执行规范
            spec/SPEC.md 全文（若存在）
          tidy              → 附上代码扫描算出的"问题清单"（方案 §7.1），
            不要求 agent 自己从零判断"哪里乱了"
      - 提醒本轮结束前写一份 notes/cycle_NNNN.md 总结笔记

    phase_info：由 `_fire_goal_cycle` 调用 `_resolve_execution_phase()` 后
    传入，包含 effective_mode/spec_confirmed/spec。为 None 或
    effective_mode 取不到值时，按最保守的 "running"（只允许写 output/）
    处理——这是有意为之的保守兜底：不确定阶段时，"限制只能写正式产出目录"
    比"放开随便写"更安全。

    paths 为 None（拿不到 AgentPaths，理论上不应发生，防御性处理）或任何
    环节异常时，静默跳过——不影响 Goal 触发主流程。
    """
    if paths is None:
        return description
    try:
        from mini_agent.evolution import output_workspace as ow
        goal_id = goal.id
        # [goal_user_output_dir_plan.md] 用户明确设置过 user_output_dir 时，
        # 正式产出目录（output/）解析到用户指定的路径；notes/spec/scratch
        # 三个内部目录永远走默认位置（不受这个字段影响，见字段注释）。
        user_output_dir = getattr(goal, "user_output_dir", None)
        out_dir = ow.goal_output_dir(paths, goal_id, user_output_dir=user_output_dir)
        notes_dir = ow.goal_notes_dir(paths, goal_id)
        scratch_dir = ow.goal_scratch_dir(paths, goal_id)

        # [迁移设计] 判断这是不是这个 Goal 第一次切到新的固定四目录模型
        # （必须在 ensure_output_skeleton() 之前判断——那个函数本身是幂等
        # 创建骨架，调用完 out_dir 就一定存在了）。如果存在旧模型遗留的
        # cycle_NNNN/ 目录，写一份迁移摘要进 notes/cycle_0000.md，让新
        # 模型下的执行上下文不会凭空丢失旧模型积累的历史；任何一步失败都
        # 静默跳过，不影响本轮触发主流程。
        is_first_time_new_layout = not out_dir.exists()
        ow.ensure_output_skeleton(paths, goal_id, user_output_dir=user_output_dir)
        if is_first_time_new_layout:
            try:
                if ow.has_legacy_cycle_dirs(paths, goal_id):
                    migration_summary = ow.build_legacy_migration_summary(paths, goal_id)
                    if migration_summary:
                        ow.write_cycle_note(paths, goal_id, 0, migration_summary)
            except Exception:
                pass

        parts = [description] if description and description.strip() else []

        # [迁移设计] 如果用户在创建 Goal 时的 description 里手写了"产出该
        # 放哪里"之类的路径提示（旧习惯，新模型引入前很常见），提醒 agent
        # 新模型下 output/ 是唯一正式产出目录——软性提醒，不修改用户原始
        # description，不拦截执行。
        if user_output_dir:
            # [goal_user_output_dir_plan.md] 用户已经在看板上明确确认过产出
            # 目录，不再需要"软性提醒/让 agent 自己判断"——直接告知本轮的
            # 正式产出目录就是用户指定的这个路径，消除歧义。
            parts.append(
                "## 产出目录（用户指定）\n\n"
                f"本 Goal 已由用户明确指定产出目录为 `{user_output_dir}`"
                f"（解析后的绝对路径：{out_dir}），本轮的正式产出统一写入这里"
                "（跨轮共用，不再按轮次新建）。"
            )
        else:
            try:
                output_hints = ow.detect_user_specified_output_hint(description or "")
            except Exception:
                output_hints = []
            if output_hints:
                parts.append(
                    "## 关于描述里提到的自定义产出路径\n\n"
                    f"检测到 Goal 描述里提到了这些路径片段：{', '.join(output_hints)}。"
                    f"新的产出目录模型下，正式产出统一写入 {out_dir}"
                    "（跨轮共用的固定目录，不再按轮次新建）——如果这些路径本意是"
                    "output/ 内部的业务子目录（比如 'reports/weekly.md' 对应"
                    f" {out_dir}/reports/weekly.md），继续这么组织没问题；如果本意"
                    "是 output/ 之外的绝对路径，看板上有一个「产出目录」建议"
                    "（已根据这段描述自动检测），确认后 agent 会直接把该路径作为"
                    "正式产出目录，不需要再靠这条软性提醒自行判断。"
                )

        recent_notes = ow.read_recent_notes(paths, goal_id, limit=3)
        if recent_notes:
            notes_text = "\n\n".join(
                f"--- 第 {n['cycle_no']} 轮总结笔记 ---\n{n['content']}" for n in recent_notes
            )
            parts.append(notes_text)

        mode = (phase_info or {}).get("effective_mode") or "running"
        note_reminder = f"完成后请在 {notes_dir} 下写一份 cycle_{cycle_no:04d}.md 总结笔记"

        if mode in ("explore", "converge"):
            experiments_dir = out_dir / "scripts" / "_experiments"
            lines = [
                "## 产出目录约束（探索/收敛期）",
                "",
                f"- 正式产出目录：{out_dir}（本轮**不允许**直接写入）",
                f"- 本轮请把所有产出写入试验目录：{scratch_dir}",
                f"  （脚本类草稿请统一放在 {experiments_dir} 下，而不是 scratch/ 本身）",
                f"- {note_reminder}",
            ]
            if mode == "converge":
                spec_for_converge = (phase_info or {}).get("spec")
                lines += [
                    "",
                    "## 收敛期额外要求",
                    "",
                    "- 从 scratch/（含 output/scripts/_experiments/ 中选定的脚本）里"
                    "挑选一个方案，**搬进** output/（脚本搬进 output/scripts/ 根目录，"
                    "遵循正式命名约定：动词开头的 snake_case，不用版本后缀）",
                    "- 未被选中的方案连同其数据一起挪进 output/_archive/，并注明淘汰原因",
                    "- 在本轮总结笔记里写清楚搬运理由 + 淘汰了哪些方案"
                    "（这份'方案对比说明'是本轮的重点产出之一，请单独成段，方便后续核对）",
                ]
                # [Stage 8d] hardening_target/sub_exploration 接入 converge
                # 搬迁行为——之前这两个字段只出现在 render_prompt_block() 的
                # 常驻说明里（每轮都有，语气偏"背景信息"），本阶段在 converge
                # 收尾这个"真正决定搬去哪里"的关键节点上，额外给一条更明确的
                # 指令，避免被淹没在常驻说明里。
                hardening_target = getattr(spec_for_converge, "hardening_target", "") if spec_for_converge else ""
                if hardening_target:
                    lines += [
                        "",
                        f"- ⚠️ 本 Goal 声明了外部固化目标 hardening_target："
                        f"{hardening_target}——验证有效的方案，**搬迁的最终落点是"
                        f"这里，而不是（或不仅是）本 Goal 私有的 output/scripts/**。"
                        "若本轮验证的内容确实达到可固化标准，请直接改动"
                        f"{hardening_target}（更新其自身的 README/CHANGELOG），"
                        "output/scripts/ 内可以只保留驱动/验证脚本本身。",
                    ]
                sub_exploration = getattr(spec_for_converge, "sub_exploration", "") if spec_for_converge else ""
                if sub_exploration:
                    lines += [
                        "",
                        f"- ℹ️ 本 Goal 声明了独立生命周期的子探索：{sub_exploration}——"
                        "这部分内容请落在 output/scripts/_experiments/ 或专门的"
                        " output/_sources/ 下，其探索/收敛节奏与主轨独立，"
                        "**不要**因为子探索还没收敛就认为主轨也没收敛"
                        "（子探索不参与本 Goal 的 spec_phase 判定）。",
                    ]
            parts.append("\n".join(lines))
        elif mode == "tidy":
            spec_for_tidy_checklist = (phase_info or {}).get("spec")
            checklist = _build_tidy_problem_checklist(
                paths, goal_id, spec=spec_for_tidy_checklist, user_output_dir=user_output_dir,
            )
            lines = [
                "## 产出目录整理任务（代码预检结果）",
                "",
                f"正式产出目录：{out_dir}",
                "",
                checklist,
                "",
                f"{note_reminder}（整理报告本身），并按上述问题项逐一处理"
                "（能确定性判断的问题已经列在上面，不需要从零判断'哪里乱了'，"
                "专注于'怎么处理这些具体问题'即可）。",
            ]
            parts.append("\n".join(lines))
        else:  # running（含未知/None 兜底）
            lines = [
                "## 产出目录约束（稳定期）",
                "",
                f"- 本轮只允许写入正式产出目录：{out_dir}（含 output/scripts/ 根目录的"
                "增量修改），不允许新增 scratch/ 或 output/scripts/_experiments/ 内容——"
                "如果确实需要先探索验证，这本身就是一个信号，说明可能没有真正收敛，"
                "请在总结笔记里如实指出，必要时建议退回收敛期。",
                f"- {note_reminder}，记录本轮的增量变化。",
            ]
            spec = (phase_info or {}).get("spec")
            spec_confirmed = bool((phase_info or {}).get("spec_confirmed"))
            if spec_confirmed:
                spec_md_text = _read_spec_md_full_text(paths, goal_id, fallback_spec=spec)
                if spec_md_text:
                    lines += ["", "## 已确认的执行规范（spec/SPEC.md 全文）", "", spec_md_text]
            parts.append("\n".join(lines))

        return "\n\n".join(parts)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.goal_cron_bridge._append_output_workspace_context')
        return description


def _append_legacy_migration_directive(paths, goal_backlog: "GoalBacklog", goal: "GoalNode",
                                        cycle_no: int, description: str) -> str:
    """[goal_output_directory_and_execution_phase_redesign_plan.md Stage 9]
    消费 `goal.legacy_migration_requested` 一次性标记（与 skip_next_cycle/
    next_cycle_lightweight 同一模式）：命中时清零标记，追加一段迁移指令
    （见 `output_workspace.build_legacy_migration_directive()`），不影响本轮
    阶段判定，与执行阶段的 explore/converge/running/tidy 提示是叠加关系。

    没有任何未处理的 legacy 目录时（指令函数返回 None），仍清零标记（避免
    用户请求过一次后，因为目录已经不在了而在下次触发时又意外重新出现这段
    指令的假象），但只留一条 progress_note 说明"未检测到需要迁移的内容"，
    不追加空指令到 description。
    """
    if not getattr(goal, "legacy_migration_requested", False):
        return description
    goal_backlog.update_fields(goal.id, legacy_migration_requested=False)
    from mini_agent.evolution import output_workspace as ow
    try:
        directive = ow.build_legacy_migration_directive(paths, goal.id, user_output_dir=getattr(goal, "user_output_dir", None))
    except Exception:
        directive = None
    if not directive:
        goal_backlog.append_progress_note(
            goal.id, f"第 {cycle_no} 轮：用户请求的历史数据迁移未执行——未检测到需要迁移的旧 cycle 目录。"
        )
        return description
    goal_backlog.append_progress_note(goal.id, f"第 {cycle_no} 轮附加历史数据迁移任务（legacy cycle 目录搬迁）。")
    from mini_agent.perception.goal_backlog import compose_context
    return compose_context(description, directive)


def _read_spec_md_full_text(paths, goal_id: str, *, fallback_spec=None) -> str:
    """读取 `spec/SPEC.md` 落盘全文（方案 §4，由
    `goal_execution_spec.save_spec()` 写入）。文件不存在时（例如快照写入
    曾经失败过，或历史数据尚未触发过一次 save_spec）退回用内存里已加载的
    `fallback_spec.render_summary_for_user()` 现算一份，尽量不让"稳定期
    附上规范全文"这件事因为快照文件缺失而完全失效。两者都拿不到时返回
    空字符串。
    """
    try:
        from mini_agent.evolution.output_workspace import goal_spec_dir
        spec_md = goal_spec_dir(paths, goal_id) / "SPEC.md"
        if spec_md.exists():
            text = spec_md.read_text(encoding="utf-8").strip()
            if text:
                return text
    except Exception:
        pass
    if fallback_spec is not None:
        try:
            return fallback_spec.render_summary_for_user().strip()
        except Exception:
            pass
    return ""


def _build_tidy_problem_checklist(paths, goal_id: str, *, spec=None, user_output_dir: Optional[str] = None) -> str:
    """[方案 §7.1] tidy 阶段核查清单里能确定性代码判断的那部分——第一版
    （Stage 3）覆盖第 1/2/5/6/8 条；[Stage 5] 补齐第 7 条（requirements.txt
    与 scripts/*.py 实际 import 是否一致）和第 9 条（_experiments/ 里是否
    存在应转正但一直没转正的脚本）。第 3/4 条（`retention`/`naming_pattern`
    规则核对）仍需结合 `GoalExecutionSpec` 的业务子目录声明才能判断，留待
    后续阶段。

    `spec`：[Stage 8d] 可选传入已确认的 `GoalExecutionSpec`，仅用于让
    `_experiments/` 转正提示区分"该转正到 output/scripts/ 根目录"还是
    "该固化到 spec.hardening_target 声明的外部路径"——不传入（`None`）
    时行为与 Stage 5 完全一致，不影响任何未使用 Stage 8 新字段的存量 Goal。
    [Stage 8f] 同时按 `spec.output_mode` 应用三种 output_mode 各自的 tidy
    默认模板差异：`capability_hardening` 用更低的转正提及阈值（见
    `default_promotion_mention_threshold()`）；`accretive` 额外跑一遍
    `detect_accretive_duplicate_candidates()`，检查 output/ 顶层是否有
    疑似未去重的重复累积文件；`converging`/默认（`spec is None`）行为与
    Stage 5 完全一致。

    返回给 agent 看的 Markdown 文本，全部是"代码已经算出来的问题"，
    agent 不需要自己判断"这里乱不乱"，只需要决定"怎么处理"。没有发现
    任何问题时返回一句确认性文字，而不是空字符串（避免 agent 误以为
    没有跑扫描）。
    """
    try:
        from mini_agent.evolution import output_workspace as ow
        stats = ow.scan_output_structure(paths, goal_id, user_output_dir=user_output_dir)
        scratch_empty = ow.scratch_is_empty(paths, goal_id)
    except Exception:
        return "（本轮目录扫描失败，请自行检查 output/ 目录结构）"

    # [Stage 5] requirements.txt 一致性核查 / _experiments/ 转正检测——两者
    # 各自独立 try/except，任一失败不影响其余检查项正常展示（比上面几项
    # 更依赖文本解析，出错概率相对更高，值得单独兜底）。
    try:
        missing_requirements = ow.check_scripts_requirements_consistency(paths, goal_id, user_output_dir=user_output_dir)
    except Exception:
        missing_requirements = []
    try:
        output_mode = getattr(spec, "output_mode", "converging") if spec is not None else "converging"
        min_mentions = ow.default_promotion_mention_threshold(output_mode)
        promotion_candidates = ow.detect_experiments_promotion_candidates(
            paths, goal_id, min_mentions=min_mentions, user_output_dir=user_output_dir,
        )
    except Exception:
        promotion_candidates = []
    # [Stage 8f] accretive 型 Goal 的 tidy 默认模板重点不同——检查
    # output/ 顶层是否存在疑似未去重的重复累积文件，而不是（只）看
    # _experiments/ 转正。仅当 spec.output_mode == "accretive" 时才跑，
    # 避免给不相关的 Goal 徒增一次目录扫描；spec 为 None（未确认）时
    # 同样跳过——沿用 converging 默认模板不做该项检查。
    duplicate_candidates: dict = {}
    if spec is not None and getattr(spec, "output_mode", "converging") == "accretive":
        try:
            duplicate_candidates = ow.detect_accretive_duplicate_candidates(paths, goal_id, user_output_dir=user_output_dir)
        except Exception:
            duplicate_candidates = {}

    lines: list[str] = []
    if stats["root_unexpected"]:
        lines.append("- ⚠️ output/ 根目录下存在未分类文件（应归入某个业务子目录或 "
                      "_misc/）：" + "、".join(stats["root_unexpected"]))
    if stats["misc_count"]:
        lines.append(f"- ⚠️ _misc/ 有 {stats['misc_count']} 个文件未清空："
                      + "、".join(stats["misc_files"][:20])
                      + "（请归类或挪进 _archive/ 并注明原因）")
    scripts = stats["scripts"]
    if scripts["unexpected_root_files"]:
        lines.append("- ⚠️ scripts/ 根目录下存在疑似临时脚本，应挪进 "
                      "scripts/_experiments/：" + "、".join(scripts["unexpected_root_files"]))
    if scripts["run_logs_count"] > 10:
        lines.append(f"- ⚠️ scripts/_run_logs/ 已有 {scripts['run_logs_count']} 个日志"
                      "（建议只保留最近 10 次，其余清理）")
    if not scratch_empty:
        lines.append("- ⚠️ scratch/ 尚未清空——进入 running 前必须清空或仅保留明确标注"
                      "'仅存档不再维护'的内容")
    # [Stage 9] 提醒是否仍有未处理的旧模型 legacy cycle_NNNN/ 目录——纯提醒，
    # 不强制在 tidy 阶段处理（迁移是独立的、用户显式请求的一次性任务，见
    # `/agent goals migrate-legacy`），只是让 agent/用户知道"还有历史包袱"。
    try:
        pending_legacy = ow.list_legacy_cycle_dirs(paths, goal_id, include_migrated=False)
    except Exception:
        pending_legacy = []
    if pending_legacy:
        lines.append(
            f"- ℹ️ 检测到 {len(pending_legacy)} 个旧模型遗留的 cycle_NNNN/ 目录尚未迁移"
            "（不影响本轮 tidy 判定，如需处理可用 /agent goals migrate-legacy 触发专门的迁移轮）"
        )
    if stats["archive_entries"] > 200:
        lines.append(f"- ℹ️ _archive/ 已有 {stats['archive_entries']} 项归档，"
                      "可评估是否有过老内容可以彻底删除（默认不自动删，仅提示）")
    if missing_requirements:
        lines.append("- ⚠️ scripts/*.py 里出现但 requirements.txt 未见记录的第三方包"
                      "（启发式核查，可能有误判，请人工核实）：" + "、".join(missing_requirements))
    if promotion_candidates:
        hardening_target = getattr(spec, "hardening_target", "") if spec is not None else ""
        if hardening_target:
            lines.append(
                "- ℹ️ scripts/_experiments/ 下这些脚本被最近几轮总结笔记多次提及，"
                f"但尚未转正——本 Goal 声明了外部固化目标 hardening_target："
                f"{hardening_target}，验证有效的脚本请优先评估是否应直接固化到"
                "那里（而非仅转正到本 Goal 的 scripts/ 根目录）：" + "、".join(promotion_candidates)
            )
        else:
            lines.append("- ℹ️ scripts/_experiments/ 下这些脚本被最近几轮总结笔记多次提及，"
                          "但尚未转正到 scripts/ 根目录，评估是否需要按 §6.1 命名约定搬迁转正："
                          + "、".join(promotion_candidates))
    if duplicate_candidates:
        parts = [f"{base}（{ '、'.join(names) }）" for base, names in sorted(duplicate_candidates.items())]
        lines.append(
            "- ℹ️ output/ 顶层这些文件疑似同一份内容的重复累积、未做到 execution_routine "
            "里约定的'去重合并'，请核实是否应该合并为一份或把旧版本挪进 _archive/："
            + "；".join(parts)
        )

    if not lines:
        return "本轮代码扫描未发现确定性问题（根目录整洁、_misc/ 为空、scratch/ 已清空）。"
    return "以下是本轮代码扫描出的问题清单：\n\n" + "\n".join(lines)


def _append_execution_phase_context(paths, goal: "GoalNode", description: str, phase_info: dict) -> str:
    """[goal_execution_phase_improvement_plan.md §4 / Stage B / 方案 Stage 3]
    把 `_resolve_execution_phase()` 算出的 effective_mode 对应的阶段文案
    片段（explore/converge/running/tidy）拼进本轮子 Objective description。

    与产出目录约束（`_append_output_workspace_context`，负责"能写哪里、
    不能写哪里"这类结构化约束）分工不同，这里只负责阶段本身的文字说明和
    两个既有的提示钩子（converge 未确认规范时提示生成、tidy 已确认规范时
    的核对清单）——tidy 的"代码算出的问题清单"已经移到
    `_append_output_workspace_context` 里（方案 §7.1 要求的是"目录/文件
    层面的确定性核查"，跟这里的 spec deliverables 核对清单是互补关系，
    都保留）。

    phase_info 的 effective_mode 为 None（`_resolve_execution_phase` 内部
    异常兜底）时，直接跳过阶段文案（不影响 Goal 触发主流程，产出目录约束
    那边已经按 running 做了保守兜底）。
    """
    if paths is None:
        return description
    effective_mode = phase_info.get("effective_mode")
    if effective_mode is None:
        return description
    try:
        from mini_agent.prompts import pm
        from mini_agent.perception import goal_execution_spec as ges

        key_map = {
            "explore": "EXPLORE_BLOCK",
            "converge": "CONVERGE_BLOCK",
            "running": "RUNNING_BLOCK",
            "tidy": "TIDY_BLOCK",
        }
        key = key_map.get(effective_mode)
        if key is None:
            return description
        block = pm.fragment("execution_phase", key)
        if not block:
            return description
        parts = [description] if description and description.strip() else []
        parts.append(block)

        spec_confirmed = bool(phase_info.get("spec_confirmed"))

        # [Stage B] converge 阶段：如果执行规范尚未确认，额外提示"收敛完成后
        # 建议生成/确认执行规范"。
        if effective_mode == "converge" and not spec_confirmed:
            parts.append(pm.fragment("execution_phase", "CONVERGE_SPEC_HINT_BLOCK"))

        # [Stage B] tidy 阶段：如果已有确认的执行规范，把规范里声明的
        # deliverables/sub_directories 罗列出来，作为整理时的核对清单
        # （与目录扫描出的确定性问题清单互补，spec 层面的核对仍需要读
        # spec 内容才能判断，不属于纯目录扫描的范畴）。
        if effective_mode == "tidy" and spec_confirmed:
            spec_for_tidy = phase_info.get("spec")
            if spec_for_tidy is None:
                try:
                    spec_for_tidy = ges.load_spec(paths, goal.id)
                except Exception:
                    spec_for_tidy = None
            checklist_hint = _build_tidy_checklist_hint(spec_for_tidy)
            if checklist_hint:
                parts.append(checklist_hint)

        return "\n\n".join(parts)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.goal_cron_bridge._append_execution_phase_context')
        return description
    """[goal_execution_phase_improvement_plan.md Stage B] 基于已确认的
    GoalExecutionSpec 生成一份 tidy 阶段专属核对清单文本；spec 为 None 或
    没有 deliverables/sub_directories 时返回空字符串（调用方据此跳过）。
    """
    if spec is None:
        return ""
    lines: list[str] = []
    if getattr(spec, "deliverables", None):
        lines.append("应存在的产出文件（按命名规则核对，缺失/命名不一致需在报告中指出）：")
        for d in spec.deliverables:
            pattern = f"（命名规则：{d.naming_pattern}）" if getattr(d, "naming_pattern", "") else ""
            lines.append(f"  - {d.name}{pattern}")
    if getattr(spec, "sub_directories", None):
        lines.append("预期的子目录结构（核对是否有游离在外、未归入这些目录的产出）：")
        for s in spec.sub_directories:
            purpose = f"：{s.purpose}" if getattr(s, "purpose", "") else ""
            lines.append(f"  - {s.name}{purpose}")
    if not lines:
        return ""
    return "## 整理核对清单（依据已确认的执行规范）\n\n" + "\n".join(lines)


def _build_tidy_checklist_hint(spec) -> str:
    """[goal_execution_phase_improvement_plan.md Stage B] 基于已确认的
    GoalExecutionSpec 生成一份 tidy 阶段专属核对清单文本；spec 为 None 或
    没有 deliverables/sub_directories 时返回空字符串（调用方据此跳过）。
    """
    if spec is None:
        return ""
    lines: list[str] = []
    if getattr(spec, "deliverables", None):
        lines.append("应存在的产出文件（按命名规则核对，缺失/命名不一致需在报告中指出）：")
        for d in spec.deliverables:
            pattern = f"（命名规则：{d.naming_pattern}）" if getattr(d, "naming_pattern", "") else ""
            lines.append(f"  - {d.name}{pattern}")
    if getattr(spec, "sub_directories", None):
        lines.append("预期的子目录结构（核对是否有游离在外、未归入这些目录的产出）：")
        for s in spec.sub_directories:
            purpose = f"：{s.purpose}" if getattr(s, "purpose", "") else ""
            lines.append(f"  - {s.name}{purpose}")
    if not lines:
        return ""
    return "## 整理核对清单（依据已确认的执行规范）\n\n" + "\n".join(lines)


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
        # [Track D / goal_cron_task_optimization_holistic_plan.md 方向 A]
        # 归档已跑过多轮、已经计过数的旧子节点，避免 goals.json 随轮数无限
        # 增长。只读遍历+命中才写，跟本函数其余部分同一种开销可控的设计
        # 哲学。方向 A 新增一层阶段感知的门禁：只在 execution phase 判定为
        # running/tidy（已收敛）时才归档，explore/converge 阶段的早期尝试
        # 细节可能还有参考价值，暂缓归档；paths 拿不到、阶段状态读取异常时
        # 保守地按"允许归档"处理（与本条门禁引入之前的行为一致），不因为
        # 诊断信息缺失而阻塞归档这个主功能。
        try:
            allow_archive = True
            paths = getattr(goal_backlog, "_paths", None)
            if paths is not None:
                from mini_agent.perception import execution_phase as ep
                phase_state = ep.load_phase(paths, goal.id)
                effective_mode = ep.last_known_effective_mode(phase_state)
                allow_archive = effective_mode in ("running", "tidy")
            if allow_archive:
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


def _notify_phase_health_issue(paths, goal: "GoalNode", reason: str) -> None:
    """[goal_cron_task_optimization_holistic_plan.md 方向 B] execution phase
    健康告警（长期卡在 explore / 阶段反复回退）推一条通知。跟
    `_notify_cycle_failed` 同一套通知网关，复用同样的"kanban 渠道恒真兜底 +
    异常整体吞掉"约定——告警是感知增强，不能反过来影响 execution phase
    判定或 Goal 触发主流程。
    """
    try:
        if paths is None:
            return
        from mini_agent.notification.dispatcher import NotificationDispatcher, NotificationMessage
        NotificationDispatcher(paths).dispatch(NotificationMessage(
            title=f"周期性目标「{goal.title}」执行阶段可能需要关注",
            body=reason[:200],
            source="goal_cycle_phase_health",
            meta={"goal_id": goal.id},
        ))
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.goal_cron_bridge._notify_phase_health_issue')


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
