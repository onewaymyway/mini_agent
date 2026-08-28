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

import threading
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
        objective_isolated_runner=None,
        llm_helper_provider=None,
    ) -> None:
        self._goal_backlog = goal_backlog
        self._input_queue = input_queue
        self._paths = paths
        self._cfg = cfg
        self._tick_interval = tick_interval_seconds
        # [growth_advisor_autonomy_deepening_plan_v2.md 方向 1] 惰性
        # llm_helper 获取，透传给 reap_finished_cycles() 供 B1 的可选
        # LLM 复核使用；不传时该复核步骤自动跳过，行为与改动前一致。
        self._llm_helper_provider = llm_helper_provider
        self._last_tick_at: float = 0.0
        # [cron_async_feedback_hardening_plan.md D5] cron_questions.jsonl
        # 归档的上次执行时间，_tick_maintenance() 里节流成每 24 小时最多
        # 跑一次——归档是维护性操作，没必要每个 tick 都扫一遍全量记录。
        self._last_questions_archive_at: float = 0.0
        self._last_questions_expire_at: float = 0.0
        self._tick_count: int = 0
        self._digest_records: list[dict] = []  # 待写入 activity_digest.jsonl 的记录
        # Phase 1 新增：CronScheduler 和 ObjectiveExecutor（可选注入，降级安全）
        self._cron_scheduler = cron_scheduler
        self._objective_executor = objective_executor
        # [daemon_task_hang_recovery_and_watchdog_hardening_plan.md 阶段四]
        # 仅在 objective_isolated_context_enabled=True 时由调用方注入，
        # 其余情况保持 None（降级为不检查，与 cron_scheduler/objective_
        # executor 同一套"可选注入、None 时静默跳过"风格）。
        self._objective_isolated_runner = objective_isolated_runner
        # Goal→Objective 自动拆解用的 LLM 回调：(GoalNode) -> list[str]。
        # 未注入时 _ensure_goal_objectives() 直接降级为 1:1 镜像 Objective，
        # 不影响"有 Objective 才能被执行"这条主链路。
        self._goal_decompose_fn = goal_decompose_fn

        # [daemon_execution_model_and_scheduler_heartbeat_improvement_plan.md
        # 阶段二 违规修复] 探索实验后台线程的忙碌标记，保护 tick() 不被
        # _run_capability_exploration() 内部的同步等待卡住。独立小锁纯粹
        # 是为了避免"读的时候线程刚结束但还没来得及标记"这类极端时序
        # 问题，不是为了应对高并发（同一时刻最多只有一个探索线程在跑）。
        self._exploration_thread: Optional[threading.Thread] = None
        self._exploration_state_lock: threading.Lock = threading.Lock()

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

        # [看板"停止调度"功能] 用户手动全局暂停开关，最外层短路：不推进
        # tick_count（看板"距下次Tick"/统计不应该在暂停期间继续跳动，
        # 造成"好像还在跑"的错觉），不触碰 cron_scheduler/objective_executor/
        # goal_backlog 任何方法。恢复后从下一次 tick 正常继续，不补跑错过
        # 的周期（与 cron job 本身"错过就错过，不追赶"的既有语义一致）。
        # 手动调试接口（POST /cron/jobs/{id}/run、goal 手动操作等）不经过
        # tick()，不受此开关影响。
        if self._is_scheduling_paused():
            return

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
        # CronScheduler.tick()：检查所有 enabled job 是否到期并触发。
        # [goal_cron_unified_scheduler_improvement_plan.md P5 第 5 步]
        # `scheduler.unified_dispatch_enabled=True`（默认 False）时改用
        # `unified_task_scheduler.dispatch_due_cron_jobs()`——经
        # `CronChannelAdapter`/`GoalCycleChannelAdapter.execute()` 委托
        # 派发，内部复用的仍是 `CronScheduler.trigger_job_now()` →
        # `_trigger_and_record()`（与 `tick()` 共用同一份记账），返回值
        # 语义与 `tick()` 完全一致（触发成功的 job_id 列表），因此下面
        # 的记账/digest 记录逻辑无需区分走的是哪条路径。开关关闭（默认）
        # 时行为与改造前完全一致。**注意**：本方法是 `_tick_maintenance()`/
        # `_tick_autonomous()` 的第一步（见下方两个方法体开头的
        # `self._tick_passive()` 调用），因此这个开关对 passive/
        # maintenance/autonomous 三个档位同时生效，不需要在另外两个
        # 方法里重复接线。
        if self._cron_scheduler is not None:
            try:
                _scheduler_cfg = getattr(self._cfg, "scheduler", None)
                if bool(getattr(_scheduler_cfg, "unified_dispatch_enabled", False)):
                    from mini_agent.evolution.unified_task_scheduler import dispatch_due_cron_jobs
                    triggered = dispatch_due_cron_jobs(self._cron_scheduler)
                else:
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

    def _trigger_objective_candidate(
        self, obj, *, fairness_paused_ids: set, user_paused_ids: set,
    ) -> bool:
        """[goal_cron_convergence_and_governance_improvement_plan.md
        Track 1] 从 `_tick_maintenance()` 原地循环体中抽取的"选中一个
        Objective 候选后具体怎么触发它"逻辑，行为与抽取前完全等价（同样
        的 resume_fairness / start 分支选择、同样的 mark_scheduled 记账、
        同样的 digest 记录），只是抽成一个可以被外部单独调用的方法，供
        `trigger_objective_now()` 复用，不是重新实现一份。

        调用方需要在调用前自行完成排序、`is_running`/`user_paused_ids`/
        `can_start_new()`/per-Goal 并发上限检查——这个方法只负责"确定要
        触发这个候选之后，具体怎么触发"，不重复做筛选判断（筛选判断在
        两个调用方那里语境不同：`_tick_maintenance()` 是遍历排序结果，
        `trigger_objective_now()` 是针对单个指定 id，让两者各自决定要不要
        检查、怎么检查更合适）。

        返回 True 表示确实触发了一次调度动作（resume 或 start 成功），
        False 表示命中 `fairness_paused_ids` 但 resume 未成功，或
        `start()` 未返回 execution_id（不是异常，是"这次没排上"）。
        """
        if obj.id in fairness_paused_ids:
            resumed = self._objective_executor.resume_fairness(obj.id)
            if resumed:
                try:
                    self._goal_backlog.mark_scheduled(obj.id)
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop.AutonomousLoop._trigger_objective_candidate.mark_scheduled')
                self._record_digest({
                    "type": "objective_resumed_from_fairness_pause",
                    "objective_id": obj.id,
                    "title": obj.title,
                    "summary": f"从公平性暂停恢复执行 Objective：{obj.title}",
                })
            return bool(resumed)

        exec_id = self._objective_executor.start(obj)
        if exec_id:
            try:
                self._goal_backlog.mark_scheduled(obj.id)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop.AutonomousLoop._trigger_objective_candidate.mark_scheduled')
            self._record_digest({
                "type": "objective_started",
                "objective_id": obj.id,
                "title": obj.title,
                "execution_id": exec_id,
                "summary": f"开始执行 Objective：{obj.title}",
            })
        return bool(exec_id)

    def trigger_objective_now(self, objective_id: str) -> bool:
        """[goal_cron_convergence_and_governance_improvement_plan.md
        Track 1] 供 `evolution/unified_task_scheduler.py::
        ObjectiveChannelAdapter.execute()` 调用的公开安全入口——语义与
        `CronScheduler.trigger_job_now(job_id) -> bool` 对齐：成功触发
        返回 True；因并发上限已满/该 Objective 正在运行/找不到节点等
        原因未触发返回 False，不抛异常（异常场景内部已兜底记录日志）。

        这不是一份"简化版"调度逻辑——检查项（是否已在运行、是否处于
        用户主动暂停、全局并发上限、per-Goal 并发上限）与
        `_tick_maintenance()` 排序循环体内的检查完全一致，且复用同一个
        `_trigger_objective_candidate()` 做实际触发，保证两个调用路径
        行为不会出现分叉。
        """
        if self._objective_executor is None or self._goal_backlog is None:
            return False
        try:
            if self._objective_executor.is_running(objective_id):
                return False
            try:
                user_paused_ids = set(self._objective_executor.user_paused_objective_ids())
            except Exception:
                user_paused_ids = set()
            if objective_id in user_paused_ids:
                return False
            if not self._objective_executor.can_start_new():
                return False

            autonomy_cfg = getattr(self._cfg, "autonomy", None) if self._cfg is not None else None
            per_goal_cap = getattr(autonomy_cfg, "max_concurrent_objectives_per_goal", 1) \
                if autonomy_cfg is not None else 1
            if per_goal_cap and per_goal_cap > 0:
                goal_id = self._objective_executor._goal_id_of_objective(objective_id)
                if self._objective_executor.running_count_for_goal(goal_id) >= per_goal_cap:
                    return False

            obj = self._goal_backlog.get(objective_id)
            if obj is None:
                return False

            try:
                fairness_paused_ids = set(self._objective_executor.fairness_paused_objective_ids())
            except Exception:
                fairness_paused_ids = set()

            return self._trigger_objective_candidate(
                obj, fairness_paused_ids=fairness_paused_ids, user_paused_ids=user_paused_ids,
            )
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop.AutonomousLoop.trigger_objective_now')
            return False

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

        # [goal_cron_binding_plan.md Track C/D] 回收周期性 Goal 本轮已终态的
        # 子 Objective：cycle_count += 1 + progress_notes 追加一行摘要。放在
        # maintenance 档位（而不是 passive）是刻意的——见
        # goal_cron_bridge._fire_goal_cycle() 里同样的档位边界说明。纯读写
        # goals.json，失败静默降级，不影响本次 tick 其余步骤。
        try:
            from mini_agent.evolution.goal_cron_bridge import reap_finished_cycles
            reap_finished_cycles(self._goal_backlog, llm_helper_provider=self._llm_helper_provider)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop._tick_maintenance.reap_finished_cycles')
            pass

        # ── 周期性 Goal/Cron 任务主动巡检推送（能力 C，见 next_doc/
        # goal_cron_cycle_proactive_patrol_and_health_overview_plan.md §2）──
        # 放在 _tick_maintenance() 而不是方案文档字面提到的 _tick_passive()：
        # 巡检需要遍历 GoalBacklog 里所有 recurring Goal，而 _tick_passive()
        # 按本文件顶部注释的既有边界"不引用 GoalBacklog 任何方法"，与
        # goal_relevance_candidate/reap_finished_cycles 是同一档位边界
        # 理由，见上面两段注释。`run_cycle_patrol()` 内部已经做了间隔
        # 节流 + 去重冷却，本方法体只负责在命中时把文案交给 InputQueue
        # （NotificationDispatcher 通道已经在函数内部调用过）。总开关
        # 默认关闭，读取配置本身就是零成本判断。
        try:
            cycle_patrol_cfg = getattr(self._cfg, "cycle_patrol", None)
            if cycle_patrol_cfg is not None and getattr(cycle_patrol_cfg, "enabled", False):
                from mini_agent.evolution.cycle_patrol import run_cycle_patrol
                llm_ask = None
                if getattr(cycle_patrol_cfg, "llm_enabled", True) and self._llm_helper_provider is not None:
                    try:
                        helper = self._llm_helper_provider()
                    except Exception:
                        helper = None
                    if helper is not None:
                        llm_ask = lambda prompt: helper.ask(prompt)
                push = run_cycle_patrol(
                    self._paths, self._goal_backlog, cycle_patrol_cfg, llm_ask=llm_ask,
                    app_cfg=self._cfg,
                )
                if push is not None and self._input_queue is not None:
                    self._input_queue.enqueue(
                        message=push["body"],
                        initiator="scheduled",
                        meta=push["meta"],
                    )
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop._tick_maintenance.cycle_patrol')
            pass

        # [daemon_task_hang_recovery_and_watchdog_hardening_plan.md 阶段一]
        # CronJobRunner：回收卡死超过有效超时阈值的 cron job，代替永远
        # 不会执行到的 finally 释放并发许可，使其可以被下一次到期重新
        # submit()。与下面 reap_stale_steps() 相邻、同样放在资源仲裁
        # early-return 之前——回收动作不该依赖"当前是否允许发起新的自主
        # 任务"这个跟它无关的门控，理由完全一致。
        if self._cron_scheduler is not None:
            try:
                self._cron_scheduler.reap_stale_jobs()
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop._tick_maintenance.reap_stale_jobs')
                pass

        # [cron_async_feedback_hardening_plan.md D5] cron_questions.jsonl
        # 归档：每 24 小时最多跑一次（维护性操作，节流避免每个 tick 都
        # 扫一遍全量记录），把超过保留期的 answered/dismissed 记录挪到
        # archive 文件，主文件只留 pending + 近期记录，控制线性增长。
        # 失败静默降级，不影响 tick 其余步骤。
        now = time.time()
        if now - self._last_questions_archive_at >= 86400:
            self._last_questions_archive_at = now
            try:
                from mini_agent.notification import questions_store
                questions_store.archive_old_records(self._paths)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop._tick_maintenance.archive_old_records')
                pass

        # [cron_async_feedback_lifecycle_and_usability_plan.md E1] 长期无人
        # 回答的问题自动关闭：跟上面归档同一节流窗口（24 小时最多跑一次，
        # 全量扫描成本不高但没必要每个 tick 都做），避免"待我反馈"列表
        # 无限堆积。阈值从 `.agent/notification/config.yaml` 的
        # `cron_question_stale_after_days` 读取（默认 14 天，<=0 关闭该
        # 机制）。每条被关闭的问题都补发一条 kanban 通知，明确告诉用户
        # "为什么这个问题消失了"——静默关闭本身就是一种新的可用性缺陷。
        if now - self._last_questions_expire_at >= 86400:
            self._last_questions_expire_at = now
            try:
                from mini_agent.notification import questions_store
                from mini_agent.notification.config import load_notification_config
                from mini_agent.notification.dispatcher import NotificationDispatcher, NotificationMessage
                notif_cfg = load_notification_config(self._paths)
                stale_after_days = notif_cfg.cron_question_stale_after_days
                if stale_after_days and stale_after_days > 0:
                    expired = questions_store.expire_stale_pending_questions(
                        self._paths, stale_after_days=stale_after_days,
                    )
                    if expired:
                        dispatcher = NotificationDispatcher(self._paths)
                        for rec in expired:
                            try:
                                dispatcher.dispatch(NotificationMessage(
                                    title=f"任务「{rec.get('job_id', '-')}」的一个问题因长期未回答已自动关闭",
                                    body=(
                                        f"{rec.get('question', '')}\n\n"
                                        f"这个问题超过 {int(stale_after_days)} 天没有得到回答，系统已"
                                        "自动关闭，不再提醒。如果仍然需要处理，可以直接在对应任务里"
                                        "重新说明，或等它下次任务执行时再次被问到。"
                                    ),
                                    source="cron_question_expired",
                                    meta={"job_id": rec.get("job_id"), "question_id": rec.get("question_id")},
                                ))
                            except Exception as _mini_agent_exc:
                                from mini_agent.errors import log_exception
                                log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop._tick_maintenance.expire_stale_pending_questions.notify')
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop._tick_maintenance.expire_stale_pending_questions')
                pass

        # ObjectiveExecutor：先回收卡死的 step（并发槽位卡死修复，见
        # ObjectiveExecutor.reap_stale_steps() 说明）。必须放在资源仲裁的
        # early-return 之前：否则一旦某次 tick 恰好赶上预算耗尽/用户在场
        # 等门控触发提前 return，卡死的 step 就会一直没人清理——回收动作
        # 不该依赖"当前是否允许发起新的自主任务"这个跟它无关的门控。
        if self._objective_executor is not None:
            try:
                timeout_override = None
                autonomy_cfg = getattr(self._cfg, "autonomy", None)
                if autonomy_cfg is not None:
                    timeout_override = getattr(autonomy_cfg, "objective_step_stale_timeout_seconds", None)
                if timeout_override is not None:
                    self._objective_executor.reap_stale_steps(timeout_seconds=timeout_override)
                else:
                    self._objective_executor.reap_stale_steps()
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop')
                pass
            # [daemon_task_hang_recovery_and_watchdog_hardening_plan.md
            # 阶段四] ObjectiveIsolatedRunner 共享线程池的整体健康检查——
            # 与上面 cron/step 的 reap 相邻、同样放在资源仲裁 early-return
            # 之前，理由一致：回收/自愈动作不该依赖跟它无关的门控。仅在
            # 注入了 isolated runner（即 objective_isolated_context_enabled
            # =True 且未被持久 Worker 抢占）时才会真正执行。
            if self._objective_isolated_runner is not None:
                try:
                    self._objective_isolated_runner.check_health()
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop._tick_maintenance.objective_isolated_runner_check_health')
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
            # [goal_cron_unified_scheduler_improvement_plan.md P0] 普通 cron
            # 通道（CronJobRunner）与 Objective 通道用同一次 tick 内算好的
            # 仲裁结果，不重复计算。degraded 时收紧并发（不再是整体跳过），
            # full 时恢复——与上面 objective_executor 分支同一套"每次 tick
            # 都重新设置，天然随资源状况变化自动升降档"的写法。
            if self._cron_scheduler is not None:
                try:
                    self._cron_scheduler.set_gating_degraded(state == "degraded")
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop._tick_maintenance.cron_set_gating_degraded')
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
            autonomy_cfg = getattr(self._cfg, "autonomy", None) if self._cfg is not None else None
            strategy = getattr(autonomy_cfg, "goal_scheduling_strategy", "fair_round_robin") \
                if autonomy_cfg is not None else "fair_round_robin"
            if strategy == "priority":
                objectives = self._goal_backlog.active_objectives()
            else:
                stale_days = getattr(self._cfg, "next_action_stale_days", 7.0) if self._cfg is not None else 7.0
                boost_per_day = getattr(autonomy_cfg, "fairness_aging_boost_per_day", 1.0) \
                    if autonomy_cfg is not None else 1.0
                boost_max_days = getattr(autonomy_cfg, "fairness_aging_boost_max_days", 14.0) \
                    if autonomy_cfg is not None else 14.0
                objectives = self._goal_backlog.active_objectives_fair_ranked(
                    stale_days=stale_days,
                    aging_boost_per_day=boost_per_day,
                    aging_boost_max_days=boost_max_days,
                )

            per_goal_cap = getattr(autonomy_cfg, "max_concurrent_objectives_per_goal", 1) \
                if autonomy_cfg is not None else 1

            # [goal_execution_fairness_improvement_plan.md P4] 先取一次当前
            # 因公平性让出槽位的 objective_id 集合——排序结果里如果命中，走
            # resume_fairness()（从断点续跑），而不是当成"全新 Objective"
            # 再走一次 start()（那样会重新拆解、丢失已完成的 step 进度）。
            try:
                fairness_paused_ids = set(self._objective_executor.fairness_paused_objective_ids())
            except Exception:
                fairness_paused_ids = set()
            # [daemon_stability_and_ux_improvement_plan.md P1-5] 用户主动暂停
            # 的 execution 不应该被调度器当作"已结束/可以重新 start()"处理——
            # 与 fairness_paused_ids 不同，这里不自动恢复（用户没有明确表示
            # 要继续），只是跳过本轮候选，等用户显式调用 resume_user_pause()。
            try:
                user_paused_ids = set(self._objective_executor.user_paused_objective_ids())
            except Exception:
                user_paused_ids = set()

            for obj in objectives:
                if self._objective_executor.is_running(obj.id):
                    continue
                if obj.id in user_paused_ids:
                    continue
                if not self._objective_executor.can_start_new():
                    break
                # [P1] 同一 Goal 并发上限：达到上限则跳过本候选，继续看排序
                # 里的下一个（而不是 break），避免一个 Goal 顶到上限就让本轮
                # 调度提前结束，其它 Goal 的候选仍有机会被挑中。
                if per_goal_cap and per_goal_cap > 0:
                    goal_id = self._objective_executor._goal_id_of_objective(obj.id)
                    if self._objective_executor.running_count_for_goal(goal_id) >= per_goal_cap:
                        continue

                self._trigger_objective_candidate(
                    obj, fairness_paused_ids=fairness_paused_ids, user_paused_ids=user_paused_ids,
                )
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

            # capability 类：每次 tick 最多起 1 个探索实验。
            # [daemon_execution_model_and_scheduler_heartbeat_improvement_plan.md
            # 阶段二 违规修复] _run_capability_exploration() 内部会同步阻塞
            # 等待探索任务完成（最多 5 分钟，见 _submit_exploration_task）。
            # 这与本文件顶部/scheduler_heartbeat.py 明确写下的设计原则相
            # 违背——tick() 只应该做"决策 + 提交"，不能持锁跑真正耗时的
            # 调用，否则一次探索实验就能把 SchedulerHeartbeat 卡住 5
            # 分钟，期间 CronScheduler.tick() 完全没有机会被调用，表现为
            # "到点的 cron job 迟迟不触发"。这里改成后台线程 fire-and-
            # forget，tick() 立即返回；用一个忙碌标记防止上一个探索还没
            # 跑完就又起一个新的（避免并发跑多个探索实验抢资源）。
            started_exploration = False
            if cap_candidates and not self._exploration_busy():
                started_exploration = self._start_capability_exploration_bg(cap_candidates[0], deriver)

            # 记录"本轮已经做过一次 derive 尝试"：无论探索实验是异步起来的
            # 还是这轮压根没有 capability 候选，只要产出了新 Goal 或者
            # 确实起了一个探索任务，都算一次有效尝试，语义与改造前
            # `if new_goals or cap_candidates:` 一致（cap_candidates 非空
            # 但因为已有一个探索在跑而跳过本轮的情况除外，那种情况下没有
            # 消耗任何新资源，不应该被计入节流窗口）。
            if new_goals or started_exploration:
                deriver._record_derive()
                self._goal_backlog.save()

        except ImportError:
            pass
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop')
            pass

    def _exploration_busy(self) -> bool:
        """是否已有一个探索实验线程在跑（未结束）。"""
        with self._exploration_state_lock:
            t = self._exploration_thread
            return t is not None and t.is_alive()

    def _start_capability_exploration_bg(self, candidate, deriver) -> bool:
        """
        在后台线程里跑 _run_capability_exploration()，tick() 本身立即
        返回，不持锁等待。返回 True 表示这次真的起了一个新线程。

        _run_capability_exploration() 内部的所有写操作（commit_goals /
        _record_digest / skill_propose）本身就是"追加写 jsonl / 更新
        goals.json 再 save()"这类幂等性较好的操作，与 _tick_maintenance()
        走的 ObjectiveExecutor 异步执行路径（同样是后台起线程/进程，写
        完再回调）是同一类并发模型，不需要额外加锁。
        """
        def _worker() -> None:
            try:
                self._run_capability_exploration(candidate, deriver)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(
                    _mini_agent_exc,
                    where='mini_agent.evolution.autonomous_loop.AutonomousLoop._start_capability_exploration_bg',
                )

        t = threading.Thread(
            target=_worker, name="capability-exploration", daemon=True,
        )
        with self._exploration_state_lock:
            self._exploration_thread = t
        t.start()
        return True

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

        [daemon_execution_model_and_scheduler_heartbeat_improvement_plan.md
        阶段二 违规修复] 这个同步等待本身现在跑在
        _start_capability_exploration_bg() 派生的后台线程里，不再占用
        SchedulerHeartbeat 持有的 sched_lock，因此不会再卡住 tick()/
        cron 触发；这里的等待上限保持不变，只是不再是"谁在阻塞"的问题。
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

    def _is_scheduling_paused(self) -> bool:
        """读取 self_profile.json 里的全局调度暂停开关。"""
        try:
            from mini_agent.perception.global_knowledge import is_scheduling_paused
            return is_scheduling_paused(self._paths)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.autonomous_loop')
            return False  # 读取失败时保守降级为不暂停（与改动前行为一致）

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
            "scheduling_paused": self._is_scheduling_paused(),
        }


__all__ = ["AutonomousLoop"]
