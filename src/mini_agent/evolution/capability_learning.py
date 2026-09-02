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
    - [v0.22 §14.4] make_agent_retriever(cfg)：另一种检索回调，用受限
      工具集的 SubAgent（web_search/search_knowledge/skill_list/
      skill_activate 等只读工具）自主完成调研，而不是单次 web_search
      API 调用——解决"只能做最朴素的关键词搜索，用不上 skill 生态"的
      局限。由 `CapabilityLearningConfig.retriever_mode="agent"` 选用
      （默认仍是 `"web_search"`，opt-in 切换）；同样受
      `retriever_enabled` 总开关和 §13.3-g 合规过滤约束
    - [v0.23] agent_retriever_tool_mode="full"：`retriever_mode="agent"`
      时可选把调研 SubAgent 的工具白名单从只读扩展到 bash/write_file/
      create_file/patch_file 等读写/执行类工具（不含 delete_file），
      支持需要脚本/命令行工具才能完成的复杂调研
    - [v0.23] wiki_write_mode="agent"：新增 make_agent_wiki_writer(cfg,
      paths)，改由 SubAgent 直接调用专属的 capability_wiki_write 工具
      把内容写进 wiki 页面（而不是"agent 产出摘要 → 固定模板渲染"），
      §13.3-g 合规过滤在工具内部执行、不可绕过；SubAgent 未成功写入时
      自动退回固定模板兜底
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
    - `miss_observed` 台账接入 `scan_outline_gaps()` 优先级排序（§14.1-a
      收尾）：`_topic_miss_counts()` 统计最近 200 条台账里各子主题的未
      命中次数，同一 coverage_state 内 miss 次数越多排序越靠前，用户
      实际检索碰壁过的子主题不再和"系统扫描出来但没人问过"的子主题
      用同一套 last_touched_at 排序
    - LLM 辅助大纲起草（§14 P2 提前实现）：`draft_outline_with_llm()` +
      `CapabilityTrackStore.create(..., llm_helper=...)`，CLI
      `/capability create --llm-draft` 与 HTTP API `llm_draft` 字段均已
      接线，看板新建表单加了对应复选框；起草失败/无 LLM 上下文时静默
      退回空大纲，不报错
    - §12.1-a capability_map 排序信号（单向只读消费，本轮接入）：
      `_topic_capability_confidence()` 只读消费
      `consolidation.build_capability_map()`，用关键词双向子串匹配把
      领域置信度粗略映射到子主题，`scan_outline_gaps()` 在 miss_counts
      之后、last_touched_at 之前用它做第二级排序（置信度越低越优先）；
      不匹配的子主题给中性值 0.5，不产生任何反向副作用/不写回
    - §13.1-b 多 Track 公平调度：`CapabilityTrack.last_advanced_at`
      记录该 Track 上次真正被推进（处理过至少 1 个子主题）的时间戳，
      `run_capability_learning_cycle()` 按这个字段升序处理 active
      Track；新增可选的 `max_topics_per_run_cycle` 全局预算参数（默认
      None=不设上限，向后兼容），设置后多个 Track 会共享同一份预算，
      预算耗尽的 Track 本轮不推进，下一轮因为 last_advanced_at 更旧会
      排到最前面，长期下来公平，不会出现早建 Track 永远占满配额
    - §13.2-d 知识时效性衰减：`OutlineTopic.volatility`
      （volatile/periodic/stable）在 P1 就带上了字段，本轮补上消费——
      `scan_outline_gaps()` 新增 `now` 参数，已 covered 但距上次触达
      超过对应阈值（volatile 7 天/periodic 30 天）的子主题会被重新
      纳入候选，避免"名义覆盖率 100%，内容早已过期"的假象
    - §13.1-c 跨 Track 子主题去重与知识共享：`find_cross_track_reuse()`
      用字符级 2-gram Jaccard 相似度（关键词/tag 层面，不引入语义
      匹配）在其它 active Track 里找名字高度相似且已 covered 的子
      主题，命中就直接复用其 wiki 页面（台账记为 action="reused"），
      不重复检索——只在本子主题自己还没有任何 wiki 页面时才会触发
    - [next_doc/outline_revision_and_suggestion_improvement_plan.md]
      大纲修订从"整体替换"改成"基于旧大纲的 diff"：
      `revise_outline_with_llm()` 只输出 ADD/RENAME/REMOVE 变更（不落盘，
      纯预览），`apply_outline_revision()` 是"重新生成大纲"和"手动编辑
      大纲"共同的落地函数（rename/remove 都不影响既有 coverage_state/
      wiki_page_ids）。自动大纲建议新增三个并列来源：miss_counts 驱动
      （规则式、默认开启）、检索沉淀驱动 / 覆盖率里程碑驱动（都要调
      LLM、默认关闭），均由 `run_capability_learning_cycle()` 新增的
      `outline_suggestion_*` 关键字参数控制

留给 P3 的方向（P1/P2 刻意不做，避免过早引入不确定性/耦合）：
    - target_type="persona" 全链路（人设草稿生成 / 发布，见文档 §10）
    - 与 external_trend_capability_link / objective_executor / decision_profile_builder
      的协同（见文档 §12.1-b/c、§12.2，12.1-a capability_map 已接入，见上）
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

# [next_doc/capability_wiki_freshness_improvement_plan.md 阶段 1] 判定一个子
# 主题"内容是否足够"的最小字数阈值——合并所有检索结果的非空摘要/正文
# 后的总字数达到这个阈值才算 sufficient（可以标 covered），否则算 thin
# （保持 partial，下一轮继续重试）。先写死常量，不做成配置项，观察默认值
# 是否合适后再评估要不要暴露给用户调整。
CONTENT_SUFFICIENT_MIN_CHARS = 120


# ── 数据模型（对应设计文档 §3）────────────────────────────────────────────


@dataclass
class OutlineTopic:
    topic_id: str
    name: str
    coverage_state: str = "uncovered"          # uncovered / partial / covered
    # [next_doc/capability_wiki_freshness_improvement_plan.md 阶段 2] 默认值
    # 从 "stable"（永不过期）改为 "periodic"（30 天刷新周期）——大部分能力
    # 学习子主题的内容都应该被定期重新检索验证，"stable" 只留给用户手动
    # 标注的、确实基本不随时间变化的极少数子主题。
    volatility: str = "periodic"                # stable / periodic / volatile（§14.2-d）
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
            volatility=d.get("volatility", "periodic"),
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
    # [§13.1-b 多 Track 公平调度] 上一次本 Track 在 run_capability_learning_cycle()
    # 里真正被推进过（至少处理了 1 个子主题，不论是 researched/question_raised/
    # skipped）的时间戳。None = 从未被推进过。仅用于排序，不影响其它任何行为；
    # 向后兼容旧数据（旧 Track 文件没有这个字段时 from_dict 里默认 None）。
    last_advanced_at: Optional[float] = None

    # [next_doc/outline_revision_and_suggestion_improvement_plan.md §二-2]
    # "检索沉淀驱动"大纲建议的节流时间戳：每个 Track 每天最多触发一次 LLM
    # 调用，避免每轮循环都额外多一次 LLM 调用。None = 从未触发过。
    outline_research_suggestion_last_at: Optional[float] = None

    # [next_doc/outline_revision_and_suggestion_improvement_plan.md §二-3]
    # "覆盖率里程碑驱动"大纲建议是否已经为这个 Track 触发过一次——覆盖率
    # 达到阈值时只问一次，不会因为后续每轮循环覆盖率仍然达标而重复生成
    # 建议。默认 False（向后兼容：旧 Track 数据没有这个字段时视为未触发）。
    outline_milestone_notified: bool = False

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
            last_advanced_at=d.get("last_advanced_at"),
            outline_research_suggestion_last_at=d.get("outline_research_suggestion_last_at"),
            outline_milestone_notified=d.get("outline_milestone_notified", False),
        )


@dataclass
class CapabilityLedgerEntry:
    track_id: str
    topic_id: str
    action: str                                  # researched / question_raised /
                                                   # question_answered / question_reused /
                                                   # skipped / miss_observed（§14.1-a）
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


