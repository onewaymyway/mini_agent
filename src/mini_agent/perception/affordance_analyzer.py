"""
perception/affordance_analyzer.py — 余裕感知层（具身改进 v3 B4）

具身来源：Gibson 的余裕理论（affordance）——生物感知到的不是环境的客观属性，
而是环境相对于自身能力提供的"行动可能性"。一段河流对鱼而言意味着"可以游泳"，
对人而言意味着"需要绕路或架桥"——同一个环境，因为感知者的能力不同，呈现出
完全不同的行动机会集合。

当前 ProjectScanner（perception/project_scanner.py）生成的是"这里有什么"的
描述性快照（语言、依赖、目录树），停留在客观属性层面。AffordanceAnalyzer 在
此之上做一层交叉分析，回答"这个环境对*现在的我*意味着哪些值得优先关注的
行动机会"——综合 open_threads（已知待解决问题）、capability_map（能力边界，
哪些领域置信度低/历史失败多）、lesson memory（高风险操作的历史教训），
生成一份排序后的"当前最值得关注"清单。

架构归属（对应 v3 文档 §四 B4 + §九 设计原则 2）：
  - 与 ProprioceptionModule（B1，"我现在感觉如何"）不同，AffordanceMap 回答
    的是"环境现在对我意味着什么"——一个是内感觉，一个是外感知，互补但不同源。
  - 在 session 开始时构建一次（而非每轮 turn 重新计算），因为它依赖的输入
    （open_threads / capability_map / lesson memory）都是慢变量，没有必要
    每轮重算；构建成本也不适合放在请求路径的热路径上。
  - 不引入新的全局单例：AffordanceAnalyzer 本身无状态（纯函数式 analyze()），
    调用方（SessionAgentPool._build_session_cfg() 或 Agent 初始化路径）负责
    在 session 构造时调用一次，把结果文本拼进 system_extra。
  - 只生成"建议关注什么"，不触发任何动作——这是感知层而不是决策层，
    决策权仍然在 LLM 手里（呼应 v3 §九"保留人类控制权"原则的延伸：
    Agent 自身的自主决策也不应该被感知层直接代劳）。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.perception.memory_store import MemoryEntry
    from mini_agent.perception.workdir_knowledge import OpenThread
    from mini_agent.storage.paths import AgentPaths


# 高风险关键词：lesson body/trigger 中出现这些词时，将对应 domain/动作
# 标记为"高风险区域"。规则式判断，不依赖 LLM——与 consolidation.py::_infer_domain
# 同样的设计取舍：可解释、零额外调用成本，覆盖最常见场景即可。
_RISK_KEYWORDS = (
    "失败", "出错", "崩溃", "丢失", "误删", "回退", "revert",
    "fail", "error", "crash", "delete", "破坏", "danger",
)

# capability_map 置信度低于该阈值视为"能力盲区"
_LOW_CONFIDENCE_THRESHOLD = 0.5
# open_thread 优先级映射到排序分数
_PRIORITY_SCORE = {"high": 3, "medium": 2, "low": 1}


@dataclass
class AffordanceMap:
    """当前环境为 Agent 提供的行动可能性地图（session 开始时构建一次）。"""

    known_issues: list[str] = field(default_factory=list)       # open_threads 中的已知问题标题
    unexplored_areas: list[str] = field(default_factory=list)   # capability_map 低置信度领域
    high_risk_zones: list[str] = field(default_factory=list)    # 近期有失败历史的领域/操作
    top_opportunities: list[str] = field(default_factory=list)  # 综合排序后的 top N 行动机会
    behavior_notes: list[str] = field(default_factory=list)     # [新增] 与用户行为感知层交叉得到的提示（可选输入源，默认空）

    def is_empty(self) -> bool:
        return not (
            self.known_issues or self.unexplored_areas
            or self.high_risk_zones or self.top_opportunities
            or self.behavior_notes
        )

    def to_dict(self) -> dict:
        return {
            "known_issues": list(self.known_issues),
            "unexplored_areas": list(self.unexplored_areas),
            "high_risk_zones": list(self.high_risk_zones),
            "top_opportunities": list(self.top_opportunities),
            "behavior_notes": list(self.behavior_notes),
        }

    def to_system_prompt_fragment(self) -> str:
        """格式化为注入 system_extra 的文本块。空地图返回空字符串（调用方
        据此判断是否要拼接，避免在 system prompt 里留一个空标题）。"""
        if self.is_empty():
            return ""

        lines = ["## 当前环境行动可能性（自动感知，仅供参考）"]
        if self.known_issues:
            lines.append(f"- 已知待解决问题：{', '.join(self.known_issues[:3])}")
        if self.unexplored_areas:
            lines.append(f"- 能力盲区（建议谨慎，必要时先确认再动手）：{', '.join(self.unexplored_areas[:2])}")
        if self.high_risk_zones:
            lines.append(f"- 历史高风险区域（涉及时建议额外确认）：{', '.join(self.high_risk_zones[:2])}")
        if self.top_opportunities:
            lines.append("- 当前最值得关注：")
            for opp in self.top_opportunities[:3]:
                lines.append(f"  · {opp}")
        if self.behavior_notes:
            lines.append("- 用户近期活动提示（来自行为感知层，仅供参考）：")
            for note in self.behavior_notes[:2]:
                lines.append(f"  · {note}")
        return "\n".join(lines)


class AffordanceAnalyzer:
    """
    交叉分析：open_threads + capability_map + lesson memory
    → 行动可能性地图。

    无状态、只读，不做任何写入或 LLM 调用——纯粹是对已有数据的重新组织和
    排序。调用方持有 AgentPaths / MemoryBackend，本类不自己构造它们，
    避免在感知层里重新决定"项目根目录在哪""用哪个记忆后端"这类已经由
    上层（Agent / SessionAgentPool）决定好的事情。
    """

    def analyze(
        self,
        *,
        open_threads: Optional[list["OpenThread"]] = None,
        lesson_entries: Optional[list["MemoryEntry"]] = None,
        capability_entries: Optional[list] = None,
        behavior_context: Optional["BehaviorContext"] = None,
        weights: Optional["AffordanceWeights"] = None,   # [方案四新增] 默认 None = 现有硬编码行为
    ) -> AffordanceMap:
        """
        Args:
            open_threads: workdir_knowledge.load_open_threads() 的结果
                （调用方负责加载，本方法只做状态为 "open" 的过滤）。
            lesson_entries: entry_type == "lesson" 的 MemoryEntry 列表
                （调用方负责从 MemoryStore.all_entries() 过滤出来）。
            capability_entries: consolidation.CapabilityMapEntry 列表（或具备
                .domain / .confidence 属性的等价对象）——可选，因为不是
                每个项目都已经跑过 巩固循环 扫描积累出能力地图。
            behavior_context: [新增] perception/behavior/ 用户行为感知层的
                只读摘要（可选，默认 None）。为 None 时该输入源视为缺失，
                不影响其余三路分析结果——调用方（inject_affordance_map）
                负责按双重开关决定是否加载并传入。
            weights: [方案四新增] Affordance 排序权重（perception/
                affordance_calibration.py::AffordanceWeights），由
                outcome_tracker 效果回填间接校准。为 None 时使用全部为
                1.0 的默认权重，等价于改造前行为（回归保证）。
        """
        if weights is None:
            from mini_agent.perception.affordance_calibration import AffordanceWeights
            weights = AffordanceWeights()

        known_issues = self._extract_known_issues(open_threads or [])
        unexplored = self._find_unexplored(capability_entries or [])
        risky = self._find_risky_zones(lesson_entries or [])
        opportunities = self._rank_opportunities(open_threads or [], unexplored, weights)
        behavior_notes = self._derive_behavior_notes(behavior_context, known_issues)

        return AffordanceMap(
            known_issues=known_issues,
            unexplored_areas=unexplored,
            high_risk_zones=risky,
            top_opportunities=opportunities[:3],
            behavior_notes=behavior_notes,
        )

    @staticmethod
    def _derive_behavior_notes(
        behavior_context: Optional["BehaviorContext"], known_issues: list[str]
    ) -> list[str]:
        """把 BehaviorContext 摘要转成 1-2 条行动提示。纯规则匹配，不做语义判断。"""
        if behavior_context is None:
            return []
        notes: list[str] = []
        if behavior_context.recent_git_touched_paths:
            paths_preview = ", ".join(behavior_context.recent_git_touched_paths[:3])
            notes.append(f"用户近期在其他终端提交/切换过：{paths_preview}，建议先确认最新状态再继续相关改动")
        if behavior_context.context_switch_count >= 15:
            notes.append("用户近期应用切换频繁，可能处于分心状态，非紧急事项可降低打扰优先级")
        elif behavior_context.is_actively_engaged is False and behavior_context.context_switch_count == 0:
            notes.append("用户近期无前台活动信号（可能离开/空闲），非紧急事项可延后汇报")
        return notes[:2]

    # ── 内部计算 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_known_issues(open_threads: list["OpenThread"]) -> list[str]:
        """只取状态为 open 的线索，按优先级排序，标题去重保序。"""
        open_items = [t for t in open_threads if getattr(t, "status", "open") == "open"]
        open_items.sort(
            key=lambda t: -_PRIORITY_SCORE.get(getattr(t, "priority", "medium"), 2)
        )
        seen: set[str] = set()
        titles: list[str] = []
        for t in open_items:
            title = getattr(t, "title", "") or ""
            if title and title not in seen:
                seen.add(title)
                titles.append(title)
        return titles

    @staticmethod
    def _find_unexplored(capability_entries: list) -> list[str]:
        """capability_map 中置信度低于阈值的领域——"能力盲区"。
        没有 capability_map（尚未跑过 巩固循环 扫描）时返回空列表，
        这不是错误，只是"暂时没有这层信息"。"""
        unexplored = [
            getattr(e, "domain", "")
            for e in capability_entries
            if getattr(e, "confidence", 1.0) < _LOW_CONFIDENCE_THRESHOLD
            and getattr(e, "domain", "")
        ]
        # 置信度越低越靠前
        unexplored_with_score = sorted(
            ((e.domain, e.confidence) for e in capability_entries
             if getattr(e, "confidence", 1.0) < _LOW_CONFIDENCE_THRESHOLD),
            key=lambda x: x[1],
        )
        return [d for d, _ in unexplored_with_score] or unexplored

    @staticmethod
    def _find_risky_zones(lesson_entries: list["MemoryEntry"]) -> list[str]:
        """从 lesson 的 trigger/outcome 文本中找高风险关键词命中的条目，
        取其 trigger（截断展示）作为"高风险区域"描述。human_feedback 来源
        优先（用户亲自纠正过的，比自我反思更可信）。"""
        scored: list[tuple[str, int]] = []
        for entry in lesson_entries:
            text = " ".join([
                getattr(entry, "trigger", "") or "",
                getattr(entry, "outcome", "") or "",
            ]).lower()
            hit = sum(1 for kw in _RISK_KEYWORDS if kw in text)
            if hit == 0:
                continue
            priority_bonus = 10 if getattr(entry, "source", "") == "human_feedback" else 0
            trigger = (getattr(entry, "trigger", "") or "").strip()
            if not trigger:
                continue
            label = trigger if len(trigger) <= 40 else trigger[:40] + "…"
            scored.append((label, hit + priority_bonus))

        scored.sort(key=lambda x: -x[1])
        seen: set[str] = set()
        result: list[str] = []
        for label, _ in scored:
            if label not in seen:
                seen.add(label)
                result.append(label)
        return result

    @staticmethod
    def _rank_opportunities(
        open_threads: list["OpenThread"], unexplored: list[str], weights=None,
    ) -> list[str]:
        """top_opportunities：当前最值得关注的行动机会，混合"已知待修问题"
        （优先级高 + 类型为 bug/blocker 的更靠前，按 weights.known_issues_weight
        缩放）和"待探索能力边界"（提醒一下，但不抢已知问题的位置，按
        weights.unexplored_areas_weight 决定是否/如何靠前展示）。
        weights 为 None 时等价于全部 1.0（改造前行为）。"""
        known_weight = getattr(weights, "known_issues_weight", 1.0) if weights else 1.0
        unexplored_weight = getattr(weights, "unexplored_areas_weight", 1.0) if weights else 1.0

        scored: list[tuple[str, float]] = []
        for t in open_threads:
            if getattr(t, "status", "open") != "open":
                continue
            title = getattr(t, "title", "") or ""
            if not title:
                continue
            score = _PRIORITY_SCORE.get(getattr(t, "priority", "medium"), 2)
            if getattr(t, "type", "") in ("bug", "blocker"):
                score += 2
            scored.append((f"{title}（{getattr(t, 'type', 'issue')}/{getattr(t, 'priority', 'medium')}）", score * known_weight))

        scored.sort(key=lambda x: -x[1])
        opportunities = [label for label, _ in scored]

        if unexplored and unexplored_weight > 0:
            opportunities.append(f"探索能力盲区：{unexplored[0]}")

        return opportunities


__all__ = [
    "AffordanceMap", "AffordanceAnalyzer", "BehaviorContext", "inject_affordance_map",
    "persist_affordance_map", "load_recent_high_risk_zones", "load_behavior_context",
]


# ── [方案一新增] AffordanceMap 落盘 / 只读消费 ─────────────────────────────────
#
# 设计原则（详见 next_doc/embodied_autonomy_integration_design.md 方案一）：
#   - AffordanceMap 本身只在 session 开始时构建一次，不适合在
#     _tick_autonomous() 这种后台节奏里重新触发一次 analyze()（会重复读
#     open_threads/lesson_entries/capability_entries 造成不必要的 IO）。
#   - 这里补的落盘点不是给"下一次 session"复用，而是让同一时间窗口内的
#     后台 tick 能读到"当前 session 的具身风险判断"，因此有 max_age 过期
#     判定，避免几小时前、可能已经被人工修复的风险判断继续压制新候选。
#   - 失败静默：写入/读取失败都不应影响调用方主流程。

def _affordance_snapshot_path(paths: "AgentPaths") -> Path:
    return paths.workdir_dir / "affordance_snapshot.json"


def _atomic_write_json(path: Path, data: object) -> None:
    """原子写 JSON（写临时文件后 os.replace），与仓库内其余模块的同名
    私有工具函数保持同样的实现风格（各模块各自持有一份，不引入跨模块
    私有函数依赖）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def _read_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def persist_affordance_map(paths: "AgentPaths", affordance_map: "AffordanceMap") -> None:
    """[方案一新增] inject_affordance_map() 构建完 AffordanceMap 后调用一次，
    落盘到 workdir 供后台自主决策层只读消费（不是给下一次 session 复用——
    每个 session 开始都会重新 analyze 一次，这里只是让同一时间窗口内的
    后台 tick 能读到"当前 session 的具身风险判断"）。

    失败静默：写入失败不应阻断 session 启动，风险感知是旁路增强。
    """
    try:
        data = {"ts": time.time(), **affordance_map.to_dict()}
        _atomic_write_json(_affordance_snapshot_path(paths), data)
    except Exception:
        pass


