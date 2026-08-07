"""成长顾问 Growth Advisor（对应 next_doc/growth_advisor_design.md）。

与 evolution/ 目录下服务"Agent 自我进化"的模块（soft_goal_deriver /
decision_profile_builder / objective_outcome_tracker ...）是姊妹关系：
那些模块把用户的反馈/记忆折射回 Agent 自身的行为改进，这个模块则是把
同一批记忆信号折射回**用户自己的成长方向**——候选生成、调研报告生成、
反馈台账三层结构完全复用 evolution/ 里已经跑通的"证据 → 候选 → 采纳/
忽略反馈回路"范式（方案第 3 节 P1 里程碑）。

P1 范围（信号扫描 → 候选生成 → 调研报告 → 看板展示，已完成）：
    - GrowthCandidate / GrowthReport 数据模型
    - GrowthBacklog：候选队列（pending/accepted/dismissed/expired），
      按 dedupe_key 去重、dismissed 有冷却期、pending 有数量上限
    - GrowthFeedbackLedger：用户对候选/报告的采纳/忽略反馈流水
    - growth_signal_scan()：规则式信号扫描（不依赖 LLM），从 memory
      entries 的 tags/summary 里做关键词频次统计，写回
      UserProfile.derived["growth_focus_areas"] /
      UserProfile.derived["growth_gaps"]
    - growth_candidate_derive()：从 focus areas 里挑选证据数达标的方向，
      生成/追加候选到 backlog（克制：达不到 min_evidence_count 的方向
      不生成候选，命中 excluded_topics 的直接跳过）
    - generate_growth_report()：为一个候选生成调研报告（Markdown），
      P1 阶段用规则式模板兜底；如调用方传入 llm_helper（可调用对象，
      签名 `llm_helper(prompt: str) -> str`），则优先用它起草正文——
      同 decision_profile_builder 的"可选 LLM 增强，缺省仍要能跑"原则。

P2 范围（反馈驱动的置信度调权 + 推送节流接入 + 复盘深度归因，本次新增）：
    - `_feedback_multiplier()` / `_dismiss_counts_by_dedupe_key()`：读取
      GrowthFeedbackLedger 里的历史 dismiss 记录，同一方向被忽略过的
      次数越多，下次（冷却期过后）重新生成候选时默认置信度打的折扣越
      大，但不会打到 0——呼应方案第 6 节"不是完全屏蔽，避免用户当时忙、
      后来又感兴趣的情况被永久拒绝"。
    - `_maybe_dispatch_notification()`：把 `run_daily_cycle()` 产出的
      调研报告接入已有的 `NotificationDispatcher`（复用 email/kanban
      channel）与 `notification.reports_store`，落实方案第 4.2 节的
      推送节流规则——`notification_frequency=kanban_only` 时不推送；
      否则当天最多推 `notification_max_per_day` 条（默认 1 条），且必须
      是这一轮新生成报告里置信度最高、达到 `notification_min_confidence`
      阈值的一条；节流状态落盘在 `paths.growth_state_path`。
    - `monthly_retrospective_summary()` 新增 `acceptance_rate`（采纳率）
      与 `top_accepted_topics` / `top_dismissed_topics`（按候选标题聚合
      的采纳/忽略排行），作为方案第 6 节"推荐命中率"指标的落地。

P3 范围（首次触达提示跨会话持久化 + 黑名单可视化编辑，本次新增）：
    - `first_touch_notice_shown()` / `mark_first_touch_notice_shown()`：
      把方案第 8 节第 1 条"首次触达必须透明告知，但不能每次都打断"落到
      跨会话持久化，状态复用 `growth_state_path`（与推送节流状态同一个
      文件，互不覆盖）。看板侧通过 `POST /growth/first_touch_ack` 落盘。
    - `excluded_topics` 黑名单的看板可视化编辑：不是在这个模块加代码，
      而是修好了通用配置编辑器（`kanban/app.py` 的
      `_render_config_field_widget`）里 list 类型字段此前被当纯文本框
      处理的缺口，改成一行一项的文本域——这个修复对所有 list 类型配置
      字段生效，不止 `excluded_topics`。

P3 范围（本次新增）——`notification_frequency=weekly_digest` 的真实周摘要
打包：
    - `_maybe_dispatch_weekly_digest()`：独立于 `_maybe_dispatch_notification`
      的按天节流路径，按"距上次推送是否满 7 天"（而非自然日）判断是否
      触发；到期后把窗口期内新生成的全部调研报告标题打包成一条摘要消息
      一次性推送，不再逐条推。`run_daily_cycle()` 按 `notification_frequency`
      分流：`weekly_digest` 走这里，其余（`daily`/`kanban_only`）仍走
      `_maybe_dispatch_notification`。

P3 范围（本次新增，第二项）——月度复盘的跨候选能力地图聚合：
    - `growth_topic_map()`：按 `dedupe_key` 聚合 backlog 里全部历史候选
      （含同一主题因 dismiss 冷却结束后重新生成的多条记录），产出每个
      主题的当前状态/历史累计采纳与忽略次数/历史峰值置信度/首次出现与
      最近更新时间，按最近更新时间倒序排列。思路对齐
      `self_model_snapshot.py`（Agent 自己的能力弱项趋势），只是聚合对象
      换成了用户的成长方向推进轨迹。`monthly_retrospective_summary()`
      新增 `topic_map` 字段直接复用该函数，`GET /growth/summary` 已经
      透传整个 `retrospective`，未新增 API 端点。

P3 范围（本次新增，第四项）——`growth_signal_scan` 的 LLM 增强版归纳
（默认关闭，opt-in）：
    - `_llm_augment_topics()`：只对关键词表命中不到的近期记忆条目（数量
      不足 `_LLM_AUGMENT_MIN_UNMATCHED` 时直接跳过，避免为了归纳专门调
      一次 LLM 却大概率凑不满候选证据阈值）做一次 LLM 归纳，把发现的新
      主题与规则命中结果按 `normalize_title_key` 去重合并；entry_ids 必须
      是调用方提供的合法子集，任何解析失败/字段缺失都直接丢弃对应结果，
      不让异常向上传播、也不影响规则式扫描已经拿到的结果。
    - `growth_signal_scan()` / `run_daily_cycle()` 新增可选 `llm_helper`
      形参（约定同 `generate_growth_report`），但只有
      `GrowthAdvisorConfig.llm_signal_augment_enabled=True` 时
      `run_daily_cycle()` 才会真正把它传给扫描函数——即使调用方处于有
      agent 上下文的场景（因而总能拿到 `llm_helper`），默认仍然按纯规则
      式运行，保持"`enabled=True` 默认开启不产生额外 LLM 成本"的底线不变。
    - CLI `/growth scan`/`/growth report`、API `POST /growth/scan` 新增/
      修正了把 `agent.llm_helper`（`LLMHelper` 实例，不可直接调用）包成
      `Callable[[str], str]` 闭包再传下去的逻辑——顺带修掉了 `/growth
      report` 里此前直接把 `LLMHelper` 实例当函数传给 `generate_growth_
      report` 的既有 bug（`LLMHelper` 没有 `__call__`，此前这条路径一旦
      真的没有已生成报告、需要现场生成，会在调用 `llm_helper(prompt)`
      时抛 `TypeError`，被 `generate_growth_report` 内部的 try/except 吞掉
      后静默回退模板——功能上不报错，但"LLM 优先起草"从未真正生效过）。

仍不在本次范围内（见方案 P3 剩余项，占位在 next_doc 文档里）：
    - 看板里的拖拽式看板视图（当前仍是列表 + 采纳/忽略两个动作）
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Optional

# 与 objective_outcome_tracker.normalize_title_key 保持完全一致的去重规则，
# 两处独立实现是为了不引入 evolution 内部模块间的横向依赖（该函数本身
# 已经是从 soft_goal_deriver 抽出来复用的稳定契约，这里直接复制其算法）。
def normalize_title_key(title: str) -> str:
    s = (title or "").lower().strip()
    s = re.sub(r"[^\w\s]", " ", s)
    return " ".join(sorted(s.split()))


JOB_ID_DAILY = "sys:growth_advisor_daily"
JOB_ID_MONTHLY = "sys:growth_monthly_retrospective"

# 候选状态机：pending -> accepted | dismissed；超过 TTL 未处理 -> expired
STATUS_PENDING = "pending"
STATUS_ACCEPTED = "accepted"
STATUS_DISMISSED = "dismissed"
STATUS_EXPIRED = "expired"

_VALID_STATUSES = (STATUS_PENDING, STATUS_ACCEPTED, STATUS_DISMISSED, STATUS_EXPIRED)

# pending 候选超过这么多天没人处理，下次扫描时自动标记为 expired
# （避免看板里堆积"已经不新鲜"的建议，呼应方案第 8 节"克制"原则）。
PENDING_TTL_DAYS = 45

# 一条 memory entry 的 tag 至少要在窗口内出现这么多次，才有资格被当作
# "growth_focus_area"候选主题（与 decision_profile_builder 的
# MIN_EVIDENCE_COUNT 同量级但独立配置，通过 GrowthAdvisorConfig 传入）。
_DEFAULT_MIN_EVIDENCE_COUNT = 3

# 信号扫描只看最近这么多天的记忆，避免陈年旧事一直反复被提起
SIGNAL_SCAN_WINDOW_DAYS = 90


# ────────────────────────── 数据模型 ──────────────────────────


@dataclass
class GrowthCandidate:
    candidate_id: str
    title: str
    rationale: str                       # 为什么值得关注（面向用户的一句话）
    evidence_refs: list[str] = field(default_factory=list)   # memory entry_id 列表
    evidence_count: int = 0
    confidence: float = 0.0              # 0~1，由 evidence_count 归一化得到
    status: str = STATUS_PENDING
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    report_id: Optional[str] = None      # 生成过调研报告后回填

    def dedupe_key(self) -> str:
        return normalize_title_key(self.title)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "GrowthCandidate":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class GrowthReport:
    report_id: str
    candidate_id: str
    title: str
    slug: str
    summary: str                         # 报告摘要（看板里展示用）
    body_path: str                       # 相对/绝对路径，正文落在 wiki_growth_dir
    created_at: float = field(default_factory=time.time)
    source: str = "template"             # "template" | "llm"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "GrowthReport":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


# ────────────────────────── JSONL 存取工具 ──────────────────────────


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    path.write_text(text + ("\n" if rows else ""), encoding="utf-8")


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ────────────────────────── GrowthBacklog ──────────────────────────


class GrowthBacklog:
    """候选队列的读写封装，落盘为 `growth_backlog.jsonl`（每次整表重写，
    数据量级是"用户成长方向候选"，天然不大，不需要 append-only）。
    """

    def __init__(self, paths) -> None:
        self._paths = paths
        self._path = paths.growth_backlog_path

    def load_all(self) -> list[GrowthCandidate]:
        return [GrowthCandidate.from_dict(d) for d in _read_jsonl(self._path)]

    def save_all(self, candidates: list[GrowthCandidate]) -> None:
        _write_jsonl(self._path, [c.to_dict() for c in candidates])

    def pending(self) -> list[GrowthCandidate]:
        return [c for c in self.load_all() if c.status == STATUS_PENDING]

    def get(self, candidate_id: str) -> Optional[GrowthCandidate]:
        for c in self.load_all():
            if c.candidate_id == candidate_id:
                return c
        return None

    def expire_stale(self, ttl_days: int = PENDING_TTL_DAYS) -> int:
        """把超过 ttl_days 还是 pending 的候选标记为 expired，返回处理条数。"""
        cutoff = time.time() - ttl_days * 86400
        all_c = self.load_all()
        n = 0
        for c in all_c:
            if c.status == STATUS_PENDING and c.created_at < cutoff:
                c.status = STATUS_EXPIRED
                c.updated_at = time.time()
                n += 1
        if n:
            self.save_all(all_c)
        return n

    def add_or_merge(
        self,
        title: str,
        rationale: str,
        evidence_refs: list[str],
        *,
        min_evidence_count: int,
        max_pending: int,
        dismissed_cooldown_days: int,
        confidence_multiplier: float = 1.0,
    ) -> Optional[GrowthCandidate]:
        """尝试新增一条候选。规则（对应方案第 3 节"克制"要求）：
            - evidence_refs 数量不达标 → 不生成，返回 None
            - 已存在同 dedupe_key 的 pending/accepted 候选 → 合并证据、
              不重复创建
            - 曾被 dismissed 且仍在冷却期内 → 跳过，返回 None
            - pending 数量已达上限 → 跳过，返回 None（避免无限堆积）
        """
        if len(evidence_refs) < min_evidence_count:
            return None

        key = normalize_title_key(title)
        all_c = self.load_all()

        for c in all_c:
            if c.dedupe_key() != key:
                continue
            if c.status in (STATUS_PENDING, STATUS_ACCEPTED):
                merged = sorted(set(c.evidence_refs) | set(evidence_refs))
                c.evidence_refs = merged
                c.evidence_count = len(merged)
                c.confidence = _confidence_from_evidence(c.evidence_count)
                c.updated_at = time.time()
                self.save_all(all_c)
                return c
            if c.status == STATUS_DISMISSED:
                cooldown_cutoff = time.time() - dismissed_cooldown_days * 86400
                if c.updated_at > cooldown_cutoff:
                    return None  # 冷却期内，不重新生成

        pending_count = sum(1 for c in all_c if c.status == STATUS_PENDING)
        if pending_count >= max_pending:
            return None

        base_confidence = _confidence_from_evidence(len(set(evidence_refs)))
        cand = GrowthCandidate(
            candidate_id=uuid.uuid4().hex[:12],
            title=title,
            rationale=rationale,
            evidence_refs=sorted(set(evidence_refs)),
            evidence_count=len(set(evidence_refs)),
            # confidence_multiplier < 1.0 时说明这个方向此前被 dismiss 过
            # （见 _feedback_multiplier），新建候选默认置信度打折但不清零。
            confidence=round(base_confidence * confidence_multiplier, 3),
        )
        all_c.append(cand)
        self.save_all(all_c)
        return cand

    def set_status(self, candidate_id: str, status: str) -> Optional[GrowthCandidate]:
        if status not in _VALID_STATUSES:
            raise ValueError(f"invalid status: {status}")
        all_c = self.load_all()
        for c in all_c:
            if c.candidate_id == candidate_id:
                c.status = status
                c.updated_at = time.time()
                self.save_all(all_c)
                return c
        return None

    def attach_report(self, candidate_id: str, report_id: str) -> None:
        all_c = self.load_all()
        for c in all_c:
            if c.candidate_id == candidate_id:
                c.report_id = report_id
                c.updated_at = time.time()
        self.save_all(all_c)


def _confidence_from_evidence(evidence_count: int, cap: int = 8) -> float:
    """证据条数 → 0~1 置信度的简单饱和映射（超过 cap 条封顶为 1.0）。"""
    return round(min(evidence_count, cap) / cap, 3)


# ────────────────────────── GrowthFeedbackLedger ──────────────────────────


class GrowthFeedbackLedger:
    """用户对候选/报告的采纳/忽略流水（append-only），供未来（P2）用于
    调整同类候选的置信度权重——本次先只落盘，不做加权，避免过度设计。
    """

    def __init__(self, paths) -> None:
        self._path = paths.growth_feedback_ledger_path

    def record(self, candidate_id: str, action: str, *, note: str = "") -> None:
        _append_jsonl(
            self._path,
            {
                "candidate_id": candidate_id,
                "action": action,          # "accepted" | "dismissed"
                "note": note,
                "ts": time.time(),
            },
        )

    def all_entries(self) -> list[dict]:
        return _read_jsonl(self._path)


# ────────────────────────── P2：反馈驱动的置信度调权 ──────────────────────────

# 每被 dismiss 一次，新建候选的默认置信度衰减为原来的这个比例（复利式衰
# 减，而不是线性扣分，理由是"第 1 次忽略"和"第 5 次忽略"传达的信号强度
# 显然不该线性对待）。下限见 _MIN_FEEDBACK_MULTIPLIER——不会打到 0，避免
# "用户当时忙、后来又感兴趣"被永久拒绝（方案第 6 节明确要求）。
_DISMISS_DECAY_FACTOR = 0.85
_MIN_FEEDBACK_MULTIPLIER = 0.4


def _dismiss_counts_by_dedupe_key(paths) -> dict[str, int]:
    """统计每个 dedupe_key（归一化标题）历史上被 dismiss 过多少次。

    GrowthFeedbackLedger 只记录 candidate_id，需要反查 backlog 里对应
    候选的标题才能归一化到 dedupe_key——包括已经不在 pending 状态、甚至
    早已被 expire_stale 清理状态的旧候选（backlog 是整表重写，历史记录
    仍在文件里，只是 status 字段变了），因此这里读全量 load_all()。
    """
    id_to_key = {c.candidate_id: c.dedupe_key() for c in GrowthBacklog(paths).load_all()}
    counts: dict[str, int] = {}
    for entry in GrowthFeedbackLedger(paths).all_entries():
        if entry.get("action") != STATUS_DISMISSED:
            continue
        key = id_to_key.get(entry.get("candidate_id"))
        if key:
            counts[key] = counts.get(key, 0) + 1
    return counts


def _feedback_multiplier(dismiss_count: int) -> float:
    if dismiss_count <= 0:
        return 1.0
    return max(_MIN_FEEDBACK_MULTIPLIER, round(_DISMISS_DECAY_FACTOR ** dismiss_count, 3))


# ────────────────────────── 信号扫描 growth_signal_scan ──────────────────────────


# 中文/英文成长方向关键词 → 归一化主题名。规则式 MVP，先覆盖高频场景；
# 后续如需扩展，直接往这个表里加词条即可，不需要改扫描逻辑。
_TOPIC_KEYWORDS: dict[str, list[str]] = {
    "Python 工程实践": ["python", "pytest", "packaging", "asyncio"],
    "前端与可视化": ["react", "frontend", "streamlit", "前端", "可视化"],
    "数据分析": ["pandas", "sql", "数据分析", "dataframe"],
    "系统设计与架构": ["架构", "设计模式", "microservice", "系统设计"],
    "写作与表达": ["写作", "文案", "表达", "沟通"],
    "项目管理": ["项目管理", "排期", "计划", "复盘"],
    "AI/LLM 应用": ["llm", "prompt", "agent", "大模型", "rag"],
}


def growth_signal_scan(
    paths, profile, memory_store, *,
    window_days: int = SIGNAL_SCAN_WINDOW_DAYS,
    llm_helper: Optional[Callable[[str], str]] = None,
) -> dict[str, list[str]]:
    """扫描最近 window_days 内的 memory entries，按 `_TOPIC_KEYWORDS` 做
    命中统计，把 {主题: [entry_id...]} 写入
    `profile.derived["growth_focus_areas"]`（结构化，供 candidate_derive
    直接消费），并返回该结果供调用方（cron / CLI）立即使用。

    这是规则式实现（P1），不依赖 LLM——保证 `enabled=True` 默认开启时
    不会给每个用户都额外产生 LLM 调用成本。

    P3：如果调用方传入 `llm_helper`（签名 `llm_helper(prompt: str) ->
    str`，同 `generate_growth_report` 的约定），会在规则扫描结束后额外
    做一次 LLM 增强归纳（见 `_llm_augment_topics`），从关键词表命中不到
    的近期记忆里尝试发现新主题，补充进返回结果——但只有调用方同时传入
    `llm_helper` 时才会触发，函数本身不读取 `GrowthAdvisorConfig`（是否
    要传 `llm_helper` 由调用方根据 `cfg.llm_signal_augment_enabled` 决定），
    保持这个函数纯粹、可测试。
    """
    cutoff = time.time() - window_days * 86400
    hits: dict[str, list[str]] = {}

    entries = memory_store.all_entries() if memory_store is not None else []
    recent_entries = [e for e in entries if getattr(e, "created_at", 0) >= cutoff]
    for entry in recent_entries:
        haystack = " ".join(
            [getattr(entry, "summary", "") or ""]
            + list(getattr(entry, "tags", []) or [])
        ).lower()
        for topic, keywords in _TOPIC_KEYWORDS.items():
            if any(kw.lower() in haystack for kw in keywords):
                hits.setdefault(topic, []).append(getattr(entry, "entry_id", "") or "")

    if llm_helper is not None:
        try:
            hits = _llm_augment_topics(hits, recent_entries, llm_helper)
        except Exception as exc:
            from mini_agent.errors import log_exception
            log_exception(exc, where="mini_agent.growth_advisor.growth_signal_scan_llm_augment")

    derived = dict(getattr(profile, "derived", {}) or {})
    derived["growth_focus_areas"] = hits
    derived["growth_focus_areas_updated_at"] = time.time()
    profile.derived = derived
    return hits


# 一次 LLM 增强归纳最多送多少条"规则未命中"的记忆条目，避免 prompt 无限
# 增长；条目本身不多的账号成本也很低，条目多的账号只取最近的一批。
_LLM_AUGMENT_MAX_ENTRIES = 40
# 未命中条目太少时不值得为了归纳专门调一次 LLM（大概率凑不满
# min_evidence_count，调了也白调）。
_LLM_AUGMENT_MIN_UNMATCHED = 3


def _llm_augment_topics(
    hits: dict[str, list[str]], recent_entries: list, llm_helper: Callable[[str], str]
) -> dict[str, list[str]]:
    """在规则式 `hits` 基础上，对关键词表命中不到的近期记忆条目做一次
    LLM 归纳，尝试发现 `_TOPIC_KEYWORDS` 没覆盖到的新主题。

    只处理"未命中"的条目——已经被规则命中的条目不重复送给 LLM，既省
    token，也避免 LLM 把规则已经归好的话题换个说法再归一遍造成主题碎片
    化。返回的新主题会按 `normalize_title_key` 与已有主题去重合并，不
    会产生"一个意思两个不同大小写/标点的 key"这种重复。

    任何解析失败、字段缺失、entry_id 对不上号的情况，都直接丢弃对应
    条目/主题而不是让异常向上传播——LLM 输出不可信，只做"能用就用，
    用不了就当没发生"的宽松吸收。
    """
    matched_ids = {eid for ids in hits.values() for eid in ids}
    unmatched = [e for e in recent_entries if (getattr(e, "entry_id", "") or "") not in matched_ids]
    if len(unmatched) < _LLM_AUGMENT_MIN_UNMATCHED:
        return hits

    unmatched = unmatched[-_LLM_AUGMENT_MAX_ENTRIES:]
    id_to_entry = {getattr(e, "entry_id", "") or "": e for e in unmatched}
    valid_ids = set(id_to_entry.keys())

    lines = []
    for eid, e in id_to_entry.items():
        summary = (getattr(e, "summary", "") or "").strip().replace("\n", " ")[:200]
        lines.append(f"- entry_id={eid}: {summary}")
    prompt = (
        "以下是一批用户最近的记忆摘要，逐条带有 entry_id。请找出其中反复\n"
        "出现、可能值得用户系统学习/深入投入的成长方向（不要包括日常琐事、\n"
        "一次性事件）。只根据已发生的内容归纳，不要编造。\n"
        "只输出 JSON 数组，不要有其他文字，每个元素形如：\n"
        '{\"topic\": \"简短主题名\", \"entry_ids\": [\"命中的 entry_id\", ...]}\n'
        "entry_ids 必须原样从下面列表里选，不要发明新的 id。没有发现\n"
        "任何值得关注的主题时输出空数组 []。\n\n" + "\n".join(lines)
    )

    raw = llm_helper(prompt)
    if not raw or not raw.strip():
        return hits

    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"```\s*$", "", text).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            return hits
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return hits

    if not isinstance(parsed, list):
        return hits

    merged = {k: list(v) for k, v in hits.items()}
    existing_keys = {normalize_title_key(k): k for k in merged}

    for item in parsed:
        if not isinstance(item, dict):
            continue
        topic = str(item.get("topic") or "").strip()
        raw_ids = item.get("entry_ids")
        if not topic or not isinstance(raw_ids, list):
            continue
        ids = sorted({str(i) for i in raw_ids if str(i) in valid_ids})
        if not ids:
            continue

        key = normalize_title_key(topic)
        canonical = existing_keys.get(key)
        if canonical is None:
            existing_keys[key] = topic
            merged[topic] = ids
        else:
            merged[canonical] = sorted(set(merged.get(canonical, [])) | set(ids))

    return merged


# ────────────────────────── 候选生成 growth_candidate_derive ──────────────────────────


def growth_candidate_derive(paths, cfg, profile) -> list[GrowthCandidate]:
    """消费 `profile.derived["growth_focus_areas"]`（由 growth_signal_scan
    产出），对证据数达标、未命中 excluded_topics 的主题生成/合并候选到
    backlog，返回本次新增或有更新的候选列表。
    """
    focus_areas: dict[str, list[str]] = dict(
        (getattr(profile, "derived", {}) or {}).get("growth_focus_areas", {})
    )
    excluded = {t.strip().lower() for t in getattr(cfg, "excluded_topics", []) or []}
    backlog = GrowthBacklog(paths)
    backlog.expire_stale()

    min_evidence_count = getattr(cfg, "min_evidence_count", _DEFAULT_MIN_EVIDENCE_COUNT)
    max_pending = getattr(cfg, "max_pending_candidates", 10)
    cooldown_days = getattr(cfg, "dismissed_cooldown_days", 30)

    # P2：反馈驱动的置信度调权（方案第 6 节）——先一次性读取历史 dismiss
    # 统计，逐主题查表即可，避免在循环里重复扫描 ledger。
    dismiss_counts = _dismiss_counts_by_dedupe_key(paths)

    produced: list[GrowthCandidate] = []
    # 按证据数从多到少处理，保证 max_pending 限额下优先生成信号更强的候选
    for topic, refs in sorted(focus_areas.items(), key=lambda kv: -len(kv[1])):
        if topic.strip().lower() in excluded:
            continue
        rationale = f"最近记忆里与「{topic}」相关的内容出现了 {len(set(refs))} 次，可能是值得投入的方向。"
        multiplier = _feedback_multiplier(dismiss_counts.get(normalize_title_key(topic), 0))
        cand = backlog.add_or_merge(
            title=topic,
            rationale=rationale,
            evidence_refs=refs,
            min_evidence_count=min_evidence_count,
            max_pending=max_pending,
            dismissed_cooldown_days=cooldown_days,
            confidence_multiplier=multiplier,
        )
        if cand is not None:
            produced.append(cand)
    return produced


# ────────────────────────── 调研报告生成 ──────────────────────────


def _slugify(title: str) -> str:
    key = normalize_title_key(title).replace(" ", "-")
    return key or uuid.uuid4().hex[:8]


def generate_growth_report(
    paths,
    candidate: GrowthCandidate,
    *,
    llm_helper: Optional[Callable[[str], str]] = None,
) -> GrowthReport:
    """为一个候选生成调研报告并落盘。

    P1 默认走规则模板（保证零 LLM 成本也能跑通闭环）；如果调用方传入
    `llm_helper`（例如 cron job 触发时由 Agent 自己的 LLM 会话承担），
    优先用它起草正文，模板兜底失败时的输出。
    """
    report_id = uuid.uuid4().hex[:12]
    slug = f"{_slugify(candidate.title)}-{report_id[:6]}"

    body = None
    source = "template"
    if llm_helper is not None:
        prompt = (
            "请为以下用户成长方向候选撰写一份简短调研报告（Markdown，"
            "包含：为什么值得关注、可以怎么入门、常见资源/路径、"
            "预计投入与见效周期，4 个小节即可，不要超过 500 字）：\n"
            f"主题：{candidate.title}\n理由：{candidate.rationale}\n"
        )
        try:
            body = llm_helper(prompt)
            if body and body.strip():
                source = "llm"
        except Exception:
            body = None

    if not body:
        body = (
            f"# {candidate.title}\n\n"
            f"## 为什么值得关注\n{candidate.rationale}\n\n"
            "## 可以怎么入门\n"
            "- 先花 30 分钟检索该方向的入门资料，建立整体轮廓\n"
            "- 找一个与近期实际任务相关的小切口先动手试一次\n\n"
            "## 常见资源/路径\n"
            "- 官方文档 / 权威教程（优先）\n"
            "- 社区实践案例，关注踩坑记录\n\n"
            "## 预计投入与见效周期\n"
            "建议先按 1~2 周的轻量投入评估是否继续深入，避免一次性重投入。\n"
        )

    report_path = paths.growth_report_path(slug)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(body, encoding="utf-8")

    summary = candidate.rationale
    report = GrowthReport(
        report_id=report_id,
        candidate_id=candidate.candidate_id,
        title=candidate.title,
        slug=slug,
        summary=summary,
        body_path=str(report_path),
        source=source,
    )
    _append_jsonl(paths.growth_reports_index_path, report.to_dict())
    GrowthBacklog(paths).attach_report(candidate.candidate_id, report_id)
    return report


def list_reports(paths) -> list[GrowthReport]:
    return [GrowthReport.from_dict(d) for d in _read_jsonl(paths.growth_reports_index_path)]


# ────────────────────────── P2：推送节流状态（growth_advisor_state.json） ──────────────────────────


def _today_str() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def _load_growth_state(paths) -> dict:
    p = paths.growth_state_path
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_growth_state(paths, state: dict) -> None:
    p = paths.growth_state_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


# ────────────────────────── P3：首次触达提示的跨会话持久化 ──────────────────────────
# 方案第 8 节第 1 条："默认开启，但首次触达必须透明告知"。P2 阶段看板只用
# st.session_state 做了单次会话内的提示（见 P2 实施记录"已知简化"），这里
# 补上跨会话持久化：状态落盘复用 growth_advisor_state.json，跟推送节流
# 状态放在同一个文件里（同样是"低频写的小文件"，不需要单独开一个文件）。


def first_touch_notice_shown(paths) -> bool:
    """看板是否已经展示过首次触达提示（跨会话持久化，落盘查询）。"""
    return bool(_load_growth_state(paths).get("first_touch_notice_shown"))


def mark_first_touch_notice_shown(paths) -> None:
    """记录首次触达提示已经展示过，之后不再重复弹出。"""
    state = _load_growth_state(paths)
    if not state.get("first_touch_notice_shown"):
        state["first_touch_notice_shown"] = True
        state["first_touch_notice_shown_at"] = time.time()
        _save_growth_state(paths, state)


# ────────────────────────── P3：weekly_digest 真实周摘要打包 ──────────────────────────
# 此前 notification_frequency="weekly_digest" 与 "daily" 走同一套按天节流的
# 逻辑（见 P2 实施记录"已知简化"），效果只是"daily 但通常不会真的每天都
# 推"，并不是方案第 4.2 节要求的"把一周内的报告打包成一条"。这里补上真正
# 的周频聚合：状态里新增 `last_weekly_digest_at`（时间戳，不是自然日），
# 距上次推送不满 7 天则跳过；到期后把窗口内新生成的报告标题打包成一条
# 摘要消息一次性推送，而不是逐条推。

WEEKLY_DIGEST_INTERVAL_DAYS = 7


def _maybe_dispatch_weekly_digest(paths, cfg) -> Optional[dict]:
    """`notification_frequency == "weekly_digest"` 时的推送逻辑：每 7 天
    最多推一次，内容是窗口期内（上次推送至今，首次则取最近 7 天）新生成
    的全部调研报告标题打包成一条摘要，而不是逐条推送。

    与 `_maybe_dispatch_notification` 的按天节流是互斥的两套路径，由
    `run_daily_cycle` 按 `notification_frequency` 分流调用，不会同时触发。
    """
    try:
        state = _load_growth_state(paths)
        now = time.time()
        last_at = state.get("last_weekly_digest_at")
        if last_at and (now - last_at) < WEEKLY_DIGEST_INTERVAL_DAYS * 86400:
            return None

        window_start = last_at if last_at else (now - WEEKLY_DIGEST_INTERVAL_DAYS * 86400)
        window_reports = [r for r in list_reports(paths) if r.created_at >= window_start]

        if not window_reports:
            # 没有新报告也要推进"上次检查时间"，避免每次 daily cycle 都
            # 重新计算同一个空窗口——但不落一条空摘要消息。
            state["last_weekly_digest_at"] = now
            _save_growth_state(paths, state)
            return None

        window_reports.sort(key=lambda r: -r.created_at)
        lines = [f"- {r.title}" for r in window_reports]
        body = (
            f"过去 {WEEKLY_DIGEST_INTERVAL_DAYS} 天为你生成了 {len(window_reports)} "
            f"份成长调研报告：\n" + "\n".join(lines)
        )

        from mini_agent.notification.dispatcher import NotificationDispatcher, NotificationMessage
        from mini_agent.notification import reports_store

        message = NotificationMessage(
            title=f"成长顾问周摘要（{len(window_reports)} 份报告）",
            body=body,
            source="growth_weekly_digest",
            meta={"report_ids": [r.report_id for r in window_reports]},
        )
        results = NotificationDispatcher(paths).dispatch(message)
        reports_store.append_report(
            paths,
            {
                "title": message.title,
                "body": message.body,
                "source": message.source,
                "report_ids": [r.report_id for r in window_reports],
                "created_at": message.created_at,
                "acknowledged": False,
            },
        )
        state["last_weekly_digest_at"] = now
        _save_growth_state(paths, state)
        return {
            "report_ids": [r.report_id for r in window_reports],
            "count": len(window_reports),
            "channels": results,
        }
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.growth_advisor._maybe_dispatch_weekly_digest")
        return None


def _maybe_dispatch_notification(
    paths, cfg, candidates_by_id: dict[str, GrowthCandidate], reports: list[GrowthReport]
) -> Optional[dict]:
    """方案第 4.2 节推送节流：看板展示不受限，主动推送（通知中心/邮件）
    才需要节流——本函数只负责"要不要推、推哪一条"，看板轮询走的是
    `/growth/summary` 只读端点，跟这里完全独立、不受影响。

    规则：
        - `notification_frequency == "kanban_only"` 或本轮没有新报告 ->
          不推送。
        - 只在达到 `notification_min_confidence` 的报告里选置信度最高
          的一条；全部达不到阈值 -> 不推送（"宁可不推，不为了凑数硬推"，
          方案第 4.2 节原文）。
        - 当天（自然日，本地时区）已推送次数达到 `notification_max_per_day`
          -> 不再推送，状态落盘在 `paths.growth_state_path`。
        - 任何一步异常都不应该打断 `run_daily_cycle` 主流程，统一
          try/except + log_exception 兜底，返回 None。
    """
    reports = list(reports or [])
    if not reports:
        return None
    freq = getattr(cfg, "notification_frequency", "daily")
    if freq in ("kanban_only", "weekly_digest"):
        # weekly_digest 走独立的 _maybe_dispatch_weekly_digest()，不复用
        # 这里的按天节流；防御性地在这里也短路一次，避免调用方误接错分支
        # 时把 weekly_digest 误当成 daily 逐条推送。
        return None

    min_conf = getattr(cfg, "notification_min_confidence", 0.6)
    max_per_day = getattr(cfg, "notification_max_per_day", 1)

    scored: list[tuple[float, GrowthReport]] = []
    for r in reports:
        cand = candidates_by_id.get(r.candidate_id)
        conf = cand.confidence if cand is not None else 0.0
        if conf >= min_conf:
            scored.append((conf, r))
    if not scored:
        return None
    scored.sort(key=lambda t: -t[0])
    best_conf, best_report = scored[0]

    try:
        state = _load_growth_state(paths)
        today = _today_str()
        if state.get("last_notify_date") != today:
            state["last_notify_date"] = today
            state["notify_count_today"] = 0
        if state.get("notify_count_today", 0) >= max_per_day:
            return None

        from mini_agent.notification.dispatcher import NotificationDispatcher, NotificationMessage
        from mini_agent.notification import reports_store

        message = NotificationMessage(
            title=f"成长顾问：{best_report.title}",
            body=best_report.summary,
            source="growth_report",
            meta={
                "candidate_id": best_report.candidate_id,
                "report_id": best_report.report_id,
                "confidence": best_conf,
            },
        )
        results = NotificationDispatcher(paths).dispatch(message)
        reports_store.append_report(
            paths,
            {
                "title": message.title,
                "body": message.body,
                "source": message.source,
                "candidate_id": best_report.candidate_id,
                "report_id": best_report.report_id,
                "confidence": best_conf,
                "created_at": message.created_at,
                "acknowledged": False,
            },
        )
        state["notify_count_today"] = state.get("notify_count_today", 0) + 1
        _save_growth_state(paths, state)
        return {"report_id": best_report.report_id, "confidence": best_conf, "channels": results}
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.growth_advisor._maybe_dispatch_notification")
        return None


# ────────────────────────── 每日流程封装（供 cron / CLI 复用） ──────────────────────────


def run_daily_cycle(paths, cfg, profile, memory_store, *, llm_helper: Optional[Callable[[str], str]] = None) -> dict[str, Any]:
    """`sys:growth_advisor_daily` 与 `/growth scan` 共用的主流程：
    信号扫描 -> 候选生成 -> （置信度达标的）Top-N 生成调研报告 ->
    （P2 新增）按 4.2 节节流规则决定要不要推送一条通知。

    P3：`llm_helper` 只有在 `cfg.llm_signal_augment_enabled=True` 时才会
    真正传给 `growth_signal_scan`（默认 False，零 LLM 成本）——即使调用方
    在有 agent 上下文的场景下总是能拿到 `llm_helper`，是否使用仍然由
    这个显式开关控制，不因为"恰好有"就默认用上。
    """
    if not getattr(cfg, "enabled", True):
        return {"skipped": True, "reason": "growth_advisor disabled"}

    scan_llm_helper = llm_helper if getattr(cfg, "llm_signal_augment_enabled", False) else None
    growth_signal_scan(paths, profile, memory_store, llm_helper=scan_llm_helper)
    new_candidates = growth_candidate_derive(paths, cfg, profile)

    max_reports = getattr(cfg, "max_reports_per_run", 2)
    top = sorted(new_candidates, key=lambda c: -c.confidence)[:max_reports]
    reports = [generate_growth_report(paths, c) for c in top]

    candidates_by_id = {c.candidate_id: c for c in top}
    freq = getattr(cfg, "notification_frequency", "daily")
    if freq == "weekly_digest":
        notification = _maybe_dispatch_weekly_digest(paths, cfg)
    else:
        notification = _maybe_dispatch_notification(paths, cfg, candidates_by_id, reports)

    return {
        "skipped": False,
        "new_candidates": [c.candidate_id for c in new_candidates],
        "reports": [r.report_id for r in reports],
        "notification": notification,
    }


def growth_topic_map(paths) -> list[dict]:
    """跨候选的主题聚合视图（方案第 6 节"能力地图"聚合，对齐
    `self_model_snapshot.py` 的思路——只是问题从"Agent 自己的能力弱项
    清单变长变短"换成了"用户在每个成长方向上的推进轨迹"）。

    按 `dedupe_key`（归一化标题）聚合 backlog 里**全部**历史候选（包括
    因 dismiss 冷却期结束后重新生成、标题相同但 candidate_id 不同的多
    条记录），得到每个主题的：
        - 当前状态（取 updated_at 最新的一条）
        - 历史累计被采纳/被忽略次数（一个主题可能经历多轮 dismiss ->
          冷却 -> 重新生成 -> 再次 dismiss/accepted）
        - 历史出现过的最高置信度（衡量该方向证据积累的峰值，不因为
          某次 dismiss 后置信度被打折而"倒退"）
        - 首次出现时间 / 最近更新时间

    只做聚合展示，不做任何预测/排序推荐——聚合结果按 `updated_at` 倒序
    返回，供看板/CLI 直接渲染成一张列表，不引入新的落盘文件。
    """
    all_c = GrowthBacklog(paths).load_all()
    if not all_c:
        return []

    groups: dict[str, list[GrowthCandidate]] = {}
    for c in all_c:
        groups.setdefault(c.dedupe_key(), []).append(c)

    rows: list[dict] = []
    for key, items in groups.items():
        items_sorted = sorted(items, key=lambda c: c.updated_at)
        latest = items_sorted[-1]
        rows.append(
            {
                "topic": latest.title,
                "current_status": latest.status,
                "current_confidence": latest.confidence,
                "peak_confidence": max(c.confidence for c in items),
                "times_accepted": sum(1 for c in items if c.status == STATUS_ACCEPTED),
                "times_dismissed": sum(1 for c in items if c.status == STATUS_DISMISSED),
                "occurrences": len(items),
                "first_seen_at": min(c.created_at for c in items),
                "last_updated_at": latest.updated_at,
            }
        )

    rows.sort(key=lambda r: -r["last_updated_at"])
    return rows


def monthly_retrospective_summary(paths) -> dict[str, Any]:
    """月度成长复盘统计。P2 在 P1 的数量统计基础上新增 `acceptance_rate`
    （采纳率）与按候选标题聚合的采纳/忽略排行——对应方案第 6 节"推荐命中
    率"这类自我评估指标；跨候选的能力地图聚合仍留给 P3。"""
    backlog = GrowthBacklog(paths)
    all_c = backlog.load_all()
    ledger = GrowthFeedbackLedger(paths).all_entries()
    accepted = sum(1 for c in all_c if c.status == STATUS_ACCEPTED)
    dismissed = sum(1 for c in all_c if c.status == STATUS_DISMISSED)
    pending = sum(1 for c in all_c if c.status == STATUS_PENDING)
    decided = accepted + dismissed
    acceptance_rate = round(accepted / decided, 3) if decided else None

    accepted_topics: dict[str, int] = {}
    dismissed_topics: dict[str, int] = {}
    for c in all_c:
        if c.status == STATUS_ACCEPTED:
            accepted_topics[c.title] = accepted_topics.get(c.title, 0) + 1
        elif c.status == STATUS_DISMISSED:
            dismissed_topics[c.title] = dismissed_topics.get(c.title, 0) + 1

    top_accepted = sorted(accepted_topics.items(), key=lambda kv: -kv[1])[:5]
    top_dismissed = sorted(dismissed_topics.items(), key=lambda kv: -kv[1])[:5]

    return {
        "total_candidates": len(all_c),
        "accepted": accepted,
        "dismissed": dismissed,
        "pending": pending,
        "acceptance_rate": acceptance_rate,
        "feedback_events": len(ledger),
        "reports_generated": len(list_reports(paths)),
        "top_accepted_topics": top_accepted,
        "top_dismissed_topics": top_dismissed,
        "topic_map": growth_topic_map(paths),
    }
