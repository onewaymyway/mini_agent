"""
evolution/resource_arbiter.py — Stage 9 资源仲裁（第八节）

实现设计文档 7.5 节三条仲裁规则：
1. 用户优先：自主任务执行期间收到用户消息时，自主任务暂停（PAUSED 状态）
2. 资源锁：提交自主任务前检查用户最近触碰的路径是否重叠
3. 预算硬限制：used_today < daily_token_budget 才允许自主执行

同时管理探索预算（第八节补充）：
  resource_budget 新增 exploration_budget_ratio（默认 10%）
  used_today 拆分为 used_today_goals + used_today_exploration 两个计数器

降级路径：tracing 未开启时，资源锁退化为"保守地一律视为重叠"（宁可错误暂停，
不可错误覆盖用户文件）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional, TYPE_CHECKING
from mini_agent.time_utils import ts_to_str

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.config.models import AppConfig


# ── 探索预算扩展字段（补充 Stage 5 ResourceBudget）────────────────────────────

_EXPLORATION_BUDGET_RATIO_DEFAULT = 0.10  # 默认 10%
_RESOURCE_LOCK_WINDOW_MINUTES = 10        # 检查最近 N 分钟内用户触碰的路径
_FRUSTRATION_SNAPSHOT_STALE_MINUTES = 10  # proprioception 快照超过此时长视为过期，不阻塞


class ResourceArbiter:
    """
    自主任务的资源仲裁器。
    由 AutonomousLoop._tick_maintenance() 在提交任务前调用。
    """

    def __init__(self, paths: "AgentPaths", cfg: "AppConfig") -> None:
        self._paths = paths
        self._cfg = cfg

    # ── 主仲裁入口 ────────────────────────────────────────────────────────────

    def can_run_autonomous(self) -> bool:
        """
        综合判断是否可以提交自主任务。
        四条规则均通过才返回 True。
        """
        # 规则 3：预算硬限制
        if not self._check_budget():
            return False

        # 规则 4：本体感知信号（B1 → Stage 9 信号桥接）——一个正在反复受挫的
        # Agent 不应该同时还在后台跑高置信度要求的自主探索。
        if not self._check_frustration():
            return False

        # 规则 5：[方案二新增] 用户在场信号（BehaviorContext → Stage 9 信号
        # 桥接）——用户当前明显活跃切换时，收敛自主任务，避免抢资源/写冲突。
        if not self._check_user_presence():
            return False

        return True

    def can_run_exploration(self) -> bool:
        """判断探索预算是否还有余量。"""
        return self._check_exploration_budget()

    def check_path_conflict(self, task_paths: list[str]) -> bool:
        """
        规则 2：检查 task_paths 与最近用户触碰路径是否重叠。
        返回 True 表示有冲突（应暂停/跳过自主任务）。
        降级：tracing 未开启时一律返回 True（保守）。
        """
        recent = self._recent_user_touched_paths()
        if recent is None:
            # tracing 未开启，保守地认为有冲突
            return True
        task_set = {str(Path(p).resolve()) for p in task_paths}
        return bool(task_set & recent)

    # ── 规则实现 ──────────────────────────────────────────────────────────────

    def _check_budget(self) -> bool:
        """规则 3：used_today < daily_token_budget。"""
        try:
            from mini_agent.perception.global_knowledge import load_self_profile
            profile = load_self_profile(self._paths)
            if not profile:
                return True  # 读取失败时不阻塞
            rb = profile.resource_budget
            used = rb.used_today
            budget = rb.daily_token_budget
            if budget <= 0:
                return True  # 无限制
            return used < budget
        except Exception:
            return True

    def _check_frustration(self) -> bool:
        """
        规则 4：读取 agent.py 写入的 proprioception_snapshot.json（B1 → Stage 9
        信号桥接，见 resource_arbiter.py 模块 docstring 之外的设计说明）。

        - 快照不存在 / 读取失败：视为当前没有可用的本体感知信号，不阻塞（与
          规则 3 predicate 一贯的"读取失败不阻塞"风格一致）。
        - 快照过旧（超过 _FRUSTRATION_SNAPSHOT_STALE_MINUTES 分钟没更新，说明
          近期没有活跃 session 在跑）：同样不阻塞，避免用一个过期信号长期卡住
          自主任务。
        - frustration 达到阈值：返回 False（阻塞本次自主任务提交）。
        """
        try:
            snapshot_path = self._paths.proprioception_snapshot
            if not snapshot_path.exists():
                return True
            data = json.loads(snapshot_path.read_text(encoding="utf-8"))
            updated_at = float(data.get("updated_at", 0))
            if time.time() - updated_at > _FRUSTRATION_SNAPSHOT_STALE_MINUTES * 60:
                return True
            threshold = getattr(
                getattr(self._cfg, "proprioception", None),
                "frustration_threshold",
                0.5,
            )
            frustration = float(data.get("frustration", 0.0))
            return frustration < threshold
        except Exception:
            return True

    def _check_user_presence(self) -> bool:
        """
        规则 5（方案二新增）：用户当前明显活跃（近期有应用切换）时，收敛
        自主任务，避免和用户抢资源/写冲突；用户 idle 或信号缺失时不阻塞
        （保守：不确定就不阻断，behavior 采集本身就是可选组件，缺失是
        常态而非异常）。

        双开关哲学，与 affordance.use_behavior_context 保持一致：默认
        autonomy.behavior_gating_enabled=False，关闭时本方法恒真，
        can_run_autonomous() 行为与改动前完全一致。
        """
        gating_cfg = getattr(self._cfg, "autonomy", None)
        if not gating_cfg or not getattr(gating_cfg, "behavior_gating_enabled", False):
            return True
        try:
            from mini_agent.perception.affordance_analyzer import load_behavior_context
            # 短窗口：只关心"刚刚"，与 AffordanceAnalyzer 默认的 30 分钟
            # 观察窗口不同——自主调度门控关心的是"此刻是否该让路"，
            # 用更短的窗口能更快感知到用户已经离开/恢复空闲。
            ctx = load_behavior_context(self._cfg, window_minutes=5)
            if ctx is None:
                return True  # 信号缺失，不阻断
            threshold = getattr(gating_cfg, "behavior_gating_switch_threshold", 3)
            if ctx.is_actively_engaged and ctx.context_switch_count >= threshold:
                return False  # 用户明显在忙碌切换，暂缓自主任务
            return True
        except Exception:
            return True  # 读取失败保守放行

    def _check_exploration_budget(self) -> bool:
        """探索预算：used_today_exploration < exploration_budget（daily_budget * ratio）。"""
        try:
            from mini_agent.perception.global_knowledge import load_self_profile
            profile = load_self_profile(self._paths)
            if not profile:
                return True
            rb = profile.resource_budget
            total = rb.daily_token_budget
            ratio = getattr(rb, "exploration_budget_ratio", _EXPLORATION_BUDGET_RATIO_DEFAULT)
            exploration_budget = int(total * ratio)
            used_exploration = getattr(rb, "used_today_exploration", 0)
            if exploration_budget <= 0:
                return False
            return used_exploration < exploration_budget
        except Exception:
            return True

    def _recent_user_touched_paths(
        self,
        window_minutes: float = _RESOURCE_LOCK_WINDOW_MINUTES,
    ) -> Optional[set[str]]:
        """
        从 Stage 6 traces.jsonl 提取最近 window_minutes 内用户触碰的文件路径。
        tracing 未开启时返回 None（调用方应保守处理）。
        """
        try:
            # 查找最近的 session traces
            sessions_dir = self._paths.sessions_dir if hasattr(self._paths, "sessions_dir") else None
            if sessions_dir is None:
                # 尝试从 workdir 目录推断
                sessions_dir = self._paths.workdir_dir / "sessions"

            if not sessions_dir.exists():
                return None

            cutoff = time.time() - window_minutes * 60
            touched: set[str] = set()

            # 遍历 session 目录，查找最近的 traces.jsonl
            for session_dir in sorted(sessions_dir.iterdir(), reverse=True)[:5]:
                traces_file = session_dir / "traces.jsonl"
                if not traces_file.exists():
                    continue
                paths = self._extract_paths_from_traces(traces_file, cutoff, "user")
                touched.update(paths)

            return touched
        except Exception:
            return None

    def _extract_paths_from_traces(
        self, traces_file: Path, cutoff: float, initiator_filter: str
    ) -> set[str]:
        """从 traces.jsonl 提取指定 initiator 触碰的文件路径。"""
        paths: set[str] = set()
        _PATH_TOOLS = {"read_file", "write_file", "patch_file", "bash", "grep"}
        try:
            with open(traces_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if rec.get("ts", 0) < cutoff:
                        continue
                    if rec.get("initiator", "user") != initiator_filter:
                        continue
                    tool = rec.get("tool_name", "")
                    if tool not in _PATH_TOOLS:
                        continue
                    tool_input = rec.get("tool_input", {})
                    if isinstance(tool_input, dict):
                        for key in ("path", "file_path", "filepath"):
                            val = tool_input.get(key)
                            if val and isinstance(val, str):
                                try:
                                    paths.add(str(Path(val).resolve()))
                                except Exception:
                                    paths.add(val)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.resource_arbiter')
            pass
        return paths

    def record_autonomous_token_usage(self, tokens: int, usage_type: str = "goals") -> None:
        """
        记录自主任务的 token 用量（与 update_token_usage 分开计数）。
        usage_type: "goals"（目标执行）| "exploration"（探索实验）
        """
        try:
            from mini_agent.perception.global_knowledge import (
                load_self_profile, save_self_profile,
            )
            profile = load_self_profile(self._paths)
            if not profile:
                return
            rb = profile.resource_budget
            if usage_type == "exploration":
                current = getattr(rb, "used_today_exploration", 0)
                rb.__dict__["used_today_exploration"] = current + max(0, tokens)
            else:
                current = getattr(rb, "used_today_goals", 0)
                rb.__dict__["used_today_goals"] = current + max(0, tokens)
            # 也累加到 used_today（总计数）
            rb.used_today += max(0, tokens)
            save_self_profile(self._paths, profile)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.resource_arbiter')
            pass


# ── activity_digest.jsonl 辅助 ────────────────────────────────────────────────

def append_activity_digest(paths: "AgentPaths", record: dict) -> None:
    """
    向 activity_digest.jsonl 追加一条记录。
    与 activity_log.jsonl（Stage 5，粒度=session）不同：
    这里粒度=自主行为（task/proposal/goal）。
    """
    try:
        digest_path = paths.workdir_dir / "activity_digest.jsonl"
        digest_path.parent.mkdir(parents=True, exist_ok=True)
        _now = time.time()
        entry = {"at": _now, "at_str": ts_to_str(_now), **record}
        with open(digest_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False))
            f.write("\n")
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.resource_arbiter')
        pass


def read_activity_digest(
    paths: "AgentPaths",
    since_ts: Optional[float] = None,
) -> list[dict]:
    """读取 activity_digest.jsonl（可按时间戳过滤）。"""
    digest_path = paths.workdir_dir / "activity_digest.jsonl"
    if not digest_path.exists():
        return []
    records: list[dict] = []
    try:
        with open(digest_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if since_ts is None or rec.get("at", 0) >= since_ts:
                        records.append(rec)
                except Exception:
                    continue
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.resource_arbiter')
        pass
    return records


def build_digest_summary(records: list[dict]) -> str:
    """
    将 activity_digest 记录分四组展示：

      【Objective 进展】  — objective_started / objective_completed / objective_failed
      【Cron 执行记录】  — cron_run
      【探索实验结果】   — exploration_result
      【Agent 建议目标】 — soft_goal_created（附 accept/reject 快捷指令）
    """
    if not records:
        return "（自上次交互以来无自主活动）"

    import time as _time

    def _ago(ts: float) -> str:
        if not ts:
            return ""
        delta = _time.time() - ts
        if delta < 60:
            return "刚刚"
        if delta < 3600:
            return f"{delta/60:.0f}m前"
        if delta < 86400:
            return f"{delta/3600:.1f}h前"
        return f"{delta/86400:.1f}d前"

    # 分组
    obj_records   = [r for r in records if r.get("type", "").startswith("objective_")]
    cron_records  = [r for r in records if r.get("type") == "cron_run"]
    explore_records = [r for r in records if r.get("type") == "exploration_result"]
    goal_records  = [r for r in records if r.get("type") == "soft_goal_created"]
    evolve_records = [r for r in records if r.get("type") == "evolve_proposal"]
    other_records = [r for r in records if r.get("type", "") not in (
        "objective_started", "objective_completed", "objective_failed",
        "cron_run", "exploration_result", "soft_goal_created", "evolve_proposal",
    )]

    # 将 obj_records 按 objective_id 折叠
    obj_by_id: dict[str, list[dict]] = {}
    for r in obj_records:
        oid = r.get("objective_id") or r.get("execution_id", "?")
        obj_by_id.setdefault(oid, []).append(r)

    total = len(records)
    lines = [f"自上次交互以来的自主活动（{total} 条，最近 24h）："]

    # ── Objective 进展 ─────────────────────────────────────────────────────────
    if obj_by_id:
        lines.append(f"\n【Objective 进展】")
        for oid, recs in list(obj_by_id.items())[:6]:
            # 找最新记录
            latest = max(recs, key=lambda r: r.get("at", 0))
            rtype = latest.get("type", "")
            title = latest.get("title", oid)
            ago = _ago(latest.get("at", 0))

            if rtype == "objective_completed":
                steps = latest.get("steps", "?")
                dur = latest.get("duration", 0)
                dur_str = f"，用时 {dur/60:.0f}m" if dur > 60 else ""
                lines.append(f"  ✅ {title}（{steps} 步完成{dur_str}）[{ago}]")
            elif rtype == "objective_failed":
                reason = latest.get("reason", "")
                lines.append(f"  ✗  {title} — 执行失败：{reason[:60]} [{ago}]")
                lines.append(f"     /goals progress <id> <备注> 后可重新激活")
            elif rtype == "objective_started":
                lines.append(f"  ●  {title} — 已启动 [{ago}]")
            else:
                lines.append(f"  ·  {title} [{ago}]")

    # ── Cron 执行记录 ──────────────────────────────────────────────────────────
    if cron_records:
        lines.append(f"\n【Cron 执行记录】")
        for r in cron_records[-6:]:
            job_id   = r.get("job_id", "?")
            job_name = r.get("job_name", job_id)
            summary  = r.get("summary", "")
            ago      = _ago(r.get("at", 0))
            detail   = f" — {summary[:60]}" if summary and summary != f"Cron job 触发：{job_id}" else ""
            lines.append(f"  ✓ {job_name}{detail} [{ago}]")
        if len(cron_records) > 6:
            lines.append(f"  ... 还有 {len(cron_records)-6} 条")

    # ── 探索实验结果 ───────────────────────────────────────────────────────────
    if explore_records:
        lines.append(f"\n【探索实验结果】")
        for r in explore_records[-4:]:
            ok      = r.get("success", False)
            goal    = r.get("goal", "")[:60]
            finding = r.get("finding", "")[:80]
            tokens  = r.get("tokens_used", 0)
            ago     = _ago(r.get("at", 0))
            icon    = "✅" if ok else "✗ "
            token_str = f"，{tokens} tokens" if tokens else ""
            lines.append(f"  {icon} {goal} [{ago}{token_str}]")
            if finding:
                lines.append(f"     → {finding}")
            skill_id = r.get("proposed_skill_id")
            if skill_id:
                lines.append(f"     → 已生成技能提案：{skill_id}（/evolve review 查看）")

    # ── Agent 建议目标（含 accept/reject 快捷指令）─────────────────────────────
    if goal_records:
        lines.append(f"\n【💡 Agent 建议目标】")
        for r in goal_records[-4:]:
            goal_id = r.get("goal_id", "?")
            title   = r.get("title", goal_id)
            summary = r.get("summary", "")
            ago     = _ago(r.get("at", 0))
            # 来源说明：从 summary 中提取（例如 "来自 capability_map（成功率 28%）"）
            source_note = ""
            if " — " in summary:
                source_note = " — " + summary.split(" — ", 1)[-1]
            lines.append(f"  💡 \"{title}\"{source_note} [{ago}]")
            lines.append(f"     /goals accept {goal_id}  接受  |  /goals reject {goal_id}  拒绝（30天去重）")

    # ── 进化提案 ────────────────────────────────────────────────────────────────
    if evolve_records:
        lines.append(f"\n【进化提案】{len(evolve_records)} 个待审：")
        for r in evolve_records[-3:]:
            lines.append(f"  · {r.get('summary', r.get('branch', ''))}")
        if len(evolve_records) > 3:
            lines.append(f"  ... 还有 {len(evolve_records)-3} 个")
        lines.append("  /evolve review 查看并审核")

    # ── 其余活动 ────────────────────────────────────────────────────────────────
    if other_records:
        lines.append(f"\n【其他活动】{len(other_records)} 条：")
        for r in other_records[-4:]:
            summary = r.get("summary") or r.get("task_desc") or r.get("type", "")
            ago = _ago(r.get("at", 0))
            lines.append(f"  · [{ago}] {summary[:80]}")

    return "\n".join(lines)


__all__ = [
    "ResourceArbiter",
    "append_activity_digest",
    "read_activity_digest",
    "build_digest_summary",
]