def load_recent_high_risk_zones(paths: "AgentPaths", *, max_age_minutes: float = 60.0) -> list[str]:
    """[方案一新增] 供 SoftGoalDeriver / ExplorationSandbox 只读消费。
    超过 max_age_minutes 视为过期（避免用几小时前、可能已经被人工修复的
    风险判断继续压制新候选），过期或文件不存在均返回空列表——空列表
    语义等价于"没有已知风险"，调用方不需要区分"过期"和"从未产生过"。
    """
    try:
        raw = _read_json(_affordance_snapshot_path(paths), {})
        if not raw or time.time() - float(raw.get("ts", 0.0)) > max_age_minutes * 60:
            return []
        return list(raw.get("high_risk_zones", []))
    except Exception:
        return []


# ── [打通具身感知与行为感知] 行为感知层只读桥接 ──────────────────────────────
#
# 设计原则（详见 next_doc/priority_improvements_implementation_plan.md 方案二）：
#   - 单向只读：AffordanceAnalyzer 只查询 BehaviorEventStore，不写入、不影响采集。
#   - 双重开关：behavior.enabled 与 affordance.use_behavior_context 必须同时为
#     True 才生效，任一为 False 直接返回 None（等同于该输入源缺失）。
#   - 一次性快照：与 AffordanceMap 本身"session 开始时构建一次"的慢变量粒度
#     对齐，不做逐 turn 实时查询，降低敏感数据暴露窗口。
#   - 失败静默降级：behavior perception 未启用/查询异常时完全不影响其余三路分析。

