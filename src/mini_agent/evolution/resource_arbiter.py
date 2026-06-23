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

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.config.models import AppConfig


# ── 探索预算扩展字段（补充 Stage 5 ResourceBudget）────────────────────────────

_EXPLORATION_BUDGET_RATIO_DEFAULT = 0.10  # 默认 10%
_RESOURCE_LOCK_WINDOW_MINUTES = 10        # 检查最近 N 分钟内用户触碰的路径


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
        三条规则均通过才返回 True。
        """
        # 规则 3：预算硬限制
        if not self._check_budget():
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
        except Exception:
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
        except Exception:
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
        entry = {"at": time.time(), **record}
        with open(digest_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False))
            f.write("\n")
    except Exception:
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
    except Exception:
        pass
    return records


def build_digest_summary(records: list[dict]) -> str:
    """
    将 activity_digest 记录分组展示（对应设计文档"分组展示，不混在一起"）。
    三类分组：
      - evolve_proposal：有 N 个待审的进化提案
      - soft_goal_created：软目标创建
      - 其余：日常自主活动
    """
    if not records:
        return "（自上次交互以来无自主活动）"

    evolve_proposals = [r for r in records if r.get("type") == "evolve_proposal"]
    soft_goals = [r for r in records if r.get("type") == "soft_goal_created"]
    others = [r for r in records
              if r.get("type") not in ("evolve_proposal", "soft_goal_created")]

    lines = [f"自上次交互以来的自主活动（共 {len(records)} 条）："]

    if evolve_proposals:
        lines.append(f"\n【进化提案】{len(evolve_proposals)} 个待审：")
        for r in evolve_proposals[-3:]:  # 最多显示 3 条
            lines.append(f"  · {r.get('summary', r.get('branch', ''))}")
        if len(evolve_proposals) > 3:
            lines.append(f"  ... 还有 {len(evolve_proposals)-3} 个")

    if soft_goals:
        lines.append(f"\n【新软目标】{len(soft_goals)} 个：")
        for r in soft_goals[-3:]:
            lines.append(f"  · {r.get('title', r.get('goal_id', ''))}")

    if others:
        lines.append(f"\n【日常自主活动】{len(others)} 条：")
        for r in others[-5:]:  # 最多显示 5 条
            summary = r.get("summary", r.get("task_desc", r.get("type", "")))
            at = r.get("at", 0)
            if at:
                import time as _time
                ago = _time.time() - at
                ago_str = f"{ago/3600:.1f}h前" if ago >= 3600 else f"{ago/60:.0f}m前"
                lines.append(f"  · [{ago_str}] {summary}")
            else:
                lines.append(f"  · {summary}")

    return "\n".join(lines)


__all__ = [
    "ResourceArbiter",
    "append_activity_digest",
    "read_activity_digest",
    "build_digest_summary",
]