@dataclass
class OutlineSuggestion:
    """v0.21 §13.2-f 大纲动态生长建议：消费已回答问题时，用可选的
    `llm_helper` 提炼出的"大纲之外、用户主动提到的新关注点"。不是自动
    追加进大纲——只是生成一条待用户在看板/CLI 采纳或忽略的建议
    （`status`），采纳后才会真正变成 `OutlineTopic`。"""
    suggestion_id: str
    track_id: str
    source_question_id: str
    suggested_name: str
    rationale: str = ""
    status: str = "pending"                       # pending / accepted / dismissed
    created_at: float = field(default_factory=time.time)
    # [next_doc/outline_revision_and_suggestion_improvement_plan.md §二]
    # 建议来源，供看板展示区分。"answer"（默认，此前唯一来源，向后兼容）/
    # "miss_counts"（规则式，检索未命中驱动）/ "research"（检索沉淀驱动）/
    # "milestone"（覆盖率里程碑驱动）。不影响任何既有行为，纯展示用途。
    source: str = "answer"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "OutlineSuggestion":
        return cls(
            suggestion_id=d["suggestion_id"],
            track_id=d["track_id"],
            source_question_id=d["source_question_id"],
            suggested_name=d["suggested_name"],
            rationale=d.get("rationale", ""),
            status=d.get("status", "pending"),
            created_at=d.get("created_at", time.time()),
            source=d.get("source", "answer"),
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
        llm_helper: Optional[Callable[[str], str]] = None,
    ) -> CapabilityTrack:
        """创建一个新 Track。outline_names 为空且传入了 `llm_helper` 时，
        用 `draft_outline_with_llm()` 起草一份初始大纲（P2，opt-in，见该
        函数文档字符串）；outline_names 为空且没有 `llm_helper` 时退化为
        空大纲，由调用方（看板 / 后续手动补充）再补充子主题——三种入参
        组合都兼容，不强制要求任何一种。"""
        track_id = f"cap_{uuid.uuid4().hex[:12]}"
        names = list(outline_names or [])
        if not names and llm_helper is not None:
            names = draft_outline_with_llm(title, persona_desc, llm_helper)
        outline = [
            OutlineTopic(topic_id=f"topic_{uuid.uuid4().hex[:8]}", name=n)
            for n in names
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

    def migrate_stable_volatility_to_periodic(self) -> dict:
        """[next_doc/capability_wiki_freshness_improvement_plan.md 阶段 2]
        批量把存量子主题里 `volatility == "stable"` 的改成 `"periodic"`
        （30 天刷新周期）——`OutlineTopic` 的默认值已经改了，但这只影响
        新建的子主题，不会改动此前已经落盘、字段值就是字面 "stable" 的
        存量数据。不做成 daemon 启动时自动静默迁移，由用户通过
        `/capability migrate-volatility` 显式触发一次，符合本项目一贯
        "改动用户数据前需要显式确认"的取向。

        返回 `{"tracks_affected": int, "topics_migrated": int}`，均为 0
        时表示没有任何 "stable" 子主题需要迁移（幂等，可以放心重复执行）。
        """
        tracks = self._load_all()
        tracks_affected = 0
        topics_migrated = 0
        for track in tracks:
            track_changed = False
            for topic in track.outline:
                if topic.volatility == "stable":
                    topic.volatility = "periodic"
                    topics_migrated += 1
                    track_changed = True
            if track_changed:
                tracks_affected += 1
        if topics_migrated:
            self._save_all(tracks)
        return {"tracks_affected": tracks_affected, "topics_migrated": topics_migrated}

    def force_refresh_all_topics(self, track_id: Optional[str] = None) -> dict:
        """[看板"🔄 刷新所有存量"按钮 / `/capability refresh-all`] 把已经
        判定 `coverage_state == "covered"` 的子主题批量重置为 `"partial"`，
        让它们立刻重新进入 `scan_outline_gaps()` 的候选池，不需要等
        `volatility` 对应的周期性刷新窗口（30 天/7 天）——用于用户明确
        感觉"存量 wiki 内容有问题，希望马上全部重新检索一轮"的场景，是
        §14.6 完整性判定（阈值判断新写入的内容）之外的另一条路径：面向
        "旧数据本来就该重新过一遍"，而不是"新数据写得够不够"。

        只重置 `coverage_state`，不清空已有的 `wiki_page_ids`/正文——
        重新检索沉淀出新内容前，旧页面依然可读，不会出现"点了刷新反而
        什么都看不到"的体验倒退；下一轮 `run_capability_learning_cycle()`
        检索到新内容后会覆盖同一个 `page_id` 对应的页面。

        `track_id` 为 `None` 时对所有 Track 生效（不限 active/paused，
        跟 `migrate_stable_volatility_to_periodic()` 保持同样的"全量存量"
        语义）；传入具体 `track_id` 时只影响该 Track，找不到时返回
        `topics_reset=0`（不报错，调用方按返回值判断即可）。

        返回 `{"tracks_affected": int, "topics_reset": int}`，均为 0
        表示没有任何 `covered` 子主题需要重置（幂等，可以放心重复调用）。
        """
        tracks = self._load_all()
        tracks_affected = 0
        topics_reset = 0
        for track in tracks:
            if track_id is not None and track.track_id != track_id:
                continue
            track_changed = False
            for topic in track.outline:
                if topic.coverage_state == "covered":
                    topic.coverage_state = "partial"
                    topics_reset += 1
                    track_changed = True
            if track_changed:
                tracks_affected += 1
        if topics_reset:
            self._save_all(tracks)
        return {"tracks_affected": tracks_affected, "topics_reset": topics_reset}

    # ── [next_doc/outline_revision_and_suggestion_improvement_plan.md §一]
    # 手动编辑大纲的三个薄封装——增/改名/删，内部都委托给
    # `apply_outline_revision()`，和"重新生成大纲（LLM diff）"共用同一份
    # 落地逻辑，保证两条路径行为一致（尤其是 rename/remove 都不影响
    # coverage_state/wiki_page_ids/last_touched_at）。`paths` 参数取自
    # `self._paths`，调用方不需要重复传入。

    def add_outline_topic(self, track_id: str, name: str) -> Optional[CapabilityTrack]:
        return apply_outline_revision(self._paths, track_id, [{"op": "add", "name": name}])

    def rename_outline_topic(self, track_id: str, topic_id: str, name: str) -> Optional[CapabilityTrack]:
        return apply_outline_revision(
            self._paths, track_id, [{"op": "rename", "topic_id": topic_id, "name": name}],
        )

    def remove_outline_topic(self, track_id: str, topic_id: str) -> Optional[CapabilityTrack]:
        return apply_outline_revision(
            self._paths, track_id, [{"op": "remove", "topic_id": topic_id}],
        )


# ── CapabilityLedgerStore：单 Track 的进度台账 ─────────────────────────────


class CapabilityLedgerStore:
    def __init__(self, paths: AgentPaths):
        self._paths = paths

    def append(self, entry: CapabilityLedgerEntry) -> None:
        _append_jsonl(self._paths.capability_ledger_path(entry.track_id), entry.to_dict())

    def list_for_track(self, track_id: str, limit: int = 50) -> list[CapabilityLedgerEntry]:
        rows = _read_jsonl(self._paths.capability_ledger_path(track_id))
        entries: list[CapabilityLedgerEntry] = []
        for r in rows:
            try:
                entries.append(CapabilityLedgerEntry.from_dict(r))
            except (KeyError, TypeError, ValueError):
                # 跳过格式不匹配的条目（例如其他系统写入的脏数据）
                pass
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


# ── CapabilityOutlineSuggestionStore：大纲动态生长建议队列（v0.21 §13.2-f）──


class CapabilityOutlineSuggestionStore:
    """同样是"整体读出、内存改、整体写回"，量级和 CapabilityQuestionStore
    一致（单个用户的待采纳建议数不会大）。"""

    def __init__(self, paths: AgentPaths):
        self._paths = paths

    def _load_all(self) -> list[OutlineSuggestion]:
        rows = _read_jsonl(self._paths.capability_outline_suggestions_path)
        return [OutlineSuggestion.from_dict(r) for r in rows]

    def _save_all(self, suggestions: list[OutlineSuggestion]) -> None:
        _write_jsonl(
            self._paths.capability_outline_suggestions_path,
            [s.to_dict() for s in suggestions],
        )

    def list_suggestions(
        self, status: Optional[str] = None, track_id: Optional[str] = None,
    ) -> list[OutlineSuggestion]:
        items = self._load_all()
        if status:
            items = [s for s in items if s.status == status]
        if track_id:
            items = [s for s in items if s.track_id == track_id]
        items.sort(key=lambda s: s.created_at, reverse=True)
        return items

    def add(self, suggestion: OutlineSuggestion) -> None:
        items = self._load_all()
        items.append(suggestion)
        self._save_all(items)

    def dismiss(self, suggestion_id: str) -> bool:
        items = self._load_all()
        for i, s in enumerate(items):
            if s.suggestion_id == suggestion_id:
                s.status = "dismissed"
                items[i] = s
                self._save_all(items)
                return True
        return False

    def mark_accepted(self, suggestion_id: str) -> Optional[OutlineSuggestion]:
        """只更新建议自身的状态——真正把子主题加进大纲是调用方
        （`accept_outline_suggestion()`）的职责，两步拆开是为了让"加大纲"
        这一步能独立处理 Track 不存在等错误，不把两件事捆在一次写入里。"""
        items = self._load_all()
        for i, s in enumerate(items):
            if s.suggestion_id == suggestion_id:
                s.status = "accepted"
                items[i] = s
                self._save_all(items)
                return s
        return None


def _mark_topic_covered(
    track_store: "CapabilityTrackStore", track: "CapabilityTrack", topic_id: str,
) -> None:
    """[capability_learning_duplicate_question_dedup_plan.md 根因一] 把
    `track.outline` 里 `topic_id` 匹配的子主题标记为 `covered` 并刷新
    `last_touched_at`，再落盘。用于"这个子主题已经有了确定答案（用户回答
    或复用历史回答）"之后，让 `scan_outline_gaps()` 下一轮不再把它选回来
    重复提问——这是修复"周期性问同一个问题"的关键一步：此前 `raise_
    question()` 只写问题台账，从不回写 `coverage_state`，子主题永远停在
    `uncovered`，每轮都会被重新选中。

    找不到对应 `topic_id`（大纲被用户手动改过、子主题已被删除等）时静默
    跳过，不影响调用方主流程。
    """
    changed = False
    for t in track.outline:
        if t.topic_id == topic_id and t.coverage_state != "covered":
            t.coverage_state = "covered"
            t.last_touched_at = time.time()
            changed = True
            break
    if changed:
        track_store.update(track.track_id, outline=track.outline)


def find_reusable_answered_question(
    new_question_text: str,
    track: "CapabilityTrack",
    question_store: "CapabilityQuestionStore",
    llm_helper: Optional[Callable[[str], str]] = None,
) -> Optional["CapabilityQuestion"]:
    """[capability_learning_duplicate_question_dedup_plan.md 根因二] 在真正
    向用户抛出一个新问题之前，检查这个 Track 下有没有已经问过、且语义上是
    同一件事的历史问题——如果有，调用方应直接复用那条已回答的答案，不再
    重复打扰用户。

    背景：大纲里可能存在名字不同但实际在问同一件事的子主题（比如"数据
    采集技术基础" vs "A股数据源类型"），各自独立触发提问，对用户来说就
    是被换了个说法反复问同一个问题。字符串/关键词相似度（本文件里
    `_topic_name_similarity()` 用的字符 2-gram Jaccard）对这类"字面不像、
    语义一样"的情况基本无效，所以这里用 LLM 做语义判断。

    只在"这个 Track 下确实存在已回答问题"时才调用 LLM（早退），避免空
    Track / 冷启动场景产生无意义的 LLM 调用。`llm_helper` 为 `None`
    （未接线）时直接返回 `None`，退化为原有行为（照常提问）。

    判断偏保守：宁可漏判（正常提问一次，最多是稍微多问一次），也不要
    误判成重复（导致该问的没问到）——LLM 调用异常、输出解析不出有效结果
    时都按"不是重复"处理。

    返回命中的历史 `CapabilityQuestion`（`status == "answered"`），未命中
    或跳过判断时返回 `None`。
    """
    if llm_helper is None:
        return None
    answered = [
        q for q in question_store.list_questions(status="answered", track_id=track.track_id)
        if q.answer
    ]
    if not answered:
        return None

    numbered = "\n".join(
        f"{i}. 问题：{q.question}\n   已有答案：{q.answer}"
        for i, q in enumerate(answered)
    )
    prompt = (
        f"下面是关于「{track.title}」这个能力学习方向，系统之前已经问过\n"
        f"用户、并且已经拿到答案的历史问题列表：\n\n{numbered}\n\n"
        f"现在系统准备问用户一个新问题：\n「{new_question_text}」\n\n"
        "请判断这个新问题是否与上面某一条历史问题在语义上是同一件事"
        "（即历史答案已经足够回答这个新问题，不需要再问用户一遍）。"
        "只在你有把握时才判定为\"是同一件事\"，宁可漏判（正常提问），"
        "也不要误判（导致该问的没问）。\n\n"
        "如果是同一件事，请只输出上面列表里对应的那个序号数字（比如 0 或 "
        "2），不要有任何其它文字。如果不是同一件事，或者没有把握，请只"
        "输出 NONE。不要输出除以上两种情况之外的任何内容。"
    )
    try:
        raw = llm_helper(prompt)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(
            _mini_agent_exc,
            where="mini_agent.evolution.capability_learning.find_reusable_answered_question",
        )
        return None
    if not raw or not raw.strip():
        return None
    first_line = raw.strip().splitlines()[0].strip()
    if first_line.upper() == "NONE":
        return None
    try:
        idx = int(first_line)
    except ValueError:
        return None
    if not (0 <= idx < len(answered)):
        return None
    return answered[idx]


def generate_outline_suggestion_from_answer(
    track: CapabilityTrack,
    question: CapabilityQuestion,
    llm_helper: Optional[Callable[[str], str]],
    existing_pending_names: Optional[list[str]] = None,
) -> Optional[OutlineSuggestion]:
    """v0.21 §13.2-f：消费一条已回答问题时，尝试提炼"是否存在明显在原
    大纲之外、但用户主动提到的新关注点"。

    没有 `llm_helper` 时整体跳过，返回 None——不做规则式猜测（比如从
    答案里抠关键词），设计文档原话是"误报成本比'暂时不建议'更高"，
    跟 §13.1-a/c 一贯的"生成问题/建议要克制"是同一条原则。

    命中且与现有大纲子主题、以及已有的 pending 建议都不重复（复用
    §13.1-c 的 `_topic_name_similarity`，同一阈值 `CROSS_TRACK_REUSE_
    SIMILARITY_THRESHOLD`）时才返回一条 `OutlineSuggestion`；LLM 判定
    "没有新方向"（约定输出 `NONE`）或输出解析不出有效名称时也返回
    None。不重试、不做多轮修正——和 `draft_outline_with_llm()` 同款
    "起草辅助而非关键路径"的克制。
    """
    if llm_helper is None:
        return None
    if not question.answer:
        return None

    prompt = (
        f"用户正在持续学习一个能力方向，标题是「{track.title}」，\n"
        f"已有大纲子主题：{', '.join(t.name for t in track.outline) or '（暂无）'}\n\n"
        f"系统之前问了用户：「{question.question}」\n"
        f"用户的回答是：「{question.answer}」\n\n"
        "如果这条回答里提到了一个明显在已有大纲之外、值得单独作为一个新"
        "子主题加入大纲的新关注点，请只输出这个子主题的名称（4-12 个汉字"
        "左右，不要标点、不要解释）。如果回答里没有这样的新方向（比如"
        "只是回答了原问题本身、或者提到的内容已经被现有子主题覆盖），"
        "请只输出 NONE。不要输出除以上两种情况之外的任何内容。"
    )
    try:
        raw = llm_helper(prompt)
    except Exception:
        return None
    if not raw or not raw.strip():
        return None
    name = raw.strip().splitlines()[0].strip().lstrip("0123456789.、-•* ").strip()
    if not name or name.upper() == "NONE" or len(name) > 30:
        return None

    existing_names = [t.name for t in track.outline] + list(existing_pending_names or [])
    for existing in existing_names:
        if _topic_name_similarity(name, existing) >= CROSS_TRACK_REUSE_SIMILARITY_THRESHOLD:
            return None

    return OutlineSuggestion(
        suggestion_id=f"capsug_{uuid.uuid4().hex[:12]}",
        track_id=track.track_id,
        source_question_id=question.question_id,
        suggested_name=name,
        rationale=f"用户在回答「{question.question}」时提到，可能是大纲之外的新关注点",
    )


def accept_outline_suggestion(
    paths: AgentPaths, suggestion_id: str,
) -> Optional[OutlineTopic]:
    """用户在看板/CLI 采纳一条建议：把 `suggested_name` 追加成一个新的
    `OutlineTopic`（`coverage_state="uncovered"`），写回对应 Track 的大纲，
    并把建议自身标记为 accepted。Track 已被删除、或建议不存在/已处理过
    时返回 None，不抛异常——调用方（CLI/API）据此给出用户可读的错误提示。
    """
    suggestion_store = CapabilityOutlineSuggestionStore(paths)
    suggestions = suggestion_store.list_suggestions()
    target = next((s for s in suggestions if s.suggestion_id == suggestion_id), None)
    if target is None or target.status != "pending":
        return None

    track_store = CapabilityTrackStore(paths)
    track = track_store.get(target.track_id)
    if track is None:
        return None

    new_topic = OutlineTopic(topic_id=f"topic_{uuid.uuid4().hex[:8]}", name=target.suggested_name)
    track_store.update(track.track_id, outline=track.outline + [new_topic])
    suggestion_store.mark_accepted(suggestion_id)
    return new_topic


# ── 自动大纲建议的三个新来源（§二，next_doc/outline_revision_and_
#    suggestion_improvement_plan.md）────────────────────────────────────
#
# 与上面 generate_outline_suggestion_from_answer() 是同一条 OutlineSuggestion
# 队列的四个并列生产者，互不依赖；生成的建议一律走既有的
# CapabilityOutlineSuggestionStore + 看板"💡 大纲扩展建议"区采纳/忽略。


def _extract_miss_query_counts(
    ledger_store: "CapabilityLedgerStore", track_id: str, limit: int = 200,
) -> dict[str, int]:
    """统计某个 Track 台账里最近 `limit` 条 `miss_observed` 记录中，各
    检索 query 文本（`record_wiki_miss()` 落盘时 summary 格式固定为
    "检索未命中：{query}"）出现的次数——和 `_topic_miss_counts()` 按
    `topic_id` 聚合不同，这里按**具体查询文本**聚合，因为多数 miss
    记录的 `topic_id` 就是 persona 名字或 "unclassified"（不对应任何
    现有子主题），没法直接用来判断"哪个子主题该被优先推进"，但恰好
    可以用来判断"哪个反复被问到的方向压根不在大纲里"。"""
    entries = ledger_store.list_for_track(track_id, limit=limit)
    counts: dict[str, int] = {}
    for e in entries:
        if e.action != "miss_observed":
            continue
        query = e.summary.split("：", 1)[-1].strip() if "：" in e.summary else e.summary.strip()
        if not query:
            continue
        counts[query] = counts.get(query, 0) + 1
    return counts


def generate_outline_suggestion_from_miss_counts(
    track: CapabilityTrack,
    ledger_store: "CapabilityLedgerStore",
    threshold: int = 3,
    existing_pending_names: Optional[list[str]] = None,
) -> Optional[OutlineSuggestion]:
    """[规则式，不调用 LLM，默认开启] 检索未命中的查询文本在最近 200 条
    台账里出现次数达到 `threshold` 次、且与现有大纲/已有 pending 建议都
    不相似时，直接生成一条建议——用户反复搜不到答案的方向，本身就是
    "大纲有缺口"的强信号，不需要 LLM 判断。

    多个查询同时达标时，取出现次数最多的一个（最保守：每次调用最多
    产出一条建议，调用方——`run_capability_learning_cycle()`——每轮每个
    Track 只调用一次，避免同一轮里因为一堆相似的未命中查询生成多条
    高度重复的建议）。没有任何达标查询时返回 None。"""
    query_counts = _extract_miss_query_counts(ledger_store, track.track_id)
    if not query_counts:
        return None

    existing_names = [t.name for t in track.outline] + list(existing_pending_names or [])
    ranked = sorted(query_counts.items(), key=lambda kv: -kv[1])
    for query, count in ranked:
        if count < threshold:
            break
        name = query.strip()[:30]
        if not name:
            continue
        if any(_topic_name_similarity(name, existing) >= CROSS_TRACK_REUSE_SIMILARITY_THRESHOLD
               for existing in existing_names):
            continue
        return OutlineSuggestion(
            suggestion_id=f"capsug_{uuid.uuid4().hex[:12]}",
            track_id=track.track_id,
            source_question_id="",
            suggested_name=name,
            rationale=f"最近检索「{query}」未命中 {count} 次，但不在现有大纲内，"
                      f"可能是缺失的子主题",
            source="miss_counts",
        )
    return None


def generate_outline_suggestion_from_research(
    track: CapabilityTrack,
    topic: OutlineTopic,
    results: list[dict],
    llm_helper: Optional[Callable[[str], str]],
    existing_pending_names: Optional[list[str]] = None,
) -> Optional[OutlineSuggestion]:
    """[要调 LLM，默认关闭] 某个子主题本轮检索沉淀（completeness=
    sufficient）之后，把检索结果摘要 + 现有大纲交给 LLM，判断"这次
    检索到的内容里有没有明显该独立开的子主题"。和
    `generate_outline_suggestion_from_answer()` 同款"LLM 判定没有就输出
    NONE，不做规则式猜测"的克制。调用方（`run_capability_learning_cycle()`）
    负责按 `CapabilityTrack.outline_research_suggestion_last_at` 做
    "每个 Track 每天最多触发一次"的节流，本函数本身不管节流。"""
    if llm_helper is None:
        return None
    content = "\n".join(
        (r.get("summary") or r.get("text") or "").strip() for r in results
        if (r.get("summary") or r.get("text"))
    )[:1500]
    if not content:
        return None

    prompt = (
        f"用户正在持续学习一个能力方向，标题是「{track.title}」，\n"
        f"已有大纲子主题：{', '.join(t.name for t in track.outline) or '（暂无）'}\n\n"
        f"刚刚针对子主题「{topic.name}」检索到以下内容摘要：\n{content}\n\n"
        "如果这些内容里提到了一个明显在已有大纲之外、值得单独作为一个新"
        "子主题加入大纲的方向，请只输出这个子主题的名称（4-12 个汉字"
        "左右，不要标点、不要解释）。如果没有这样的新方向（内容已经被"
        "现有子主题覆盖，或者不足以独立成一个新子主题），请只输出 NONE。"
        "不要输出除以上两种情况之外的任何内容。"
    )
    try:
        raw = llm_helper(prompt)
    except Exception:
        return None
    if not raw or not raw.strip():
        return None
    name = raw.strip().splitlines()[0].strip().lstrip("0123456789.、-•* ").strip()
    if not name or name.upper() == "NONE" or len(name) > 30:
        return None

    existing_names = [t.name for t in track.outline] + list(existing_pending_names or [])
    for existing in existing_names:
        if _topic_name_similarity(name, existing) >= CROSS_TRACK_REUSE_SIMILARITY_THRESHOLD:
            return None

    return OutlineSuggestion(
        suggestion_id=f"capsug_{uuid.uuid4().hex[:12]}",
        track_id=track.track_id,
        source_question_id="",
        suggested_name=name,
        rationale=f"检索子主题「{topic.name}」时发现的内容里，可能存在一个大纲之外的新方向",
        source="research",
    )


def generate_outline_suggestion_from_coverage_milestone(
    track: CapabilityTrack,
    llm_helper: Optional[Callable[[str], str]],
    existing_pending_names: Optional[list[str]] = None,
) -> Optional[OutlineSuggestion]:
    """[要调 LLM，默认关闭] 大纲覆盖率（covered / total）首次达到阈值时，
    触发一次"要不要往深/往新方向扩展"的建议。调用方
    （`run_capability_learning_cycle()`）负责判断是否跨越阈值、以及
    `CapabilityTrack.outline_milestone_notified` 去重标记的读写，本函数
    只负责生成建议本身。"""
    if llm_helper is None or not track.outline:
        return None

    prompt = (
        f"用户正在持续学习一个能力方向，标题是「{track.title}」，\n"
        f"已有大纲子主题：{', '.join(t.name for t in track.outline)}\n\n"
        "这个方向的大纲已经基本学完了。请判断是否存在一个值得继续深入、"
        "或者相关但还没覆盖到的新子主题，帮助用户继续往深/往新方向扩展。"
        "如果有，请只输出这个子主题的名称（4-12 个汉字左右，不要标点、"
        "不要解释）。如果暂时没有合适的扩展方向，请只输出 NONE。不要"
        "输出除以上两种情况之外的任何内容。"
    )
    try:
        raw = llm_helper(prompt)
    except Exception:
        return None
    if not raw or not raw.strip():
        return None
    name = raw.strip().splitlines()[0].strip().lstrip("0123456789.、-•* ").strip()
    if not name or name.upper() == "NONE" or len(name) > 30:
        return None

    existing_names = [t.name for t in track.outline] + list(existing_pending_names or [])
    for existing in existing_names:
        if _topic_name_similarity(name, existing) >= CROSS_TRACK_REUSE_SIMILARITY_THRESHOLD:
            return None

    return OutlineSuggestion(
        suggestion_id=f"capsug_{uuid.uuid4().hex[:12]}",
        track_id=track.track_id,
        source_question_id="",
        suggested_name=name,
        rationale="大纲覆盖率已达到里程碑阈值，建议往深/往新方向继续扩展",
        source="milestone",
    )


# ── 大纲修订：基于旧大纲的 diff（§一，next_doc/outline_revision_and_
#    suggestion_improvement_plan.md）────────────────────────────────────
#
# 与 draft_outline_with_llm() 的关键区别：draft 是"从零起草"（创建 Track
# 时旧大纲本来就不存在），revise 是"在已有大纲基础上修订"——旧子主题的
# coverage_state/wiki_page_ids/last_touched_at 这些学习进度必须原样保留，
# 不能因为用户点了一次"重新生成大纲"就被推倒重来。


def apply_outline_revision(
    paths: AgentPaths, track_id: str, ops: list[dict],
) -> Optional[CapabilityTrack]:
    """在**当前**大纲基础上按顺序应用一组修订操作，返回更新后的 Track。
    Track 不存在时返回 None。这是"重新生成大纲（LLM diff，见
    `revise_outline_with_llm()`）"和"手动编辑大纲"两条路径共同的落地
    函数，保证两条路径行为完全一致。

    每个 op 是一个 dict，`op` 字段取值：
        - `{"op": "add", "name": str}`：追加一个新 `OutlineTopic`
          （`coverage_state="uncovered"`）。`name` 为空时该条被忽略。
        - `{"op": "rename", "topic_id": str, "name": str}`：只改
          `name`，`topic_id`/`coverage_state`/`wiki_page_ids`/
          `last_touched_at`/`volatility` 全部不变——改名不等于这个
          方向的学习进度要清零。`topic_id` 匹配不到时该条被忽略。
        - `{"op": "remove", "topic_id": str}`：从大纲摘除，但不删除
          已经沉淀的 wiki 页面本身（对齐"删除 Track 不级联删 wiki"的
          既有原则）。`topic_id` 匹配不到时该条被忽略。
    未识别的 `op` 值同样被忽略，不抛异常——调用方（API/手动编辑表单）
    传入的 ops 已经是用户确认过的操作，这里只做防御性容错，不做严格
    校验报错。
    """
    track_store = CapabilityTrackStore(paths)
    track = track_store.get(track_id)
    if track is None:
        return None

    outline = list(track.outline)
    for op in ops:
        kind = (op or {}).get("op")
        if kind == "add":
            name = (op.get("name") or "").strip()
            if name:
                outline.append(OutlineTopic(topic_id=f"topic_{uuid.uuid4().hex[:8]}", name=name))
        elif kind == "rename":
            topic_id = op.get("topic_id")
            name = (op.get("name") or "").strip()
            if topic_id and name:
                for t in outline:
                    if t.topic_id == topic_id:
                        t.name = name
                        break
        elif kind == "remove":
            topic_id = op.get("topic_id")
            if topic_id:
                outline = [t for t in outline if t.topic_id != topic_id]
        # 未识别的 op 静默忽略（见函数文档字符串）

    return track_store.update(track_id, outline=outline)


def revise_outline_with_llm(
    track: CapabilityTrack, llm_helper: Optional[Callable[[str], str]],
) -> list[dict]:
    """把当前完整大纲（每个子主题名字 + coverage_state + 关联 wiki 页数）
    连同 Track 标题/描述一起交给 LLM，要求只输出**变更**而不是整份新
    大纲——每行一个操作，格式约定成：
        KEEP <name>
        ADD <name>
        RENAME <old name> -> <new name>
        REMOVE <name>

    解析后只返回 `ADD`/`RENAME`/`REMOVE` 三种 op（`KEEP` 或未提及的
    子主题保持原样，不需要用户处理，不出现在返回结果里）。`RENAME`/
    `REMOVE` 必须能按名称精确匹配到现有子主题才会被采纳，匹配不到的
    行直接丢弃（不猜测、不模糊匹配，避免误伤到错误的子主题）。`ADD`
    建议如果与现有子主题或本次结果里其它 `ADD` 建议高度相似（复用
    `_topic_name_similarity()`、同一阈值 `CROSS_TRACK_REUSE_SIMILARITY_
    THRESHOLD`）会被丢弃，避免建议列表里出现重复项。

    这一步**不落盘**，只返回预览用的 op 列表——真正写回由调用方拿到
    用户勾选后的最终 op 子集，调用 `apply_outline_revision()` 完成。

    没有 `llm_helper`、LLM 调用异常、或解析不出任何有效行时返回 `[]`，
    不抛异常——和 `draft_outline_with_llm()` 同款"起草辅助而非关键
    路径"的克制：LLM 不可用时用户仍然可以走手动编辑大纲的路径。
    """
    if llm_helper is None:
        return []

    outline_lines = "\n".join(
        f"- {t.name}（状态：{t.coverage_state}，已关联 {len(t.wiki_page_ids)} 篇 wiki）"
        for t in track.outline
    ) or "（当前大纲为空）"
    prompt = (
        f"用户正在持续学习一个能力方向，标题是「{track.title}」，"
        f"描述：{track.persona_desc}\n\n"
        f"当前大纲子主题：\n{outline_lines}\n\n"
        "请给出这份大纲的修订建议。只输出变更，每行一个操作，格式如下"
        "（不要输出其它任何内容、不要编号、不要解释）：\n"
        "KEEP <保持不变的子主题名>\n"
        "ADD <建议新增的子主题名>\n"
        "RENAME <旧名称> -> <新名称>\n"
        "REMOVE <建议移除的子主题名>\n"
        "如果某个子主题不需要变化，用 KEEP 列出；没有需要新增/改名/移除"
        "的内容时对应类型可以不输出。子主题名 4-12 个汉字左右。"
    )
    try:
        raw = llm_helper(prompt)
    except Exception:
        return []
    if not raw or not raw.strip():
        return []

    by_name = {t.name: t for t in track.outline}
    ops: list[dict] = []
    add_names_seen: list[str] = []
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        upper = line.upper()
        if upper.startswith("KEEP "):
            continue
        if upper.startswith("ADD "):
            name = line[4:].strip().lstrip("0123456789.、-•* ").strip()
            if not name or len(name) > 30:
                continue
            existing_names = list(by_name.keys()) + add_names_seen
            if any(_topic_name_similarity(name, e) >= CROSS_TRACK_REUSE_SIMILARITY_THRESHOLD
                   for e in existing_names):
                continue
            ops.append({"op": "add", "name": name, "topic_id": None, "old_name": None})
            add_names_seen.append(name)
        elif upper.startswith("RENAME "):
            body = line[7:].strip()
            if "->" not in body:
                continue
            old_name, _, new_name = body.partition("->")
            old_name, new_name = old_name.strip(), new_name.strip()
            topic = by_name.get(old_name)
            if topic is None or not new_name or len(new_name) > 30:
                continue
            ops.append({
                "op": "rename", "topic_id": topic.topic_id,
                "name": new_name, "old_name": old_name,
            })
        elif upper.startswith("REMOVE "):
            name = line[7:].strip()
            topic = by_name.get(name)
            if topic is None:
                continue
            ops.append({
                "op": "remove", "topic_id": topic.topic_id,
                "name": name, "old_name": name,
            })
    return ops


# ── LLM 辅助大纲起草（§14 P2，opt-in，见 CapabilityTrackStore.create）──────

DRAFT_OUTLINE_MIN_TOPICS = 3
DRAFT_OUTLINE_MAX_TOPICS = 8


def draft_outline_with_llm(
    title: str, persona_desc: str, llm_helper: Callable[[str], str],
) -> list[str]:
    """用 `llm_helper(prompt) -> str` 起草一份初始大纲子主题名称列表。

    跟 growth_advisor.py 里 `_llm_summarize_feedback_pattern()` 同款"能用
    就用，用不了就当没发生"的克制：LLM 返回空、条数不在
    [DRAFT_OUTLINE_MIN_TOPICS, DRAFT_OUTLINE_MAX_TOPICS] 范围内、或者解析
    不出有效行，直接返回空列表——调用方（`CapabilityTrackStore.create`）
    退回到空大纲，不会因为 LLM 输出格式异常而创建出一份包含垃圾数据的
    大纲。不做重试/多轮修正：这是"起草辅助"而不是"必须成功的关键路径"，
    起草失败用户在看板手动加子主题的成本很低，没有必要为了让它更"智能"
    引入额外的不确定性（多轮重试可能让同一次创建操作的延迟变得不可预期）。
    """
    prompt = (
        f"我想持续学习/养成一个能力方向，标题是「{title}」，"
        f"具体描述：{persona_desc}\n\n"
        f"请帮我列出 {DRAFT_OUTLINE_MIN_TOPICS}-{DRAFT_OUTLINE_MAX_TOPICS} 个"
        "循序渐进的子主题，覆盖从基础到进阶的关键知识点或能力维度。"
        "每行一个子主题名称（4-12 个汉字左右，不用编号、不用标点、不用"
        "多余解释），不要输出标题之外的任何内容。"
    )
    try:
        raw = llm_helper(prompt)
    except Exception:
        return []
    if not raw or not raw.strip():
        return []

    names: list[str] = []
    for line in raw.strip().splitlines():
        # 防御性清理：去掉常见的编号/项目符号前缀（"1. "/"- "/"• " 等），
        # LLM 即使被要求不加编号也时常习惯性加上。
        cleaned = line.strip().lstrip("0123456789.、-•* ").strip()
        if cleaned and len(cleaned) <= 30:
            names.append(cleaned)

    if not (DRAFT_OUTLINE_MIN_TOPICS <= len(names) <= DRAFT_OUTLINE_MAX_TOPICS):
        return []
    return names


# ── 大纲缺口扫描（§4 伪流程第一步，规则式，P1 版本）──────────────────────


# [§13.2-d 知识时效性衰减] volatility → 距上次触达多久后视为"该重新检索"。
# 只对 volatile / periodic 生效；stable（默认值）永不因为时间过期被重新
# 纳入候选——"技术分析基础"这类内容几乎不过时，不应该被无谓地重复检索。
# 具体秒数先写死常量（P1 一贯做法，后续可迁移进 config_catalog）：
#   volatile（"当前宏观利率环境"这类）— 7 天
#   periodic（介于两者之间，比如"季度财报解读方法"）— 30 天
STALENESS_SECONDS_BY_VOLATILITY = {
    "volatile": 7 * 86400,
    "periodic": 30 * 86400,
}


def _needs_staleness_refresh(topic: OutlineTopic, now: float) -> bool:
    """判断一个已经 `coverage_state == "covered"` 的子主题，是否因为
    `volatility` 标注和距上次触达的时间，需要被重新纳入本轮候选。
    `stable`（或未识别的取值）永远返回 False——只有 §13.2-d 明确定义的
    两档时效性标注才会触发重新检索，避免"忘记打标"的子主题被误判过期。"""
    if topic.coverage_state != "covered":
        return False
    threshold = STALENESS_SECONDS_BY_VOLATILITY.get(topic.volatility)
    if not threshold:
        return False
    if topic.last_touched_at is None:
        return True
    return (now - topic.last_touched_at) >= threshold


def scan_outline_gaps(
    track: CapabilityTrack,
    limit: int = DEFAULT_TOPICS_PER_CYCLE,
    miss_counts: Optional[dict[str, int]] = None,
    capability_confidence: Optional[dict[str, float]] = None,
    now: Optional[float] = None,
) -> list[OutlineTopic]:
    """规则式缺口扫描：优先选 uncovered，其次 partial；同一 coverage_state
    内，先按 `miss_counts`（§14.1-a 的 `miss_observed` 台账统计，见
    `_topic_miss_counts()`）降序——用户实际在对话里检索碰壁过的子主题，
    比"系统扫描出来但还没人真正需要过"的子主题更值得优先推进；miss 次数
    相同时，再按 `capability_confidence`（§12.1-a，见
    `_topic_capability_confidence()`）升序——Agent 自评置信度越低的领域
    越优先，没有匹配到 capability_map 条目的子主题给中性值 0.5（既不
    因为"没数据"被排到最后，也不会抢在明确低置信度的子主题前面）；
    以上信号都缺失或相同时，退化为原有的 last_touched_at 从旧到新排序
    （越久没碰过的越优先）。

    [§13.2-d 知识时效性衰减] 候选集不再只是"非 covered"的子主题——已经
    `covered` 但被标注为 `volatility="volatile"/"periodic"` 且距上次
    触达超过对应阈值（见 `_needs_staleness_refresh()`）的子主题，也会
    被重新纳入候选，排序上和 `partial` 同一优先级（已经有内容、只是
    可能过期，不该抢在真正 `uncovered` 的子主题前面，但也不该排到
    "确定还新鲜"的 covered 子主题之后）。这样才不会出现"名义覆盖率
    100%，内容早已过期"的假象却永远不会被重新检索的问题。

    `now` 默认取 `time.time()`；单测里可以传固定时间戳避免依赖真实
    系统时钟。三个新参数（`miss_counts`/`capability_confidence`/`now`）
    默认值下行为与此前完全一致，接线方不用改调用方式。"""
    miss_counts = miss_counts or {}
    capability_confidence = capability_confidence or {}
    now = now if now is not None else time.time()

    def sort_key(t: OutlineTopic, stale: bool):
        state_rank = {"uncovered": 0, "partial": 1, "covered": 2}.get(t.coverage_state, 1)
        if stale:
            state_rank = 1  # 与 partial 同档：已有内容，但需要刷新
        miss_count = miss_counts.get(t.topic_id, 0)
        confidence = capability_confidence.get(t.topic_id, 0.5)
        touched = t.last_touched_at or 0
        return (state_rank, -miss_count, confidence, touched)

    candidates = []
    for t in track.outline:
        stale = _needs_staleness_refresh(t, now)
        if t.coverage_state == "covered" and not stale:
            continue
        candidates.append((sort_key(t, stale), t))
    candidates.sort(key=lambda pair: pair[0])
    return [t for _, t in candidates[:limit]]


def _topic_capability_confidence(track: CapabilityTrack, paths: AgentPaths) -> dict[str, float]:
    """[§12.1-a] 单向只读消费 `perception/self_model.py` 依赖的
    `evolution/consolidation.py::build_capability_map()`，把 Agent 现有
    的领域置信度粗略映射到本 Track 的子主题上，供 `scan_outline_gaps()`
    排优先级——置信度越低的领域，Track 里对应的子主题越应该优先推进。

    匹配方式故意用最朴素的关键词双向子串匹配（`domain in topic.name` 或
    `topic.name in domain`，不区分大小写），不引入语义匹配/embedding——
    `build_capability_map()` 自己的 domain 也是按关键词从 goal 文本里
    粗略推断出来的（见 `_infer_domain()`），子主题匹配精度对齐上游数据
    本身的精度即可，没必要在这一层做得比数据源更精细。一个子主题可能
    匹配到多个 domain 时取置信度最低的那个（更保守，宁可多推进一点）。

    这是纯只读消费、单向依赖（见设计文档 §12.1-a），不产生任何反向
    副作用、不写回 capability_map，也不影响 self_model 自身的读取逻辑。
    失败（consolidation 模块不可用/无数据）时静默返回空字典，
    调用方退化为不带 capability_map 信号的原有排序，不报错、不阻断。
    """
    try:
        from mini_agent.evolution.consolidation import build_capability_map
        entries = build_capability_map(paths, None)  # None=只读，不写回
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.capability_learning._topic_capability_confidence')
        return {}

    if not entries:
        return {}

    result: dict[str, float] = {}
    for topic in track.outline:
        name_lower = topic.name.lower()
        best: Optional[float] = None
        for entry in entries:
            domain_lower = entry.domain.lower()
            if not domain_lower or not name_lower:
                continue
            if domain_lower in name_lower or name_lower in domain_lower:
                if best is None or entry.confidence < best:
                    best = entry.confidence
        if best is not None:
            result[topic.topic_id] = best
    return result


def _topic_miss_counts(ledger_store: "CapabilityLedgerStore", track_id: str) -> dict[str, int]:
    """统计某个 Track 台账里各 topic_id 的 miss_observed 次数，供
    `scan_outline_gaps()` 的优先级排序使用。取最近 200 条台账里的
    miss_observed 计数——不是全量：台账文件会随时间无限增长，只看最近
    一段时间的信号足够反映"最近是不是真的常被问到"，也避免几年前的一次
    未命中永远把某个子主题钉在队首。"""
    entries = ledger_store.list_for_track(track_id, limit=200)
    counts: dict[str, int] = {}
    for e in entries:
        if e.action == "miss_observed":
            counts[e.topic_id] = counts.get(e.topic_id, 0) + 1
    return counts


def needs_user_context(topic: OutlineTopic, track: CapabilityTrack) -> bool:
    """判断这个子主题是否属于"互联网查不到，只有用户自己知道"的类型。
    P1 用非常保守的规则式占位实现：只有 persona 型 Track 默认判定为
    需要用户输入（因为人格细节大部分天然只能靠问，见设计文档 §10.2），
    knowledge 型默认不需要（P2 才接入更细致的判定，比如关键词命中
    "偏好/风险承受能力/关注哪些具体标的"这类主观性强的表述）。"""
    return track.target_type == "persona"


# ── 跨 Track 子主题去重与知识共享（§13.1-b，本轮实现）──────────────────────
#
# 用户同时开多个 Track 时子主题会有交叉（比如"股票分析"和"宏观经济"两个
# Track 都可能各自检索一遍"利率对资产价格的影响"）。设计文档 §13.1-c 要求
# 这一步用"关键词/tag 相似度即可，不需要语义匹配"——这里用字符级 2-gram
# （bigram）Jaccard 相似度：对中文场景比空格分词更友好（中文子主题名称
# 通常没有空格），对英文场景也退化成朴素的重叠度量，足够作为"值不值得
# 复用已有页面"这种粗粒度判断的依据，不需要引入 embedding/语义模型。

CROSS_TRACK_REUSE_SIMILARITY_THRESHOLD = 0.5


def _name_bigrams(name: str) -> set[str]:
    s = name.strip().lower()
    if len(s) < 2:
        return {s} if s else set()
    return {s[i : i + 2] for i in range(len(s) - 1)}


def _topic_name_similarity(a: str, b: str) -> float:
    """字符级 2-gram Jaccard 相似度，取值 [0, 1]。两边任一为空返回 0。"""
    sa, sb = _name_bigrams(a), _name_bigrams(b)
    if not sa or not sb:
        return 0.0
    union = len(sa | sb)
    if not union:
        return 0.0
    return len(sa & sb) / union


def find_cross_track_reuse(
    topic: OutlineTopic,
    track: CapabilityTrack,
    other_tracks: list[CapabilityTrack],
) -> Optional[OutlineTopic]:
    """在其它 active Track 的大纲里找一个"名字足够相似、已经 covered、
    已有 wiki 页面"的子主题，供本轮复用而不是重新检索。

    只在同一子主题**没有自己的 wiki 页面**时才有复用的意义（已经有
    页面的子主题走既有的 §13.2-d 时效性刷新逻辑，不应该被"复用"覆盖掉
    自己已有的、可能更贴合本 Track 语境的内容）。多个候选命中时取
    相似度最高的一个；相似度相同时取先出现的（`other_tracks` 顺序
    由调用方决定，本函数不额外排序，保持纯函数、无副作用）。

    找不到满足条件的候选时返回 None，调用方应退回原有的检索/跳过逻辑，
    这个函数本身不产生任何副作用（不写台账、不改任何字段），方便
    单测和复用。"""
    if topic.wiki_page_ids:
        return None
    best: Optional[OutlineTopic] = None
    best_score = 0.0
    for other in other_tracks:
        if other.track_id == track.track_id:
            continue
        if other.status != "active":
            continue
        for candidate in other.outline:
            if candidate.coverage_state != "covered" or not candidate.wiki_page_ids:
                continue
            score = _topic_name_similarity(topic.name, candidate.name)
            if score >= CROSS_TRACK_REUSE_SIMILARITY_THRESHOLD and score > best_score:
                best = candidate
                best_score = score
    return best


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
    max_topics_per_run_cycle: Optional[int] = None,
    llm_helper: Optional[Callable[[str], str]] = None,
    outline_suggestion_miss_count_enabled: bool = True,
    outline_suggestion_miss_count_threshold: int = 3,
    outline_suggestion_research_enabled: bool = False,
    outline_suggestion_milestone_enabled: bool = False,
    outline_suggestion_milestone_threshold: float = 0.8,
) -> dict:
    """sys:capability_learning_cycle 对应的单轮编排逻辑（§4 伪流程的落地）。

    P1 阶段：如果没有传入 retriever/wiki_writer，遇到需要检索的子主题会
    记一条 action="skipped" 的台账并跳过，不产生任何外部请求或 wiki 写入
    副作用——这样这个函数在未接线真实检索/写入实现之前就是安全的、
    可以直接单元测试/在 cron 里试跑而不用担心误触发真实抓取。

    [§13.1-b 多 Track 公平调度] 同时开多个 active Track 时，按
    `last_advanced_at` 升序处理（从未推进过/最久没被推进过的优先），
    避免早建的 Track 长期占满配额、后建的 Track 得不到推进。这一点
    在没有 `max_topics_per_run_cycle` 全局预算时不改变最终结果（因为
    P1 起每个 Track 本来就各自独立拿到 `topics_per_cycle` 份额，互不
    挤占），只在设置了全局预算、单轮处理不完所有 Track 时才真正影响
    "谁先谁后"——预算耗尽时排在后面的 Track 本轮不推进，下一轮因为
    `last_advanced_at` 更旧会被排到最前面，长期下来仍然公平。
    `max_topics_per_run_cycle` 默认 None（不设全局预算，向后兼容此前
    行为：每个 Track 各自跑满 `topics_per_cycle`）。

    `llm_helper`：[v0.21 §13.2-f] 可选，`Callable[[str], str]`，传入时会
    在消费已回答问题的同时尝试生成大纲动态生长建议（见
    `generate_outline_suggestion_from_answer()`）；不传时这一步整体跳过，
    行为与此前完全一致（向后兼容）。

    [next_doc/outline_revision_and_suggestion_improvement_plan.md §二]
    新增三个可选的大纲建议来源开关，默认值对齐该文档的"默认开启/关闭"
    决策：`outline_suggestion_miss_count_enabled`（规则式、不需要
    `llm_helper`，默认 True）、`outline_suggestion_research_enabled`/
    `outline_suggestion_milestone_enabled`（都要调 LLM，默认 False）。
    三者都关闭时行为与此前完全一致。

    返回一份本轮执行摘要（供 cron 日志 / 看板展示）。
    """
    track_store = CapabilityTrackStore(paths)
    ledger_store = CapabilityLedgerStore(paths)
    question_store = CapabilityQuestionStore(paths)
    suggestions_may_be_generated = (
        llm_helper is not None or outline_suggestion_miss_count_enabled
    )
    suggestion_store = CapabilityOutlineSuggestionStore(paths) if suggestions_may_be_generated else None

    summary = {"tracks_processed": 0, "topics_researched": 0, "questions_raised": 0,
               "questions_consumed": 0, "topics_skipped": 0, "topics_reused": 0,
               "outline_suggestions_generated": 0, "topics_research_empty": 0,
               "topics_research_thin": 0, "questions_reused": 0}

    active_tracks = sorted(
        track_store.list_tracks(status="active"),
        key=lambda t: t.last_advanced_at if t.last_advanced_at is not None else 0.0,
    )
    global_budget_remaining = max_topics_per_run_cycle

    for track in active_tracks:
        summary["tracks_processed"] += 1
        track_advanced = False

        # 消费已回答但尚未处理的问题（§4 伪流程最后一步）——不占用检索/
        # 写入类的全局预算（成本可忽略：只是读一条已有答案记一笔台账），
        # 不受 max_topics_per_run_cycle 限制，任何情况下都会处理。
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
            # [capability_learning_duplicate_question_dedup_plan.md 根因一]
            # 回答被消费时把对应子主题标记为 covered，避免下一轮
            # scan_outline_gaps() 把它重新选中、needs_user_context() 再问
            # 一遍一模一样的问题。
            _mark_topic_covered(track_store, track, q.topic_id)
            if suggestion_store is not None:
                pending_names = [
                    s.suggested_name for s in suggestion_store.list_suggestions(
                        status="pending", track_id=track.track_id,
                    )
                ]
                new_suggestion = generate_outline_suggestion_from_answer(
                    track, q, llm_helper, existing_pending_names=pending_names,
                )
                if new_suggestion is not None:
                    suggestion_store.add(new_suggestion)
                    ledger_store.append(CapabilityLedgerEntry(
                        track_id=track.track_id,
                        topic_id=q.topic_id,
                        action="outline_suggested",
                        summary=f"从回答中提炼出大纲外新关注点建议：「{new_suggestion.suggested_name}」，"
                                f"等待用户在看板/CLI 采纳或忽略",
                    ))
                    summary["outline_suggestions_generated"] += 1
            question_store.mark_consumed(q.question_id)
            summary["questions_consumed"] += 1

        # [§二-1 miss_counts 驱动，规则式，默认开启] 每个 Track 每轮最多
        # 生成 1 条这类建议，不占用检索类全局预算（不需要网络请求/LLM）。
        if outline_suggestion_miss_count_enabled and suggestion_store is not None:
            pending_names = [
                s.suggested_name for s in suggestion_store.list_suggestions(
                    status="pending", track_id=track.track_id,
                )
            ]
            miss_suggestion = generate_outline_suggestion_from_miss_counts(
                track, ledger_store,
                threshold=outline_suggestion_miss_count_threshold,
                existing_pending_names=pending_names,
            )
            if miss_suggestion is not None:
                suggestion_store.add(miss_suggestion)
                ledger_store.append(CapabilityLedgerEntry(
                    track_id=track.track_id,
                    topic_id="unclassified",
                    action="outline_suggested",
                    summary=f"从检索未命中统计中提炼出大纲外新关注点建议："
                            f"「{miss_suggestion.suggested_name}」，等待用户在看板/CLI 采纳或忽略",
                ))
                summary["outline_suggestions_generated"] += 1

        if global_budget_remaining is not None and global_budget_remaining <= 0:
            # 全局预算已耗尽，本轮不再推进任何子主题（但上面已回答问题的
            # 消费仍然发生了）。剩余 active Track 下一轮因为
            # last_advanced_at 更旧会被排到最前面，见函数文档字符串。
            continue

        # 挑选本轮推进的子主题（§14.1-a：miss_observed 台账优先级信号；
        # §12.1-a：capability_map 领域置信度优先级信号，见 _topic_capability_confidence）
        miss_counts = _topic_miss_counts(ledger_store, track.track_id)
        capability_confidence = _topic_capability_confidence(track, paths)
        pending = question_store.pending_count(track.track_id)
        per_track_limit = topics_per_cycle
        if global_budget_remaining is not None:
            per_track_limit = min(per_track_limit, global_budget_remaining)
        if pending >= max_pending_questions:
            # 待回答问题已达上限，本轮只推进不需要用户输入的子主题
            topics = [
                t for t in scan_outline_gaps(
                    track, limit=max(per_track_limit, topics_per_cycle) * 2,
                    miss_counts=miss_counts, capability_confidence=capability_confidence,
                )
                if not needs_user_context(t, track)
            ][:per_track_limit]
        else:
            topics = scan_outline_gaps(
                track, limit=per_track_limit,
                miss_counts=miss_counts, capability_confidence=capability_confidence,
            )

        for topic in topics:
            if global_budget_remaining is not None and global_budget_remaining <= 0:
                break

            if needs_user_context(topic, track):
                if pending >= max_pending_questions:
                    continue
                question_text = (
                    f"关于「{topic.name}」，能告诉我更多你的具体偏好/背景吗？"
                    f"这会影响后续推进的方向。"
                )
                # [capability_learning_duplicate_question_dedup_plan.md
                # 根因二] 真正提问之前，先看这个 Track 下有没有语义上问的
                # 是同一件事、且已经回答过的历史问题——命中就直接复用答案，
                # 不再重复打扰用户；不生成新的 pending 问题，直接把子主题
                # 标记为 covered。
                reused = find_reusable_answered_question(
                    question_text, track, question_store, llm_helper=llm_helper,
                )
                if reused is not None:
                    _mark_topic_covered(track_store, track, topic.topic_id)
                    ledger_store.append(CapabilityLedgerEntry(
                        track_id=track.track_id,
                        topic_id=topic.topic_id,
                        action="question_reused",
                        summary=f"「{topic.name}」与历史问题「{reused.question}」语义重复，"
                                f"直接复用其答案「{reused.answer}」，未再次询问用户",
                    ))
                    summary["questions_reused"] += 1
                    track_advanced = True
                    if global_budget_remaining is not None:
                        global_budget_remaining -= 1
                    continue
                q = question_store.raise_question(
                    track_id=track.track_id,
                    topic_id=topic.topic_id,
                    question=question_text,
                )
                ledger_store.append(CapabilityLedgerEntry(
                    track_id=track.track_id,
                    topic_id=topic.topic_id,
                    action="question_raised",
                    summary=f"生成待回答问题：{q.question}",
                ))
                summary["questions_raised"] += 1
                pending += 1
                track_advanced = True
                if global_budget_remaining is not None:
                    global_budget_remaining -= 1
                continue

            # [§13.1-c 跨 Track 复用] 检索之前先看看有没有其它 active Track
            # 已经把非常相似的子主题检索完了——复用检测本身不依赖
            # retriever/wiki_writer 是否接线（只是读别的 Track 已有的
            # wiki_page_ids），所以放在最前面，即使本轮没接线真实检索/写入
            # 回调也照样生效，不会被 P1 安全默认的"未接线跳过"分支挡住。
            reuse_source = find_cross_track_reuse(topic, track, active_tracks)
            if reuse_source is not None:
                topic.coverage_state = "covered"
                topic.last_touched_at = time.time()
                topic.wiki_page_ids = list(set(topic.wiki_page_ids + reuse_source.wiki_page_ids))
                ledger_store.append(CapabilityLedgerEntry(
                    track_id=track.track_id,
                    topic_id=topic.topic_id,
                    action="reused",
                    summary=f"与其它 Track 的子主题「{reuse_source.name}」高度相似，"
                            f"复用其 {len(reuse_source.wiki_page_ids)} 个 wiki 页面，未重复检索",
                    wiki_page_ids=reuse_source.wiki_page_ids,
                ))
                summary["topics_reused"] += 1
                track_advanced = True
                if global_budget_remaining is not None:
                    global_budget_remaining -= 1
                continue

            if retriever is None or wiki_writer is None:
                ledger_store.append(CapabilityLedgerEntry(
                    track_id=track.track_id,
                    topic_id=topic.topic_id,
                    action="skipped",
                    summary="未接线真实检索/wiki 写入回调，本轮跳过（P1 安全默认）",
                ))
                summary["topics_skipped"] += 1
                track_advanced = True
                if global_budget_remaining is not None:
                    global_budget_remaining -= 1
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
                track_advanced = True
                if global_budget_remaining is not None:
                    global_budget_remaining -= 1
                continue

            # [修复] `results` 为空（检索没找到任何东西，比如 web_search
            # provider 报错/被限流后 `make_web_search_retriever()` 兜底
            # 吞掉异常返回 `[]`，或查询本身确实没有可用信息）时，
            # `wiki_writer()`（见 `make_wiki_writer()`）仍然会无条件写出
            # 一页占位内容（正文只有"（暂无检索结果）"），导致 `page_ids`
            # 永远非空——此前这里直接拿 `page_ids` 是否非空来判断
            # `coverage_state`，会把这种"其实什么也没查到"的子主题错误
            # 标成 `covered`，`scan_outline_gaps()` 从此再也不会把它选回
            # 候选池重试，看起来"已覆盖"实际上永远是一页空内容。
            # `has_real_content` 直接复用 `wiki_writer`（`make_wiki_writer`
            # 里的 `_writer`）判断"是否有实质内容"的同一条件（存在非空
            # `summary`/`text` 字段），保持两边口径一致；自定义
            # `wiki_writer` 只要遵循同一约定（真正没有内容时传入的
            # `results` 本身就是空/无有效摘要）也会得到正确的判断。
            # [next_doc/capability_wiki_freshness_improvement_plan.md 阶段 1]
            # 二元的 has_real_content 扩展成三态：empty（完全没查到内容，
            # v0.21.1 已有行为）/ thin（查到内容但明显太单薄，新增）/
            # sufficient（内容量达标）。只有 sufficient 才允许标 covered，
            # thin 和 empty 都保持/回退 partial，下一轮会被 scan_outline_gaps()
            # 重新选中重试，不需要等 volatility 的周期性刷新窗口。
            content_chars = sum(
                len((r.get("summary") or r.get("text") or "").strip()) for r in results
            )
            if content_chars <= 0:
                completeness = "empty"
            elif content_chars < CONTENT_SUFFICIENT_MIN_CHARS:
                completeness = "thin"
            else:
                completeness = "sufficient"
            has_real_content = completeness != "empty"

            # 优先尝试把完整性信号传给 wiki_writer，让它落盘到 frontmatter；
            # 自定义 wiki_writer 若还是旧的三参数签名（不接受 completeness
            # 关键字参数），TypeError 后退回旧式调用——不强制所有调用方都
            # 跟着改签名，符合本项目"失败路径回退宽松默认"的一贯约定。
            try:
                page_ids = wiki_writer(topic, track, results, completeness=completeness)
            except TypeError:
                page_ids = wiki_writer(topic, track, results)

            action_by_completeness = {
                "empty": "research_empty",
                "thin": "research_thin",
                "sufficient": "researched",
            }
            summary_by_completeness = {
                "empty": "本轮检索未获得有效结果（写入了占位页面，下轮会重新尝试，"
                         "不计入已覆盖）",
                "thin": f"本轮检索到内容但明显不够充分（合计 {content_chars} 字，"
                        f"未达 {CONTENT_SUFFICIENT_MIN_CHARS} 字阈值），下轮会重新尝试补充，"
                        "不计入已覆盖",
                "sufficient": f"检索并写入 {len(page_ids)} 个 wiki 页面",
            }
            ledger_store.append(CapabilityLedgerEntry(
                track_id=track.track_id,
                topic_id=topic.topic_id,
                action=action_by_completeness[completeness],
                summary=summary_by_completeness[completeness],
                wiki_page_ids=page_ids,
            ))
            summary["topics_researched"] += 1
            if not has_real_content:
                summary["topics_research_empty"] += 1
            if completeness == "thin":
                summary["topics_research_thin"] += 1
            track_advanced = True
            if global_budget_remaining is not None:
                global_budget_remaining -= 1

            # [§二-2 检索沉淀驱动，要调 LLM，默认关闭] 每个 Track 每天最多
            # 触发一次，节流用 outline_research_suggestion_last_at。
            if (
                outline_suggestion_research_enabled
                and completeness == "sufficient"
                and suggestion_store is not None
                and llm_helper is not None
            ):
                last_at = track.outline_research_suggestion_last_at
                if last_at is None or (time.time() - last_at) >= 86400:
                    pending_names = [
                        s.suggested_name for s in suggestion_store.list_suggestions(
                            status="pending", track_id=track.track_id,
                        )
                    ]
                    research_suggestion = generate_outline_suggestion_from_research(
                        track, topic, results, llm_helper,
                        existing_pending_names=pending_names,
                    )
                    track.outline_research_suggestion_last_at = time.time()
                    if research_suggestion is not None:
                        suggestion_store.add(research_suggestion)
                        ledger_store.append(CapabilityLedgerEntry(
                            track_id=track.track_id,
                            topic_id=topic.topic_id,
                            action="outline_suggested",
                            summary=f"从本轮检索内容中提炼出大纲外新关注点建议："
                                    f"「{research_suggestion.suggested_name}」，等待用户在看板/CLI 采纳或忽略",
                        ))
                        summary["outline_suggestions_generated"] += 1

            # 更新大纲覆盖状态：只有内容量达标（sufficient）才算 covered；
            # thin（内容太单薄）和 empty（没查到任何东西）都归 partial，
            # 保证下一轮还会被 `scan_outline_gaps()` 选中重试，不会因为
            # 写了一页内容单薄/空的页面就被判定"已经完成，不用再管了"。
            topic.coverage_state = "covered" if (completeness == "sufficient" and page_ids) else "partial"
            topic.last_touched_at = time.time()
            topic.wiki_page_ids = list(set(topic.wiki_page_ids + page_ids))

        # [§二-3 覆盖率里程碑驱动，要调 LLM，默认关闭] 覆盖率首次达到阈值时
        # 触发一次，`outline_milestone_notified` 去重，每个 Track 只问一次。
        if (
            outline_suggestion_milestone_enabled
            and suggestion_store is not None
            and llm_helper is not None
            and not track.outline_milestone_notified
            and track.outline
        ):
            covered_count = sum(1 for t in track.outline if t.coverage_state == "covered")
            coverage_ratio = covered_count / len(track.outline)
            if coverage_ratio >= outline_suggestion_milestone_threshold:
                pending_names = [
                    s.suggested_name for s in suggestion_store.list_suggestions(
                        status="pending", track_id=track.track_id,
                    )
                ]
                milestone_suggestion = generate_outline_suggestion_from_coverage_milestone(
                    track, llm_helper, existing_pending_names=pending_names,
                )
                track.outline_milestone_notified = True
                if milestone_suggestion is not None:
                    suggestion_store.add(milestone_suggestion)
                    ledger_store.append(CapabilityLedgerEntry(
                        track_id=track.track_id,
                        topic_id="unclassified",
                        action="outline_suggested",
                        summary=f"大纲覆盖率已达里程碑，提炼出继续扩展方向建议："
                                f"「{milestone_suggestion.suggested_name}」，等待用户在看板/CLI 采纳或忽略",
                    ))
                    summary["outline_suggestions_generated"] += 1

        update_fields = {
            "outline": track.outline,
            "outline_research_suggestion_last_at": track.outline_research_suggestion_last_at,
            "outline_milestone_notified": track.outline_milestone_notified,
        }
        if track_advanced:
            update_fields["last_advanced_at"] = time.time()
        track_store.update(track.track_id, **update_fields)

    return summary


