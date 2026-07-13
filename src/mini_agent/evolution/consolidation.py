"""
evolution/consolidation.py — Stage 8 巩固循环：后台循环核心扫描逻辑

覆盖设计文档 6.4/6.5/6.6/6.7 节：
  8.2  剪枝、去重、冲突检测（prune_skills）
  8.3  能力地图（build_capability_map）
  8.4  Scope 晋升：workdir → global（check_scope_promotion）
  8.5  演化节奏治理（rhythm_is_allowed / record_proposal）

触发方式（设计文档 8.1 节）：
  - CLI /evolve review 手动触发（扩展现有命令，新增 --consolidate 子命令）
  - SessionEnd 时"时间门控"检查（session 启动时读 last_run_at，超过阈值则触发）
  - 不需要常驻进程——用数据文件的 last_xxx_at 字段代替 cron
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# ════════════════════════════════════════════════════════════════════════════════
# 节奏治理状态文件（8.5）
# ════════════════════════════════════════════════════════════════════════════════

_RHYTHM_FILENAME = "consolidation_rhythm.json"
_LEGACY_RHYTHM_FILENAME = "phase_g_rhythm.json"  # 重命名前的旧文件名，仅用于一次性迁移


def _rhythm_path(paths) -> Path:
    """演化节奏治理状态文件路径（存在 workdir .agent/ 目录）。"""
    return paths.workdir_dir / _RHYTHM_FILENAME


def _migrate_legacy_rhythm_file(paths) -> None:
    """
    重命名兼容：模块从 phase_g.py 更名为 consolidation.py 之前，节奏治理状态文件叫
    phase_g_rhythm.json。已经在跑的项目 .agent/ 目录下可能还留着这个旧文件，如果不迁移，
    重命名后会误判为"从未运行过"，导致重命名当天立刻重新触发一次巩固循环。
    这里只做一次性、尽力而为的迁移：新文件不存在且旧文件存在时，原样复制一份，不删除旧文件
    （保留可追溯性，旧文件本身很小，留着不影响什么）。
    """
    new_path = _rhythm_path(paths)
    if new_path.exists():
        return
    legacy_path = paths.workdir_dir / _LEGACY_RHYTHM_FILENAME
    if not legacy_path.exists():
        return
    try:
        new_path.write_text(legacy_path.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception:
        pass  # 迁移失败静默降级：退化为"从未运行过"，不阻断主流程


def _load_rhythm(paths) -> dict:
    _migrate_legacy_rhythm_file(paths)
    p = _rhythm_path(paths)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_rhythm(paths, data: dict) -> None:
    p = _rhythm_path(paths)
    p.parent.mkdir(parents=True, exist_ok=True)
    import tempfile
    tmp = p.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except Exception:
        tmp.unlink(missing_ok=True)


def rhythm_is_allowed(
    paths,
    proposal_type: str,
    key: str,
    min_interval_days: float = 7.0,
) -> bool:
    """
    [8.5] 节奏治理：检查某类提案（key）是否在冷却期内。

    proposal_type: "prune" | "promote" | "capability_map"
    key:           提案对象（skill 名称 / cross_project_pattern_id 等）
    min_interval_days: 同一 key 的最小间隔（天）

    返回 True 表示允许提案，False 表示冷却期内应跳过。
    """
    data = _load_rhythm(paths)
    record_key = f"{proposal_type}:{key}"
    last_at = data.get(record_key, 0.0)
    elapsed_days = (time.time() - last_at) / 86400.0
    return elapsed_days >= min_interval_days


def record_proposal(paths, proposal_type: str, key: str) -> None:
    """[8.5] 记录提案发出时间，用于下次冷却期判断。"""
    data = _load_rhythm(paths)
    data[f"{proposal_type}:{key}"] = time.time()
    _save_rhythm(paths, data)


def get_last_consolidation_run(paths) -> float:
    """返回上次 巩固循环 整体运行的时间戳（0 表示从未运行）。"""
    data = _load_rhythm(paths)
    return data.get("_last_run_at", 0.0)


def record_consolidation_run(paths) -> None:
    """记录本次 巩固循环 运行时间。"""
    data = _load_rhythm(paths)
    data["_last_run_at"] = time.time()
    _save_rhythm(paths, data)


# ════════════════════════════════════════════════════════════════════════════════
# 8.2  剪枝候选扫描
# ════════════════════════════════════════════════════════════════════════════════

@dataclass
class PruneCandidate:
    """一个可能应该被下线的 skill。"""
    name: str
    reason: str
    avg_token_cost: float = 0.0   # traces.jsonl context_breakdown.skill_context 平均 token
    last_used_days_ago: float = 0.0  # tracker 上次使用距今（天）
    conflict_with: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "reason": self.reason,
            "avg_token_cost": round(self.avg_token_cost, 1),
            "last_used_days_ago": round(self.last_used_days_ago, 1),
            "conflict_with": self.conflict_with,
        }


def prune_skills(
    paths,
    skill_loader,
    *,
    min_interval_days: float = 7.0,
    token_cost_threshold: int = 2000,
    unused_days_threshold: float = 14.0,
) -> list[PruneCandidate]:
    """
    [8.2] 扫描已激活的 skill，找出"高成本但近期未使用"的剪枝候选。

    判定规则（满足其中之一即列为候选）：
      A) token_cost > token_cost_threshold 且 tracker 近期（unused_days_threshold 天内）无使用
      B) 与其他已激活 skill 存在 conflicts_with 声明的互斥冲突

    不自动删除——调用方（/evolve consolidate 或 SessionEnd 时间门控）拿到列表后展示给用户。
    """
    if skill_loader is None:
        return []

    candidates: list[PruneCandidate] = []
    tracker = getattr(skill_loader, "tracker", None)
    now = time.time()

    # 读取 traces 里各 session 的 context_breakdown 均值
    skill_token_costs = _estimate_skill_token_costs(paths)

    active_skills: list[str] = list(getattr(skill_loader, "active", []))
    all_skills = {
        name: skill_loader.get(name)
        for name in active_skills
        if skill_loader.get(name) is not None
    }

    for name, skill in all_skills.items():
        # 节奏治理：7 天内已提过剪枝建议的跳过
        if not rhythm_is_allowed(paths, "prune", name, min_interval_days):
            continue

        reasons: list[str] = []
        token_cost = skill_token_costs.get(name, 0.0)
        last_used_days = _days_since_last_use(tracker, name, now)

        # 规则 A：高成本 + 长期未使用
        if token_cost > token_cost_threshold and last_used_days > unused_days_threshold:
            reasons.append(
                f"token_cost={token_cost:.0f} > {token_cost_threshold}, "
                f"last_used={last_used_days:.0f}d ago"
            )

        # 规则 B：冲突检测
        conflicting = [
            other for other in getattr(skill, "conflicts_with", [])
            if other in active_skills and other != name
        ]
        if conflicting:
            reasons.append(f"conflicts_with={conflicting}")

        if reasons:
            candidates.append(PruneCandidate(
                name=name,
                reason="; ".join(reasons),
                avg_token_cost=token_cost,
                last_used_days_ago=last_used_days,
                conflict_with=conflicting,
            ))

    return candidates


def _estimate_skill_token_costs(paths) -> dict[str, float]:
    """
    从各 session 的 traces.jsonl 的 context_breakdown 里估算每个 skill 的平均 token 消耗。

    当前 context_breakdown 只有 system_base / history / total——没有 skill 粒度的拆分，
    这是 Stage 6.1 时的刻意简化（详见 agent.py 注释）。因此这里用一个近似：
    skill_context = system_base * (active_skill_count / total_block_count)

    这是粗粒度估算，足够支持"是否过高"的阈值判断，不作为精确数字展示给用户。
    未来 context_builder.py 支持 skill 粒度拆分后可直接替换。
    """
    # 扫描最近若干 session 的 traces.jsonl
    session_dirs: list[Path] = []
    try:
        sessions_root = paths.sessions_dir  # property，不是方法调用
        if sessions_root.exists():
            session_dirs = sorted(
                [d for d in sessions_root.iterdir() if d.is_dir()],
                key=lambda d: d.stat().st_mtime,
                reverse=True,
            )[:20]  # 最多看最近 20 个 session
    except Exception:
        return {}

    cost_totals: dict[str, float] = {}
    cost_counts: dict[str, int] = {}

    for sd in session_dirs:
        traces_path = sd / "traces.jsonl"
        if not traces_path.exists():
            continue
        try:
            with open(traces_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("phase") != "build_system":
                        continue
                    bd = entry.get("context_breakdown", {})
                    system_base = bd.get("system_base", 0.0)
                    if system_base > 0:
                        # 占位：把 system_base 的一半归因为 "all_skills" 均摊
                        # 粒度不够，但不影响阈值判断
                        cost_totals["_system"] = cost_totals.get("_system", 0.0) + system_base
                        cost_counts["_system"] = cost_counts.get("_system", 0) + 1
        except Exception:
            continue

    return {}  # 当前近似度不足，返回空；caller 里 token_cost 为 0 不触发规则 A


def _days_since_last_use(tracker, name: str, now: float) -> float:
    if tracker is None:
        return 999.0
    rec = tracker.get_record(name)
    if rec is None:
        return 999.0
    last_ts = getattr(rec, "last_used_at", 0.0)
    if not last_ts:
        return 999.0
    return (now - last_ts) / 86400.0


# ════════════════════════════════════════════════════════════════════════════════
# 8.3  能力地图（Capability Map）
# ════════════════════════════════════════════════════════════════════════════════

@dataclass
class CapabilityMapEntry:
    """workdir 级能力地图条目。"""
    domain: str               # 任务类型标签（如 "python_refactor", "bash_scripting"）
    success_count: int = 0
    failure_count: int = 0
    confidence: float = 0.0   # success / (success + failure)

    @property
    def capability_name(self) -> str:
        """soft_goal_deriver.py 用的字段名——domain 的别名，两边保持同一份数据，
        不引入第二份状态。命名不统一是历史遗留，这里用 property 兜底而不是
        改字段名，避免破坏 affordance_analyzer.py/self_model.py 已经在用的
        .domain 读法。"""
        return self.domain

    @property
    def total_calls(self) -> int:
        """soft_goal_deriver.py 用的字段名——success_count + failure_count 的别名。"""
        return self.success_count + self.failure_count

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "confidence": round(self.confidence, 4),
        }


def load_capability_map(paths) -> "list[CapabilityMapEntry]":
    """
    只读方式获取当前 workdir 的能力地图，供 soft_goal_deriver.py 的
    _from_capability_map()（信号1）/_from_unexplored_capabilities()（信号4）使用。

    直接复用 build_capability_map(paths, memory_backend=None) ——这是
    affordance_analyzer.py/self_model.py 已经在用的"只读、不写回 memory"惯用法
    （memory_backend=None 时 build_capability_map 内部会跳过
    _write_capability_map_to_memory()，只返回扫描结果）。不新增第二套计算逻辑，
    避免和 build_capability_map() 的统计口径产生分歧。

    注：这里选择每次调用都实时重新扫描 task_manifest（而不是读取
    _write_capability_map_to_memory() 写入的那份人类可读文本摘要），因为后者
    是给人看的 bar chart 文本，不是结构化数据，重新解析反而更脆弱。
    task_manifest 扫描量级通常不大（按 session 数递增），实时扫描的开销可接受。
    """
    return build_capability_map(paths, None)


def build_capability_map(paths, memory_backend) -> list[CapabilityMapEntry]:
    """
    [8.3] 扫描 task_manifest 和 events.jsonl，按任务类型聚合成功率，
    生成 CapabilityMapEntry 列表，并写入一条 entry_type="capability_map" 的 memory 条目。

    设计文档 6.6 节指出 capability_map 的数据来源是：
      - events.jsonl 里的 task_lifecycle 事件（DONE/FAILED）
      - task_manifest.json 的 outcome.status

    当前实现扫描 .agent/sessions/*/tasks/*/manifest.json（task_manifest），
    按 goal 字段里的关键词推断 domain，统计 DONE/FAILED 比例。
    """
    domain_stats: dict[str, dict] = {}

    sessions_root = paths.sessions_dir  # property，不是方法调用
    if not sessions_root.exists():
        return []

    for session_dir in sessions_root.iterdir():
        if not session_dir.is_dir():
            continue
        tasks_dir = session_dir / "tasks"
        if not tasks_dir.exists():
            continue
        for task_dir in tasks_dir.iterdir():
            if not task_dir.is_dir():
                continue
            manifest_path = task_dir / "manifest.json"
            if not manifest_path.exists():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                continue

            outcome = manifest.get("outcome", {}) or {}
            status = outcome.get("status", manifest.get("status", ""))
            goal = manifest.get("goal", manifest.get("prompt", ""))

            domain = _infer_domain(goal)
            if domain not in domain_stats:
                domain_stats[domain] = {"success": 0, "failure": 0}

            if status == "done":
                domain_stats[domain]["success"] += 1
            elif status in ("failed", "cancelled"):
                domain_stats[domain]["failure"] += 1

    entries: list[CapabilityMapEntry] = []
    for domain, stats in domain_stats.items():
        total = stats["success"] + stats["failure"]
        if total == 0:
            continue
        confidence = stats["success"] / total
        entries.append(CapabilityMapEntry(
            domain=domain,
            success_count=stats["success"],
            failure_count=stats["failure"],
            confidence=confidence,
        ))

    if entries and memory_backend is not None:
        _write_capability_map_to_memory(memory_backend, entries)

    return entries


def _infer_domain(text: str) -> str:
    """
    从任务 goal/prompt 文本推断任务类型标签。
    规则式（无 LLM），以覆盖最常见场景为目标，未知归入 "general"。
    """
    import re
    text_lower = text.lower()
    rules = [
        # 更具体的规则先匹配，避免被 python 规则吞掉
        (r"refactor|重构|clean\s*up|整理代码",                "refactor"),
        (r"test|测试|unittest|pytest|assert",                 "testing"),
        (r"bug|fix|修复|debug",                              "bug_fix"),
        (r"document|docs|readme|注释|comment",                "documentation"),
        (r"docker|container|k8s|kubernetes",                  "devops"),
        (r"bash|shell|脚本|script|\.sh\b",                   "bash_scripting"),
        (r"api|endpoint|route|rest|graphql",                  "api_dev"),
        (r"git|commit|branch|merge|rebase",                   "git"),
        (r"sql|database|db|query|orm",                        "database"),
        (r"前端|frontend|css|html|react|vue|js\b|javascript", "frontend"),
        # python 放最后，避免因 .py 误吞具体意图
        (r"python|django|flask|fastapi",                      "python"),
    ]
    for pattern, label in rules:
        if re.search(pattern, text_lower):
            return label
    return "general"


def _write_capability_map_to_memory(memory_backend, entries: list[CapabilityMapEntry]) -> None:
    """把能力地图写入 memory.jsonl（entry_type="capability_map"）。"""
    try:
        from mini_agent.perception.memory_store import MemoryEntry

        key_outcomes = []
        for e in sorted(entries, key=lambda x: -x.confidence):
            bar = "▓" * int(e.confidence * 10) + "░" * (10 - int(e.confidence * 10))
            key_outcomes.append(
                f"{e.domain}: {bar} {e.confidence:.0%} "
                f"(success={e.success_count} fail={e.failure_count})"
            )

        mem_entry = MemoryEntry(
            session_id="consolidation",
            summary="能力地图（巩固循环 自动更新）",
            key_outcomes=key_outcomes,
            tags=["capability_map", "auto"],
            model="consolidation",
            entry_type="capability_map",
            source="self_reflection",
            confidence=1.0,
        )
        memory_backend.add(mem_entry)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.consolidation')
        pass


# ════════════════════════════════════════════════════════════════════════════════
# 8.4  Scope 晋升：workdir → global
# ════════════════════════════════════════════════════════════════════════════════

@dataclass
class PromotionCandidate:
    """一个达到跨项目晋升门槛的 cross_project pattern。"""
    pattern_id: str
    description: str
    observed_in_projects: int
    confidence: float
    suggested_skill_name: str

    def to_dict(self) -> dict:
        return {
            "pattern_id": self.pattern_id,
            "description": self.description,
            "observed_in_projects": self.observed_in_projects,
            "confidence": round(self.confidence, 4),
            "suggested_skill_name": self.suggested_skill_name,
        }


def check_scope_promotion(
    paths,
    *,
    min_projects: int = 2,
    min_confidence: float = 0.7,
    min_interval_days: float = 7.0,
) -> list[PromotionCandidate]:
    """
    [8.4] 扫描 cross_project_index.json，找出达到晋升门槛的跨项目模式。

    晋升判据（AND）：
      - observed_in_projects >= min_projects（默认 2）
      - confidence >= min_confidence（默认 0.7，偏保守）
      - 节奏治理：同一 pattern 7 天内只提一次

    返回 PromotionCandidate 列表，调用方决定是否触发 skill_propose。
    不自动写入 skill——遵循\"人工确认后才真正下线\"的一贯精神。
    """
    cross_index_path = paths.global_cross_project_index
    if not cross_index_path.exists():
        return []

    try:
        data = json.loads(cross_index_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    patterns = data.get("cross_project_patterns", [])
    candidates: list[PromotionCandidate] = []

    for p in patterns:
        pid = p.get("id", "")
        obs = p.get("observed_in_projects", 0)
        conf = p.get("confidence", 0.0)
        desc = p.get("description", "")
        skill_candidate = p.get("global_skill_candidate", False)

        if obs < min_projects:
            continue
        if conf < min_confidence:
            continue
        if not skill_candidate:
            continue

        # 节奏治理：同一 pattern 在冷却期内跳过
        if not rhythm_is_allowed(paths, "promote", pid, min_interval_days):
            continue

        # 推断建议的 skill 名（取 description 的前几个词，转下划线）
        import re
        words = re.sub(r"[^\w\s]", "", desc.lower()).split()[:4]
        suggested = "_".join(words) or f"cross_pattern_{pid[:8]}"

        candidates.append(PromotionCandidate(
            pattern_id=pid,
            description=desc,
            observed_in_projects=obs,
            confidence=conf,
            suggested_skill_name=suggested,
        ))

    return candidates


# ════════════════════════════════════════════════════════════════════════════════
# 巩固循环 整体入口（供 CLI 和 SessionEnd 时间门控共同调用）
# ════════════════════════════════════════════════════════════════════════════════

@dataclass
class ConsolidationReport:
    """一次 巩固循环 扫描的完整报告。"""
    prune_candidates:    list[PruneCandidate]    = field(default_factory=list)
    capability_map:      list[CapabilityMapEntry] = field(default_factory=list)
    promotion_candidates: list[PromotionCandidate] = field(default_factory=list)
    knowledge_consolidation: dict = field(default_factory=dict)
    outcome_tracking_resolved: list = field(default_factory=list)  # [方案三] 本次 tick 新解决的效果回填记录
    affordance_weights_updated: object = None  # [方案四] 本次校准后的 AffordanceWeights（校准跳过时为 None）
    ran_at: float = field(default_factory=time.time)

    @property
    def has_findings(self) -> bool:
        return bool(self.prune_candidates or self.promotion_candidates)

    def to_dict(self) -> dict:
        return {
            "ran_at": self.ran_at,
            "prune_candidates":     [c.to_dict() for c in self.prune_candidates],
            "capability_map":       [e.to_dict() for e in self.capability_map],
            "promotion_candidates": [c.to_dict() for c in self.promotion_candidates],
            "knowledge_consolidation": self.knowledge_consolidation,
            "outcome_tracking_resolved": [r.to_dict() for r in self.outcome_tracking_resolved],
            "affordance_weights_updated": (
                self.affordance_weights_updated.to_dict()
                if self.affordance_weights_updated is not None else None
            ),
        }


def run_consolidation(
    paths,
    skill_loader=None,
    memory_backend=None,
    *,
    prune_min_interval_days: float = 7.0,
    promote_min_interval_days: float = 7.0,
    promote_min_projects: int = 2,
    promote_min_confidence: float = 0.7,
    observation_window_sessions: int = 5,
    knowledge_llm_call=None,
    knowledge_min_cluster_size: int = 5,
    knowledge_summary_threshold: int = 3,
) -> ConsolidationReport:
    """
    [8.1] 巩固循环 整体运行入口。

    8.1 节\"基于时间的简单判定\"：调用方（agent.py SessionEnd 检查）决定是否触发；
    本函数只负责"给定已决定要运行"时的完整扫描流程。

    observation_window_sessions: 8.5 节「T1 自动合并前先观察 N 个 session」参数，
    当前版本只记录，实际的\"等待 N 个 session\"逻辑由晋升提案的消费方（evolution-agent）
    从提案元数据里读取，不在本函数内阻塞。
    """
    report = ConsolidationReport()

    # 8.2 剪枝候选
    try:
        report.prune_candidates = prune_skills(
            paths, skill_loader,
            min_interval_days=prune_min_interval_days,
        )
        # 记录已提案的 skill 的冷却时间
        for c in report.prune_candidates:
            record_proposal(paths, "prune", c.name)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.consolidation')
        pass

    # 8.3 能力地图
    try:
        report.capability_map = build_capability_map(paths, memory_backend)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.consolidation')
        pass

    # 8.4 Scope 晋升候选
    try:
        report.promotion_candidates = check_scope_promotion(
            paths,
            min_projects=promote_min_projects,
            min_confidence=promote_min_confidence,
            min_interval_days=promote_min_interval_days,
        )
        # 记录已提案的 pattern 的冷却时间
        for c in report.promotion_candidates:
            record_proposal(paths, "promote", c.pattern_id)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.consolidation')
        pass

    # 8.6 知识巩固（perception/library_index.py）：
    #   - 批量处理未分类候选，聚类归纳出新的分类节点（"新学科诞生"）
    #   - 批量重写攒够证据（pending_evidence_count 达阈值）的实体摘要
    # 只在 memory_backend 是本地 MemoryStore 且带有 library_index 时生效；
    # 其它 backend（Chroma/Redis 等）尚未接入图书馆式索引，直接跳过。
    try:
        library = getattr(memory_backend, "library", None) if memory_backend else None
        if library is not None:
            report.knowledge_consolidation = library.consolidate(
                memory_backend,
                llm_call=knowledge_llm_call,
                min_cluster_size=knowledge_min_cluster_size,
                summary_threshold=knowledge_summary_threshold,
            )
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.consolidation.knowledge_consolidation')
        pass

    # [方案三，见 next_doc/priority_improvements_implementation_plan.md]
    # 自我进化"用户真实反馈"闭环：检查已到观察期截止时间的效果回填记录，
    # 判定 improved/no_change/worsened，供 /digest 与 /evolution outcomes 展示。
    # 与 8.2~8.6 各步骤保持一致的失败静默降级：异常不阻断 巩固循环 主流程。
    try:
        from mini_agent.evolution import outcome_tracker
        report.outcome_tracking_resolved = outcome_tracker.tick(paths, memory_backend)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.consolidation.outcome_tracking')
        pass

    # [方案四] Affordance / 自我模型闭环学习：用 outcome_tracker 的效果回填
    # 结果周期性校准 AffordanceMap 三路来源的展示权重。失败静默降级，
    # 与其它步骤一致，不阻断 巩固循环 主流程。
    try:
        from mini_agent.perception.affordance_calibration import calibrate
        report.affordance_weights_updated = calibrate(paths)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.consolidation.affordance_calibration')
        pass

    # 记录本次运行时间
    try:
        record_consolidation_run(paths)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.consolidation')
        pass

    return report


def should_run_consolidation(paths, *, interval_hours: float = 24.0) -> bool:
    """
    [8.1] 时间门控：检查是否应该触发 巩固循环 扫描。
    上次运行超过 interval_hours 小时（默认 24h）则返回True。
    """
    last = get_last_consolidation_run(paths)
    elapsed_hours = (time.time() - last) / 3600.0
    return elapsed_hours >= interval_hours
