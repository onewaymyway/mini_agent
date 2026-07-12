"""
evolution/soft_goal_deriver.py — 软目标 derive（autonomous 档位专属）

当 AutonomousLoop 处于 autonomous 档位时，每次 tick 调用 SoftGoalDeriver.derive()：
  1. 读 capability_map  → 找 confidence < CONFIDENCE_LOW 的能力条目
  2. 读 work_index      → 找 next_suggested 非空且 30 天无 active Objective 的 WorkThread
  3. 读 lesson_review   → 找高频触发 (T1+) 的 LessonGroup，尚无对应 Goal 的
  4. 三路合并去重，每次最多输出 MAX_NEW_GOALS 个新 Goal
  5. 直接写入 GoalBacklog（source="agent_derived"，priority 比用户 Goal 低一级）

节奏治理：
  - 通过 phase_g_rhythm.json 记录上次 derive 时间，最少间隔 DERIVE_INTERVAL_SECONDS
  - 若 GoalBacklog 中 agent_derived 类 active Goal 已有 MAX_PENDING_DERIVED 个，跳过

用户体验：
  - derive 出的 Goal 会在 /digest 中以"💡 Agent 建议"形式展示
  - 用户可通过 /goals accept <id> 或 /goals reject <id> 显式处理
  - reject 的 Goal 被标记为 abandoned，30 天内不会再 derive 相同主题
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.config.models import AppConfig
    from mini_agent.perception.goal_backlog import GoalBacklog, GoalNode


# ── 常量 ──────────────────────────────────────────────────────────────────────

CONFIDENCE_LOW          = 0.35   # capability_map 中低置信度阈值
STALE_WORKTHREAD_DAYS   = 30     # WorkThread next_suggested 超过此天数无进展则触发 derive
T1_THRESHOLD            = 3      # LessonGroup total_occurrence 高频门槛
DERIVE_INTERVAL_SECONDS = 21600  # 最少 6 小时 derive 一次
MAX_NEW_GOALS           = 2      # 每次 derive 最多产生新 Goal 数量
MAX_PENDING_DERIVED     = 5      # GoalBacklog 中 agent_derived active Goal 上限
REJECTED_TTL_SECONDS    = 30 * 86400  # rejected goal 的去重窗口（30 天）

# ── [方案三] 好奇心评分常量 ─────────────────────────────────────────────────
MIN_CALLS_FOR_KNOWN     = 2      # total_calls 低于此值视为"几乎未探索"（可被 cfg.autonomy 覆盖）


# ── 候选来源 ──────────────────────────────────────────────────────────────────

@dataclass
class _DeriveCandidate:
    title: str
    description: str
    source_tag: str     # "capability" | "workthread" | "lesson"
    priority: int = 20  # agent_derived Goal 的优先级（低于用户 Goal 的默认 50）
    urgency: float = 0.0  # 用于排序，越高越先出
    novelty: float = 0.0  # [方案三/好奇心评分] 信息增益评分，默认0（旧三路信号不产出，行为不变）

    def dedupe_key(self) -> str:
        """归一化 title 用于去重。"""
        s = self.title.lower().strip()
        s = re.sub(r"[^\w\s]", " ", s)
        return " ".join(sorted(s.split()))


# ── SoftGoalDeriver ───────────────────────────────────────────────────────────

class SoftGoalDeriver:
    """
    Autonomous 档位的软目标 derive 引擎。

    由 AutonomousLoop._tick_autonomous() 调用：
        deriver = SoftGoalDeriver(paths, cfg)
        if deriver.should_derive():
            new_goals = deriver.derive(goal_backlog)
    """

    def __init__(self, paths: "AgentPaths", cfg: "AppConfig") -> None:
        self._paths = paths
        self._cfg = cfg
        self._rhythm_path = paths.workdir_dir / "phase_g_rhythm.json"
        self._rejected_path = paths.workdir_dir / "soft_goal_rejected.json"

    # ── 节奏控制 ──────────────────────────────────────────────────────────────

    def should_derive(self) -> bool:
        """是否满足 derive 条件（时间间隔 + 不超过 pending 上限）。"""
        last = self._last_derive_at()
        if time.time() - last < DERIVE_INTERVAL_SECONDS:
            return False
        return True

    def _last_derive_at(self) -> float:
        try:
            data = json.loads(self._rhythm_path.read_text(encoding="utf-8"))
            return float(data.get("last_soft_goal_derive_at", 0.0))
        except Exception:
            return 0.0

    def _record_derive(self) -> None:
        try:
            data: dict = {}
            if self._rhythm_path.exists():
                data = json.loads(self._rhythm_path.read_text(encoding="utf-8"))
            data["last_soft_goal_derive_at"] = time.time()
            self._rhythm_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.soft_goal_deriver')
            pass

    # ── 已拒绝目标集 ──────────────────────────────────────────────────────────

    def _load_rejected_keys(self) -> set[str]:
        """加载用户已 reject 的 Goal dedupe_key（30 天内有效）。"""
        try:
            data = json.loads(self._rejected_path.read_text(encoding="utf-8"))
            now = time.time()
            return {
                k for k, ts in data.items()
                if now - float(ts) < REJECTED_TTL_SECONDS
            }
        except Exception:
            return set()

    def record_rejected(self, goal_title: str) -> None:
        """用户 reject 一个 Goal 后调用，30 天内不再 derive 相同主题。"""
        key = _DeriveCandidate(title=goal_title, description="", source_tag="").dedupe_key()
        try:
            data: dict = {}
            if self._rejected_path.exists():
                data = json.loads(self._rejected_path.read_text(encoding="utf-8"))
            data[key] = time.time()
            self._rejected_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.soft_goal_deriver')
            pass

    # ── 主入口 ────────────────────────────────────────────────────────────────

    def derive_candidates(
        self,
        goal_backlog: "GoalBacklog",
    ) -> "tuple[list[_DeriveCandidate], list[_DeriveCandidate]]":
        """
        分析三路信号，返回两类候选但**不写 GoalBacklog**：
          (capability_candidates, other_candidates)

        capability_candidates — source_tag="capability"，推荐经 ExplorationSandbox 验证
        other_candidates      — source_tag in ("workthread","lesson")，可直接写 Goal

        _tick_autonomous() 用此接口插入探索验证逻辑。
        """
        rejected_keys = self._load_rejected_keys()
        existing_titles = {
            _DeriveCandidate(title=g.title, description="", source_tag="").dedupe_key()
            for g in goal_backlog.active_goals()
        }

        all_candidates: list[_DeriveCandidate] = []
        all_candidates.extend(self._from_capability_map())
        all_candidates.extend(self._from_work_index())
        all_candidates.extend(self._from_lesson_review())
        all_candidates.extend(self._from_unexplored_capabilities())

        seen: set[str] = set()
        cap: list[_DeriveCandidate] = []
        other: list[_DeriveCandidate] = []

        novelty_weight = getattr(self._cfg.autonomy, "novelty_weight", 0.5) if hasattr(self._cfg, "autonomy") else 0.5
        for c in sorted(all_candidates, key=lambda x: x.urgency + novelty_weight * x.novelty, reverse=True):
            key = c.dedupe_key()
            if key in seen or key in rejected_keys or key in existing_titles:
                continue
            seen.add(key)
            (cap if c.source_tag == "capability" else other).append(c)

        return cap, other

    def commit_goals(
        self,
        candidates: "list[_DeriveCandidate]",
        goal_backlog: "GoalBacklog",
        max_new: int = MAX_NEW_GOALS,
    ) -> "list[GoalNode]":
        """将候选写入 GoalBacklog，不超过 MAX_PENDING_DERIVED 上限，返回新增节点。"""
        existing_derived = [
            g for g in goal_backlog.active_goals()
            if g.source == "agent_derived" and g.status == "active"
        ]
        slots = MAX_PENDING_DERIVED - len(existing_derived)
        if slots <= 0:
            return []
        new_goals = []
        for c in candidates[:min(max_new, slots)]:
            goal = goal_backlog.add_goal(
                title=c.title,
                description=c.description,
                source="agent_derived",
                priority=c.priority,
            )
            new_goals.append(goal)
        return new_goals

    def derive(self, goal_backlog: "GoalBacklog") -> "list[GoalNode]":
        """
        向后兼容入口：直接 derive 写 GoalBacklog，不区分 capability 来源。
        _tick_autonomous() 使用 derive_candidates() + ExplorationSandbox + commit_goals()。
        """
        cap_c, other_c = self.derive_candidates(goal_backlog)
        novelty_weight = getattr(self._cfg.autonomy, "novelty_weight", 0.5) if hasattr(self._cfg, "autonomy") else 0.5
        all_c = sorted(cap_c + other_c, key=lambda x: x.urgency + novelty_weight * x.novelty, reverse=True)
        new_goals = self.commit_goals(all_c, goal_backlog)
        if new_goals:
            self._record_derive()
        return new_goals

    # ── 三路信号采集 ──────────────────────────────────────────────────────────

    def _from_unexplored_capabilities(self) -> list[_DeriveCandidate]:
        """
        信号 4（方案三新增）：capability_map 里 total_calls 极少（< 阈值，默认2）
        的能力条目——"几乎没试过"，而不是"试过，效果不好"。

        与 _from_capability_map() 的区别：
          _from_capability_map — "试过，效果不好" → urgency 来自"确定性的失败信号"
          _from_unexplored_capabilities — "几乎没试过" → novelty 来自"信息增益"，
            即"探索这个领域能在多大程度上减少 agent 对自己能力的不确定性"

        失败静默降级：任何异常直接返回空列表，不影响其余三路信号。
        """
        candidates = []
        try:
            from mini_agent.evolution.phase_g import load_capability_map
            entries = load_capability_map(self._paths)
        except Exception:
            return candidates

        min_calls_threshold = (
            getattr(self._cfg.autonomy, "exploration_min_calls_threshold", 2)
            if hasattr(self._cfg, "autonomy") else 2
        )
        already_explored = self._recently_explored_domains()
        sparse_tokens = self._recent_sparse_region_tokens()

        for entry in entries:
            total_calls = getattr(entry, "total_calls", 0)
            if total_calls >= min_calls_threshold:
                continue  # 已经有一定数据量，不算"几乎未探索"

            domain = getattr(entry, "capability_name", None) or getattr(entry, "domain", "")
            if not domain:
                continue

            novelty = 1.0 / (1 + total_calls)
            if domain in already_explored:
                novelty *= 0.1  # 最近已探索过，大幅降权避免重复探索

            # [事件总线接入] 如果这个能力领域最近被记忆检索标记为"稀疏"
            # （即真实发生过查询、但记忆里几乎没有相关条目），说明这不只是
            # "capability_map 里没数据"的静态推断，而是有实际信号支持的
            # 空白区，novelty 应该获得额外加权。加权幅度有上限（最多 1.6x），
            # 避免稀疏信号完全压过 total_calls 本身的基础判断。
            overlap = self._domain_token_overlap(domain, sparse_tokens)
            if overlap > 0:
                novelty *= min(1.6, 1.0 + 0.2 * overlap)

            candidates.append(_DeriveCandidate(
                title=f"探索未知能力：{domain}",
                description=(
                    f"capability_map 记录 {domain} 目前仅有 {total_calls} 次调用样本，"
                    f"数据量太少无法判断真实能力边界。建议主动尝试一次小型探索任务，"
                    f"以减少 agent 对自身该能力的不确定性。"
                    + ("（近期记忆检索也发现该领域信息稀疏，信号更强）" if overlap > 0 else "")
                ),
                source_tag="capability",
                priority=15,   # 好奇心驱动，优先级略低于确定性问题
                urgency=0.0,
                novelty=novelty,
            ))
        return candidates

    def _recent_sparse_region_tokens(self) -> list[str]:
        """
        [事件总线接入] 读取 hybrid_memory_backend 发布的
        "memory.sparse_region_detected" 事件（tier="tick"，与本类的调用节奏
        天然匹配），汇总最近一批稀疏 query 的 token，用于给未探索能力的
        novelty 打分做加权（见 _domain_token_overlap()）。

        失败静默降级：事件总线读取异常/未开启 embedding 检索（不会有这类
        事件）时返回空列表，novelty 计算退化为改动前的纯 total_calls 逻辑。
        """
        try:
            from mini_agent.perception import system_events as _se

            events = _se.poll_since(
                self._paths,
                consumer_name="soft_goal_deriver",
                tiers=["tick"],
                event_types=["memory.sparse_region_detected"],
            )
            tokens: list[str] = []
            for evt in events:
                tokens.extend(evt.payload.get("query_tokens") or [])
            return tokens
        except Exception:
            return []

    @staticmethod
    def _domain_token_overlap(domain: str, sparse_tokens: list[str]) -> int:
        """domain 名称与稀疏 query token 集合的重合度（简单计数，不追求精确
        分词一致性——domain 通常是短语级的 capability_name，用子串包含判断
        比再跑一遍完整分词器更稳健，也不需要引入额外依赖）。"""
        if not sparse_tokens:
            return 0
        domain_lower = domain.lower()
        return sum(1 for tok in sparse_tokens if tok and tok.lower() in domain_lower)

    def _recently_explored_domains(self, cooldown_days: Optional[float] = None) -> set[str]:
        """
        读取 activity_digest.jsonl 中最近 cooldown_days 天内的
        type="exploration_result" 记录，返回其 capability_id 集合，
        用于 novelty 打分时对"最近已探索过"的领域降权。
        失败静默降级：返回空集合。
        """
        if cooldown_days is None:
            cooldown_days = (
                getattr(self._cfg.autonomy, "already_explored_cooldown_days", 30.0)
                if hasattr(self._cfg, "autonomy") else 30.0
            )
        try:
            import json
            digest_path = self._paths.workdir_dir / "activity_digest.jsonl"
            if not digest_path.exists():
                return set()
            cutoff = time.time() - cooldown_days * 86400
            domains: set[str] = set()
            for line in digest_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    continue
                if data.get("type") != "exploration_result":
                    continue
                at = data.get("at", 0.0)
                if at and at < cutoff:
                    continue
                cap_id = data.get("capability_id")
                if cap_id:
                    domains.add(cap_id)
            return domains
        except Exception:
            return set()

    def _from_capability_map(self) -> list[_DeriveCandidate]:
        """
        信号 1：capability_map 中 confidence < CONFIDENCE_LOW 的条目。
        说明 agent 在该能力上经常失败，有必要主动练习/改进。

        [修复记录] 这个方法此前缺少独立的 def 头，代码被误拼接进
        _recently_explored_domains() 函数体末尾（return/except 之后的
        不可达死代码），导致 self._from_capability_map() 在 derive_candidates()
        里调用时必然抛 AttributeError（被 autonomous_loop.py 的外层
        except Exception 兜住、写入 error.jsonl，不会崩溃但也从未真正
        产出过候选）。同时修复了它依赖的 phase_g.load_capability_map
        此前根本不存在的问题，见 phase_g.py 新增的 load_capability_map()。
        """
        candidates = []
        try:
            from mini_agent.evolution.phase_g import load_capability_map
            entries = load_capability_map(self._paths)
            for entry in entries:
                if entry.confidence >= CONFIDENCE_LOW:
                    continue
                if entry.total_calls < 3:
                    continue  # 样本太少，不可靠
                urgency = (CONFIDENCE_LOW - entry.confidence) * 10 + entry.total_calls * 0.1
                candidates.append(_DeriveCandidate(
                    title=f"改善 {entry.capability_name} 的执行可靠性",
                    description=(
                        f"capability_map 记录：{entry.capability_name} 的成功率仅 "
                        f"{entry.confidence:.0%}（{entry.success_count}/{entry.total_calls} 次）。"
                        f"分析失败原因并改进相关工具使用或前置检查。"
                    ),
                    source_tag="capability",
                    priority=25,
                    urgency=urgency,
                ))
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.soft_goal_deriver')
            pass
        return candidates

    def _from_work_index(self) -> list[_DeriveCandidate]:
        """
        信号 2：WorkThread.next_suggested 非空，但超过 STALE_WORKTHREAD_DAYS 天无推进。
        说明 agent 自己建议的后续工作一直没有跟进。
        """
        candidates = []
        try:
            from mini_agent.perception.workdir_knowledge import load_work_index
            threads = load_work_index(self._paths)
            now = time.time()
            stale_cutoff = now - STALE_WORKTHREAD_DAYS * 86400
            for thread in threads:
                if not thread.next_suggested:
                    continue
                last_activity = getattr(thread, "last_activity_at", 0.0) or 0.0
                if last_activity > stale_cutoff:
                    continue  # 最近有活动，不触发
                stale_days = (now - last_activity) / 86400
                urgency = min(stale_days / STALE_WORKTHREAD_DAYS, 3.0)
                candidates.append(_DeriveCandidate(
                    title=thread.next_suggested[:80],
                    description=(
                        f"WorkThread [{thread.thread_id}] 建议的后续工作已搁置 "
                        f"{stale_days:.0f} 天：{thread.next_suggested}"
                    ),
                    source_tag="workthread",
                    priority=20,
                    urgency=urgency,
                ))
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.soft_goal_deriver')
            pass
        return candidates

    def _from_lesson_review(self) -> list[_DeriveCandidate]:
        """
        信号 3：T1+ 高频 LessonGroup，说明某类错误模式反复出现，
        应当主动 derive 一个改进 Goal。
        """
        candidates = []
        try:
            from mini_agent.perception.lesson_review import scan_lesson_groups
            groups = scan_lesson_groups(self._paths)
            for group in groups:
                if not group.meets_t1_threshold():
                    continue
                count = group.total_occurrence
                # urgency 正比于触发次数，T2/T3 额外加权
                urgency = count * 0.5
                if group.meets_t2_t3_threshold():
                    urgency *= 1.5
                # 从 trigger 文本提取主题关键词作为 Goal 标题
                trigger_sample = group.entries[0].trigger if group.entries else "未知触发"
                title = f"系统性解决：{trigger_sample[:50]}"
                candidates.append(_DeriveCandidate(
                    title=title,
                    description=(
                        f"LessonGroup 高频触发（{count} 次，"
                        f"{len(group.session_ids)} 个 session）。"
                        f"触发样例：{trigger_sample[:100]}。"
                        f"建议分析根因并修复相关工具调用或流程。"
                    ),
                    source_tag="lesson",
                    priority=30,  # lesson 来源比其他信号优先级略高
                    urgency=urgency,
                ))
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.soft_goal_deriver')
            pass
        return candidates


__all__ = ["SoftGoalDeriver"]