def _today_str() -> str:
    """本地时区自然日字符串（YYYY-MM-DD），风格对齐
    growth_advisor.py::_today_str，独立实现一份而不是导入 growth_advisor
    ——两个模块的节流状态本来就要互相独立，不共用一份状态文件，见
    CapabilityLearningConfig.notification_* 字段注释。"""
    return time.strftime("%Y-%m-%d", time.localtime())


def _load_capability_notify_state(paths: AgentPaths) -> dict:
    return _read_json(paths.capability_notify_state_path(), {})


def _save_capability_notify_state(paths: AgentPaths, state: dict) -> None:
    _write_json(paths.capability_notify_state_path(), state)


def maybe_dispatch_capability_notification(
    paths: AgentPaths, cfg, cycle_summary: dict, pending_questions_count: int,
) -> Optional[dict]:
    """v0.21 §8 通知系统接入。

    `run_capability_learning_cycle()` 跑完一轮后调用：如果本轮"新产生了
    待回答问题"或"新沉淀了 wiki 页面"，按天节流（默认每天最多推
    `notification_max_per_day` 条，多轮循环合并成一条摘要，不逐轮推送）
    发一条通知，走已有的 `NotificationDispatcher`。

    - `cfg` 是 `CapabilityLearningConfig`（或 `None`/无该属性时按默认值
      处理，容错方式对齐其它调用方的 `getattr(..., default)` 惯例）。
    - `cycle_summary`：`run_capability_learning_cycle()` 的返回值，读取
      `topics_researched` 减去 `topics_research_empty`（真正查到内容
      并沉淀的子主题数——`topics_researched` 本身包含了"检索没有结果、
      只写了一页占位内容"的情况，用这两个字段的差值而不是
      `topics_researched` 本身，避免推送"新沉淀了 N 篇 wiki 页面"却
      全是空占位页的误导性摘要）和 `questions_raised`。
    - 空轮（两个数都是 0）不占用推送额度，也不发送任何通知——"没有新
      内容"本身不构成推送理由，这条规则和 growth_advisor 的"宁可不推，
      不为了凑数硬推"是同一条原则。
    - 任何一步异常都不应该打断调用方主流程，统一 try/except +
      log_exception 兜底，返回 None。

    返回本次是否实际发送、以及发送渠道结果，供 cron 日志/测试断言；
    `None` 表示本次没有发送（含"被节流跳过""关闭""空轮"三种情况，
    调用方通常不需要区分，需要区分时可自行传参 dry-run 检查前置条件）。
    """
    try:
        new_questions = int(cycle_summary.get("questions_raised", 0) or 0)
        new_pages = int(cycle_summary.get("topics_researched", 0) or 0) - int(
            cycle_summary.get("topics_research_empty", 0) or 0
        )
        if new_questions <= 0 and new_pages <= 0:
            return None

        notification_enabled = bool(getattr(cfg, "notification_enabled", True))
        if not notification_enabled:
            return None
        freq = getattr(cfg, "notification_frequency", "daily")
        if freq == "kanban_only":
            return None
        max_per_day = int(getattr(cfg, "notification_max_per_day", 1) or 1)

        state = _load_capability_notify_state(paths)
        today = _today_str()
        if state.get("last_notify_date") != today:
            state["last_notify_date"] = today
            state["notify_count_today"] = 0
        if state.get("notify_count_today", 0) >= max_per_day:
            return None

        from mini_agent.notification.dispatcher import NotificationDispatcher, NotificationMessage

        lines = []
        if pending_questions_count > 0:
            lines.append(f"你有 {pending_questions_count} 个待回答问题")
        if new_pages > 0:
            lines.append(f"本轮新沉淀 {new_pages} 篇 wiki 页面")
        if new_questions > 0:
            lines.append(f"本轮新生成 {new_questions} 个待回答问题")
        message = NotificationMessage(
            title="能力学习：本轮进展摘要",
            body="，".join(lines) + "。",
            source="capability_learning",
            meta={
                "questions_raised": new_questions,
                "topics_researched": new_pages,
                "pending_questions": pending_questions_count,
            },
        )
        results = NotificationDispatcher(paths).dispatch(message)

        state["notify_count_today"] = state.get("notify_count_today", 0) + 1
        _save_capability_notify_state(paths, state)
        return {"sent": True, "channels": results}
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.capability_learning.maybe_dispatch_capability_notification")
        return None


