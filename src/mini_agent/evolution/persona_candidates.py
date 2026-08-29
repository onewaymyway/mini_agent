"""evolution/persona_candidates.py — 候选人设/能力自动检测（Persona Candidate
Auto-Scan）。

设计背景见 next_doc/persona_candidate_autoscan_plan.md（v0.2）。本模块是
`capability_learning.py`（`target_type="persona"` 全链路）的一个平行子
系统——新建 Track/人设的另一条自动化入口，体验对齐已跑通的
`growth_advisor.py`（信号扫描 → 候选 → 用户采纳/忽略）范式。不改动
`capability_learning.py` 已有的 Track/大纲/检索/问答队列/发布任何一个
环节；候选一旦被采纳，就是一条普通的 `target_type="persona"` Track，
之后完全走既有闭环（`CapabilityTrackStore`）。

数据模型 + 存储 + 扫描/去重的纯逻辑层，不依赖 FastAPI/Streamlit，和
`capability_learning.py` 的分层原则一致：
    - HTTP 接线见 api/persona_candidate_routes.py
    - cron 接线见 cron_scheduler.py 的 `sys:persona_candidate_scan`
      （默认 `enabled: False`，opt-in）
    - CLI 接线见 cli/commands/capability_cmd.py 的
      `/capability persona_candidates ...` 子命令（供 cron task_template
      引用，与 `/capability cycle` 同款中间层模式）

扫描分三步（方案 §4，候选生成本身也过 LLM，不是规则式直接搬运
growth_advisor 主题名/wiki miss 检索词当标题——那些信号是从别的场景视角
提炼出来的，角度不一定适合直接当人设标题）：
    1. 收集原始信号（规则式，不调 LLM，只做采集+粗筛）：
       `_effective_topic_keywords()` 里已确认的成长方向 + capability
       Track 台账里的高频 wiki miss 检索词，各自按 Top N 截断。
    2. LLM 提炼候选（一次批量调用，`_extract_candidates_with_llm()`）：
       站在"是否值得养成一个专属人设/能力方向"的角度重新提炼 0~N 条候选。
    3. LLM 判重（每条候选一次调用，直接复用
       `growth_advisor._llm_find_duplicate_direction()`，不另写一套）：
       命中已存在人设/Track 或近期被忽略过的候选标题，直接跳过不落盘。

两次 LLM 调用职责单一、互不合并，理由见方案 §4 末尾。任一步骤调用/解析
失败都静默退回"本轮无候选"或"当作不重复"，不中断整个扫描（对齐
`draft_outline_with_llm()`/`_llm_find_duplicate_direction()` 的降级
策略：起草辅助而非关键路径）。
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional

from mini_agent.storage.paths import AgentPaths

# ── 状态机（pending -> accepted | dismissed，无 TTL 自动过期——候选量级
#    本身受 max_pending_candidates 节流，不像 growth_advisor 的成长方向
#    那样长期持续产生，暂不需要 expired 状态，待实际使用后再评估）───────

STATUS_PENDING = "pending"
STATUS_ACCEPTED = "accepted"
STATUS_DISMISSED = "dismissed"

_VALID_STATUSES = (STATUS_PENDING, STATUS_ACCEPTED, STATUS_DISMISSED)

# 去重判断"命中即跳过、不区分已存在/已忽略"，两个来源合并处理动作相同；
# reason 仍复用 growth_advisor 的 DISMISS_REASON_* 常量族，至少要覆盖
# ALREADY_EXISTS/NOT_INTERESTED 两个语义（方案 §3 数据模型）。
DISMISS_REASON_ALREADY_EXISTS = "already_exists"
DISMISS_REASON_NOT_INTERESTED = "not_interested"
DISMISS_REASON_UNSPECIFIED = "unspecified"


@dataclass
class PersonaCandidate:
    candidate_id: str
    title: str
    persona_desc: str
    rationale: str
    evidence_count: int
    evidence_refs: list[str] = field(default_factory=list)
    source: str = "manual_scan"       # growth_topic / wiki_miss / manual_scan（混合信号时用 manual_scan）
    dedupe_key: str = ""
    status: str = STATUS_PENDING
    created_at: float = field(default_factory=time.time)
    decided_at: Optional[float] = None
    dismiss_reason: Optional[str] = None
    accepted_track_id: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PersonaCandidate":
        return cls(
            candidate_id=d["candidate_id"],
            title=d.get("title", ""),
            persona_desc=d.get("persona_desc", ""),
            rationale=d.get("rationale", ""),
            evidence_count=int(d.get("evidence_count", 0) or 0),
            evidence_refs=list(d.get("evidence_refs", [])),
            source=d.get("source", "manual_scan"),
            dedupe_key=d.get("dedupe_key", ""),
            status=d.get("status", STATUS_PENDING),
            created_at=d.get("created_at", time.time()),
            decided_at=d.get("decided_at"),
            dismiss_reason=d.get("dismiss_reason"),
            accepted_track_id=d.get("accepted_track_id"),
        )


# ── 复用 growth_advisor.py 的去重契约（不另写一套）───────────────────────


def normalize_title_key(title: str) -> str:
    """与 `growth_advisor.normalize_title_key` 算法完全一致（字面归一化，
    LLM 语义判重之前的第一道快速过滤）。不直接 import 该函数只是因为它是
    模块私有算法的公开复制品——两边独立维护同一份简单算法比互相 import
    引入耦合更稳妥，和该函数在 growth_advisor.py 里的说明保持同样取舍。"""
    s = (title or "").lower().strip()
    s = re.sub(r"[^\w\s]", " ", s)
    return " ".join(sorted(s.split()))


def _llm_find_duplicate_title(
    new_title: str,
    existing_titles: list[str],
    llm_helper: Callable[[str], str],
) -> Optional[str]:
    """候选去重 LLM 判重（方案 §4.2）：直接复用
    `growth_advisor._llm_find_duplicate_direction()` 的现成实现和契约
    （一次性把标题池 + 新标题交给 LLM，命中要求逐字复制原文，未命中输出
    `NONE`，失败/解析不出时退回"当作不重复"）——本方案的判重对象从"成长
    方向"换成"人设/能力方向"，语义完全一致，不需要另写一份 prompt。"""
    from mini_agent.evolution.growth_advisor import _llm_find_duplicate_direction

    return _llm_find_duplicate_direction(new_title, existing_titles, llm_helper)