@dataclass
class BehaviorContext:
    """交叉分析用的最小摘要，只保留与"当前工作是否有潜在冲突/呼应"相关的字段。"""

    recent_git_touched_paths: list = field(default_factory=list)   # 用户近期在其他终端 commit/checkout 触碰的路径
    recent_terminal_commands: list = field(default_factory=list)   # 近期 shell 命令
    is_actively_engaged: Optional[bool] = None   # 前台窗口/idle 信号推导的"用户当前是否专注"
    context_switch_count: int = 0                # 观察窗口内应用切换次数


def _summarize_behavior_events(events: list) -> "BehaviorContext":
    """把 ActivityEvent 列表压缩成 BehaviorContext。纯规则聚合，不调用 LLM。"""
    git_paths: list[str] = []
    terminal_cmds: list[str] = []
    app_focus_count = 0
    idle_seen = False

    for e in events:
        source = getattr(e, "source", "") or ""
        event_type = getattr(e, "event_type", "") or ""
        meta = getattr(e, "meta", None) or {}
        if source == "git_activity":
            path = meta.get("repo") or meta.get("path")
            if path and path not in git_paths:
                git_paths.append(path)
        elif source == "terminal_command":
            cmd = meta.get("command")
            if cmd:
                terminal_cmds.append(cmd)
        if event_type == "app_focus":
            app_focus_count += 1
        if event_type in ("idle_start", "idle_end"):
            idle_seen = True

    return BehaviorContext(
        recent_git_touched_paths=git_paths[:5],
        recent_terminal_commands=terminal_cmds[:5],
        is_actively_engaged=(app_focus_count > 0) if (app_focus_count or idle_seen) else None,
        context_switch_count=app_focus_count,
    )


