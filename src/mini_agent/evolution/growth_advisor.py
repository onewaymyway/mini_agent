"""成长顾问 Growth Advisor（对应 next_doc/growth_advisor_design.md）。

与 evolution/ 目录下服务"Agent 自我进化"的模块（soft_goal_deriver /
decision_profile_builder / objective_outcome_tracker ...）是姊妹关系：
那些模块把用户的反馈/记忆折射回 Agent 自身的行为改进，这个模块则是把
同一批记忆信号折射回**用户自己的成长方向**——候选生成、调研报告生成、
反馈台账三层结构完全复用 evolution/ 里已经跑通的"证据 → 候选 → 采纳/
忽略反馈回路"范式（方案第 3 节 P1 里程碑）。

P1 范围（本次实现）：
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

不在本次范围内（见方案 P2/P3，占位在 next_doc 文档里）：
    - 月度成长复盘的深度归因、跨候选的能力地图聚合
    - 看板里的拖拽式看板视图（P1 只做列表 + 采纳/忽略两个动作）
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

        cand = GrowthCandidate(
            candidate_id=uuid.uuid4().hex[:12],
            title=title,
            rationale=rationale,
            evidence_refs=sorted(set(evidence_refs)),
            evidence_count=len(set(evidence_refs)),
            confidence=_confidence_from_evidence(len(set(evidence_refs))),
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


def growth_signal_scan(paths, profile, memory_store, *, window_days: int = SIGNAL_SCAN_WINDOW_DAYS) -> dict[str, list[str]]:
    """扫描最近 window_days 内的 memory entries，按 `_TOPIC_KEYWORDS` 做
    命中统计，把 {主题: [entry_id...]} 写入
    `profile.derived["growth_focus_areas"]`（结构化，供 candidate_derive
    直接消费），并返回该结果供调用方（cron / CLI）立即使用。

    这是规则式实现（P1），不依赖 LLM——保证 `enabled=True` 默认开启时
    不会给每个用户都额外产生 LLM 调用成本。
    """
    cutoff = time.time() - window_days * 86400
    hits: dict[str, list[str]] = {}

    entries = memory_store.all_entries() if memory_store is not None else []
    for entry in entries:
        if getattr(entry, "created_at", 0) < cutoff:
            continue
        haystack = " ".join(
            [getattr(entry, "summary", "") or ""]
            + list(getattr(entry, "tags", []) or [])
        ).lower()
        for topic, keywords in _TOPIC_KEYWORDS.items():
            if any(kw.lower() in haystack for kw in keywords):
                hits.setdefault(topic, []).append(getattr(entry, "entry_id", "") or "")

    derived = dict(getattr(profile, "derived", {}) or {})
    derived["growth_focus_areas"] = hits
    derived["growth_focus_areas_updated_at"] = time.time()
    profile.derived = derived
    return hits


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

    produced: list[GrowthCandidate] = []
    # 按证据数从多到少处理，保证 max_pending 限额下优先生成信号更强的候选
    for topic, refs in sorted(focus_areas.items(), key=lambda kv: -len(kv[1])):
        if topic.strip().lower() in excluded:
            continue
        rationale = f"最近记忆里与「{topic}」相关的内容出现了 {len(set(refs))} 次，可能是值得投入的方向。"
        cand = backlog.add_or_merge(
            title=topic,
            rationale=rationale,
            evidence_refs=refs,
            min_evidence_count=min_evidence_count,
            max_pending=max_pending,
            dismissed_cooldown_days=cooldown_days,
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


# ────────────────────────── 每日流程封装（供 cron / CLI 复用） ──────────────────────────


def run_daily_cycle(paths, cfg, profile, memory_store) -> dict[str, Any]:
    """`sys:growth_advisor_daily` 与 `/growth scan` 共用的主流程：
    信号扫描 → 候选生成 → （置信度达标的）Top-N 生成调研报告。
    不做任何推送/通知——推送节奏是 notification_frequency 独立控制的
    另一层，由上层调用方（cron job / 通知调度器）决定要不要用这里的
    返回值触发一次通知。
    """
    if not getattr(cfg, "enabled", True):
        return {"skipped": True, "reason": "growth_advisor disabled"}

    growth_signal_scan(paths, profile, memory_store)
    new_candidates = growth_candidate_derive(paths, cfg, profile)

    max_reports = getattr(cfg, "max_reports_per_run", 2)
    top = sorted(new_candidates, key=lambda c: -c.confidence)[:max_reports]
    reports = [generate_growth_report(paths, c) for c in top]

    return {
        "skipped": False,
        "new_candidates": [c.candidate_id for c in new_candidates],
        "reports": [r.report_id for r in reports],
    }


def monthly_retrospective_summary(paths) -> dict[str, Any]:
    """月度成长复盘统计（P1：只做数量统计，深度归因留给 P2）。"""
    backlog = GrowthBacklog(paths)
    all_c = backlog.load_all()
    ledger = GrowthFeedbackLedger(paths).all_entries()
    accepted = sum(1 for c in all_c if c.status == STATUS_ACCEPTED)
    dismissed = sum(1 for c in all_c if c.status == STATUS_DISMISSED)
    pending = sum(1 for c in all_c if c.status == STATUS_PENDING)
    return {
        "total_candidates": len(all_c),
        "accepted": accepted,
        "dismissed": dismissed,
        "pending": pending,
        "feedback_events": len(ledger),
        "reports_generated": len(list_reports(paths)),
    }
