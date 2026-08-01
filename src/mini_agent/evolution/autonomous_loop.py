"""
evolution/autonomous_loop.py — Stage 9 AutonomousLoop（第七节）

跑在 daemon 进程内（第三节确立的进程模型），是 AgentRunner 循环内部的一个
tick 分支，与"检查用户消息"分支并列，共享同一个常驻进程的生命周期。

只要 daemon 进程存活（不依赖任何客户端连接），tick() 就会按设定频率持续执行。
这是本类与"挂在某次 CLI 调用上的循环"的本质区别。

三档位边界（stage9_plan.md 7.2 节）：
  passive:     只做 Stage 8 已有周期性任务（巩固循环），不读 GoalBacklog
  maintenance: passive + 探索预算分配，不 derive 新 Goal
  autonomous:  maintenance + 软目标 derive（第十二节，暂未实现内部逻辑）

档位边界在代码层面的物理体现（不是靠注释承诺）：
  _tick_passive() 方法体内不引用 GoalBacklog 任何方法
  _tick_maintenance()/_tick_autonomous() 才会调用 goal_backlog.has_actionable_work()
"""

from __future__ import annotations

import time
from typing import Any, Optional, TYPE_CHECKING
from mini_agent.time_utils import ts_to_str

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.config.models import AppConfig
    from mini_agent.perception.goal_backlog import GoalBacklog
    from mini_agent.api.bridge import InputQueue