def load_behavior_context(cfg: "AppConfig", *, window_minutes: int = 30) -> Optional["BehaviorContext"]:
    """[方案二：提升为公共函数] 只读查询 BehaviorEventStore 最近 window_minutes
    分钟内的活动，压缩为摘要。

    双重开关：behavior.enabled 与 affordance.use_behavior_context 必须同时为
    True，任一为 False 直接返回 None（不触发任何 BehaviorPerceptionManager 调用）。

    此前是 affordance_analyzer.py 内部私有函数（`_load_behavior_context`），
    只服务于 AffordanceAnalyzer 一个消费方。方案二（BehaviorContext 接入
    ResourceArbiter 自主任务调度门控）落地后有了第二个消费方
    （evolution/resource_arbiter.py::ResourceArbiter._check_user_presence()），
    提升为公共函数，两个模块共用同一份"读取 + 双重开关校验"逻辑，避免
    resource_arbiter.py 重新实现一遍开关判断分叉出第二套语义。
    """
    affordance_cfg = getattr(cfg, "affordance", None)
    if not getattr(affordance_cfg, "use_behavior_context", False):
        return None
    try:
        from mini_agent.perception.behavior.config import load_behavior_config
        behavior_cfg = load_behavior_config(getattr(cfg, "project_root", None))
        if not behavior_cfg.enabled:
            return None

        from mini_agent.perception.behavior.manager import get_manager
        import time as _time

        mgr = get_manager(getattr(cfg, "project_root", None))
        since = _time.time() - window_minutes * 60
        events = mgr.query(since=since, limit=200)
        return _summarize_behavior_events(events)
    except Exception:
        return None