# ── 检索未命中记录（§14.1-a 使用驱动学习，接线方见
#    context_builder.py::ContextBuilder._maybe_record_capability_wiki_miss，
#    只在 persona 绑定的 wiki_scopes 命中某个 active knowledge 型 Track 的
#    wiki_tag 时才调用，不做全量未命中查询的猜测式关联）──────────────────


def record_wiki_miss(paths: AgentPaths, track_id: str, topic_hint: str, query: str) -> None:
    """当 context_builder 在某个 Track 的 wiki_tag 范围内检索未命中时调用，
    记一条 miss_observed 台账。下一轮 `run_capability_learning_cycle` 会
    经 `_topic_miss_counts()` 统计这份台账并传给 `scan_outline_gaps()`，
    在同一 coverage_state 内把 miss 次数更高的子主题排到前面（§14.1-a
    收尾，见 `scan_outline_gaps()` 文档字符串）——不依赖 cron 是否已经
    接线：`/capability cycle` 手动触发时同样会读取这份台账。"""
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

    def _writer(
        topic: OutlineTopic, track: CapabilityTrack, results: list[dict],
        *, completeness: Optional[str] = None,
    ) -> list[str]:
        from datetime import datetime, timezone

        from mini_agent.wiki.writer import write_page

        # §13.3-g：写入前先过滤风险表述（具体买卖建议等），并判定是否需要
        # requires_disclaimer 标记——这一步必须在这里做，不能延后到接线阶段。
        results, _filtered_any, requires_disclaimer = apply_compliance_filter(results, track)

        # [next_doc/capability_wiki_freshness_improvement_plan.md 阶段 1]
        # completeness 由调用方（run_capability_learning_cycle）算好传入；
        # 独立调用本函数（比如测试/其它调用方不传这个参数）时在这里按同一套
        # 口径自行兜底计算一次，保证任何调用路径写出来的页面都带这个字段。
        if completeness is None:
            content_chars = sum(
                len((r.get("summary") or r.get("text") or "").strip()) for r in results
            )
            if content_chars <= 0:
                completeness = "empty"
            elif content_chars < CONTENT_SUFFICIENT_MIN_CHARS:
                completeness = "thin"
            else:
                completeness = "sufficient"

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
        if not has_body_content:
            body = (
                f"# {topic.name}\n\n（本轮检索未获得有效结果，后续轮次会自动重试，"
                f"该子主题暂不计入已覆盖）"
            )
        elif completeness == "thin":
            body = (
                "\n".join(body_lines)
                + f"\n\n（本轮检索到的内容偏少，尚未达到判定为完整的字数阈值，"
                  f"后续轮次会继续补充，该子主题暂不计入已覆盖）"
            )
        else:
            body = "\n".join(body_lines)
        if requires_disclaimer:
            body += "\n\n> 仅供参考，不构成投资/医疗/法律等专业建议。"

        extra_fm = {
            "capability_track_id": track.track_id,
            "source_urls": urls,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "requires_disclaimer": requires_disclaimer,
            "content_completeness": completeness,
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


# [v0.22 §14.4] 调研 SubAgent 允许使用的工具白名单：只给只读/检索类工具
# （web_search、内部知识检索、skill 探索与激活），不给文件写入/命令执行类
# 工具——这个 SubAgent 的职责是"调研并给出一段摘要"，不应该有修改项目
# 文件或执行任意命令的能力，即使调研 prompt 本身没有诱导它这么做，也不
# 应该在权限层面留出这个口子。
_AGENT_RETRIEVER_ALLOWED_TOOLS = [
    "web_search",
    "search_knowledge",
    "read_file",
    "glob",
    "grep",
    "skill_list",
    "skill_activate",
    "skill_resource_list",
    "skill_resource_load",
]


# [v0.23] "full" 工具模式下额外授予的工具——读写文件与命令执行，用于支持
# 需要脚本/命令行工具才能完成的复杂调研（下载并解析数据文件、跑一段处理
# 脚本等）。刻意不含 delete_file：即使开放到"full"档位，也不应该让一个
# 无人值守、cron 触发的后台调研/写入任务拥有删除项目文件的能力——这是
# 唯一一条不随配置放开的硬限制，理由见 CapabilityLearningConfig.
# agent_retriever_tool_mode 字段注释。
_AGENT_RETRIEVER_FULL_EXTRA_TOOLS = [
    "bash",
    "write_file",
    "create_file",
    "patch_file",
    "patch_file_simple",
    "list_dir",
    "tree_summary",
    "diff_files",
]


def _agent_retriever_tool_list(cfg: "AppConfig") -> list[str]:
    """按 `CapabilityLearningConfig.agent_retriever_tool_mode` 拼出调研/
    写入 SubAgent 的工具白名单。`"full"` 在只读白名单基础上追加读写/执行
    类工具（不含 delete_file，见上方注释）；未识别的值按 `"readonly"`
    处理。"""
    tool_mode = str(
        getattr(cfg.capability_learning, "agent_retriever_tool_mode", "readonly") or "readonly"
    )
    tools = list(_AGENT_RETRIEVER_ALLOWED_TOOLS)
    if tool_mode == "full":
        tools += [t for t in _AGENT_RETRIEVER_FULL_EXTRA_TOOLS if t not in tools]
    return tools


def make_agent_retriever(cfg: "AppConfig") -> RetrieverFn:
    """[v0.22 §14.4] 返回一个绑定了 cfg 的 retriever 回调，与
    `make_web_search_retriever()` 签名/调用约定完全一致，直接传给
    `run_capability_learning_cycle(retriever=make_agent_retriever(cfg))`。

    和 `make_web_search_retriever()` 的区别：不直接调用 web_search
    provider，而是为每个子主题启动一个受限工具集的 `SubAgent`
    （`orchestrator/sub_agent.py`），让它带着
    `_AGENT_RETRIEVER_ALLOWED_TOOLS` 里的只读工具自主完成一轮调研——
    可以先用 `skill_list`/`skill_activate` 看看有没有更适合这个领域的
    技能可以激活辅助检索/分析，也可以用 `search_knowledge` 复用项目内已有
    知识，而不是只会做"关键词拼接 → 单次搜索引擎调用"这一种最朴素的检索。
    这解决了纯 `web_search` retriever 遇到需要多跳信息、领域专用检索技巧
    的子主题时"查不深"的问题。

    调用方必须自己先检查 `cfg.capability_learning.retriever_enabled`（和
    `make_web_search_retriever()` 同款约定，见该函数上方注释）。

    每个子主题一个独立的 `Task`（不带 `session_id`，不落盘 session/task
    记录，避免给用户的 session 列表塞进大量后台调研任务），`auto_approve=
    True`（cron 场景无人值守，不能等交互式审批），`max_turns`/超时分别由
    `agent_retriever_max_turns`/`agent_retriever_timeout_seconds` 控制。
    SubAgent 执行失败、超时或没有产出有效文本时返回空列表，让调用方走
    既有的"没有可用结果"安全路径（不中断整轮循环）——与
    `make_web_search_retriever()` 捕获 `WebSearchError` 后的兜底行为
    一致，两种 retriever_mode 对上层是等价的失败语义。
    """
    from mini_agent.orchestrator.sub_agent import SubAgent
    from mini_agent.orchestrator.task import Task, TaskRecord, TaskStatus

    max_turns = max(1, cfg.capability_learning.agent_retriever_max_turns)
    timeout_seconds = max(30, cfg.capability_learning.agent_retriever_timeout_seconds)
    summary_max_chars = max(0, cfg.capability_learning.summary_max_chars)

    def _retriever(topic: OutlineTopic, track: CapabilityTrack) -> list[dict]:
        prompt = (
            f"请围绕子主题「{topic.name}」（所属能力方向：「{track.title}」）"
            f"完成一轮调研。\n"
            f"- 可以使用 web_search、search_knowledge 等工具检索信息；\n"
            f"- 如果判断有更适合这个领域的技能可以辅助检索/分析，先用 "
            f"skill_list 看看有没有，再用 skill_activate 激活它，不要只做"
            f"最简单的关键词搜索；\n"
            f"- 调研完成后，直接输出一段 Markdown 格式的调研摘要（不需要"
            f"寒暄/前后缀说明），包含关键结论要点，并在末尾列出信息来源"
            f"（网址、或使用的技能名称）；\n"
            f"- 如果确实查不到任何相关信息，直接说明查不到，不要编造。"
        )
        task = Task(
            prompt=prompt,
            name=f"capability_research:{topic.topic_id}",
            auto_approve=True,
            max_turns=max_turns,
            allowed_tools=_agent_retriever_tool_list(cfg),
        )
        record = TaskRecord(task=task)
        sub = SubAgent(record, cfg)
        try:
            sub.start()
            sub.join(timeout=timeout_seconds)
        except Exception:
            return []

        if record.status != TaskStatus.DONE:
            # 超时（线程仍在跑但我们不再等）/失败/被取消——发一次取消信号
            # 让后台线程尽快收尾，本轮按"没有可用结果"处理，下一轮
            # scan_outline_gaps() 还会重新选中这个子主题重试。
            try:
                sub.cancel()
            except Exception:
                pass
            return []

        output = (record.result.output if record.result else "").strip()
        if not output:
            return []
        if summary_max_chars and len(output) > summary_max_chars:
            output = output[:summary_max_chars].rstrip() + "…"
        return [{"summary": output, "source": "agent_research"}]

    return _retriever


# ── [v0.23] agent 直接写 wiki 模式（wiki_write_mode="agent"）─────────────
#
# 触发背景：此前唯一的写入实现（`make_wiki_writer`）是"agent/检索器只
# 产出一段摘要文本，固定模板负责拼成页面"——agent 没有机会根据调研过程
# 自己判断内容该怎么组织、要不要再补充。这里提供一种可选替代：让一个
# SubAgent 直接调用专属的 `capability_wiki_write` 工具把最终内容写进
# wiki 页面。
#
# 没有把 wiki 目录直接暴露给通用的 write_file/create_file——那两个工具在
# "full" 工具模式下是给 agent 用来处理调研过程中的辅助性读写（比如临时
# 脚本、下载的数据文件），wiki 落盘本身只能走 capability_wiki_write 这
# 一条路径，这样 §13.3-g 合规过滤（写入前的句级风险过滤 + disclaimer
# 标注）才能保证不被绕开，page_id/tags 等结构化字段也不会被模型自由
# 决定，而是由 make_agent_wiki_writer() 按 topic/track 预先绑定好。
_CAPABILITY_WIKI_WRITE_STATE: dict = {
    "active": False,
    "paths": None,
    "page_id": None,
    "tags": None,
    "extra_fm": None,
    "requires_disclaimer": False,
    "written_page_id": None,
}


def _capability_wiki_write_impl(body: str) -> str:
    state = _CAPABILITY_WIKI_WRITE_STATE
    if not state.get("active"):
        return "错误：当前不在能力学习写入上下文里，这个工具暂不可用。"
    body = (body or "").strip()
    if not body:
        return "错误：body 不能为空。"
    from mini_agent.wiki.writer import write_page

    if state.get("requires_disclaimer"):
        body = body + "\n\n> 仅供参考，不构成投资/医疗/法律等专业建议。"
    write_page(
        paths=state["paths"],
        page_id=state["page_id"],
        page_type="topic",
        body=body,
        tags=state["tags"] or [],
        extra_frontmatter=state["extra_fm"] or {},
    )
    state["written_page_id"] = state["page_id"]
    return f"已写入 wiki 页面 {state['page_id']}"


try:
    from mini_agent.tools import tool as _capability_tool_decorator

    @_capability_tool_decorator(
        name="capability_wiki_write",
        description=(
            "[能力学习专用] 把这一轮调研的最终结论写入当前子主题对应的 wiki 页面。"
            "仅在能力学习/人设养成的调研任务里可用，一次任务里只调用一次；"
            "正文用 Markdown，需要包含关键结论要点，并在末尾列出信息来源。"
        ),
        schema={
            "type": "object",
            "properties": {
                "body": {"type": "string", "description": "wiki 页面正文（Markdown）"},
            },
            "required": ["body"],
        },
        requires_approval=False,
        group="capability_learning",
        override=True,
    )
    def capability_wiki_write(body: str) -> str:  # noqa: D401 - 见 _capability_wiki_write_impl
        return _capability_wiki_write_impl(body)
except Exception:  # pragma: no cover - 工具注册基础设施异常时不影响模块导入
    pass


def make_agent_wiki_writer(cfg: "AppConfig", paths: AgentPaths) -> WikiWriterFn:
    """[v0.23] `wiki_write_mode="agent"` 时使用，与 `make_wiki_writer(paths)`
    签名/调用约定完全一致（`WikiWriterFn`），可直接替换传给
    `run_capability_learning_cycle(wiki_writer=...)`。

    流程：把 `retriever` 已经拿到的 `results`（经 §13.3-g 合规过滤）整理成
    一份"调研素材"喂给写入 SubAgent，SubAgent 可以直接据此整理成页面，
    也可以按需再用工具补充调研（工具集受 `agent_retriever_tool_mode`
    控制，与 `make_agent_retriever()` 共享同一份白名单逻辑），最终必须
    调用 `capability_wiki_write` 落盘。

    失败兜底：SubAgent 超时/失败/没有成功调用写入工具时，退回
    `make_wiki_writer(paths)` 的固定模板渲染（复用同一份 `results`，不
    重新调用 retriever）——保证"这个子主题最终有一页落盘记录"这个 P1
    就定下的不变量（见 v0.21.1 检索空结果 bug 修复）在这个新写入模式下
    同样成立，不会退化出"researched 但没有任何页面"的情况。
    """
    from mini_agent.orchestrator.sub_agent import SubAgent
    from mini_agent.orchestrator.task import Task, TaskRecord, TaskStatus

    fallback_writer = make_wiki_writer(paths)
    max_turns = max(1, cfg.capability_learning.agent_wiki_writer_max_turns)
    timeout_seconds = max(30, cfg.capability_learning.agent_wiki_writer_timeout_seconds)

    def _writer(
        topic: OutlineTopic, track: CapabilityTrack, results: list[dict],
        *, completeness: Optional[str] = None,
    ) -> list[str]:
        from datetime import datetime, timezone

        filtered_results, _any_filtered, requires_disclaimer = apply_compliance_filter(results, track)
        urls = [r.get("url") for r in filtered_results if r.get("url")]
        material_lines = []
        for r in filtered_results:
            s = (r.get("summary") or r.get("text") or "").strip()
            if s:
                material_lines.append(f"- {s}" + (f"（来源：{r.get('url')}）" if r.get("url") else ""))
        material = "\n".join(material_lines) if material_lines else "（本轮调研未提供额外素材，可自行检索补充）"

        # [next_doc/capability_wiki_freshness_improvement_plan.md 阶段 1]
        # 兜底口径与 make_wiki_writer 一致：调用方没传 completeness 时按
        # results 自行算一次，保证走 agent 写入模式的页面也带这个字段。
        if completeness is None:
            content_chars = sum(
                len((r.get("summary") or r.get("text") or "").strip()) for r in filtered_results
            )
            if content_chars <= 0:
                completeness = "empty"
            elif content_chars < CONTENT_SUFFICIENT_MIN_CHARS:
                completeness = "thin"
            else:
                completeness = "sufficient"

        page_id = f"cap_{track.track_id}_{topic.topic_id}"
        _CAPABILITY_WIKI_WRITE_STATE.update({
            "active": True,
            "paths": paths,
            "page_id": page_id,
            "tags": [track.wiki_tag] if track.wiki_tag else [],
            "extra_fm": {
                "capability_track_id": track.track_id,
                "source_urls": urls,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "requires_disclaimer": requires_disclaimer,
                "content_completeness": completeness,
            },
            "requires_disclaimer": requires_disclaimer,
            "written_page_id": None,
        })
        try:
            prompt = (
                f"你正在为能力方向「{track.title}」的子主题「{topic.name}」整理并写入 wiki 页面。\n"
                f"已有调研素材：\n{material}\n\n"
                f"- 素材足够就直接整理成结构清晰的 Markdown 正文；判断需要补充时，"
                f"可以用 web_search/search_knowledge 等工具再查，复杂内容也可以用"
                f"允许范围内的读写/命令行工具辅助处理，但这些只是辅助手段；\n"
                f"- 最终必须调用 capability_wiki_write 工具一次，把整理好的正文写进去；\n"
                f"- 不需要在对话里重复输出正文全文，写入工具即可，完成后简单确认一句。"
            )
            task = Task(
                prompt=prompt,
                name=f"capability_wiki_write:{topic.topic_id}",
                auto_approve=True,
                max_turns=max_turns,
                allowed_tools=_agent_retriever_tool_list(cfg) + ["capability_wiki_write"],
            )
            record = TaskRecord(task=task)
            sub = SubAgent(record, cfg)
            try:
                sub.start()
                sub.join(timeout=timeout_seconds)
            except Exception:
                pass
            if record.status != TaskStatus.DONE:
                try:
                    sub.cancel()
                except Exception:
                    pass
            written = _CAPABILITY_WIKI_WRITE_STATE.get("written_page_id")
            if written:
                return [written]
        finally:
            _CAPABILITY_WIKI_WRITE_STATE.update({"active": False, "paths": None})

        # SubAgent 没有成功写入——退回固定模板兜底，复用同一份 results。
        return fallback_writer(topic, track, results, completeness=completeness)

    return _writer


# ── persona 型 Track：人设草稿合成（§10.3，本轮实现）───────────────────────
#
# knowledge 型 Track 产出的是 wiki 页面；persona 型 Track
# （target_type="persona"）产出的是一份 `.agent/personas/*.md` 格式的
# 人设草稿。素材主要来自 CapabilityQuestion 的用户回答（§10.2："信息主要
# 来源：用户异步回答为主"），所以草稿合成是一个独立于
# run_capability_learning_cycle() 主循环的显式入口，不挂进每轮 cron
# 循环——草稿不应该在用户没有察觉的情况下悄悄变化，这与 §10.3 第 4 点
# "发布必须是显式用户动作"是同一种克制哲学的延伸：生成草稿本身虽然不是
# "发布"，但也应该是用户主动触发（比如 `/capability persona draft`），
# 而不是后台无声进行。

_REAL_PERSON_REFERENCE_PATTERNS = [
    r"(像|学|模仿|扮演).{0,6}(本人|真人)",
    r"(就是|扮演成?).{1,10}(这个人|他本人|她本人)",
]


def detect_real_person_reference(persona_desc: str) -> Optional[str]:
    """[§10.4-2] 粗粒度启发式检测：用户方向描述里是否出现"要求模仿/扮演
    某个真实公众人物本人"这类表述模式。命中时返回一段供草稿预览展示的
    警示文案，未命中返回 None。

    这是关键词/正则层面的启发式，不是可靠的真人识别——正则本身无法判断
    persona_desc 里提到的是不是真的某个可辨识公众人物（做不到，也不该
    尝试做语义级判断），只识别"明确要求模仿/扮演某个真人本人"这一类
    表述模式，命中就提示用户改为"参考某种风格但作为原创虚构人物"
    （§10.4-2 原文），**不自动阻断草稿生成**——检测宁可漏报，也不应该
    因为一个不可靠的正则误伤正常的人设描述，草稿生成本身不是发布，
    留给用户在预览阶段自行判断。"""
    import re

    for pat in _REAL_PERSON_REFERENCE_PATTERNS:
        if re.search(pat, persona_desc):
            return (
                "检测到方向描述里可能包含\"模仿/扮演某个真实公众人物本人\"的表述，"
                "建议改为\"参考某种风格但作为原创虚构人物\"，避免把虚构语录归因给"
                "真实的人。这是关键词层面的粗粒度提示，请自行判断是否属实，"
                "不构成自动阻断。"
            )
    return None


def _slugify_persona_name(title: str) -> str:
    """把 Track 标题转成适合做文件名/frontmatter `name` 字段的 slug。
    保留中英文字符和数字，其余字符（空格、标点等）折叠成连字符。"""
    import re

    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", title.strip().lower()).strip("-")
    return slug or "persona"


def persona_draft_completeness(
    track: "CapabilityTrack", questions: list["CapabilityQuestion"],
) -> dict:
    """统计"草稿完成度"：大纲一共多少个维度、其中多少个已经有用户回答，
    以及缺失维度的名称列表——供看板/CLI 展示"这版草稿还缺哪些维度"
    （§10.3 第 2 点），不需要重新渲染整份 markdown 就能拿到这份摘要。"""
    answered_topic_ids = {
        q.topic_id for q in questions
        if q.status == "answered" and (q.answer or "").strip()
    }
    missing = [t.name for t in track.outline if t.topic_id not in answered_topic_ids]
    total = len(track.outline)
    return {
        "total": total,
        "answered": total - len(missing),
        "missing_topic_names": missing,
    }


def draft_persona_markdown(
    track: "CapabilityTrack", questions: list["CapabilityQuestion"],
) -> str:
    """把 persona 型 Track 目前收集到的信息（大纲子主题 + 已回答问题的
    答案）合成一版人设草稿，渲染成与手写 `.agent/personas/*.md` 完全
    同样的 frontmatter + 正文格式（§10.3 第 1 点）。

    只使用 `status == "answered"` 且答案非空的问题（不管是否已被
    `run_capability_learning_cycle` 标记 `consumed`——草稿预览应该反映
    "目前所有已知信息"，不受消费状态影响，`consumed` 只是"这一轮循环
    有没有处理过"的内部记账，不代表答案本身失效）。

    frontmatter 里 `allowed_tools` / `wiki_scopes` 故意留空（§10.4-3：
    工具权限类字段不能由自动合成随意放宽，必须用户显式确认；
    `wiki_scopes` 走既有的 §11.4 看板绑定流程，不在草稿合成这一步猜测）。

    这个函数是纯字符串拼接，不做任何文件写入——落盘由调用方决定（草稿
    目录 vs 正式 personas 目录，见 `save_persona_draft()` /
    `publish_persona_draft()`），方便离线单测、不依赖文件系统状态。"""
    from datetime import datetime, timezone

    answers_by_topic: dict[str, list[str]] = {}
    for q in questions:
        if q.status != "answered" or not (q.answer or "").strip():
            continue
        answers_by_topic.setdefault(q.topic_id, []).append(q.answer.strip())

    slug = _slugify_persona_name(track.title)
    desc = track.persona_desc.replace("\n", " ").strip()

    lines: list[str] = [
        "---",
        f"name: {slug}",
        f"display_name: {track.title}",
        f"description: {desc}",
        "tone: ",
        "break_character_policy: soft",
        "allowed_tools: ",
        "wiki_scopes: ",
        "---",
        "",
        f"<!-- 本文件由 Capability Learning 人设草稿合成于 "
        f"{datetime.now(timezone.utc).isoformat()}，尚未发布，"
        f"请人工检查/编辑后再通过 publish_persona_draft() 发布。 -->",
        "",
        f"# {track.title}",
        "",
        track.persona_desc.strip(),
        "",
    ]

    missing_dims: list[str] = []
    for topic in track.outline:
        answers = answers_by_topic.get(topic.topic_id, [])
        lines.append(f"## {topic.name}")
        lines.append("")
        if answers:
            for a in answers:
                lines.append(f"- {a}")
        else:
            lines.append("（暂无信息，尚待用户回答相关问题）")
            missing_dims.append(topic.name)
        lines.append("")

    warning = detect_real_person_reference(track.persona_desc)
    if warning:
        lines.append("<!-- 安全提示：")
        lines.append(warning)
        lines.append("-->")
        lines.append("")

    if missing_dims:
        lines.append(
            "<!-- 草稿完成度提示：以下维度尚缺信息，建议继续通过 "
            "CapabilityQuestion 问答收集后再发布：" + "、".join(missing_dims) + " -->"
        )

    return "\n".join(lines).rstrip() + "\n"


def save_persona_draft(paths: AgentPaths, track_id: str, markdown_text: str) -> Path:
    """把 `draft_persona_markdown()` 的结果落盘到草稿目录（不是正式
    personas 目录），供看板/CLI 预览。返回写入的文件路径。"""
    path = paths.capability_persona_draft_path(track_id)
    path.write_text(markdown_text, encoding="utf-8")
    return path


def load_persona_draft(paths: AgentPaths, track_id: str) -> Optional[str]:
    """读取上一次落盘的人设草稿，不存在返回 None。"""
    path = paths.capability_persona_draft_path(track_id)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def publish_persona_draft(
    paths: AgentPaths, track_id: str, draft_text: Optional[str] = None,
) -> Path:
    """把人设草稿写入正式 `.agent/personas/<name>.md`，使其对 `/role use`
    立即可见（§10.3 第 4 点：发布必须是显式用户动作，不能自动发生——这
    个函数本身不会被 `run_capability_learning_cycle()` 调用，只能由
    CLI/API 的显式命令触发）。

    `draft_text` 不传时，从 `capability_persona_draft_path(track_id)`
    读取上一次 `save_persona_draft()` 落盘的内容——调用方（CLI/API）
    应该先把草稿展示给用户确认/编辑，再调用这个函数，不应该在草稿
    生成的同一步里顺带发布。

    发布目标目录固定用项目级 `project_personas_dir`（而不是
    `global_personas_dir`）——Capability Learning 的 Track 本身是
    project_root 下的实体（`.agent/capability_tracks.json`），产出的
    人设也应该落在同一个项目级作用域，不静默污染全局 personas 目录；
    用户如果确实想要全局可用，可以自己把发布后的文件手动挪到全局目录，
    这是一次显式的、用户自己做出的额外决定。

    frontmatter 里的 `name:` 字段决定发布后的文件名（草稿里可能已被
    用户手改过 name），解析不到时退回按 `track_id` 生成一个 slug，
    确保这个函数在任何输入下都有确定的落盘位置，不会因为解析失败而
    抛出令人困惑的异常。"""
    import re

    text = draft_text if draft_text is not None else load_persona_draft(paths, track_id)
    if not text:
        raise ValueError(
            f"未找到 track_id={track_id} 的人设草稿，请先调用 "
            f"draft_persona_markdown()/save_persona_draft() 生成草稿"
        )

    m = re.search(r"^name:\s*(.+)$", text, re.MULTILINE)
    raw_slug = m.group(1).strip() if m and m.group(1).strip() else track_id
    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff_-]", "-", raw_slug).strip("-") or "persona"

    target_dir = paths.project_personas_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{slug}.md"
    target_path.write_text(text, encoding="utf-8")

    try:
        from mini_agent.orchestrator.persona_profiles import get_persona_loader
        loader = get_persona_loader()
        if loader is not None:
            loader.rediscover()
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(
            _mini_agent_exc,
            where='mini_agent.evolution.capability_learning.publish_persona_draft',
        )

    return target_path