# ── JSON 读写（与 capability_learning.py/growth_advisor.py 同风格）───────


def _read_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _write_json(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 存储 ─────────────────────────────────────────────────────────────────


class PersonaCandidateStore:
    """单个 JSON 文件落盘（含全部 pending/accepted/dismissed 记录），仿
    `growth_advisor.GrowthBacklog` 的落盘方式。候选量级不大（受
    `max_pending_candidates` 节流），先不上 JSONL 分文件（方案 §3）。"""

    def __init__(self, paths: AgentPaths):
        self._paths = paths

    def load_all(self) -> list[PersonaCandidate]:
        raw = _read_json(self._paths.persona_candidates_path, default=[])
        return [PersonaCandidate.from_dict(d) for d in raw]

    def _save_all(self, candidates: list[PersonaCandidate]) -> None:
        _write_json(self._paths.persona_candidates_path, [c.to_dict() for c in candidates])

    def list_candidates(self, status: Optional[str] = None) -> list[PersonaCandidate]:
        all_c = self.load_all()
        if status:
            all_c = [c for c in all_c if c.status == status]
        return sorted(all_c, key=lambda c: c.created_at, reverse=True)

    def get(self, candidate_id: str) -> Optional[PersonaCandidate]:
        for c in self.load_all():
            if c.candidate_id == candidate_id:
                return c
        return None

    def add(self, candidate: PersonaCandidate) -> PersonaCandidate:
        all_c = self.load_all()
        all_c.append(candidate)
        self._save_all(all_c)
        return candidate

    def set_status(
        self,
        candidate_id: str,
        status: str,
        *,
        reason: Optional[str] = None,
        accepted_track_id: Optional[str] = None,
    ) -> Optional[PersonaCandidate]:
        if status not in _VALID_STATUSES:
            raise ValueError(f"invalid status: {status}")
        all_c = self.load_all()
        for i, c in enumerate(all_c):
            if c.candidate_id != candidate_id:
                continue
            c.status = status
            c.decided_at = time.time()
            if status == STATUS_DISMISSED:
                c.dismiss_reason = reason or DISMISS_REASON_UNSPECIFIED
            if status == STATUS_ACCEPTED and accepted_track_id:
                c.accepted_track_id = accepted_track_id
            all_c[i] = c
            self._save_all(all_c)
            return c
        return None


# ── §4.1 原始信号收集（规则式，粗筛，不调用 LLM）────────────────────────


def _collect_topic_signals(profile, top_n: int) -> list[dict]:
    """复用 growth_advisor `_effective_topic_keywords()` 里
    `confirmed_by_user=True` 或 `auto_confirmed=True` 的主题——这些是已经
    被验证"用户持续关注"的方向，作为候选提炼的原始素材（不直接当标题）。
    按关键词数量粗略排序（关键词越多通常代表信号越丰富），取 Top N。"""
    from mini_agent.evolution.growth_advisor import _effective_topic_keywords

    if profile is None:
        return []
    effective = _effective_topic_keywords(profile)
    signals = [
        {"topic": topic, "keywords": info.get("keywords") or []}
        for topic, info in effective.items()
        if info.get("confirmed_by_user") or info.get("auto_confirmed")
    ]
    signals.sort(key=lambda s: len(s["keywords"]), reverse=True)
    return signals[:top_n]


def _collect_wiki_miss_signals(paths: AgentPaths, track_store, top_n: int) -> list[dict]:
    """复用 capability_learning.py 的 wiki miss 台账（`record_wiki_miss()`
    累积的高频未命中检索）——按检索词聚类，取出现次数较高的一批原始
    查询词，同样作为候选提炼的素材而非直接当标题。只扫 active 状态的
    Track（archived/paused 的台账不代表当前仍在关心的方向）。"""
    from mini_agent.evolution.capability_learning import CapabilityLedgerStore

    ledger_store = CapabilityLedgerStore(paths)
    counts: dict[str, int] = {}
    for track in track_store.list_tracks(status="active"):
        for entry in ledger_store.list_for_track(track.track_id, limit=200):
            if entry.action != "miss_observed":
                continue
            query = entry.summary or ""
            if query.startswith("检索未命中："):
                query = query[len("检索未命中："):]
            query = query.strip()
            if not query:
                continue
            counts[query] = counts.get(query, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    return [{"query": q, "count": n} for q, n in ranked[:top_n]]


# ── §4.1 LLM 候选提炼 prompt/解析 ────────────────────────────────────────


def _build_extraction_prompt(topic_signals: list[dict], miss_signals: list[dict]) -> str:
    topic_lines = "\n".join(
        f"- {s['topic']}（关键词：{', '.join(s['keywords'])}）"
        for s in topic_signals
    ) or "（无）"
    miss_lines = "\n".join(
        f"- {s['query']}（出现 {s['count']} 次）" for s in miss_signals
    ) or "（无）"
    return (
        "下面是从这个人最近的对话记忆/知识检索记录里整理出的一些原始信号"
        "（不代表最终结论，只是素材）：\n\n"
        "【持续关注的方向】（来自成长顾问，用户反复表现出兴趣并已确认）\n"
        f"{topic_lines}\n\n"
        "【反复检索但目前没有对应知识沉淀的内容】（来自知识库未命中记录）\n"
        f"{miss_lines}\n\n"
        "请你站在\"是否值得为这个人养成一个专属的人设/能力方向，让 Agent 持续"
        "学习、专精支撑 ta\"的角度，重新判断、提炼出 0 到 5 个值得建议的人设/"
        "能力方向。不要求和上面的原始素材字面一致——原始素材可能是从别的场景"
        "（成长方向追踪/单次检索）提炼出来的，角度不一定适合直接当人设标题，"
        "请你重新组织表述。如果原始素材不足以支撑任何靠谱的建议，可以输出"
        "空列表，不要为了凑数量硬造。\n\n"
        "请只输出如下 JSON 数组，不要输出任何其它文字：\n"
        "[\n"
        "  {\n"
        "    \"title\": \"人设/能力方向标题，简洁，不超过 20 字\",\n"
        "    \"persona_desc\": \"一段 1-2 句的简介，说明这个人设/能力方向具体是"
        "指什么、大致覆盖哪些子领域\",\n"
        "    \"rationale\": \"为什么建议这个方向，需要提到依据了上面哪些原始信号\"\n"
        "  }\n"
        "]"
    )


def _parse_llm_candidates_json(raw: str) -> list[dict]:
    """防御式解析：strip markdown 代码块围栏，`json.loads` 失败时整批放弃
    本轮提炼（对齐 `classify_topic_category_llm()` 等既有容错写法）。"""
    if not raw or not raw.strip():
        return []
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        persona_desc = str(item.get("persona_desc", "")).strip()
        rationale = str(item.get("rationale", "")).strip()
        if not title or not persona_desc:
            continue
        out.append({"title": title[:60], "persona_desc": persona_desc, "rationale": rationale})
    return out


def _extract_candidates_with_llm(
    topic_signals: list[dict],
    miss_signals: list[dict],
    llm_helper: Callable[[str], str],
) -> list[dict]:
    if not topic_signals and not miss_signals:
        return []
    prompt = _build_extraction_prompt(topic_signals, miss_signals)
    try:
        raw = llm_helper(prompt)
    except Exception:
        return []
    return _parse_llm_candidates_json(raw)


# ── §4 已存在/已忽略标题池 ───────────────────────────────────────────────


def _existing_title_pool(paths: AgentPaths, track_store) -> list[str]:
    """已存在人设（已发布的 `.agent/personas/*.md`）+ 已存在能力 Track
    （`target_type` 为 persona 或 knowledge 均算，避免和已有方向重复；
    active/paused 计入，archived 不计入，允许重新提议）。"""
    from mini_agent.orchestrator.persona_profiles import list_personas_for_paths

    titles: list[str] = []
    for p in list_personas_for_paths(paths):
        name = p.display_name or p.name
        if name:
            titles.append(name)
    for t in track_store.list_tracks():
        if t.status in ("active", "paused") and t.title:
            titles.append(t.title)
    return titles


def _dismissed_title_pool(store: PersonaCandidateStore, cooldown_days: int) -> list[str]:
    """近期被忽略（status == dismissed 且 decided_at 在冷却期内）的候选
    标题池——冷却期外的忽略记录允许重新提议（对齐
    `GrowthBacklog` 的 `dismissed_cooldown_days` 处理逻辑）。"""
    cutoff = time.time() - cooldown_days * 86400
    titles = []
    for c in store.load_all():
        if c.status != STATUS_DISMISSED:
            continue
        if c.decided_at is not None and c.decided_at < cutoff:
            continue
        titles.append(c.title)
    return titles


# ── 扫描主入口（方案 §4）────────────────────────────────────────────────


def scan_persona_candidates(
    paths: AgentPaths,
    cfg,
    profile,
    llm_helper: Optional[Callable[[str], str]],
) -> list[PersonaCandidate]:
    """执行一轮候选人设/能力方向扫描，返回本轮新落盘的候选列表（不包含
    历史已有的 pending 候选）。

    `cfg` 预期是 `PersonaCandidateConfig`（或兼容其字段的对象），用到的
    字段：`dismissed_cooldown_days`/`max_pending_candidates`/
    `topic_signal_top_n`/`wiki_miss_signal_top_n`。没有 `llm_helper`
    （拿不到 agent.llm_helper，或调用方显式不传）时直接返回空列表——候选
    生成本身依赖 LLM 提炼（方案 §4 背景），没有 LLM 就没有候选，不做
    规则式兜底生成。
    """
    if llm_helper is None:
        return []

    from mini_agent.evolution.capability_learning import CapabilityTrackStore

    track_store = CapabilityTrackStore(paths)
    candidate_store = PersonaCandidateStore(paths)

    max_pending = getattr(cfg, "max_pending_candidates", 10)
    pending_count = len(candidate_store.list_candidates(status=STATUS_PENDING))
    if pending_count >= max_pending:
        return []

    topic_top_n = getattr(cfg, "topic_signal_top_n", 8)
    miss_top_n = getattr(cfg, "wiki_miss_signal_top_n", 8)
    cooldown_days = getattr(cfg, "dismissed_cooldown_days", 30)

    topic_signals = _collect_topic_signals(profile, topic_top_n)
    miss_signals = _collect_wiki_miss_signals(paths, track_store, miss_top_n)
    if not topic_signals and not miss_signals:
        return []

    raw_candidates = _extract_candidates_with_llm(topic_signals, miss_signals, llm_helper)
    if not raw_candidates:
        return []

    existing_titles = _existing_title_pool(paths, track_store)
    dismissed_titles = _dismissed_title_pool(candidate_store, cooldown_days)
    dedupe_pool = existing_titles + dismissed_titles

    evidence_refs = [f"topic:{s['topic']}" for s in topic_signals] + [
        f"wiki_miss:{s['query']}" for s in miss_signals
    ]
    evidence_count = len(evidence_refs)

    created: list[PersonaCandidate] = []
    room = max_pending - pending_count
    for item in raw_candidates:
        if room <= 0:
            break
        title = item["title"]
        key = normalize_title_key(title)
        # 字面重复：候选池/已存在标题池里已经有完全同名的，跳过（不需要
        # 走一次 LLM 调用）。
        if any(normalize_title_key(t) == key for t in dedupe_pool):
            continue
        if any(normalize_title_key(c.title) == key for c in candidate_store.list_candidates(status=STATUS_PENDING)):
            continue
        # 语义重复：LLM 判重（方案 §4.2），失败/无匹配时当作不重复。
        match = _llm_find_duplicate_title(title, dedupe_pool, llm_helper) if dedupe_pool else None
        if match is not None:
            continue

        candidate = PersonaCandidate(
            candidate_id=f"pcand_{uuid.uuid4().hex[:12]}",
            title=title,
            persona_desc=item["persona_desc"],
            rationale=item.get("rationale", ""),
            evidence_count=evidence_count,
            evidence_refs=evidence_refs,
            source="manual_scan",
            dedupe_key=key,
        )
        candidate_store.add(candidate)
        created.append(candidate)
        room -= 1

    return created


# ── 采纳 / 忽略 ─────────────────────────────────────────────────────────


def accept_candidate(paths: AgentPaths, candidate_id: str) -> Optional[dict]:
    """采纳一条候选：调用 `CapabilityTrackStore.create(..., target_type=
    "persona")` 创建一条空大纲的 Track（大纲之后在 Track 详情页补充，
    对齐方案 §8 待确认问题 3 的倾向），回写 `accepted_track_id`。不自动
    创建 `.agent/personas/*.md` 人设文件——是否/何时正式发布，沿用
    `persona_capability_learning_design.md` §10 已有的发布流程。"""
    from mini_agent.evolution.capability_learning import CapabilityTrackStore

    store = PersonaCandidateStore(paths)
    candidate = store.get(candidate_id)
    if candidate is None or candidate.status != STATUS_PENDING:
        return None

    track_store = CapabilityTrackStore(paths)
    track = track_store.create(
        title=candidate.title,
        persona_desc=candidate.persona_desc,
        target_type="persona",
    )
    updated = store.set_status(candidate_id, STATUS_ACCEPTED, accepted_track_id=track.track_id)
    return {"candidate": updated.to_dict(), "track": track.to_dict()} if updated else None


def dismiss_candidate(
    paths: AgentPaths, candidate_id: str, reason: Optional[str] = None
) -> Optional[PersonaCandidate]:
    store = PersonaCandidateStore(paths)
    candidate = store.get(candidate_id)
    if candidate is None or candidate.status != STATUS_PENDING:
        return None
    return store.set_status(candidate_id, STATUS_DISMISSED, reason=reason)