# 向后兼容别名：仓库内其余代码此前可能直接引用下划线前缀的私有名字。
_load_behavior_context = load_behavior_context


def inject_affordance_map(agent: "Agent", cfg: "AppConfig", *, log=None) -> None:
    """
    构建一次 AffordanceMap 并拼进 agent.cfg.system_extra。

    daemon 多用户路径（api/session_pool.py::SessionAgentPool._create_entry()）
    与本地单 Agent 路径（cli/app.py::_main_inner()）共用同一实现，消除此前
    "AffordanceMap 只在多用户 daemon 路径生效"的已知不对称
    （见 docs/embodied-agent-guide.md §8）。

    调用时机要求：必须在 Agent() 构造之后（复用 agent._memory，避免重复
    构造 MemoryStore 实例指向同一文件）。

    失败静默降级：感知层失败不应阻断 Agent 启动/session 创建。
    """
    affordance_cfg = getattr(cfg, "affordance", None)
    if affordance_cfg is None or not getattr(affordance_cfg, "enabled", True):
        return

    try:
        from mini_agent.perception.workdir_knowledge import load_open_threads
        from mini_agent.storage.paths import AgentPaths

        paths = AgentPaths(cfg.project_root)
        open_threads = load_open_threads(paths)

        memory_backend = getattr(agent, "_memory", None)
        lesson_entries: list = []
        capability_entries: list = []
        if memory_backend is not None and hasattr(memory_backend, "all_entries"):
            all_entries = memory_backend.all_entries()
            lesson_entries = [e for e in all_entries if getattr(e, "entry_type", "") == "lesson"]
            if getattr(affordance_cfg, "use_capability_map", True):
                try:
                    from mini_agent.evolution.consolidation import build_capability_map
                    capability_entries = build_capability_map(paths, None)
                except Exception:
                    capability_entries = []

        behavior_context = _load_behavior_context(cfg)

        weights = None
        try:
            from mini_agent.perception.affordance_calibration import load_weights
            weights = load_weights(paths)   # 读取上次 calibrate() 持久化的权重，文件不存在则默认权重
        except Exception:
            weights = None

        affordance_map = AffordanceAnalyzer().analyze(
            open_threads=open_threads,
            lesson_entries=lesson_entries,
            capability_entries=capability_entries,
            behavior_context=behavior_context,
            weights=weights,
        )
        fragment = affordance_map.to_system_prompt_fragment()

        # [方案一] 落盘供后台自主决策层（SoftGoalDeriver/ExplorationSandbox）
        # 只读消费，见 persist_affordance_map() docstring。放在 fragment 计算
        # 之后、system_extra 拼接之前均可——与是否为空块无关，只要
        # AffordanceMap 构建成功就落盘。
        persist_affordance_map(paths, affordance_map)

        # [打通具身感知与行为感知] 放在 "fragment 为空则 return" 之前：
        # AgentSelfModel 在 Agent.__init__ 阶段构建时 affordance_map 还是 None
        # （B4 注入本来就晚于 Agent 构造），BehaviorContext 因此从未被写回过
        # AgentSelfModel 的任何字段。behavior_context 里 is_actively_engaged
        # 这类信号即使在 known_issues/opportunities 等都为空、fragment 整体为空
        # 时也可能有值——不应该因为"没有值得写进 system prompt 的文字"就连带
        # 丢掉这个结构化信号。失败静默降级，不影响 AffordanceMap 本身的注入。
        self_model = getattr(agent, "_self_model", None)
        if self_model is not None:
            self_model.user_presence = behavior_context

        if not fragment:
            return

        if getattr(affordance_cfg, "verbose", False) and log is not None:
            log.info("[AffordanceMap] %s", affordance_map.to_dict())

        # 写到 agent.cfg（ContextBuilder 实际持有、每轮读取的那个对象），
        # 而不是闭包参数 cfg——二者在当前实现里恰好是同一对象
        # （Agent(cfg=cfg) 不做深拷贝），但显式走 agent.cfg 更准确地表达
        # "我要影响的是这个 agent 接下来读到的 system_extra"，不依赖
        # "cfg 和 agent.cfg 是否同一对象"这个实现细节。
        target_cfg = getattr(agent, "cfg", None) or cfg
        existing = getattr(target_cfg, "system_extra", "") or ""
        target_cfg.system_extra = (existing + "\n\n" + fragment).strip()
    except Exception:
        import logging
        (log or logging.getLogger(__name__)).debug(
            "[AffordanceMap] injection failed", exc_info=True
        )
