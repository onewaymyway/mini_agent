"""evolution/capability_learning.py — 人设能力自主学习（P1 最小可用闭环）

设计背景见 next_doc/persona_capability_learning_design.md。

本模块只负责数据模型 + 存储 + 纯逻辑（缺口扫描 / 台账 / 异步问答队列），
不直接依赖 FastAPI / cron_scheduler / kanban，方便单元测试和后续拆分接线：
    - HTTP 接线见 api/capability_routes.py（新增，独立 router，未挂进
      routes.py 主文件，避免在超大文件里手改带来的风险，挂载方式见该文件
      顶部注释）
    - cron 接线见本文件 `run_capability_learning_cycle()`，供
      cron_scheduler.py 注册 `sys:capability_learning_cycle` 时直接调用
      （P1 阶段先提供函数本体，实际注册到 CRON_JOBS 表留到接线阶段做，
      避免在评审通过前误触发真实的互联网检索）。

已落地（P1）：
    - CapabilityTrack / OutlineTopic / CapabilityLedgerEntry / CapabilityQuestion
      数据模型 + 存储路径（storage/paths.py 已新增对应 property）
    - 大纲缺口扫描（规则式，§4 设计文档）
    - CapabilityQuestion 异步问答队列的生成 / 提交 / 消费（§3.3、§10.2）
    - CapabilityLedgerEntry 台账记录（§3.2）
    - run_capability_learning_cycle()：单轮循环的编排函数，检索/wiki 写入
      两步以可注入的回调形式暴露
    - make_wiki_writer(paths)：真实的 wiki 写入回调（对接 wiki/writer.py），
      不依赖网络，已有单测覆盖并验证能写出可被 wiki/parser.py 解析的合法
      页面。**尚未接入 wiki/dedup.py 判重**——需要先确认"按 wiki_tag
      批量加载已有页面"该走哪条现成接口，留到接线阶段和 wiki 模块维护者
      一起确认，避免猜测一个不确定正确性的集成方式

尚未落地（按设计文档标注的阶段留到后续）：
    - 真实 retriever 回调（对接 web_search，需要网络，P1 单测里全部用假
      实现替代，避免单测依赖外部网络请求）
    - cron 任务表注册 sys:capability_learning_cycle（cron_scheduler.py 里
      内置任务是"生成 task_template 文本交给 Agent 自己执行"的模式，不是
      直接调用 Python 函数——意味着真正接线还需要一个新的 slash command
      处理器，把 run_capability_learning_cycle() 包装成 Agent 能触发的
      命令，这一步比预想的多一层，留到下一步单独做）
    - target_type="persona" 全链路（人设草稿生成 / 发布，见文档 §10）
    - PersonaProfile.wiki_scopes 接线（见文档 §11）
    - 与 external_trend_capability_link / objective_executor / decision_profile_builder /
      capability_map 的协同（见文档 §12，P1 阶段刻意不打通，避免引入耦合风险）
    - LLM 辅助的大纲生成/缺口判定（P1 是规则式，见文档 §14 P2 阶段）
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

from mini_agent.storage.paths import AgentPaths

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


# ── 检索未命中记录（§14.1-a 使用驱动学习，P1 先提供记录接口，
#    context_builder.py 的接线留到实现阶段单独评审）─────────────────────


def record_wiki_miss(paths: AgentPaths, track_id: str, topic_hint: str, query: str) -> None:
    """当 context_builder 在某个 Track 的 wiki_tag 范围内检索未命中时调用，
    记一条 miss_observed 台账，供下一轮 scan_outline_gaps 提高优先级
    （P1 先只落台账，"提高优先级"的实际排序逻辑留到 P2 与 LLM 辅助判定
    一起做，避免规则式实现里出现"频繁提问却查不到"的噪音）。"""
    ledger_store = CapabilityLedgerStore(paths)
    ledger_store.append(CapabilityLedgerEntry(
        track_id=track_id,
        topic_id=topic_hint or "unclassified",
        action="miss_observed",
        summary=f"检索未命中：{query}",
    ))


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

        page_id = f"cap_{track.track_id}_{topic.topic_id}"
        body_lines = [f"# {topic.name}", ""]
        urls: list[str] = []
        for r in results:
            summary = r.get("summary") or r.get("text") or ""
            url = r.get("url") or ""
            if url:
                urls.append(url)
            body_lines.append(f"- {summary}" + (f"（来源：{url}）" if url else ""))
        body = "\n".join(body_lines) if results else f"# {topic.name}\n\n（暂无检索结果）"

        extra_fm = {
            "capability_track_id": track.track_id,
            "source_urls": urls,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
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
