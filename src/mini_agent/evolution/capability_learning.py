"""evolution/capability_learning.py — 人设能力自主学习（P1 最小可用闭环）

设计背景见 next_doc/persona_capability_learning_design.md。

本模块只负责数据模型 + 存储 + 纯逻辑（缺口扫描 / 台账 / 异步问答队列），
不直接依赖 FastAPI / cron_scheduler / kanban，方便单元测试和后续拆分接线：
    - HTTP 接线见 api/capability_routes.py（独立 router，已挂载到
      api/server.py，见该文件顶部注释）
    - cron 接线见本文件 `run_capability_learning_cycle()`，`cron_scheduler.py`
      已注册 `sys:capability_learning_cycle`（默认 `enabled: False`，opt-in）

已落地（P1，全部完成）：
    - CapabilityTrack / OutlineTopic / CapabilityLedgerEntry / CapabilityQuestion
      数据模型 + 存储路径（storage/paths.py 已新增对应 property）
    - 大纲缺口扫描（规则式，§4 设计文档）
    - CapabilityQuestion 异步问答队列的生成 / 提交 / 消费 / 过期清理
      （§3.3、§10.2，`sweep_expired()` 供 `sys:capability_question_sweep` 引用）
    - CapabilityLedgerEntry 台账记录（§3.2）
    - run_capability_learning_cycle()：单轮循环的编排函数，检索/wiki 写入
      两步以可注入的回调形式暴露
    - make_wiki_writer(paths)：真实的 wiki 写入回调（对接 wiki/writer.py），
      不依赖网络，已有单测覆盖并验证能写出可被 wiki/parser.py 解析的合法
      页面。**尚未接入 wiki/dedup.py 判重**——需要先确认"按 wiki_tag
      批量加载已有页面"该走哪条现成接口，留到接线阶段和 wiki 模块维护者
      一起确认，避免猜测一个不确定正确性的集成方式
    - make_web_search_retriever(cfg)：真实的检索回调（对接
      web_search/factory.py 既有 provider 抽象），受
      `CapabilityLearningConfig.retriever_enabled` 开关控制（默认 False，
      opt-in，见该配置字段上方注释）；开启后写入前仍会经过
      §13.3-g 合规过滤，不会绕过
    - PersonaProfile.wiki_scopes 接线（§11）：`context_builder.py` 每轮
      检索把当前激活角色的 `wiki_scopes` 透传给 `wiki_shelf_search(tags=...)`
    - record_wiki_miss() 的接线（§14.1-a）：`context_builder.py` 在 persona
      绑定的 `wiki_scopes` 命中某个 active knowledge 型 Track 的 `wiki_tag`
      且检索未命中时，自动调用 `record_wiki_miss()` 记台账（见
      `ContextBuilder._maybe_record_capability_wiki_miss()`）。刻意只在能
      明确关联到具体 Track 时才记录，不对所有未命中查询做关键词/语义猜测
      式关联
    - HTTP API 已挂载到 api/server.py（`app.include_router(capability_router)`）
    - 看板三区域 UI（人设管理/进度展示/待回答问题），见 apps/mini_agent_kanban
    - §13.3-g 合规过滤：`apply_compliance_filter()` 在 `make_wiki_writer()`
      写入前对检索结果做句级风险表述过滤（具体买卖建议等整句剔除），并对
      金融/医疗/法律等专业建议领域的 Track 页面加 `requires_disclaimer:
      true` frontmatter 标记 + 正文追加免责声明。规则式实现（关键词/正则
      句级过滤），不接 LLM 改写——见该函数上方注释的取舍说明
    - cron 任务表注册 `sys:capability_learning_cycle` / `sys:capability_
      question_sweep`（cron_scheduler.py SYSTEM_JOBS），默认 `enabled: False`
      （opt-in，理由见该条目上方注释）

留给 P2 的方向（P1 刻意不做，避免过早引入不确定性/耦合）：
    - target_type="persona" 全链路（人设草稿生成 / 发布，见文档 §10）
    - 与 external_trend_capability_link / objective_executor / decision_profile_builder /
      capability_map 的协同（见文档 §12，P1 阶段刻意不打通，避免引入耦合风险）
    - LLM 辅助的大纲生成/缺口判定（P1 是规则式，见文档 §14 P2 阶段）
    - 把 `miss_observed` 台账接入 `scan_outline_gaps()` 的优先级排序——
      目前台账只是静态积累，`scan_outline_gaps()` 还没有读取它来调整排序
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING

from mini_agent.storage.paths import AgentPaths

if TYPE_CHECKING:
    from mini_agent.config import AppConfig

# ── 常量（默认值均可后续做成 config_catalog 配置项，P1 先写死）──────────────

DEFAULT_MAX_PENDING_QUESTIONS = 3          # §3.3：单 Track 同时 pending 的问题数上限
DEFAULT_TOPICS_PER_CYCLE = 2               # §4：每轮每个 Track 最多推进的子主题数
DEFAULT_QUESTION_TTL_SECONDS = 14 * 86400  # §3.3：问题超过 14 天未回答自动过期


# ── 数据模型（对应设计文档 §3）────────────────────────────────────────────


@dataclass
class OutlineTopic:
    topic_id: str
    name: str
    coverage_state: str = "uncovered"          # uncovered / partial / covered
    volatility: str = "stable"                  # stable / periodic / volatile（§14.2-d）
    last_touched_at: Optional[float] = None
    wiki_page_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "OutlineTopic":
        return cls(
            topic_id=d["topic_id"],
            name=d["name"],
            coverage_state=d.get("coverage_state", "uncovered"),
            volatility=d.get("volatility", "stable"),
            last_touched_at=d.get("last_touched_at"),
            wiki_page_ids=list(d.get("wiki_page_ids", [])),
        )


@dataclass
class CapabilityTrack:
    track_id: str
    title: str
    persona_desc: str
    outline: list[OutlineTopic] = field(default_factory=list)
    status: str = "active"                      # active / paused / archived
    target_type: str = "knowledge"               # knowledge / persona（§10）
    wiki_tag: str = ""
    excluded_keywords: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    cadence: str = "interval:21600"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["outline"] = [t.to_dict() for t in self.outline]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "CapabilityTrack":
        return cls(
            track_id=d["track_id"],
            title=d["title"],
            persona_desc=d.get("persona_desc", ""),
            outline=[OutlineTopic.from_dict(t) for t in d.get("outline", [])],
            status=d.get("status", "active"),
            target_type=d.get("target_type", "knowledge"),
            wiki_tag=d.get("wiki_tag", ""),
            excluded_keywords=list(d.get("excluded_keywords", [])),
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
            cadence=d.get("cadence", "interval:21600"),
        )


@dataclass
class CapabilityLedgerEntry:
    track_id: str
    topic_id: str
    action: str                                  # researched / question_raised /
                                                   # question_answered / skipped /
                                                   # miss_observed（§14.1-a）
    summary: str
    cycle_ts: float = field(default_factory=time.time)
    wiki_page_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CapabilityLedgerEntry":
        return cls(
            track_id=d["track_id"],
            topic_id=d["topic_id"],
            action=d["action"],
            summary=d.get("summary", ""),
            cycle_ts=d.get("cycle_ts", time.time()),
            wiki_page_ids=list(d.get("wiki_page_ids", [])),
        )


@dataclass
class CapabilityQuestion:
    question_id: str
    track_id: str
    topic_id: str
    question: str
    hint: Optional[str] = None
    status: str = "pending"                       # pending / answered / dismissed / expired
    created_at: float = field(default_factory=time.time)
    answered_at: Optional[float] = None
    answer: Optional[str] = None
    expires_at: Optional[float] = None
    consumed: bool = False                         # 是否已被下一轮循环消费（§4 伪流程）

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CapabilityQuestion":
        return cls(
            question_id=d["question_id"],
            track_id=d["track_id"],
            topic_id=d["topic_id"],
            question=d["question"],
            hint=d.get("hint"),
            status=d.get("status", "pending"),
            created_at=d.get("created_at", time.time()),
            answered_at=d.get("answered_at"),
            answer=d.get("answer"),
            expires_at=d.get("expires_at"),
            consumed=d.get("consumed", False),
        )


# ── 通用 jsonl / json 读写（与 growth_advisor.py 同风格，避免另造一套）──────


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


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── CapabilityTrackStore：Track 的 CRUD（对应设计文档 §7.1 API 的后端实现）──


class CapabilityTrackStore:
    def __init__(self, paths: AgentPaths):
        self._paths = paths

    def _load_all(self) -> list[CapabilityTrack]:
        raw = _read_json(self._paths.capability_tracks_path, default=[])
        return [CapabilityTrack.from_dict(d) for d in raw]

    def _save_all(self, tracks: list[CapabilityTrack]) -> None:
        _write_json(self._paths.capability_tracks_path, [t.to_dict() for t in tracks])

    def list_tracks(self, status: Optional[str] = None) -> list[CapabilityTrack]:
        tracks = self._load_all()
        if status:
            tracks = [t for t in tracks if t.status == status]
        return tracks

    def get(self, track_id: str) -> Optional[CapabilityTrack]:
        for t in self._load_all():
            if t.track_id == track_id:
                return t
        return None

    def create(
        self,
        title: str,
        persona_desc: str,
        outline_names: Optional[list[str]] = None,
        target_type: str = "knowledge",
        wiki_tag: str = "",
    ) -> CapabilityTrack:
        """创建一个新 Track。outline_names 为空时先建空大纲，
        由调用方（看板 / LLM 起草流程，P2 才接 LLM）再补充子主题，
        P1 阶段允许调用方直接传入一份规则式/用户编辑的初始大纲。"""
        track_id = f"cap_{uuid.uuid4().hex[:12]}"
        outline = [
            OutlineTopic(topic_id=f"topic_{uuid.uuid4().hex[:8]}", name=n)
            for n in (outline_names or [])
        ]
        if not wiki_tag:
            slug = "".join(c if c.isalnum() else "_" for c in title.lower())[:40]
            wiki_tag = f"capability:{slug}"
        track = CapabilityTrack(
            track_id=track_id,
            title=title,
            persona_desc=persona_desc,
            outline=outline,
            target_type=target_type,
            wiki_tag=wiki_tag,
        )
        tracks = self._load_all()
        tracks.append(track)
        self._save_all(tracks)
        return track

    def update(self, track_id: str, **fields) -> Optional[CapabilityTrack]:
        """允许更新的字段：title/persona_desc/outline/status/excluded_keywords/cadence。
        outline 若传入，需为 list[OutlineTopic] 或 list[dict]（看板编辑走这条）。"""
        tracks = self._load_all()
        for i, t in enumerate(tracks):
            if t.track_id != track_id:
                continue
            data = t.to_dict()
            for k, v in fields.items():
                if k == "outline" and v is not None:
                    data["outline"] = [
                        (x.to_dict() if isinstance(x, OutlineTopic) else x) for x in v
                    ]
                elif k in data:
                    data[k] = v
            data["updated_at"] = time.time()
            updated = CapabilityTrack.from_dict(data)
            tracks[i] = updated
            self._save_all(tracks)
            return updated
        return None

    def delete(self, track_id: str) -> bool:
        """删除 Track 本身，不级联删除已产出的 wiki 页面（§7.1 设计原则：
        wiki 页面是独立资产，用户可能仍想保留阅读）。"""
        tracks = self._load_all()
        remaining = [t for t in tracks if t.track_id != track_id]
        if len(remaining) == len(tracks):
            return False
        self._save_all(remaining)
        return True


# ── CapabilityLedgerStore：单 Track 的进度台账 ─────────────────────────────


class CapabilityLedgerStore:
    def __init__(self, paths: AgentPaths):
        self._paths = paths

    def append(self, entry: CapabilityLedgerEntry) -> None:
        _append_jsonl(self._paths.capability_ledger_path(entry.track_id), entry.to_dict())

    def list_for_track(self, track_id: str, limit: int = 50) -> list[CapabilityLedgerEntry]:
        rows = _read_jsonl(self._paths.capability_ledger_path(track_id))
        entries = [CapabilityLedgerEntry.from_dict(r) for r in rows]
        entries.sort(key=lambda e: e.cycle_ts, reverse=True)
        return entries[:limit]


# ── CapabilityQuestionStore：异步问答队列（§3.3，本方案最核心的新基础设施）──


class CapabilityQuestionStore:
    """所有读写都是"整体读出、内存改、整体写回"，量级（单个用户的待办问题数）
    不会大到需要索引，和 growth_feedback_ledger 的量级假设一致。"""

    def __init__(self, paths: AgentPaths):
        self._paths = paths

    def _load_all(self) -> list[CapabilityQuestion]:
        rows = _read_jsonl(self._paths.capability_questions_path)
        return [CapabilityQuestion.from_dict(r) for r in rows]

    def _save_all(self, questions: list[CapabilityQuestion]) -> None:
        _write_jsonl(self._paths.capability_questions_path, [q.to_dict() for q in questions])

    def pending_count(self, track_id: str) -> int:
        return sum(
            1 for q in self._load_all() if q.track_id == track_id and q.status == "pending"
        )

    def list_questions(
        self, status: Optional[str] = None, track_id: Optional[str] = None
    ) -> list[CapabilityQuestion]:
        qs = self._load_all()
        if status:
            qs = [q for q in qs if q.status == status]
        if track_id:
            qs = [q for q in qs if q.track_id == track_id]
        qs.sort(key=lambda q: q.created_at, reverse=True)
        return qs

    def raise_question(
        self,
        track_id: str,
        topic_id: str,
        question: str,
        hint: Optional[str] = None,
        ttl_seconds: int = DEFAULT_QUESTION_TTL_SECONDS,
    ) -> CapabilityQuestion:
        """生成一条问题并立即返回——调用方（cron 循环）不等待任何响应，
        这是异步问答机制和 tools/user_input.py 里同步 ask_user 的本质区别
        （见设计文档 §3.3 / §9 第 6 条）。"""
        now = time.time()
        q = CapabilityQuestion(
            question_id=f"capq_{uuid.uuid4().hex[:12]}",
            track_id=track_id,
            topic_id=topic_id,
            question=question,
            hint=hint,
            created_at=now,
            expires_at=now + ttl_seconds,
        )
        questions = self._load_all()
        questions.append(q)
        self._save_all(questions)
        return q

    def answer(self, question_id: str, answer_text: str) -> Optional[CapabilityQuestion]:
        """用户在看板提交回答——纯写入，不触发任何 cron 循环内的即时处理，
        真正"消费"答案是下一轮 run_capability_learning_cycle() 里做的事。"""
        questions = self._load_all()
        for i, q in enumerate(questions):
            if q.question_id != question_id:
                continue
            q.status = "answered"
            q.answer = answer_text
            q.answered_at = time.time()
            questions[i] = q
            self._save_all(questions)
            return q
        return None

    def dismiss(self, question_id: str) -> bool:
        questions = self._load_all()
        for i, q in enumerate(questions):
            if q.question_id == question_id:
                q.status = "dismissed"
                questions[i] = q
                self._save_all(questions)
                return True
        return False

    def sweep_expired(self) -> int:
        """sys:capability_question_sweep 对应的清理逻辑（§4）。
        返回本次清理掉的数量，供 cron 日志记录。"""
        questions = self._load_all()
        now = time.time()
        n = 0
        for i, q in enumerate(questions):
            if q.status == "pending" and q.expires_at and q.expires_at < now:
                q.status = "expired"
                questions[i] = q
                n += 1
        if n:
            self._save_all(questions)
        return n

    def mark_consumed(self, question_id: str) -> None:
        questions = self._load_all()
        for i, q in enumerate(questions):
            if q.question_id == question_id:
                q.consumed = True
                questions[i] = q
                self._save_all(questions)
                return


# ── 大纲缺口扫描（§4 伪流程第一步，规则式，P1 版本）──────────────────────


def scan_outline_gaps(track: CapabilityTrack, limit: int = DEFAULT_TOPICS_PER_CYCLE) -> list[OutlineTopic]:
    """规则式缺口扫描：优先选 uncovered，其次 partial，按 last_touched_at
    从旧到新排序（越久没碰过的越优先）。P2 阶段会替换/叠加 LLM 辅助判定
    与 capability_map 排序信号（见设计文档 §12.1-a、§14），
    但函数签名保持不变，接线方不用改。"""
    def sort_key(t: OutlineTopic):
        state_rank = {"uncovered": 0, "partial": 1, "covered": 2}.get(t.coverage_state, 1)
        touched = t.last_touched_at or 0
        return (state_rank, touched)

    candidates = [t for t in track.outline if t.coverage_state != "covered"]
    candidates.sort(key=sort_key)
    return candidates[:limit]


def needs_user_context(topic: OutlineTopic, track: CapabilityTrack) -> bool:
    """判断这个子主题是否属于"互联网查不到，只有用户自己知道"的类型。
    P1 用非常保守的规则式占位实现：只有 persona 型 Track 默认判定为
    需要用户输入（因为人格细节大部分天然只能靠问，见设计文档 §10.2），
    knowledge 型默认不需要（P2 才接入更细致的判定，比如关键词命中
    "偏好/风险承受能力/关注哪些具体标的"这类主观性强的表述）。"""
    return track.target_type == "persona"


# ── 单轮循环编排（§4）────────────────────────────────────────────────────


RetrieverFn = Callable[[OutlineTopic, CapabilityTrack], list[dict]]
"""检索回调：输入子主题和 Track，返回检索结果列表（每条至少含 url/summary）。
P1 不提供真实实现（避免未评审就产生外部请求），接线时传入真正调用
web_search 的函数。"""

WikiWriterFn = Callable[[OutlineTopic, CapabilityTrack, list[dict]], list[str]]
"""wiki 写入回调：输入子主题/Track/检索结果，返回写入后的 wiki 页面 id 列表。
接线时应换成真正调用 wiki/writer.py + wiki/dedup.py 的实现。"""


def run_capability_learning_cycle(
    paths: AgentPaths,
    retriever: Optional[RetrieverFn] = None,
    wiki_writer: Optional[WikiWriterFn] = None,
    max_pending_questions: int = DEFAULT_MAX_PENDING_QUESTIONS,
    topics_per_cycle: int = DEFAULT_TOPICS_PER_CYCLE,
) -> dict:
    """sys:capability_learning_cycle 对应的单轮编排逻辑（§4 伪流程的落地）。

    P1 阶段：如果没有传入 retriever/wiki_writer，遇到需要检索的子主题会
    记一条 action="skipped" 的台账并跳过，不产生任何外部请求或 wiki 写入
    副作用——这样这个函数在未接线真实检索/写入实现之前就是安全的、
    可以直接单元测试/在 cron 里试跑而不用担心误触发真实抓取。

    返回一份本轮执行摘要（供 cron 日志 / 看板展示）。
    """
    track_store = CapabilityTrackStore(paths)
    ledger_store = CapabilityLedgerStore(paths)
    question_store = CapabilityQuestionStore(paths)

    summary = {"tracks_processed": 0, "topics_researched": 0, "questions_raised": 0,
               "questions_consumed": 0, "topics_skipped": 0}

    for track in track_store.list_tracks(status="active"):
        summary["tracks_processed"] += 1

        # 消费已回答但尚未处理的问题（§4 伪流程最后一步）
        answered = [
            q for q in question_store.list_questions(status="answered", track_id=track.track_id)
            if not q.consumed
        ]
        for q in answered:
            ledger_store.append(CapabilityLedgerEntry(
                track_id=track.track_id,
                topic_id=q.topic_id,
                action="question_answered",
                summary=f"用户回答了「{q.question}」，答案已记录，供后续检索/草稿使用",
            ))
            question_store.mark_consumed(q.question_id)
            summary["questions_consumed"] += 1

        # 挑选本轮推进的子主题
        pending = question_store.pending_count(track.track_id)
        if pending >= max_pending_questions:
            # 待回答问题已达上限，本轮只推进不需要用户输入的子主题
            topics = [
                t for t in scan_outline_gaps(track, limit=topics_per_cycle * 2)
                if not needs_user_context(t, track)
            ][:topics_per_cycle]
        else:
            topics = scan_outline_gaps(track, limit=topics_per_cycle)

        for topic in topics:
            if needs_user_context(topic, track):
                if pending >= max_pending_questions:
                    continue
                q = question_store.raise_question(
                    track_id=track.track_id,
                    topic_id=topic.topic_id,
                    question=f"关于「{topic.name}」，能告诉我更多你的具体偏好/背景吗？"
                             f"这会影响后续推进的方向。",
                )
                ledger_store.append(CapabilityLedgerEntry(
                    track_id=track.track_id,
                    topic_id=topic.topic_id,
                    action="question_raised",
                    summary=f"生成待回答问题：{q.question}",
                ))
                summary["questions_raised"] += 1
                pending += 1
                continue

            if retriever is None or wiki_writer is None:
                ledger_store.append(CapabilityLedgerEntry(
                    track_id=track.track_id,
                    topic_id=topic.topic_id,
                    action="skipped",
                    summary="未接线真实检索/wiki 写入回调，本轮跳过（P1 安全默认）",
                ))
                summary["topics_skipped"] += 1
                continue

            results = retriever(topic, track)
            if any(kw and kw in topic.name for kw in track.excluded_keywords):
                ledger_store.append(CapabilityLedgerEntry(
                    track_id=track.track_id,
                    topic_id=topic.topic_id,
                    action="skipped",
                    summary="命中黑名单关键词，跳过",
                ))
                summary["topics_skipped"] += 1
                continue

            page_ids = wiki_writer(topic, track, results)
            ledger_store.append(CapabilityLedgerEntry(
                track_id=track.track_id,
                topic_id=topic.topic_id,
                action="researched",
                summary=f"检索并写入 {len(page_ids)} 个 wiki 页面",
                wiki_page_ids=page_ids,
            ))
            summary["topics_researched"] += 1

            # 更新大纲覆盖状态
            topic.coverage_state = "covered" if page_ids else "partial"
            topic.last_touched_at = time.time()
            topic.wiki_page_ids = list(set(topic.wiki_page_ids + page_ids))
        track_store.update(track.track_id, outline=track.outline)

    return summary


# ── 检索未命中记录（§14.1-a 使用驱动学习，接线方见
#    context_builder.py::ContextBuilder._maybe_record_capability_wiki_miss，
#    只在 persona 绑定的 wiki_scopes 命中某个 active knowledge 型 Track 的
#    wiki_tag 时才调用，不做全量未命中查询的猜测式关联）──────────────────


def record_wiki_miss(paths: AgentPaths, track_id: str, topic_hint: str, query: str) -> None:
    """当 context_builder 在某个 Track 的 wiki_tag 范围内检索未命中时调用，
    记一条 miss_observed 台账，供下一轮 scan_outline_gaps 提高优先级
    （P1 先只落台账，"提高优先级"的实际排序逻辑——即 scan_outline_gaps()
    读取 miss_observed 台账并据此调整候选排序——留到 P2 与 LLM 辅助判定
    一起做，避免规则式实现里出现"频繁提问却查不到"的噪音；目前 cron 也
    还没接线，这份台账暂时只是静态积累，等 P2/cron 接线后才会被真正
    消费）。"""
    ledger_store = CapabilityLedgerStore(paths)
    ledger_store.append(CapabilityLedgerEntry(
        track_id=track_id,
        topic_id=topic_hint or "unclassified",
        action="miss_observed",
        summary=f"检索未命中：{query}",
    ))


# ── 合规过滤（§13.3-g，必须在 P1 写入环节内置，不可延后）────────────────────
#
# 设计文档 §13.3-g 明确要求：wiki 页面可以沉淀"分析方法论"，但检索结果里
# 如果混入了具体的"买入/卖出建议"这类内容，写入前必须过滤/改写，只保留
# 方法论和事实性信息；同时对金融/医疗/法律这类专业建议领域的页面，
# frontmatter 加 `requires_disclaimer: true` 标记。P1 用规则式实现
# （关键词/正则句级过滤），不接 LLM 改写——规则式虽然召回率有限，但
# 不会引入"LLM 误判把正常内容也改写掉"的新增不确定性，且延迟低、
# 可离线单测，符合"这一步必须在检索/写入环节内置，风险补救成本远高于
# 预防"的克制要求。

# 逐句匹配的风险短语——命中即整句剔除，不做局部替换（局部替换容易把
# 句子改得语义不通，整句剔除更保守、也更容易审计）。
_COMPLIANCE_RISKY_PHRASE_PATTERNS = [
    r"建议(买入|卖出|加仓|减仓|做多|做空)",
    r"(现在|目前|近期).{0,6}(应该|可以|值得).{0,4}(买入|卖出|入场|建仓)",
    r"(推荐|首推).{0,4}(买入|买进|加仓)",
    r"目标价.{0,10}(元|美元|港元|\$)",
    r"止损位",
    r"仓位建议",
    r"(强烈)?(推荐|建议).{0,4}(购买|投资).{0,10}(股票|基金|标的)",
]

# 领域关键词——命中任一即视为需要 `requires_disclaimer` 标记的专业建议
# 领域（§13.3-g 提到"类似场景（医疗、法律等专业建议类方向）都可能踩同样
# 的线"，不只是金融，这里三类一起覆盖）。
_COMPLIANCE_DISCLAIMER_DOMAIN_KEYWORDS = [
    "股票", "基金", "投资", "证券", "期货", "外汇", "理财",  # 金融
    "疾病", "诊断", "用药", "药物", "治疗", "病症", "医疗",    # 医疗
    "诉讼", "法律", "合同纠纷", "律师", "法规",              # 法律
]


def _filter_compliance_risky_text(text: str) -> tuple[str, bool]:
    """按句号/换行切句，剔除命中风险短语的整句。返回（过滤后文本，是否有
    内容被剔除）。空文本/无命中时原样返回，第二个返回值为 False。"""
    import re

    if not text:
        return text, False
    # 按中英文句末标点切句，保留标点本身，避免把风险短语所在句子的边界
    # 判断依赖过于精细的分句库——够用即可，不追求语法学意义上的精确分句。
    sentences = re.split(r"(?<=[。！？\n])", text)
    kept: list[str] = []
    filtered_any = False
    for sent in sentences:
        if not sent.strip():
            continue
        if any(re.search(pat, sent) for pat in _COMPLIANCE_RISKY_PHRASE_PATTERNS):
            filtered_any = True
            continue
        kept.append(sent)
    return "".join(kept).strip(), filtered_any


def is_disclaimer_required_track(track: "CapabilityTrack") -> bool:
    """判断这个 Track 是否落在需要 `requires_disclaimer` 标记的专业建议
    领域（金融/医疗/法律等，见 §13.3-g）。规则式关键词匹配，宁可多标注
    （对不需要的页面多加一句"仅供参考"没有实质坏处），也不漏标注。"""
    haystack = f"{track.title} {track.persona_desc} {track.wiki_tag}"
    return any(kw in haystack for kw in _COMPLIANCE_DISCLAIMER_DOMAIN_KEYWORDS)


def apply_compliance_filter(
    results: list[dict], track: "CapabilityTrack",
) -> tuple[list[dict], bool, bool]:
    """对检索结果的每条 summary/text 做句级风险过滤。

    返回 (过滤后的 results 副本, 本次是否实际剔除了内容, 是否需要
    requires_disclaimer 标记)。不修改传入的 results，返回新列表——
    调用方（make_wiki_writer）用返回值渲染页面正文，不依赖副作用。
    """
    filtered_results: list[dict] = []
    any_filtered = False
    for r in results:
        r2 = dict(r)
        for key in ("summary", "text"):
            if r2.get(key):
                cleaned, did_filter = _filter_compliance_risky_text(r2[key])
                r2[key] = cleaned
                any_filtered = any_filtered or did_filter
        filtered_results.append(r2)
    requires_disclaimer = is_disclaimer_required_track(track) or any_filtered
    return filtered_results, any_filtered, requires_disclaimer


# ── 真实 wiki_writer 实现（§5 wiki 沉淀规范）───────────────────────────────
#
# 之所以到这一步才提供"真实"实现，是因为它不依赖网络/外部服务——纯粹是
# "把已经检索到的 results 渲染成 wiki 页面落盘"，可以完全离线单元测试，
# 和 retriever（依赖真实 web_search，P1 阶段刻意不提供，见下方说明）
# 风险等级不同，值得分开推进。


def make_wiki_writer(paths: AgentPaths) -> WikiWriterFn:
    """返回一个绑定了 paths 的 wiki_writer 回调，直接传给
    `run_capability_learning_cycle(wiki_writer=make_wiki_writer(paths))`。

    做成"返回闭包"而不是让 `default_wiki_writer` 自己接收 paths 参数，
    是为了匹配 `WikiWriterFn` 已经定义好的签名
    `(topic, track, results) -> list[str]`，不用因为多一个 paths 参数
    而改动 run_capability_learning_cycle 里的调用方式。

    P1 版本每个子主题固定写一页，不做多页拆分；也**没有接入
    `wiki/dedup.py` 的判重**（P2 再做，见设计文档 §5、§14——判重需要先
    确定"怎么批量加载某个 wiki_tag 下的已有页面"这个更基础的接口，
    P1 阶段不确定该用哪一套现成的加载路径，与其写一个不确定正确性的
    集成，不如先留空、明确标注，等接线阶段和 wiki 模块的维护者一起确认）。
    """

    def _writer(topic: OutlineTopic, track: CapabilityTrack, results: list[dict]) -> list[str]:
        from datetime import datetime, timezone

        from mini_agent.wiki.writer import write_page

        # §13.3-g：写入前先过滤风险表述（具体买卖建议等），并判定是否需要
        # requires_disclaimer 标记——这一步必须在这里做，不能延后到接线阶段。
        results, _filtered_any, requires_disclaimer = apply_compliance_filter(results, track)

        page_id = f"cap_{track.track_id}_{topic.topic_id}"
        body_lines = [f"# {topic.name}", ""]
        urls: list[str] = []
        for r in results:
            summary = r.get("summary") or r.get("text") or ""
            if not summary:
                continue
            url = r.get("url") or ""
            if url:
                urls.append(url)
            body_lines.append(f"- {summary}" + (f"（来源：{url}）" if url else ""))
        has_body_content = len(body_lines) > 2
        body = "\n".join(body_lines) if has_body_content else f"# {topic.name}\n\n（暂无检索结果）"
        if requires_disclaimer:
            body += "\n\n> 仅供参考，不构成投资/医疗/法律等专业建议。"

        extra_fm = {
            "capability_track_id": track.track_id,
            "source_urls": urls,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "requires_disclaimer": requires_disclaimer,
        }
        write_page(
            paths=paths,
            page_id=page_id,
            page_type="topic",
            body=body,
            tags=[track.wiki_tag] if track.wiki_tag else [],
            extra_frontmatter=extra_fm,
        )
        return [page_id]

    return _writer


# ── 真实 retriever 实现（对接 web_search，默认关闭，见 CapabilityLearningConfig）───

def make_web_search_retriever(cfg: "AppConfig") -> RetrieverFn:
    """返回一个绑定了 cfg 的 retriever 回调，直接传给
    `run_capability_learning_cycle(retriever=make_web_search_retriever(cfg))`。

    调用方必须自己先检查 `cfg.capability_learning.retriever_enabled`——这个
    函数本身不检查这个开关（构造出来的 retriever 只要被传入就会被调用，
    开关判断放在调用方更直观，也方便调用方在开关关闭时完全不导入这个
    函数，避免不必要的 web_search 依赖加载）。

    每个子主题的检索 query 直接用 `f"{track.title} {topic.name}"`（P1
    用最朴素的拼接，不做查询改写/多轮检索——查询质量优化留到有真实使用
    反馈后再做，避免过早引入不确定性）。search() 抛出的 `WebSearchError`
    会被这里捕获并转成空结果列表，让调用方（`run_capability_learning_cycle`）
    走"没有可用结果，记 skipped 台账"的既有安全路径，而不是让一次检索
    失败中断整轮循环。
    """
    from mini_agent.web_search.base import WebSearchError
    from mini_agent.web_search.factory import create_web_search_provider

    max_results = max(1, cfg.capability_learning.max_results_per_topic)
    summary_max_chars = max(0, cfg.capability_learning.summary_max_chars)

    def _retriever(topic: OutlineTopic, track: CapabilityTrack) -> list[dict]:
        provider = create_web_search_provider(cfg)
        query = f"{track.title} {topic.name}".strip()
        try:
            results = provider.search(query, max_results=max_results)
        except WebSearchError:
            return []
        out: list[dict] = []
        for r in results:
            summary = (r.snippet or r.title or "").strip()
            if summary_max_chars and len(summary) > summary_max_chars:
                summary = summary[:summary_max_chars].rstrip() + "…"
            if not summary:
                continue
            out.append({"url": r.url, "summary": summary})
        return out

    return _retriever
