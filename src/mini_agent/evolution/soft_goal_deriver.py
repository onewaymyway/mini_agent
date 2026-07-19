"""
evolution/soft_goal_deriver.py — 软目标 derive（autonomous 档位专属）

当 AutonomousLoop 处于 autonomous 档位时，每次 tick 调用 SoftGoalDeriver.derive()：
  1. 读 capability_map  → 找 confidence < CONFIDENCE_LOW 的能力条目
  2. 读 work_index      → 找 next_suggested 非空且 30 天无 active Objective 的 WorkThread
  3. 读 lesson_review   → 找高频触发 (T1+) 的 LessonGroup，尚无对应 Goal 的
  4. 三路合并去重，每次最多输出 MAX_NEW_GOALS 个新 Goal
  5. 直接写入 GoalBacklog（source="agent_derived"，priority 比用户 Goal 低一级）

节奏治理：
  - 通过 consolidation_rhythm.json 记录上次 derive 时间，最少间隔 DERIVE_INTERVAL_SECONDS
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
        self._rhythm_path = paths.workdir_dir / "consolidation_rhythm.json"
        self._rejected_path = paths.workdir_dir / "soft_goal_rejected.json"

    # ── 节奏控制 ──────────────────────────────────────────────────────────────

    def should_derive(self) -> bool:
        """是否满足 derive 条件（时间间隔 + 不超过 pending 上限）。"""
        last = self._last_derive_at()
        if time.time() - last < DERIVE_INTERVAL_SECONDS:
            return False
        return True

    def _last_derive_at(self) -> float:
        self._migrate_legacy_rhythm_file()
        try:
            data = json.loads(self._rhythm_path.read_text(encoding="utf-8"))
            return float(data.get("last_soft_goal_derive_at", 0.0))
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.soft_goal_deriver.SoftGoalDeriver._last_derive_at')
            return 0.0

    def _migrate_legacy_rhythm_file(self) -> None:
        """
        重命名兼容：本文件与 consolidation.py 共享同一份节奏状态文件，该文件在
        phase_g.py 更名为 consolidation.py 之前叫 phase_g_rhythm.json。此处补一份
        与 consolidation._migrate_legacy_rhythm_file() 相同的一次性迁移，避免两个
        调用方（本模块可能先于 consolidation.py 被调用）步调不一致。
        """
        if self._rhythm_path.exists():
            return
        legacy_path = self._paths.workdir_dir / "phase_g_rhythm.json"
        if not legacy_path.exists():
            return
        try:
            self._rhythm_path.write_text(
                legacy_path.read_text(encoding="utf-8"), encoding="utf-8"
            )
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.soft_goal_deriver.SoftGoalDeriver._migrate_legacy_rhythm_file')
            pass

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
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.soft_goal_deriver.SoftGoalDeriver._load_rejected_keys')
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

        # [方案四·谨慎推进的单场景验证] 落在"最近效果回填为负面"的域里的
        # 候选做强降权（0.15，比风险域的 0.4 更激进——这不是具身层的经验性
        # 判断，而是确凿有 baseline/post 实测数据支持的负面结论，可信度
        # 更高）。只做降权，不做拒绝，与其余三个方案同一条准则一致。
        negative_domains = self._recent_negative_outcome_domains()
        if negative_domains:
            for c in all_candidates:
                if self._domain_token_overlap(c.title, negative_domains) > 0:
                    c.urgency *= 0.15

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
            # [事件总线接入] capability 类候选在写入前已经过 ExplorationSandbox
            # 验证（见 autonomous_loop._run_capability_exploration()），
            # workthread/lesson 类候选目前没有对应的验证步骤，直接进入
            # GoalBacklog——这是第16节提到的"验证不对称"。用 needs_review
            # 标签 + 事件广播补一层轻量一致性复核（不是完整 ExplorationSandbox，
            # 成本低很多），而不是假装它们已经验证过。
            needs_review = c.source_tag in ("workthread", "lesson")
            goal = goal_backlog.add_goal(
                title=c.title,
                description=c.description,
                source="agent_derived",
                priority=c.priority,
                tags=["needs_review"] if needs_review else None,
            )
            new_goals.append(goal)
            if needs_review:
                try:
                    from mini_agent.perception import system_events as _se

                    _se.publish(
                        self._paths,
                        source="soft_goal_deriver",
                        event_type="goal.candidate_unvalidated",
                        tier="tick",
                        payload={
                            "goal_id": goal.id,
                            "title": goal.title,
                            "source_tag": c.source_tag,
                        },
                    )
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.evolution.soft_goal_deriver.SoftGoalDeriver.commit_goals')
                    pass
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

    def review_unvalidated_candidates(self, goal_backlog: "GoalBacklog") -> int:
        """
        [事件总线接入] 消费 commit_goals() 发布的 "goal.candidate_unvalidated"
        事件（tier="tick"），对带 needs_review 标签的 workthread/lesson 类
        候选做轻量一致性复核——不是完整的 ExplorationSandbox 验证，只重新
        核对"当初触发这个候选的信号现在是否还成立"：

          - workthread 类：候选是"某个 WorkThread.next_suggested 长期无
            推进"，复核时重新加载 work_index，看该 thread 是否已经在别处
            被推进（last_activity_at 已更新到 stale_cutoff 之后），如果是
            说明候选已经过时。
          - lesson 类：候选是"某个 LessonGroup 触发次数达到 T1 阈值"，
            复核时重新扫描 lesson groups，看该分组是否仍然存在且仍达标
            （可能在候选产出后、复核之前，问题已经被其他修复解决掉）。

        复核通过：移除 needs_review 标签，goal 保持 active。
        复核不通过：status 改为 "paused"（不是删除——留痕供人工判断，
        且 GoalNode 现有的 4 态枚举里 paused 语义最贴切：不是失败/完成，
        是"先别推进"），tags 换成 review_failed，progress_notes 记录原因。

        返回本次实际复核处理的候选数量（供调用方写入 activity_digest）。
        """
        try:
            from mini_agent.perception import system_events as _se
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.soft_goal_deriver.SoftGoalDeriver.review_unvalidated_candidates')
            return 0

        try:
            events = _se.poll_since(
                self._paths,
                consumer_name="goal_consistency_checker",
                tiers=["tick"],
                event_types=["goal.candidate_unvalidated"],
            )
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.soft_goal_deriver.SoftGoalDeriver.review_unvalidated_candidates')
            return 0

        if not events:
            return 0

        processed = 0
        for evt in events:
            try:
                goal_id = evt.payload.get("goal_id", "")
                source_tag = evt.payload.get("source_tag", "")
                node = goal_backlog.get(goal_id)
                if node is None or node.status != "active" or "needs_review" not in node.tags:
                    continue  # 已经被处理过/已被用户手动改状态，跳过

                still_valid, reason = self._reverify_candidate_signal(source_tag, node)
                if still_valid:
                    goal_backlog.update_fields(
                        goal_id, tags=[t for t in node.tags if t != "needs_review"],
                    )
                else:
                    goal_backlog.update_fields(
                        goal_id,
                        status="paused",
                        tags=[t for t in node.tags if t != "needs_review"] + ["review_failed"],
                        progress_notes=(node.progress_notes + f"\n[自动复核] {reason}").strip(),
                    )
                processed += 1
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.evolution.soft_goal_deriver')
                continue
        return processed

    def _reverify_candidate_signal(self, source_tag: str, node: "GoalNode") -> tuple[bool, str]:
        """返回 (是否仍然成立, 不成立时的原因说明)。未知 source_tag 一律
        判定为仍然成立（保守：不确定就不阻断，避免误伤）。"""
        if source_tag == "workthread":
            try:
                from mini_agent.perception.workdir_knowledge import load_work_index

                now = time.time()
                stale_cutoff = now - STALE_WORKTHREAD_DAYS * 86400
                threads = load_work_index(self._paths)
                for thread in threads:
                    if thread.next_suggested and thread.next_suggested[:80] == node.title:
                        # 与 _from_work_index() 用同一个字段，保持口径一致。
                        # WorkThread 现在有真正的 last_activity_at 字段
                        # （见 workdir_knowledge.py），不再需要 started_at 近似。
                        last_activity = getattr(thread, "last_activity_at", 0.0) or 0.0
                        if last_activity > stale_cutoff:
                            return False, "对应 WorkThread 已有新进展，候选目标已过时"
                        return True, ""
                return False, "对应 WorkThread 已不存在（可能已被清理或合并）"
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.evolution.soft_goal_deriver.SoftGoalDeriver._reverify_candidate_signal')
                return True, ""  # 复核本身失败，保守放行，不因为复核逻辑的异常阻断候选
        elif source_tag == "lesson":
            try:
                from mini_agent.perception.lesson_review import scan_lesson_groups

                groups = scan_lesson_groups(self._paths)
                for group in groups:
                    trigger_sample = group.entries[0].trigger if group.entries else ""
                    title = f"系统性解决：{trigger_sample[:50]}"
                    if title == node.title:
                        if group.meets_t1_threshold:
                            return True, ""
                        return False, "对应 LessonGroup 触发次数已回落到阈值以下，问题可能已缓解"
                return False, "对应 LessonGroup 已不存在（问题可能已被其他修复解决）"
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.evolution.soft_goal_deriver.SoftGoalDeriver._reverify_candidate_signal')
                return True, ""
        return True, ""

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
            from mini_agent.evolution.consolidation import load_capability_map
            entries = load_capability_map(self._paths)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.soft_goal_deriver.SoftGoalDeriver._from_unexplored_capabilities')
            return candidates

        min_calls_threshold = (
            getattr(self._cfg.autonomy, "exploration_min_calls_threshold", 2)
            if hasattr(self._cfg, "autonomy") else 2
        )
        already_explored = self._recently_explored_domains()
        sparse_tokens = self._recent_sparse_region_tokens()
        uncertainty_domains = self._recent_uncertainty_domains()  # [方案三新增]

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
            # [方案三新增] LLM 自陈的"犹豫/不确定"域重合判断，与记忆稀疏
            # 信号同构：两路证据同时命中同一个域时，加权取两者中较大值
            # （不是相乘叠加——避免两个都是弱信号时相乘后虚高），上限
            # 仍然是 1.6x，与既有"加权有封顶"哲学保持一致。
            uncertainty_overlap = self._domain_token_overlap(domain, uncertainty_domains)
            weight_factor = 1.0
            if overlap > 0:
                weight_factor = max(weight_factor, min(1.6, 1.0 + 0.2 * overlap))
            if uncertainty_overlap > 0:
                weight_factor = max(weight_factor, min(1.6, 1.0 + 0.2 * uncertainty_overlap))
            novelty *= weight_factor

            extra_hint = ""
            if overlap > 0 and uncertainty_overlap > 0:
                extra_hint = "（近期记忆检索发现该领域信息稀疏，且模型自身也表现出不确定性，信号更强）"
            elif overlap > 0:
                extra_hint = "（近期记忆检索也发现该领域信息稀疏，信号更强）"
            elif uncertainty_overlap > 0:
                extra_hint = "（近期模型对该领域任务持续表现出不确定性，信号更强）"

            candidates.append(_DeriveCandidate(
                title=f"探索未知能力：{domain}",
                description=(
                    f"capability_map 记录 {domain} 目前仅有 {total_calls} 次调用样本，"
                    f"数据量太少无法判断真实能力边界。建议主动尝试一次小型探索任务，"
                    f"以减少 agent 对自身该能力的不确定性。"
                    + extra_hint
                ),
                source_tag="capability",
                priority=15,   # 好奇心驱动，优先级略低于确定性问题
                urgency=0.0,
                novelty=novelty,
            ))
        return candidates

    def _recent_uncertainty_domains(self) -> list[str]:
        """[方案三新增] 与 _recent_sparse_region_tokens() 同构，只是订阅
        不同的 event_type（proprioception.uncertainty_sustained）。两路
        证据（记忆检索稀疏 + LLM 自陈不确定）同时命中同一个域时，novelty
        应该比只有一路命中更高——但上限仍然是 1.6x，与既有"加权有封顶"
        哲学保持一致，不引入新的封顶数字。

        失败静默降级：事件总线读取异常时返回空列表。

        注：使用独立的 consumer_name（而非复用 "soft_goal_deriver"），因为
        poll_since() 的游标是"每个 consumer_name 一个全局位置"，不是按
        event_type 分别记录——同一个消费者名同时订阅两种 event_type 会导致
        其中一次调用按过滤后的结果推进游标，可能跳过另一种事件类型里
        实际尚未读到的记录。"""
        try:
            from mini_agent.perception import system_events as _se

            events = _se.poll_since(
                self._paths,
                consumer_name="soft_goal_deriver_uncertainty",
                tiers=["tick"],
                event_types=["proprioception.uncertainty_sustained"],
            )
            return [
                e.payload.get("recent_domain_hint", "")
                for e in events if e.payload.get("recent_domain_hint")
            ]
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.soft_goal_deriver.SoftGoalDeriver._recent_uncertainty_domains')
            return []

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
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.soft_goal_deriver.SoftGoalDeriver._recent_sparse_region_tokens')
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
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.evolution.soft_goal_deriver.SoftGoalDeriver._recently_explored_domains')
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
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.soft_goal_deriver.SoftGoalDeriver._recently_explored_domains')
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
        产出过候选）。同时修复了它依赖的 consolidation.load_capability_map
        此前根本不存在的问题，见 consolidation.py 新增的 load_capability_map()。
        """
        candidates = []
        try:
            from mini_agent.evolution.consolidation import load_capability_map
            entries = load_capability_map(self._paths)
            high_risk_zones = self._recent_high_risk_zones()  # [方案一新增]
            risk_gating_enabled, risk_downweight_factor = self._risk_gating_config()
            for entry in entries:
                if entry.confidence >= CONFIDENCE_LOW:
                    continue
                if entry.total_calls < 3:
                    continue  # 样本太少，不可靠
                urgency = (CONFIDENCE_LOW - entry.confidence) * 10 + entry.total_calls * 0.1
                if (
                    risk_gating_enabled
                    and self._domain_token_overlap(entry.capability_name, high_risk_zones) > 0
                ):
                    # [方案一] 具身层近期判定这是高风险域：不阻止候选产出，
                    # 但明显降权，避免自主推导反复往一个刚出过问题的领域里冲。
                    urgency *= risk_downweight_factor
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

    def _risk_gating_config(self) -> tuple[bool, float]:
        """[方案一新增] 读取 AffordanceConfig 的风险门控开关/降权系数，
        默认值保证不改变现有行为（enabled=True, factor=0.4）。"""
        affordance_cfg = getattr(self._cfg, "affordance", None)
        enabled = getattr(affordance_cfg, "risk_gating_enabled", True)
        factor = getattr(affordance_cfg, "risk_downweight_factor", 0.4)
        return enabled, factor

    def _recent_negative_outcome_domains(self) -> list[str]:
        """[方案四新增] 通过 AgentSelfModel.recent_negative_outcome_domains()
        桥接 outcome_tracker.get_revert_candidates()。不持有跨 session 的
        AgentSelfModel 实例（SoftGoalDeriver 本身是无状态的一次性调用），
        这里就地构造一个空壳 AgentSelfModel 只是为了复用同一段桥接逻辑，
        不重复实现一遍 outcome_tracker → domain 的转换规则。

        失败静默降级：返回空列表。"""
        try:
            from mini_agent.perception.self_model import AgentSelfModel
            return AgentSelfModel().recent_negative_outcome_domains(paths=self._paths)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.soft_goal_deriver.SoftGoalDeriver._recent_negative_outcome_domains')
            return []

    def _recent_high_risk_zones(self) -> list[str]:
        """[方案一新增] 只读消费 AffordanceAnalyzer 落盘的高风险域快照。
        失败静默降级：返回空列表，不影响候选产出。"""
        try:
            from mini_agent.perception.affordance_analyzer import load_recent_high_risk_zones
            return load_recent_high_risk_zones(self._paths)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.evolution.soft_goal_deriver.SoftGoalDeriver._recent_high_risk_zones')
            return []

    def _from_work_index(self) -> list[_DeriveCandidate]:
        """
        信号 2：WorkThread.next_suggested 非空，但超过 STALE_WORKTHREAD_DAYS 天无推进。
        说明 agent 自己建议的后续工作一直没有跟进。

        [修复] 此前这里有两处与 WorkThread 真实字段对不上的问题：
          1. `thread.thread_id` 不存在（真实字段名是 `id`），构造 description
             字符串时必然 AttributeError，被外层 except 静默吞掉——信号2
             从写下来就没能真正产出过候选。
          2. `thread.last_activity_at` 当时不存在，getattr 静默回退成 0.0，
             使"是否最近有活动"的判断永远为 False（等价于每个有
             next_suggested 的 thread 都被当成"从纪元开始就没人碰过"）。
        WorkThread 现在有了真正的 `last_activity_at` 字段（`upsert_work_thread()`
        每次写入时刷新），不再需要 `started_at` 近似——一个持续被推进的
        thread，`last_activity_at` 会跟着更新，不会被误判为"长期无进展"。
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
                        f"WorkThread [{thread.id}] 建议的后续工作已搁置 "
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
                # [修复] LessonGroup.meets_t1_threshold/meets_t2_t3_threshold
                # 是 @property，此前这里当方法调用（多了一对括号），对 bool
                # 值再调用 () 必然 TypeError，被外层 except 静默吞掉——信号3
                # 从写下来就没能真正产出过候选，见
                # docs/system-events-bus-guide.md 第7节。
                if not group.meets_t1_threshold:
                    continue
                count = group.total_occurrence
                # urgency 正比于触发次数，T2/T3 额外加权
                urgency = count * 0.5
                if group.meets_t2_t3_threshold:
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
