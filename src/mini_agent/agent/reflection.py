from __future__ import annotations

import copy
import re as _re
import threading
from typing import Optional

from mini_agent.config import AppConfig, SessionStats, build_system_prompt
from mini_agent.llm import (
    LLMClient, LLMConfig, LLMResponse, ToolSchema,
    create_client, LLMError,
)
from mini_agent.llm.retry import RetryPolicy, default_retry_policy, no_retry_policy, parse_backoff
from mini_agent.llm.client_pool import LLMClientPool
from mini_agent.permissions import PermissionGuard
from mini_agent.skills import SkillLoader
from mini_agent.tools import ToolRegistry, get_default_registry
from mini_agent.session import SessionManager, Session
import mini_agent.ui.renderer as R
from mini_agent.perception.token_counter import estimate_messages_tokens
from mini_agent.perception.project_scanner import ProjectScanner
from mini_agent.perception.file_watcher import FileWatcher
from mini_agent.perception.tool_cache import ToolResultCache
from mini_agent.perception.memory_base import MemoryBackend
from mini_agent.perception.memory_store import MemoryStore, MemoryEntry
from mini_agent.perception.memory_factory import create_memory_backend
from mini_agent.context_builder import ContextBuilder
from mini_agent.tool_executor import ToolExecutor
from mini_agent.history_manager import HistoryManager
from mini_agent.reminders import ReminderManager

from mini_agent.agent._helpers import (
    _term_write_lock_ctx, _NullCtx, _locked_print_info, _locked_print_warning,
    _is_tool_error, _clamp_confidence, _parse_lesson_candidates, _parse_timeline_summary,
)