class AutonomousLoop:
    """
    daemon 进程内的自主调度循环。

    不持有自己的线程，由调用方（AgentRunner 循环）决定 tick 频率。
    AgentRunner.run() 的 dequeue(timeout=0.5) 超时返回 None（没有新用户消息）时，
    检查"距上次 tick 是否已过 tick_interval_seconds"，是则调用 tick()。
    """

    def __init__(
        self,
        *,
        goal_backlog: "GoalBacklog",
        input_queue: "InputQueue",
        paths: "AgentPaths",
        cfg: "AppConfig",
        tick_interval_seconds: float = 60.0,
        cron_scheduler=None,
        objective_executor=None,
        goal_decompose_fn=None,
    ) -> None:
        self._goal_backlog = goal_backlog
        self._input_queue = input_queue
        self._paths = paths
        self._cfg = cfg
        self._tick_interval = tick_interval_seconds
        self._last_tick_at: float = 0.0
        self._tick_count: int = 0
        self._digest_records: list[dict] = []  # 待写入 activity_digest.jsonl 的记录
        # Phase 1 新增：CronScheduler 和 ObjectiveExecutor（可选注入，降级安全）
        self._cron_scheduler = cron_scheduler
        self._objective_executor = objective_executor
        # Goal→Objective 自动拆解用的 LLM 回调：(GoalNode) -> list[str]。
        # 未注入时 _ensure_goal_objectives() 直接降级为 1:1 镜像 Objective，
        # 不影响"有 Objective 才能被执行"这条主链路。
        self._goal_decompose_fn = goal_decompose_fn

    # ── 公共接口 ──────────────────────────────────────────────────────────────

    def should_tick(self) -> bool:
        """判断是否应该执行一次 tick（由 AgentRunner 在每次 dequeue 超时后调用）。"""
        return (time.time() - self._last_tick_at) >= self._tick_interval

    def tick(self) -> None:
        """
        主调度入口。AgentRunner 在没有用户消息时调用。
        根据当前 autonomy_level 选择对应档位的 tick 逻辑。
        """
        self._last_tick_at = time.time()
        self._tick_count += 1

        autonomy_level = self._get_autonomy_level()

        if autonomy_level == "passive":
            self._tick_passive()
            return

        if autonomy_level == "maintenance":
            self._tick_maintenance()
            return

        # autonomous 档位
        self._tick_autonomous()

    @property
    def last_tick_at(self) -> float:
        return self._last_tick_at

    @property
    def tick_count(self) -> int:
        return self._tick_count

    # ── 档位实现 ──────────────────────────────────────────────────────────────

    def _tick_passive(self) -> None:
        """
        [passive 档位] 运行所有到期的 cron job。
        巩固循环、workdir_sync、self_eval、goal_review、digest_trim
        都作为 cron job 注册，不再在此直接调用。

        边界的物理体现：本方法体内不引用 self._goal_backlog 任何方法。
        """
        # CronScheduler.tick()：检查所有 enabled job 是否到期并触发
        if self._cron_scheduler is not None:
            try:
                triggered = self._cron_scheduler.tick()
                for job_id in triggered:
                    self._record_digest({
                        "type": "cron_run",
                        "job_id": job_id,
                        "summary": f"Cron job 触发：{job_id}",
                    })
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop')
                pass
        else:
            # 降级：CronScheduler 未注入时直接调用 巩固循环（保持向后兼容）
            try:
                from mini_agent.evolution.consolidation import should_run_consolidation, run_consolidation
                if should_run_consolidation(self._paths):
                    report = run_consolidation(self._paths)
                    self._record_consolidation_for_digest(report)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop')
                pass

        # Workdir knowledge 定期整合（CronScheduler 未注入时的降级路径）
        if self._cron_scheduler is None:
            try:
                self._run_workdir_consolidation()
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop')
                pass

        # ── 注意力错配 daemon 主动推送（主动推荐与数字分身机制设计方案 4.3 节）──
        # 直接在 tick 里做，不经过 cron job 的 LLM 任务描述——这里需要的是
        # "持续超过阈值时长才推送一次"这种精确的跨 tick 状态判断，不适合交给
        # 模型每次自己判断"要不要提醒"。复用 InputQueue（与 CronScheduler 提交
        # 任务用的是同一条通道），这样推送出的消息会像普通一轮对话一样，通过
        # 已有的多客户端 SSE 推送流转发给所有连接中的客户端（看板/微信/移动端）。
        try:
            digest_advisor_cfg = getattr(self._cfg, "digest_advisor", None)
            if digest_advisor_cfg is not None and digest_advisor_cfg.next_action_push_enabled:
                from mini_agent.evolution.next_action_advisor import (
                    check_persistent_attention_mismatch,
                    render_push_message,
                )
                payload = check_persistent_attention_mismatch(self._paths, digest_advisor_cfg)
                if payload is not None and self._input_queue is not None:
                    self._input_queue.enqueue(
                        message=render_push_message(payload),
                        initiator="scheduled",
                        meta={
                            "source": "attention_mismatch_push",
                            "ref_id": payload["ref_id"],
                        },
                    )
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop._tick_passive.attention_mismatch_push')
            pass

        # ── External Input Gateway：IngestionPolicy 消费点（外部输入网关设计
        # 方案 §3.4/P5，P8 收窄为两档）── 放在 _tick_passive() 而不是
        # _tick_autonomous()：notify_only（默认档、成本最低）不应该被
        # autonomy_level 挡住——外部世界产生的事件不该因为用户把档位调到
        # passive 就完全看不见。真正昂贵的 enqueue_turn 落点默认关闭，需要
        # 用户在 policies.yaml 里显式配置才会触发，天然就有节流。P8 起
        # `IngestionPolicy` 不再直接写 GoalBacklog——外部输入与已有 Goal/
        # Objective 的关联完全交给下面 GoalRelevanceEngine Stage① 那段
        # 独立处理，两条链路不再重叠。
        try:
            from mini_agent.external_input.policy import run_ingestion_policy_once
            run_ingestion_policy_once(
                self._paths,
                input_queue=self._input_queue,
            )
        except ImportError:
            pass
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop._tick_passive.external_input_policy')
            pass

        # ── WatchlistMatcher 消费点（关注对象·分级汇报·通知系统扩展设计
        # §4.1/P2）── 跟上面的 IngestionPolicy 完全独立、各自持有独立游标，
        # 不是"先路由再匹配关注词"的串联关系（见该文档 §2 的关键设计取舍）。
        # 纯规则、零 LLM 成本，同样不该被 autonomy_level 挡住。
        try:
            from mini_agent.external_input.watchlist import run_watchlist_matcher_once
            run_watchlist_matcher_once(self._paths)
        except ImportError:
            pass
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop._tick_passive.watchlist_matcher')
            pass

        # ── NoveltyJudge Stage①（候选生成，规则粗筛，§2）── 跟上面两个
        # 消费点一样各自独立游标，不看 GoalBacklog（跟 GoalRelevanceEngine
        # 判定对象完全不同），纯规则、零 LLM 成本，不受 autonomy_level 挡住。
        try:
            from mini_agent.external_input.novelty_judge import run_novelty_candidate_once
            run_novelty_candidate_once(self._paths)
        except ImportError:
            pass
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop._tick_passive.novelty_judge_candidate')
            pass

    def _tick_maintenance(self) -> None:
        """
        [maintenance 档位] passive 的全部任务 + Objective 持续执行推进。
        不 derive 新 Goal（这是与 autonomous 档位的边界——不会凭空产生新
        意图）。但会给已有的 active Goal 补 Objective 子节点：Goal 本身
        已经是用户/上游批准过的意图，只是还没被拆成"可执行单元"，
        has_actionable_work() 只认 level=objective，不补的话 Goal 会一直
        原地不动、agent 永远不会主动去做。见 _ensure_goal_objectives()。
        """
        self._tick_passive()

        # ── GoalRelevanceEngine Stage①（候选生成，规则层，P4）── 必须放在
        # _tick_maintenance() 而不是 _tick_passive()：本方法体内需要读取
        # `goal_backlog.active_goals()`，而 _tick_passive() 按既有边界
        # （见本文件顶部注释）不引用 GoalBacklog 任何方法。纯规则匹配，
        # 零 LLM 成本，不受下面资源仲裁门控影响——跟 IngestionPolicy/
        # WatchlistMatcher 一样，"记一条候选"本身不消耗任何预算。
        try:
            from mini_agent.external_input.goal_relevance import run_goal_relevance_candidate_once
            # P3（relevance_threshold_calibration）：阈值不再是写死的
            # DEFAULT_PREFILTER_THRESHOLD，改成读取校准状态里的当前生效值
            # （文件不存在时 load_calibrated_threshold 内部会退回默认值，
            # 不落盘、零额外成本）。
            from mini_agent.evolution.relevance_threshold_calibration import load_calibrated_threshold
            threshold = load_calibrated_threshold(self._paths)
            run_goal_relevance_candidate_once(self._paths, goal_backlog=self._goal_backlog, threshold=threshold)
        except ImportError:
            pass
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop._tick_maintenance.goal_relevance_candidate')
            pass

        self._ensure_goal_objectives()

        # ObjectiveExecutor：先回收卡死的 step（并发槽位卡死修复，见
        # ObjectiveExecutor.reap_stale_steps() 说明）。必须放在资源仲裁的
        # early-return 之前：否则一旦某次 tick 恰好赶上预算耗尽/用户在场
        # 等门控触发提前 return，卡死的 step 就会一直没人清理——回收动作
        # 不该依赖"当前是否允许发起新的自主任务"这个跟它无关的门控。
        if self._objective_executor is not None:
            try:
                self._objective_executor.reap_stale_steps()
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop')
                pass
            # [看板与自主性改进方案 Track C] 尝试重新提交因路径冲突被
            # blocked 的 step——占用方可能已在上一轮完成/失败/取消，
            # 释放了路径。放在 reap_stale_steps() 之后、资源仲裁 early-return
            # 之前，理由与上面一致：这是"清理/推进已存在的排队状态"，不是
            # "发起新的自主任务"，不该被门控挡住。
            try:
                self._objective_executor.retry_blocked_steps()
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop')
                pass

        # 检查资源仲裁 [Track J：三态门控，取代原来的二元 can_run_autonomous()]
        try:
            from mini_agent.evolution.resource_arbiter import ResourceArbiter
            arbiter = ResourceArbiter(self._paths, self._cfg)
            state = arbiter.gating_state()["state"]
            if state == "blocked":
                # 预算耗尽 / frustration 达到硬停摆阈值：暂停所有 Objective 执行，
                # 与改造前的 can_run_autonomous()==False 行为完全一致。
                if self._objective_executor is not None:
                    self._objective_executor.pause_all()
                return
            if self._objective_executor is not None:
                # degraded：不 pause_all，只是把并发上限临时收紧（见
                # ObjectiveExecutor.effective_max_concurrent() 里的
                # resource_gating_degraded_max_concurrent）；full：恢复不降级。
                # 每次 tick 都重新设置，天然随资源状况变化自动升降档，不需要
                # 额外的"恢复"逻辑。
                self._objective_executor.set_gating_degraded(state == "degraded")
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop.AutonomousLoop._tick_maintenance')
            return

        # ObjectiveExecutor：推进已有活跃 Objective
        if self._objective_executor is not None:
            try:
                self._objective_executor.resume()  # 恢复因资源仲裁暂停的 Objective
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop')
                pass

        # 若有 ObjectiveExecutor 且还有并发槽位，从 GoalBacklog 启动新 Objective
        if (
            self._objective_executor is not None
            and self._objective_executor.can_start_new()
            and self._goal_backlog.has_actionable_work()
        ):
            objectives = self._goal_backlog.active_objectives()
            for obj in objectives:
                if self._objective_executor.is_running(obj.id):
                    continue
                if not self._objective_executor.can_start_new():
                    break
                exec_id = self._objective_executor.start(obj)
                if exec_id:
                    self._record_digest({
                        "type": "objective_started",
                        "objective_id": obj.id,
                        "title": obj.title,
                        "execution_id": exec_id,
                        "summary": f"开始执行 Objective：{obj.title}",
                    })
            return

        # ObjectiveExecutor 未注入时的降级路径：沿用旧的单次 Task 提交
        if self._objective_executor is None:
            if not self._goal_backlog.has_actionable_work():
                return
            result = self._goal_backlog.next_task_description()
            if not result:
                return
            objective_id, task_desc = result
            if self._submit_autonomous_task(task_desc, objective_id):
                self._record_digest({
                    "type": "task_submitted",
                    "objective_id": objective_id,
                    "task_desc": task_desc[:200],
                })

    def _ensure_goal_objectives(self) -> None:
        """[Goal→Objective 自动拆解] 由配置开关
        autonomy.auto_objective_from_goal_enabled 控制（默认开）。

        对每个还没有 Objective 子节点的 active Goal：
          1. 若注入了 self._goal_decompose_fn，先在锁外调用一次 LLM 拆解
             （可能耗时，绝不能在 GoalBacklog 的跨进程文件锁内做）。
          2. 拆解失败/未注入/返回空 → 降级为 1 个与 Goal 同名的 Objective，
             保证"至少可执行"这个下限，不会因为 LLM 不可用就让 Goal 卡死。
          3. 调用 add_objectives_for_goal() 做实际写入（内部才加锁，
             且只做纯数据操作，持锁时间是毫秒级）。
        每创建一个 Objective 记一条 digest，保证行为可追溯、不是静默发生。
        """
        autonomy_cfg = getattr(self._cfg, "autonomy", None)
        if autonomy_cfg is not None and not getattr(autonomy_cfg, "auto_objective_from_goal_enabled", True):
            return
        max_per_goal = getattr(autonomy_cfg, "auto_objective_max_per_goal", 3) if autonomy_cfg is not None else 3

        try:
            goals = self._goal_backlog.goals_missing_objective()
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop.AutonomousLoop._ensure_goal_objectives.read')
            return

        for goal in goals:
            titles: list[str] = []
            if self._goal_decompose_fn is not None:
                try:
                    titles = [t for t in (self._goal_decompose_fn(goal) or []) if t and t.strip()]
                    titles = titles[:max_per_goal]
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop.AutonomousLoop._ensure_goal_objectives.decompose')
                    titles = []
            if not titles:
                titles = [goal.title]  # 降级：LLM 未注入/失败时 1:1 镜像，保底可执行

            try:
                created = self._goal_backlog.add_objectives_for_goal(goal.id, titles)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop.AutonomousLoop._ensure_goal_objectives.write')
                continue

            for obj in created:
                self._record_digest({
                    "type": "objective_auto_created",
                    "goal_id": goal.id,
                    "objective_id": obj.id,
                    "title": obj.title,
                    "summary": f"自动为目标「{goal.title}」创建执行子目标：{obj.title}",
                })

    def _tick_autonomous(self) -> None:
        """
        [autonomous 档位] maintenance + 软目标 derive + 探索实验。

        流程：
          1. _tick_maintenance()（cron + Objective 推进）
          2. derive_candidates() 分两类：
               capability 类 → ExplorationSandbox 验证 → 成功才写 Goal + skill_propose
               其他类（workthread/lesson）→ 直接写 Goal
        """
        self._tick_maintenance()

        try:
            from mini_agent.evolution.soft_goal_deriver import SoftGoalDeriver
            deriver = SoftGoalDeriver(self._paths, self._cfg)
            if not deriver.should_derive():
                return

            cap_candidates, other_candidates = deriver.derive_candidates(self._goal_backlog)

            # [事件总线接入] 先复核上一轮（或更早）产出的 needs_review 候选，
            # 再提交本轮新候选——本轮新提交的 workthread/lesson 候选会在
            # 下一次 tick 才被复核到，这是事件总线"轮询+游标"模型的正常延迟，
            # 不是 bug（复核本身也不需要"立刻"，见 system-events-bus-guide.md）。
            reviewed_count = deriver.review_unvalidated_candidates(self._goal_backlog)
            if reviewed_count:
                self._record_digest({
                    "type": "goal_candidates_reviewed",
                    "summary": f"自动复核了 {reviewed_count} 个待验证的候选目标",
                })

            # 其他类：直接写 Goal
            new_goals = deriver.commit_goals(other_candidates, self._goal_backlog)
            for goal in new_goals:
                self._record_digest({
                    "type": "soft_goal_created",
                    "goal_id": goal.id,
                    "title": goal.title,
                    "summary": (
                        f"Agent 建议新目标：{goal.title} — "
                        f"来自 {goal.description[:60]}"
                    ),
                })

            # capability 类：每次 tick 最多跑 1 个探索实验（消耗较多资源）
            for candidate in cap_candidates[:1]:
                self._run_capability_exploration(candidate, deriver)

            if new_goals or cap_candidates:
                deriver._record_derive()
                self._goal_backlog.save()

        except ImportError:
            pass
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop')
            pass

    def _run_capability_exploration(self, candidate, deriver) -> None:
        """
        对 capability 类候选跑一次轻量探索实验。
        成功 → 写 Goal + 尝试 skill_propose；失败 → 静默丢弃。
        """
        try:
            from mini_agent.perception.exploration_sandbox import (
                make_exploration_sandbox,
                ExplorationBudgetExhausted,
            )
        except ImportError:
            # ExplorationSandbox 不可用时降级为直接写 Goal
            new_goals = deriver.commit_goals([candidate], self._goal_backlog, max_new=1)
            for goal in new_goals:
                self._record_digest({
                    "type": "soft_goal_created",
                    "goal_id": goal.id,
                    "title": goal.title,
                    "summary": f"Agent 建议新目标：{goal.title} — 来自 capability_map",
                })
            return

        try:
            memory_backend = None
            try:
                from mini_agent.perception.memory_factory import create_memory_backend
                if getattr(self._cfg.memory, "enabled", False):
                    memory_backend = create_memory_backend(self._cfg)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop.AutonomousLoop._run_capability_exploration')
                memory_backend = None
            sandbox = make_exploration_sandbox(self._paths, self._cfg, memory_backend=memory_backend)
            goal_text = (
                f"验证假设：{candidate.title}\n"
                f"背景：{candidate.description}\n"
                f"任务：分析失败根因，给出 1-3 条可执行改进措施；"
                f"若措施可封装为通用 skill 则简要描述 skill 内容（50 字内）。"
            )

            with sandbox.create(
                capability_id=candidate.title[:40],
                goal=goal_text,
                branch_prefix="explore/capability",
            ) as ctx:
                result = self._submit_exploration_task(goal_text, ctx)

                if result:
                    ctx.report.success = True
                    ctx.report.finding = result[:200]

                    # 验证通过：写 Goal
                    new_goals = deriver.commit_goals([candidate], self._goal_backlog, max_new=1)
                    for goal in new_goals:
                        self._record_digest({
                            "type": "soft_goal_created",
                            "goal_id": goal.id,
                            "title": goal.title,
                            "summary": (
                                f"Agent 建议新目标：{goal.title} — "
                                f"来自 capability_map（探索验证通过）"
                            ),
                        })

                    # 尝试 skill_propose
                    skill_id = self._maybe_propose_skill(candidate, result)
                    if skill_id:
                        ctx.report.proposed_skill_id = skill_id
                        self._record_digest({
                            "type": "exploration_result",
                            "goal": candidate.title,
                            "success": True,
                            "finding": result[:200],
                            "proposed_skill_id": skill_id,
                            "summary": f"探索实验成功，已提案技能：{skill_id}",
                        })

        except ExplorationBudgetExhausted:
            pass
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop')
            pass

    def _submit_exploration_task(self, goal_text: str, ctx) -> str:
        """
        提交探索任务到 InputQueue 并同步等待结果（最多 5 分钟）。
        不走 ObjectiveExecutor 的多步逻辑，是一次性轻量任务。
        """
        try:
            iq = getattr(self, "_input_queue", None)
            if iq is None:
                return ""
            import time as _t
            turn_id = iq.enqueue(
                message=f"[探索实验] {goal_text}",
                initiator="autonomous",
                meta={"exploration_sandbox_id": ctx.sandbox_id},
            )
            if not turn_id:
                return ""
            deadline = _t.time() + 300
            while _t.time() < deadline:
                _t.sleep(2)
                status = getattr(iq, "get_status", lambda _: None)(str(turn_id))
                if status == "done":
                    return getattr(iq, "get_result", lambda _: "")(str(turn_id)) or "completed"
                if status in ("error", "cancelled"):
                    return ""
            return ""
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop.AutonomousLoop._submit_exploration_task')
            return ""

    def _maybe_propose_skill(self, candidate, exploration_result: str) -> str:
        """
        若探索结果暗示可封装为 skill，调用 skill_propose 生成提案分支。
        返回分支名（即 skill_id），失败返回空字符串。
        """
        keywords = ["skill", "技能", "封装", "通用", "可复用", "pattern"]
        if not any(kw in exploration_result.lower() for kw in keywords):
            return ""
        try:
            from mini_agent.tools.evolution import skill_propose
            skill_name = (
                candidate.title.lower()
                .replace(" ", "_")
                .replace("/", "_")[:30]
            )
            result = skill_propose(
                name=skill_name,
                content=(
                    f"# {candidate.title}\n\n"
                    f"## 背景\n{candidate.description}\n\n"
                    f"## 探索发现\n{exploration_result[:500]}\n\n"
                    f"## 来源\n自动生成（ExplorationSandbox，capability_map 低置信度触发）"
                ),
                source_lessons=[],
            )
            return (result or {}).get("branch", "") if isinstance(result, dict) else ""
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop.AutonomousLoop._maybe_propose_skill')
            return ""



    def _get_autonomy_level(self) -> str:
        """读取当前 autonomy_level（从 self_profile.json）。"""
        try:
            from mini_agent.perception.global_knowledge import load_self_profile
            profile = load_self_profile(self._paths)
            if profile:
                return profile.operating_state.autonomy_level
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop')
            pass
        return "passive"  # 读取失败时保守降级

    def _run_workdir_consolidation(self) -> None:
        """
        定期运行 workdir knowledge 整合（若有对应函数）。
        这是"从 SessionEnd 时间门控迁移到 daemon tick"的另一个例子。
        """
        # 目前 Stage 4 的整合是在 session end 时触发，
        # daemon 化后可以改为 tick 触发，但本节先以 巩固循环 为主要验证目标。
        pass

    def _submit_autonomous_task(
        self, task_desc: str, objective_id: str
    ) -> bool:
        """
        通过 InputQueue 提交一条自主 Task（initiator="autonomous"）。
        与用户消息走同一条路，保证调度公平性和资源仲裁生效。
        """
        try:
            # initiator 字段在第七节已新增到 _TurnCommand 和 enqueue()
            turn_id = self._input_queue.enqueue(
                message=f"[自主任务] {task_desc}",
                initiator="autonomous",
                meta={"objective_id": objective_id},
            )
            return bool(turn_id)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop.AutonomousLoop._submit_autonomous_task')
            return False

    def _record_consolidation_for_digest(self, report: Any) -> None:
        """将 巩固循环 报告记录到 activity_digest.jsonl。"""
        try:
            prune_count = len(getattr(report, "prune_candidates", []))
            promote_count = len(getattr(report, "promotion_candidates", []))
            cap_count = len(getattr(report, "capability_map", []))
            summary = (
                f"巩固循环 扫描完成：{prune_count} 个剪枝候选，"
                f"{promote_count} 个晋升候选，{cap_count} 个能力条目"
            )
            self._record_digest({
                "type": "consolidation_completed",
                "summary": summary,
                "prune_count": prune_count,
                "promote_count": promote_count,
                "capability_count": cap_count,
            })
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop')
            pass

        # wiki_next_phase_improvement_plan.md §1.2.3：巩固循环触发后顺带检查一次
        # wiki 转正下线评估，只在"未就绪 -> 就绪"翻转的瞬间写一条提醒进 digest，
        # 不在每次巩固循环（默认 6h 一次）都重复打扰。任何异常都不影响巩固循环
        # 本身已经完成的事实，只吞掉不上抛。
        self._check_decommission_transition()

    def _check_decommission_transition(self) -> None:
        """巩固循环收尾后顺带跑一次 wiki 下线评估（只读，不执行任何下线动作）。"""
        try:
            from mini_agent.wiki.decommission import check_ready_transition

            if check_ready_transition(self._paths):
                self._record_digest({
                    "type": "wiki_decommission_ready",
                    "summary": (
                        "wiki 转正三条量化标准已连续达标，旧图书馆索引"
                        "（分类树/实体索引/编年目录）具备下线评估条件，"
                        "详见 /wiki promotion 输出的三步下线执行清单。"
                    ),
                })
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop')
            pass

    def _record_digest(self, extra: dict) -> None:
        """向 activity_digest.jsonl 追加一条记录。"""
        try:
            import json
            path = self._paths.workdir_dir / "activity_digest.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "at": time.time(),
                "at_str": ts_to_str(time.time()),
                "initiator": "autonomous",
                **extra,
            }
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False))
                f.write("\n")
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop')
            pass

    def get_digest_status(self) -> dict:
        """返回 AutonomousLoop 状态摘要（供 daemon status 命令使用）。"""
        return {
            "last_tick_at": self._last_tick_at,
            "tick_count": self._tick_count,
            "tick_interval_seconds": self._tick_interval,
            "autonomy_level": self._get_autonomy_level(),
        }


__all__ = ["AutonomousLoop"]