class ReflectionMixin:
    """会话结束反思流水线：lesson 提炼、timeline、workdir 知识、巩固与可观测性。"""

    def _current_task_domain_hint(self) -> str:
        """
        [方案三新增] 轻量推断"当前任务大致属于哪个 domain"，供
        _maybe_publish_uncertainty_signal() 发布事件时附带。复用
        evolution/consolidation.py::_infer_domain()（已存在，
        _domain_token_overlap() 系列函数依赖的同一套规则式推断），
        不新增第二套 domain 归类逻辑。

        取最近一条用户消息文本做推断（当前任务的直接来源）；取不到时
        返回空字符串，调用方据此决定是否附带该字段。
        """
        try:
            from mini_agent.evolution.consolidation import _infer_domain
            for msg in reversed(self._hist._history):
                role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", "")
                if role != "user":
                    continue
                content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
                text = content if isinstance(content, str) else str(content or "")
                if text:
                    return _infer_domain(text)
            return ""
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.agent.reflection.ReflectionMixin._current_task_domain_hint')
            return ""

    def _maybe_publish_uncertainty_signal(self, state: "AgentInternalState") -> None:
        """
        [方案三新增] ProprioceptionModule.uncertainty 信号接入事件总线。

        限流发布，不是每轮都发（uncertainty 本身就是逐轮波动的连续值，
        不像 frustration 有明确阈值边沿）：仿照 hybrid_memory_backend.py 的
        memory.sparse_region_detected 做法，设定"连续 N 轮 uncertainty 都
        超过阈值"才发布，避免刷屏。发布后重置计数，避免同一段持续不确定性
        重复发多条。

        失败静默降级：事件发布是旁路增强，任何异常都不应影响主循环。
        """
        try:
            threshold = getattr(self.cfg.proprioception, "uncertainty_threshold", 0.45)
            streak_required = getattr(self.cfg.proprioception, "uncertainty_streak_required", 3)
            if state.uncertainty >= threshold:
                self._uncertainty_streak += 1
            else:
                self._uncertainty_streak = 0
                return
            if self._uncertainty_streak < streak_required:
                return
            try:
                from mini_agent.perception import system_events as se
                from mini_agent.storage.paths import AgentPaths as _AP
                se.publish(
                    _AP(self.cfg.project_root),
                    source=f"session:{self._session.id if self._session else 'unknown'}",
                    event_type="proprioception.uncertainty_sustained",
                    tier="tick",  # 不是即时响应场景，走 tick 节奏即可
                    payload={
                        "uncertainty": round(state.uncertainty, 3),
                        "streak": self._uncertainty_streak,
                        "recent_domain_hint": self._current_task_domain_hint(),
                    },
                )
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.agent.reflection.ReflectionMixin._maybe_publish_uncertainty_signal')
                pass
            # 无论发布是否成功，都重置计数——避免同一段持续不确定性反复
            # 触发发布尝试；下一段新的连续高不确定性区间会从 0 重新累计。
            self._uncertainty_streak = 0
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.agent.reflection.ReflectionMixin._maybe_publish_uncertainty_signal')
            pass

    def _write_proprioception_snapshot(self, state: "AgentInternalState") -> None:
        """
        [B1 → Stage 9 信号桥接] 把最新一次 sense() 快照落盘到
        .agent/proprioception_snapshot.json，供跑在 daemon 后台 tick 里的
        ResourceArbiter 读取（它不持有活跃 Agent 引用，无法直接读内存状态）。

        单文件覆盖写、不追加历史——只关心"最近一次感受"。纯本地感知信号，
        不涉及用户数据，不需要额外脱敏。写入失败由调用方 try/except 吞掉，
        不影响主循环。
        """
        import json as _json
        import time as _time
        from mini_agent.storage.paths import AgentPaths
        paths = AgentPaths(self.cfg.project_root)
        snapshot_path = paths.proprioception_snapshot
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "frustration": round(state.frustration, 3),
            "consecutive_failures": self._proprioception.consecutive_failures
                if self._proprioception is not None else 0,
            "updated_at": _time.time(),
        }
        snapshot_path.write_text(_json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _append_memory_delta(self, entry) -> None:
        """将本 session 产生的记忆条目追加到 memory_delta.jsonl（审计用）。"""
        if not self._session:
            return
        try:
            from dataclasses import asdict
            import json as _json
            session_dir = self._current_session_dir()
            if session_dir is not None:
                delta_path = session_dir / "memory_delta.jsonl"
            else:
                from mini_agent.storage.paths import AgentPaths
                delta_path = AgentPaths(self.cfg.project_root).session_memory_delta(self._session.id)
            delta_path.parent.mkdir(parents=True, exist_ok=True)
            with open(delta_path, "a", encoding="utf-8") as f:
                f.write(_json.dumps(asdict(entry), ensure_ascii=False) + "\n")
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.agent')
            pass

    def trigger_session_end(self) -> None:
        """
        [SYS-SESSION-END] 会话真正结束时调用：触发 SessionEnd hook + 反思生成 lesson。

        对应 self_evolution_implementation_plan.md Stage 1.3 / 设计文档第 3 节
        "SessionEnd hook（目前是预留未接的事件）"。

        调用时机：REPL 退出（EOFError / exit / quit / /exit / /quit），即将退出进程前。
        因此本方法是同步执行（不开后台线程）——进程退出后台线程来不及跑完没有意义，
        但内部做好超时与异常隔离，确保反思失败/缓慢不会导致退出流程卡死或抛出异常。
        """
        if not self._session:
            return

        # [SYS-HOOKS] 触发 SessionEnd 事件（先于 LLM 反思，给 hook 一个"看到原始数据"的机会）
        payload = {
            "session_id": self._session.id,
            "tool_stats": dict(self.stats.tool_stats),
            "turns": self.stats.turns,
            "input_tokens": self.stats.input_tokens,
            "output_tokens": self.stats.output_tokens,
        }
        from mini_agent.hooks import get_hook_manager
        hook_mgr = get_hook_manager()
        if hook_mgr is not None:
            try:
                hook_mgr.run("SessionEnd", payload)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.agent.reflection.ReflectionMixin.trigger_session_end')
                pass  # SessionEnd hook 失败不应阻塞退出流程

        # [W2+W3 / 4.2-4.4 + 5.3 + 5.5] Workdir + Global 知识层更新：timeline /
        # work_index / open_threads / activity_log / self_profile。纯写入为主
        # （无 LLM 依赖），theme/key_outcomes 这一项需要一次轻量反思
        # 调用，单独捕获异常，不让其失败影响 lesson 反思或退出流程。
        try:
            self._update_workdir_knowledge_on_session_end()
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where='mini_agent.agent.reflection.ReflectionMixin.trigger_session_end')
            R.print_warning(f"[session-end] workdir knowledge update failed: {e}")

        # [Stage 6 / 6.3] 观察性：SessionEnd 时写入量化指标 + 异常检测
        try:
            self._run_observability_on_session_end()
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.agent')
            pass

        # [Stage 8 / 8.1] 巩固循环 与 [具身改进 C4] 自维护模块的时间门控扫描
        # 已迁移到 daemon 模式的 CronScheduler（sys:consolidation /
        # sys:self_maintain，见 evolution/cron_scheduler.py），不再在
        # 非 daemon REPL 的 session-end 同步路径里触发：这两步都可能
        # 发起真实 LLM 调用且没有超时保护，放在 exit 关键路径上会导致
        # exit/quit 卡死甚至只能靠 Ctrl+C 强制中断。daemon 模式下用户
        # 持续在线，由 CronScheduler 按 interval 在后台异步跑更合适。

        # [SYS-LESSON] 反思 LLM 调用：基于 tool_stats + 最后若干轮 history 生成 lesson 候选
        if not self.cfg.memory.enabled or self._memory is None:
            return
        try:
            self._reflect_and_save_lessons()
        except Exception as e:
            # 反思失败是可接受的降级（不影响本次对话已有的价值），仅打印警告
            from mini_agent.errors import log_exception
            log_exception(e, where='mini_agent.agent.reflection.ReflectionMixin.trigger_session_end')
            R.print_warning(f"[session-end] reflection failed: {e}")

    def _reflect_and_save_lessons(self, max_lessons: int = 5) -> int:
        """
        跑一次轻量 LLM 反思调用，基于 tool_stats + 最后若干轮 history（用
        is_turn_boundary 精确截取"用户意图轮"）生成结构化 lesson 候选并写入记忆。

        返回实际写入的 lesson 条数（供调用方/测试断言）。
        """
        from mini_agent.history.entry import is_turn_boundary

        user_turns = [
            m["content"] for m in self._history
            if is_turn_boundary(m) and isinstance(m.get("content"), str)
        ]
        if not user_turns and not self.stats.tool_stats:
            return 0  # 没有任何可反思的内容，跳过 LLM 调用

        tool_stats_lines = [
            f"- {name}: {s.get('calls', 0)} calls, {s.get('success', 0)} succeeded, {s.get('fail', 0)} failed"
            for name, s in self.stats.tool_stats.items()
        ] or ["(no tool calls this session)"]
        turns_text = "\n".join(f"- {t[:200]}" for t in user_turns[-10:]) or "(no user turns)"

        from mini_agent.prompts import pm
        prompt = pm.render(
            "user/session_reflection_request",
            tool_stats_text="\n".join(tool_stats_lines),
            turns_text=turns_text,
        )
        resp = self._llm.chat_with_retry(
            messages=[{"role": "user", "content": prompt}],
            system=pm.render("system/session_reflection"),
            tools=[],
            max_retries=3,   # 反思是锦上添花，不值得像主对话那样重试 10 次
        )
        candidates = _parse_lesson_candidates(resp.text)
        saved = 0
        for cand in candidates[:max_lessons]:
            entry = MemoryEntry(
                session_id=self._session.id,
                summary="",
                key_outcomes=[],
                tags=["lesson", "session_reflection"],
                model=self.cfg.model,
                entry_type="lesson",
                trigger=str(cand.get("trigger", ""))[:500],
                outcome=str(cand.get("outcome", ""))[:500],
                root_cause=str(cand.get("root_cause", ""))[:500],
                suggested_action=str(cand.get("suggested_action", ""))[:500],
                confidence=_clamp_confidence(cand.get("confidence", 0.5)),
                occurrence_count=1,
                source="self_reflection",
            )
            if entry.scope == "global" and self._global_memory:
                self._global_memory.add(entry)
            else:
                self._memory.add(entry)
            self._append_memory_delta(entry)
            saved += 1

        # ── [W3 / 5.5 事件驱动更新] lesson 生成是即时事件，不等 SessionEnd
        #    的批量维护路径——直接在产生的那一刻 +saved，与设计文档原话
        #    "在对应事件发生时直接 +1，不等 session 结束"一致。失败不影响
        #    已经成功写入的 lesson（self_profile 是衍生统计，不是权威数据）。
        if saved and getattr(self.cfg, "global_knowledge_enabled", True):
            try:
                from mini_agent.storage.paths import AgentPaths
                from mini_agent.perception import global_knowledge as gk
                import time as _time
                paths = AgentPaths(self.cfg.project_root)
                profile = gk.ensure_self_profile(paths)
                profile.evolution_state.lifetime_lessons_generated += saved
                profile.evolution_state.last_reflection_at = _time.time()
                gk.save_self_profile(paths, profile)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.agent')
                pass

        return saved

    # ── [W2+W3 / Stage 4-5] Workdir + Global 知识层：SessionEnd 维护路径 ─────

    def _update_workdir_knowledge_on_session_end(self) -> None:
        """
        SessionEnd hook 轻量路径（设计文档 8.2/8.3 节"三条触发路径"之一）：
          - 追加 timeline.jsonl 一条 session 概览（4.2）
          - 尝试把本次 session 关联到一个 active WorkThread（4.3 最简版本）
          - 把本次 session 各 task manifest 的 outcome.unresolved 推进
            open_threads.json（4.4）
          - 追加 activity_log.jsonl 一条全局活动记录，复用同一次 theme/
            duration 计算（5.3）
          - 更新 self_profile.json 的 operating_state（5.5）

        纯写入部分（work_index 关联、open_threads 推进、activity_log/
        self_profile 更新）无 LLM 依赖，始终执行；theme/key_outcomes 需要
        一次独立的轻量反思调用（与 _reflect_and_save_lessons 的诊断型反思
        目标不同，见 Stage 4.2 计划文档的取舍说明），调用失败时
        theme/key_outcomes 留空但仍写入 timeline 行（保留
        task_count/status/duration 等无需 LLM 的字段）。方法名沿用 W2 阶段
        命名，未改名为更通用的名字——调用方（trigger_session_end）只有一处，
        改名收益不大，保留命名稳定性。
        """
        if not getattr(self.cfg, "workdir_knowledge_enabled", True):
            return
        if not self._session:
            return

        from mini_agent.storage.paths import AgentPaths
        from mini_agent.perception import workdir_knowledge as wk

        paths = AgentPaths(self.cfg.project_root)
        session_id = self._session.id

        # ── 收集本次 session 的 task manifest（来源：磁盘上的 manifest.json，
        #    覆盖主线程内 TaskManager 已知的任务，也覆盖跨进程恢复的场景）──
        unresolved_all: list[str] = []
        task_count = 0
        try:
            tasks_root = paths.tasks_dir(session_id)
            if tasks_root.is_dir():
                for task_dir in tasks_root.iterdir():
                    manifest_path = task_dir / "manifest.json"
                    if not manifest_path.is_file():
                        continue
                    task_count += 1
                    try:
                        import json as _json
                        data = _json.loads(manifest_path.read_text(encoding="utf-8"))
                        outcome = data.get("outcome") or {}
                        unresolved_all.extend(outcome.get("unresolved", []) or [])
                    except Exception as _mini_agent_exc:
                        from mini_agent.errors import log_exception
                        log_exception(_mini_agent_exc, where='mini_agent.agent.reflection.ReflectionMixin._update_workdir_knowledge_on_session_end')
                        continue
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.agent')
            pass

        # ── 4.4：把 unresolved 推进 open_threads.json ────────────────────────
        if unresolved_all:
            try:
                wk.import_unresolved_from_manifest(paths, session_id, unresolved_all)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.agent')
                pass

        # ── 4.3：关联到 active WorkThread（轻量启发式，不新建 WorkThread）───
        related_thread = None
        try:
            relation_days = getattr(
                self.cfg.workdir_knowledge, "work_thread_relation_days", 7.0
            )
            from mini_agent.history.entry import is_turn_boundary
            first_user_turn = next(
                (m["content"] for m in self._history
                 if is_turn_boundary(m) and isinstance(m.get("content"), str)),
                "",
            )
            related_thread = wk.relate_session_to_work_thread(
                paths, session_id, first_user_turn, relation_days=relation_days,
            )
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.agent')
            pass

        # ── [主动提醒] 本次 session 看起来干了不少活，但既没有关联到已有
        #    WorkThread、也没有主动调用过 update_work_thread ────────────────
        # 不在这里直接自动创建 WorkThread（那会退化成"启发式也能自由发明
        # 工作线"，违背 relate_session_to_work_thread() 的保守取舍）；而是
        # 写一条待提醒记录，下次 session 开始时由 context_builder 读出并
        # 提示模型自己判断要不要补记——决策权仍然在模型，只是多了一次主动
        # 的"提醒"，不再完全指望模型自己想起来。
        try:
            wk_cfg = self.cfg.workdir_knowledge
            if getattr(wk_cfg, "proactive_reminder_enabled", True) and related_thread is None:
                from mini_agent.history.entry import is_turn_boundary as _is_turn_boundary
                from mini_agent.history.entry import history_contains_tool_call
                turn_count = sum(1 for m in self._history if _is_turn_boundary(m))
                reminder_duration_min = self._session_duration_minutes()
                min_duration = getattr(wk_cfg, "reminder_min_duration_minutes", 15.0)
                min_turns = getattr(wk_cfg, "reminder_min_turns", 6)
                substantial = (reminder_duration_min >= min_duration) or (turn_count >= min_turns)
                already_logged = history_contains_tool_call(self._history, "update_work_thread")
                if substantial and not already_logged:
                    wk.write_work_thread_reminder(
                        paths,
                        session_id=session_id,
                        first_user_turn=first_user_turn,
                        duration_minutes=reminder_duration_min,
                        turn_count=turn_count,
                    )
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.agent')
            pass

        # ── 4.2：timeline.jsonl 一行概览 ──────────────────────────────────
        duration_min = self._session_duration_minutes()
        theme, key_outcomes = self._reflect_timeline_summary()
        try:
            wk.append_timeline_entry(
                paths,
                session_id=session_id,
                duration_min=duration_min,
                theme=theme,
                key_outcomes=key_outcomes,
                task_count=task_count,
                status="done",
            )
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.agent')
            pass

        # ── [W3 / 5.3 + 5.5] Global 知识层 SessionEnd 维护：复用上面已经
        #    计算好的 theme/duration_min 写一行 activity_log.jsonl，避免
        #    两次遍历 session 数据（计划文档 5.3 节要求）；同时更新
        #    self_profile.json 的 operating_state（5.5 节，纯计数器更新，
        #    无 LLM 依赖）。两者独立 try/except，与 W2 部分互不阻塞。 ──
        if getattr(self.cfg, "global_knowledge_enabled", True):
            try:
                from mini_agent.perception import global_knowledge as gk
                project_id = gk.project_id_for(self.cfg.project_root)
                gk.append_activity_log(
                    paths,
                    project_id=project_id,
                    session_id=session_id,
                    theme=theme,
                    duration_min=duration_min,
                )
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.agent')
                pass
            try:
                from mini_agent.perception import global_knowledge as gk
                gk.update_self_profile_on_session_end(
                    paths,
                    active_project=str(self.cfg.project_root.resolve()),
                    tokens_used=self.stats.input_tokens + self.stats.output_tokens,
                )
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.agent')
                pass

    def _run_observability_on_session_end(self) -> None:
        """[Stage 6 / 6.3] SessionEnd 时：
        1. 把本 session 的 total_tokens / tool_count 写入 activity_log（为异常检测提供基线数据）
        2. 运行异常检测，若触发则打印警告
        写入 activity_log 的字段是对 gk.append_activity_log 的补充（后者已写 theme/duration，
        这里追加 tool_count / total_tokens 供 detect_anomalies 使用）。
        两个步骤都是观察性数据，任何异常静默降级。
        """
        if not getattr(self.cfg, "observability_enabled", True):
            return
        if not self._session:
            return
        try:
            from mini_agent.storage.paths import AgentPaths
            from mini_agent.perception.observability import detect_anomalies
            paths = AgentPaths(self.cfg.project_root)
            al_path = paths.global_activity_log

            total_tokens = self.stats.input_tokens + self.stats.output_tokens
            tool_count = getattr(self.stats, "tool_calls", 0)
            duration_min = self._session_duration_minutes()

            # 1. 把当前 session 的量化指标追加到 activity_log（追加字段，不重写已有行）
            # activity_log 条目本身由 gk.append_activity_log 写入，这里追加一条补充记录
            # 格式：单独一行 JSON，flag 字段为 "session_metrics"（与主 activity_log 行区分）
            import json as _json
            al_path.parent.mkdir(parents=True, exist_ok=True)
            from mini_agent.time_utils import now_ts, ts_to_str
            _now = now_ts()
            metrics_entry = {
                "ts":           _now,
                "ts_str":       ts_to_str(_now),
                "record_type":  "session_metrics",
                "session_id":   self._session.id,
                "tool_count":   tool_count,
                "total_tokens": total_tokens,
                "duration_min": duration_min,
            }
            with open(al_path, "a", encoding="utf-8") as f:
                f.write(_json.dumps(metrics_entry, ensure_ascii=False) + "\n")

            # 2. 异常检测（基于历史 session_metrics 记录）
            k_sigma = getattr(self.cfg.observability, "anomaly_k_sigma", 3.0)
            min_samples = getattr(self.cfg.observability, "anomaly_min_samples", 10)
            current = {
                "session_id":   self._session.id,
                "tool_count":   tool_count,
                "total_tokens": total_tokens,
                "duration_min": duration_min,
            }
            flags = detect_anomalies(al_path, current, k_sigma=k_sigma, min_samples=min_samples)
            for flag in flags:
                R.print_warning(
                    f"[anomaly] {flag.flag_type}: 当前值 {flag.value:.1f} 超出基线 "
                    f"(均值 {flag.baseline:.1f}, 阈值 {flag.threshold:.1f})"
                )
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.agent')
            pass

    def _session_duration_minutes(self) -> float:
        """从 Session.created_at（ISO 字符串，_now_iso() 格式）估算本次 session 时长（分钟）。
        解析失败时返回 0.0（不阻塞 timeline 写入）。"""
        if not self._session or not getattr(self._session, "created_at", ""):
            return 0.0
        try:
            from datetime import datetime
            created = datetime.strptime(self._session.created_at, "%Y-%m-%dT%H:%M:%S")
            now = datetime.now()
            return max(0.0, (now - created).total_seconds() / 60.0)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.agent.reflection.ReflectionMixin._session_duration_minutes')
            return 0.0

    def _reflect_timeline_summary(self) -> tuple[str, list[str]]:
        """
        独立的轻量反思调用：生成 {theme, key_outcomes}（4.2 节方案①）。

        与 _reflect_and_save_lessons 的诊断型反思（trigger/root_cause/
        suggested_action）目标不同，故不复用同一次 LLM 调用——两种反思
        目标混在一起容易互相干扰输出质量，详见 Stage 4.2 计划文档。

        没有任何用户意图轮次时跳过 LLM 调用，直接返回空概览。
        """
        from mini_agent.history.entry import is_turn_boundary

        user_turns = [
            m["content"] for m in self._history
            if is_turn_boundary(m) and isinstance(m.get("content"), str)
        ]
        if not user_turns:
            return "", []

        turns_text = "\n".join(f"- {t[:200]}" for t in user_turns[-10:])

        try:
            from mini_agent.prompts import pm
            prompt = pm.render("user/timeline_reflection_request", turns_text=turns_text)
            resp = self._llm.chat_with_retry(
                messages=[{"role": "user", "content": prompt}],
                system=pm.render("system/timeline_reflection"),
                tools=[],
                max_retries=3,
            )
            data = _parse_timeline_summary(resp.text)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.agent.reflection.ReflectionMixin._reflect_timeline_summary')
            return "", []

        theme = str(data.get("theme", ""))[:200]
        raw_outcomes = data.get("key_outcomes", []) or []
        if not isinstance(raw_outcomes, list):
            raw_outcomes = []
        key_outcomes = [str(o)[:200] for o in raw_outcomes[:5]]
        return theme, key_outcomes