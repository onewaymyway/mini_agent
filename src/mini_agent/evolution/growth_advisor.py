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

P4-1 范围（对应 next_doc/growth_advisor_improvement_plan_v2.md，本次新增）
——关键词表持久化 + 看板展示 profile / 关键词信息：
    - `_effective_topic_keywords()`：运行时合并内置 `_TOPIC_KEYWORDS` +
      `profile.derived["growth_topic_keywords"]`（用户增量），减去
      `growth_topic_keywords_removed` 里标记隐藏的内置主题。
      `growth_signal_scan()` 改用它替代直接引用模块常量。
    - `_llm_augment_topics()` 归纳出的新主题会经 `_persist_learned_topics()`
      写入 `profile.derived["growth_topic_keywords"]`
      （`source="llm_learned", confirmed_by_user=False`），不再是"用完即弃"。
    - `add_custom_topic_keyword()` / `remove_topic_keyword()` /
      `confirm_topic_keyword()`：看板侧"➕ 添加自定义主题"/"❌ 删除"/
      "✅ 保留"三个操作对应的后端函数。
    - `diagnostics_snapshot()` 新增 `signal_scan.topics_detail`（带
      source/confirmed_by_user 的关键词表明细）与 `user_profile`
      （`UserProfile.derived` 的 summary/tech_stack/habits 只读快照，
      不含 preferences），供看板"Agent 对你的了解"区块渲染。
    - 前置修复（P4-0，见 `profile.py::UserProfileManager.generate()`）：
      画像生成从整体覆盖 `profile.derived` 改成合并式更新，避免
      `growth_focus_areas`/`growth_topic_keywords` 被定期画像刷新静默清空。

P4-2 范围（对应 next_doc/growth_advisor_improvement_plan_v2.md，本次
新增）——关键词表"自动学习稳定后转正"：
    - `_update_keyword_learning_streaks()`：`growth_signal_scan()` 每次
      扫描结束时，对每个待确认的 `llm_learned` 主题更新连续命中计数
      （`consecutive_scan_hits`）——本次扫描命中则 +1，未命中则清零；
      连续命中达到 `_AUTO_CONFIRM_STREAK`（默认 3）次后自动把
      `confirmed_by_user` 置为 `True`（同时打上 `auto_confirmed=True`
      标记，供看板区分"用户手动保留"和"系统自动保留"），不需要用户
      记得去手动点确认。`user_added` 主题创建时已是确认状态，不参与
      这个计数。

P4-3 范围（对应 next_doc/growth_advisor_improvement_plan_v2.md，本次
新增）——反馈学习细化 + 采纳后回访：
    - `_TOPIC_CATEGORIES` / `_category_of()` / `_category_dismiss_counts()`
      / `_category_feedback_multiplier()`：把内置主题粗分成"技术类/管理类/
      表达类"（未登记主题归"其他类"），同一类别下累计的 dismiss 次数会
      用比单主题衰减温和得多的系数（`_CATEGORY_DECAY_FACTOR=0.95`，下限
      `_MIN_CATEGORY_MULTIPLIER=0.7`）压低同类新主题的初始置信度，
      `growth_candidate_derive()` 里与原有的单主题 `_feedback_multiplier`
      相乘生效，两者独立衰减、互不覆盖。
    - `pending_followups()` / `record_followup()`：候选被采纳
      `GrowthAdvisorConfig.followup_review_days`（默认 30）天后，如果还
      没有回访记录，进入待回访列表；用户在看板上回答"progressed"（有
      推进）或"stalled"（没推进）后写回候选（`followup_status`）并追加
      到 `GrowthFeedbackLedger`（`action="followup_progressed"` /
      `"followup_stalled"`），回访只发生一次，不强制回答。
    - `_followup_adjustment_by_dedupe_key()`：把历史回访结果折算成按
      `dedupe_key` 的置信度调节系数（stalled 温和降权、progressed 温和
      加权、封顶 1.0），供同一方向因 dismiss 冷却结束后重新生成候选时
      参考，避免"确实采纳过、只是没空推进"的方向被当成和普通 dismiss
      同等强度的负面信号对待。

P4-4 范围（对应 next_doc/growth_advisor_improvement_plan_v2.md，本次
新增）——报告质量分级 / 增量刷新：
    - `GrowthAdvisorConfig.report_quality_llm_enabled`：独立于
      `llm_signal_augment_enabled` 的另一个 opt-in 开关（默认关闭）——
      那个控制"扫描阶段要不要多花一次 LLM 归纳新主题"，这个控制
      "`run_daily_cycle()` 生成调研报告正文时要不要多花一次 LLM 调用换
      更高信息密度"；默认仍是零成本模板报告，两个开关互不影响。
    - `GrowthReport.evidence_count_at_generation`：生成报告那一刻候选的
      证据数快照；`reports_needing_refresh()` 拿候选当前证据数与这个
      快照比较，差值达到 `report_refresh_min_new_evidence`（默认 3）才
      认为"值得提示刷新"，避免证据每多 1 条就打扰用户。
    - `refresh_growth_report()`：为候选重新走一遍 `generate_growth_
      report()`，生成新报告并把候选的 `report_id` 指向新报告；旧报告
      不删除、不覆盖，只是不再是候选"当前挂着"的那份。

P4-5 范围（对应 next_doc/growth_advisor_improvement_plan_v2.md，本次
新增）——通知策略细化：
    - `GrowthAdvisorConfig.category_notification_frequency`：按类别
      （"技术类"/"管理类"/"表达类"/"其他类"）覆盖推送偏好，目前只识别
      `"kanban_only"` 这一种覆盖值（把某个类别完全静音：仍在看板展示，
      但 `_maybe_dispatch_notification`/`_maybe_dispatch_weekly_digest`
      都不会主动推送这个类别的报告）——不支持给某个类别单独设置和全局
      不同的 daily/weekly_digest 频率，那需要拆分出按类别独立的节流
      状态，留给更明确的需求出现后再做。
    - `_category_acceptance_rate()` / `_notification_priority_score()`：
      多份报告都达到 `notification_min_confidence` 门槛时，不再单纯取
      置信度最高的一条，而是用"置信度 × 该类别历史采纳率加权"算一个
      优先级分数（历史采纳率高的类别加权最多到 1.3 倍，历史上常被忽略
      的类别打到 0.7 折，没有历史决策数据的类别按中性 0.5 处理，既不
      加分也不减分），取优先级最高的一条推送。
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
    # [P4-3] 采纳后回访：accepted_at 记录首次被 set_status(accepted) 的时间
    # （只在从非 accepted 状态转入时写一次，之后即便 attach_report 等操作
    # 更新 updated_at 也不会覆盖它，保证 30 天窗口计算的是"何时被采纳"而
    # 不是"最后一次被改动"）；followup_status 为 None 表示尚未回访，
    # "progressed"/"stalled" 为用户回答后的结果，回访只发生一次。
    accepted_at: Optional[float] = None
    followup_status: Optional[str] = None

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
    # [P4-4] 生成这份报告时候选的证据数快照，供 `reports_needing_refresh()`
    # 判断"生成之后又新增了多少证据"，决定是否提示用户"要不要更新一下"。
    evidence_count_at_generation: int = 0

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
                # [P4-3] 只在首次转入 accepted 时打时间戳，重复 accept（理论上
                # 不应该发生，但幂等处理更安全）不会把 accepted_at 往后推。
                if status == STATUS_ACCEPTED and c.accepted_at is None:
                    c.accepted_at = c.updated_at
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


# ────────── [P4-3] 按主题类别聚合的反馈学习 ──────────
# next_doc/growth_advisor_improvement_plan_v2.md P4-3 第一条：连续忽略同一
# 类别下的多个主题，应该影响同类新主题的初始置信度，而不是各自独立衰减。
# 内置主题按语义分成三个粗类别，未登记的主题（用户自定义 / LLM 学到）统一
# 归为"其他"——不强行归类，避免猜错类别反而引入噪音。类别信号天然比单
# 主题信号弱（用户忽略"Python 工程实践"不代表讨厌"前端与可视化"，即便
# 两者都在"技术类"），所以衰减因子明显比 _DISMISS_DECAY_FACTOR 温和，
# 下限也更高。
_TOPIC_CATEGORIES: dict[str, str] = {
    "Python 工程实践": "技术类",
    "前端与可视化": "技术类",
    "数据分析": "技术类",
    "系统设计与架构": "技术类",
    "AI/LLM 应用": "技术类",
    "项目管理": "管理类",
    "写作与表达": "表达类",
}
_CATEGORY_DECAY_FACTOR = 0.95
_MIN_CATEGORY_MULTIPLIER = 0.7


def _category_of(topic: str) -> str:
    return _TOPIC_CATEGORIES.get(topic, "其他类")


def _category_dismiss_counts(paths) -> dict[str, int]:
    """按类别统计历史 dismiss 次数（同一类别下不同主题的忽略次数累加）。"""
    id_to_title = {c.candidate_id: c.title for c in GrowthBacklog(paths).load_all()}
    counts: dict[str, int] = {}
    for entry in GrowthFeedbackLedger(paths).all_entries():
        if entry.get("action") != STATUS_DISMISSED:
            continue
        title = id_to_title.get(entry.get("candidate_id"))
        if not title:
            continue
        category = _category_of(title)
        counts[category] = counts.get(category, 0) + 1
    return counts


def _category_feedback_multiplier(dismiss_count: int) -> float:
    if dismiss_count <= 0:
        return 1.0
    return max(_MIN_CATEGORY_MULTIPLIER, round(_CATEGORY_DECAY_FACTOR ** dismiss_count, 3))


# ────────── [P4-3] 采纳后回访（followup） ──────────
# 方案第二条：候选被采纳后，隔一段时间（默认 30 天，见
# GrowthAdvisorConfig.followup_review_days）问一次"这个方向后续有没有真的
# 推进"，答案写入 GrowthFeedbackLedger（action="followup_progressed" /
# "followup_stalled"），作为置信度调权的额外信号源——"stalled" 视为比普通
# dismiss 更弱的负向信号（用户当初确实感兴趣，只是没推进，不代表方向选
# 错了），"progressed" 则是正向信号，让同一方向即便之后被重新生成候选也
# 不会一直背着旧的 dismiss 折扣。
_FOLLOWUP_STALLED_FACTOR = 0.9
_FOLLOWUP_PROGRESSED_FACTOR = 1.05
_VALID_FOLLOWUP_OUTCOMES = ("progressed", "stalled")


def pending_followups(paths, cfg=None) -> list[GrowthCandidate]:
    """返回已采纳、满足回访窗口、且尚未回访过的候选（供看板渲染"这个方向
    后续有没有推进？"的回访卡片）。"""
    days = getattr(cfg, "followup_review_days", 30) if cfg is not None else 30
    cutoff = time.time() - max(0, days) * 86400
    out = []
    for c in GrowthBacklog(paths).load_all():
        if c.status != STATUS_ACCEPTED or c.followup_status is not None:
            continue
        if c.accepted_at is None or c.accepted_at > cutoff:
            continue
        out.append(c)
    return sorted(out, key=lambda c: c.accepted_at or 0)


def record_followup(paths, candidate_id: str, outcome: str) -> Optional[GrowthCandidate]:
    """记录一次回访结果，写回候选并追加到反馈台账。"""
    if outcome not in _VALID_FOLLOWUP_OUTCOMES:
        raise ValueError(f"invalid followup outcome: {outcome}")
    backlog = GrowthBacklog(paths)
    all_c = backlog.load_all()
    for c in all_c:
        if c.candidate_id == candidate_id:
            c.followup_status = outcome
            c.updated_at = time.time()
            backlog.save_all(all_c)
            GrowthFeedbackLedger(paths).record(candidate_id, f"followup_{outcome}")
            return c
    return None


def _followup_adjustment_by_dedupe_key(paths) -> dict[str, float]:
    """把历史回访结果折算成按 dedupe_key 的置信度调节系数，供
    `growth_candidate_derive` 在同一方向因 dismiss 冷却结束后重新生成候选
    时参考——避免"曾经采纳过、只是没推进"的方向永远只看普通 dismiss 折扣。
    """
    id_to_key = {c.candidate_id: c.dedupe_key() for c in GrowthBacklog(paths).load_all()}
    adjustments: dict[str, float] = {}
    for entry in GrowthFeedbackLedger(paths).all_entries():
        action = entry.get("action") or ""
        if not action.startswith("followup_"):
            continue
        key = id_to_key.get(entry.get("candidate_id"))
        if not key:
            continue
        current = adjustments.get(key, 1.0)
        if action == "followup_stalled":
            current = round(current * _FOLLOWUP_STALLED_FACTOR, 3)
        elif action == "followup_progressed":
            current = min(1.0, round(current * _FOLLOWUP_PROGRESSED_FACTOR, 3))
        adjustments[key] = current
    return adjustments


# ────────── [P4-5] 通知策略细化：类别级推送偏好 + 重要程度分级 ──────────
# next_doc/growth_advisor_improvement_plan_v2.md P4-5。两条独立能力：
#   1. 类别静音：某个类别的候选完全不主动推送（仍在看板展示），
#      通过 GrowthAdvisorConfig.category_notification_frequency 配置。
#   2. 重要程度分级：多份报告都达到 notification_min_confidence 门槛时，
#      不是简单取置信度最高的一条，而是结合"这个类别历史采纳率高不高"
#      算一个综合优先级分数——证据充分 + 历史上这类方向经常被采纳，应该
#      比"刚好卡线但历史上这类方向常被忽略"的方向优先级更高。


def _category_notification_muted(cfg, topic: str) -> bool:
    """某个主题所属类别是否被配置为 kanban_only（完全静音，只看板展示、
    不主动推送）。目前只识别这一种覆盖值，其余原样透传给全局频率逻辑。"""
    overrides = getattr(cfg, "category_notification_frequency", None) or {}
    return overrides.get(_category_of(topic)) == "kanban_only"


def _category_acceptance_rate(paths) -> dict[str, float]:
    """按类别统计历史采纳率（已做出 accept/dismiss 决策的候选里，
    accept 占比），只统计有过决策的类别，未出现过决策的类别不在返回值里
    （调用方对缺失类别应视为中性 0.5，既不加分也不减分）。"""
    accepted: dict[str, int] = {}
    decided: dict[str, int] = {}
    for c in GrowthBacklog(paths).load_all():
        if c.status not in (STATUS_ACCEPTED, STATUS_DISMISSED):
            continue
        category = _category_of(c.title)
        decided[category] = decided.get(category, 0) + 1
        if c.status == STATUS_ACCEPTED:
            accepted[category] = accepted.get(category, 0) + 1
    return {cat: round(accepted.get(cat, 0) / n, 3) for cat, n in decided.items() if n > 0}


# 优先级分数 = confidence * (_PRIORITY_BASE + _PRIORITY_RATE_WEIGHT * acceptance_rate)
# rate=0（历史上这类方向从没被采纳过）时打 0.7 折，rate=1（历史上逢推
# 必采纳）时打 1.3 倍，rate 缺失（这个类别还没有过任何决策）时按中性 0.5
# 处理，等价于 1.0 倍——不因为"数据不够"就惩罚或奖励。
_PRIORITY_BASE = 0.7
_PRIORITY_RATE_WEIGHT = 0.6


def _notification_priority_score(confidence: float, acceptance_rate: Optional[float]) -> float:
    rate = acceptance_rate if acceptance_rate is not None else 0.5
    return round(confidence * (_PRIORITY_BASE + _PRIORITY_RATE_WEIGHT * rate), 3)


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


# ─────────── [P4-1] 关键词表持久化：profile.derived["growth_topic_keywords"] ───────────
# next_doc/growth_advisor_improvement_plan_v2.md 第 3 节。内置表继续留在
# 代码里（_TOPIC_KEYWORDS），profile.derived 只存增量：用户自定义
# （source="user_added"）+ LLM 学到但待确认（source="llm_learned"）。

def _effective_topic_keywords(profile) -> dict[str, dict[str, Any]]:
    """合并内置关键词表 + 用户 profile 里的增量，减去用户隐藏的内置主题。

    返回 {topic: {"keywords": [...], "source": "built_in"|"user_added"|
    "llm_learned", "confirmed_by_user": bool, "added_at": float|None}}，
    供 growth_signal_scan / diagnostics_snapshot 统一消费，替代此前直接
    引用模块常量 `_TOPIC_KEYWORDS` 的写法。
    """
    derived = dict(getattr(profile, "derived", {}) or {})
    removed = set(derived.get("growth_topic_keywords_removed") or [])
    custom = dict(derived.get("growth_topic_keywords") or {})

    result: dict[str, dict[str, Any]] = {}
    for topic, kws in _TOPIC_KEYWORDS.items():
        if topic in removed:
            continue
        result[topic] = {
            "keywords": list(kws),
            "source": "built_in",
            "confirmed_by_user": True,
            "added_at": None,
            "consecutive_scan_hits": 0,
            "auto_confirmed": False,
        }
    for topic, info in custom.items():
        if not isinstance(info, dict):
            continue
        kws = [k for k in (info.get("keywords") or []) if k]
        if not kws:
            continue
        result[topic] = {
            "keywords": kws,
            "source": info.get("source") or "user_added",
            "confirmed_by_user": bool(info.get("confirmed_by_user", False)),
            "added_at": info.get("added_at"),
            "consecutive_scan_hits": int(info.get("consecutive_scan_hits", 0) or 0),
            "auto_confirmed": bool(info.get("auto_confirmed", False)),
        }
    return result


def _clean_keywords(raw) -> list[str]:
    """清洗用户/LLM 提供的关键词：去空白、去重（大小写不敏感）、丢弃空项。"""
    if isinstance(raw, str):
        raw = re.split(r"[,，、\n]+", raw)
    seen: set[str] = set()
    cleaned: list[str] = []
    for item in raw or []:
        kw = str(item).strip()
        if not kw:
            continue
        key = kw.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(kw)
    return cleaned


def add_custom_topic_keyword(profile, topic: str, keywords) -> dict[str, Any]:
    """用户在看板上手动添加一个自定义主题，直接标记为已确认。"""
    topic = (topic or "").strip()
    if not topic:
        raise ValueError("topic must not be empty")
    cleaned = _clean_keywords(keywords)
    if not cleaned:
        raise ValueError("keywords must not be empty")

    derived = dict(getattr(profile, "derived", {}) or {})
    custom = dict(derived.get("growth_topic_keywords") or {})
    entry = {
        "keywords": cleaned,
        "source": "user_added",
        "confirmed_by_user": True,
        "added_at": time.time(),
    }
    custom[topic] = entry
    derived["growth_topic_keywords"] = custom
    # 用户主动加回来的主题，如果之前被隐藏过，取消隐藏
    removed = [t for t in (derived.get("growth_topic_keywords_removed") or []) if t != topic]
    derived["growth_topic_keywords_removed"] = removed
    profile.derived = derived
    return entry


def remove_topic_keyword(profile, topic: str) -> bool:
    """删除/隐藏一个主题：自定义主题直接从增量表移除；内置主题记入
    `growth_topic_keywords_removed` 黑名单（下次扫描时会被排除）。
    """
    topic = (topic or "").strip()
    if not topic:
        return False
    derived = dict(getattr(profile, "derived", {}) or {})
    custom = dict(derived.get("growth_topic_keywords") or {})
    changed = False
    if topic in custom:
        del custom[topic]
        derived["growth_topic_keywords"] = custom
        changed = True
    if topic in _TOPIC_KEYWORDS:
        removed = set(derived.get("growth_topic_keywords_removed") or [])
        if topic not in removed:
            removed.add(topic)
            derived["growth_topic_keywords_removed"] = sorted(removed)
            changed = True
    if changed:
        profile.derived = derived
    return changed


def confirm_topic_keyword(profile, topic: str) -> bool:
    """用户在看板上点\"✅ 保留\"，把一个待确认（通常是 llm_learned）的
    自定义主题标记为已确认。对内置主题/不存在的主题是安全的空操作。
    """
    topic = (topic or "").strip()
    derived = dict(getattr(profile, "derived", {}) or {})
    custom = dict(derived.get("growth_topic_keywords") or {})
    entry = custom.get(topic)
    if not isinstance(entry, dict):
        return False
    if entry.get("confirmed_by_user"):
        return False
    entry = dict(entry)
    entry["confirmed_by_user"] = True
    custom[topic] = entry
    derived["growth_topic_keywords"] = custom
    profile.derived = derived
    return True


def _persist_learned_topics(profile, new_topics: dict[str, list[str]]) -> None:
    """把 `_llm_augment_topics` 新发现的主题写入
    `profile.derived["growth_topic_keywords"]`（source=llm_learned，
    confirmed_by_user=False）。已经存在于增量表/内置表里的主题不会被
    重复写入或覆盖已有状态（例如已被用户确认过的不会被打回未确认）。
    """
    if not new_topics:
        return
    derived = dict(getattr(profile, "derived", {}) or {})
    custom = dict(derived.get("growth_topic_keywords") or {})
    changed = False
    for topic in new_topics:
        if topic in custom or topic in _TOPIC_KEYWORDS:
            continue
        # LLM 没有直接给出关键词，用主题名自身兜底作为关键词，
        # 保证下次规则扫描也能命中同一批记忆。
        custom[topic] = {
            "keywords": [topic],
            "source": "llm_learned",
            "confirmed_by_user": False,
            "added_at": time.time(),
        }
        changed = True
    if changed:
        derived["growth_topic_keywords"] = custom
        profile.derived = derived


# ─────────── [P4-2] 关键词表"自动学习稳定后转正" ───────────
# next_doc/growth_advisor_improvement_plan_v2.md 第 4 节 P4-2。同一个
# llm_learned 待确认主题，如果连续这么多次扫描都有新证据支持（本次 hits
# 里出现），就自动把 confirmed_by_user 置为 True，不需要用户手动点确认。
_AUTO_CONFIRM_STREAK = 3


def _update_keyword_learning_streaks(profile, hits: dict[str, list[str]]) -> None:
    """在每次 growth_signal_scan 结束时调用：更新每个待确认自定义主题的
    连续命中计数，达到 `_AUTO_CONFIRM_STREAK` 时自动转正。

    - 本次扫描命中该主题（`topic in hits` 且证据非空）→ streak += 1；
      达到阈值 → `confirmed_by_user = True`，streak 清零（转正后不需要
      再继续计数）。
    - 本次扫描没有命中 → streak 重置为 0（要求"连续"，中断一次就重来，
      避免"隔三差五命中一次"也被误判为稳定信号）。
    - 只处理 `source == "llm_learned"` 且尚未确认的主题；`user_added`
      的主题创建时就已经是确认状态，不需要这个机制；已确认的主题不再
      追踪 streak（避免白白维护一个用不上的计数器）。
    - 用户手动删除/隐藏过的主题不会出现在 `_effective_topic_keywords()`
      的结果里，因而也不会出现在 `hits` 里，天然不会被这里"复活"。
    """
    derived = dict(getattr(profile, "derived", {}) or {})
    custom = dict(derived.get("growth_topic_keywords") or {})
    if not custom:
        return

    changed = False
    for topic, entry in list(custom.items()):
        if not isinstance(entry, dict):
            continue
        if entry.get("source") != "llm_learned" or entry.get("confirmed_by_user"):
            continue
        entry = dict(entry)
        hit_this_scan = bool(hits.get(topic))
        streak = int(entry.get("consecutive_scan_hits", 0) or 0)
        if hit_this_scan:
            streak += 1
        else:
            streak = 0
        entry["consecutive_scan_hits"] = streak
        if streak >= _AUTO_CONFIRM_STREAK:
            entry["confirmed_by_user"] = True
            entry["auto_confirmed"] = True
            entry["consecutive_scan_hits"] = 0
        custom[topic] = entry
        changed = True

    if changed:
        derived["growth_topic_keywords"] = custom
        profile.derived = derived


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

    effective_keywords = _effective_topic_keywords(profile)
    entries = memory_store.all_entries() if memory_store is not None else []
    recent_entries = [e for e in entries if getattr(e, "created_at", 0) >= cutoff]
    for entry in recent_entries:
        haystack = " ".join(
            [getattr(entry, "summary", "") or ""]
            + list(getattr(entry, "tags", []) or [])
        ).lower()
        for topic, info in effective_keywords.items():
            if any(kw.lower() in haystack for kw in info["keywords"]):
                hits.setdefault(topic, []).append(getattr(entry, "entry_id", "") or "")

    if llm_helper is not None:
        try:
            before_topics = set(hits.keys())
            hits = _llm_augment_topics(hits, recent_entries, llm_helper)
            new_topics = set(hits.keys()) - before_topics - set(effective_keywords.keys())
            if new_topics:
                _persist_learned_topics(profile, {t: hits[t] for t in new_topics})
        except Exception as exc:
            from mini_agent.errors import log_exception
            log_exception(exc, where="mini_agent.growth_advisor.growth_signal_scan_llm_augment")

    # [P4-2] 待确认自定义主题的连续命中计数 + 达标自动转正，必须在
    # _persist_learned_topics 之后调用（保证本次新学到的主题也能立刻开始
    # 计数），并且在最终写回 growth_focus_areas 之前调用（避免被后面的
    # `profile.derived = derived` 覆盖掉）。
    try:
        _update_keyword_learning_streaks(profile, hits)
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.growth_advisor.growth_signal_scan_auto_confirm")

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
    # P4-3：类别级反馈（同一类别下的忽略会温和地拖累同类新主题的初始置信度）
    # + 采纳后回访调节（stalled/progressed），三者相乘得到最终 multiplier。
    category_dismiss_counts = _category_dismiss_counts(paths)
    followup_adjustments = _followup_adjustment_by_dedupe_key(paths)

    produced: list[GrowthCandidate] = []
    # 按证据数从多到少处理，保证 max_pending 限额下优先生成信号更强的候选
    for topic, refs in sorted(focus_areas.items(), key=lambda kv: -len(kv[1])):
        if topic.strip().lower() in excluded:
            continue
        rationale = f"最近记忆里与「{topic}」相关的内容出现了 {len(set(refs))} 次，可能是值得投入的方向。"
        key = normalize_title_key(topic)
        topic_multiplier = _feedback_multiplier(dismiss_counts.get(key, 0))
        category_multiplier = _category_feedback_multiplier(
            category_dismiss_counts.get(_category_of(topic), 0)
        )
        followup_multiplier = followup_adjustments.get(key, 1.0)
        multiplier = round(topic_multiplier * category_multiplier * followup_multiplier, 3)
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
        evidence_count_at_generation=candidate.evidence_count,
    )
    _append_jsonl(paths.growth_reports_index_path, report.to_dict())
    GrowthBacklog(paths).attach_report(candidate.candidate_id, report_id)
    return report


def list_reports(paths) -> list[GrowthReport]:
    return [GrowthReport.from_dict(d) for d in _read_jsonl(paths.growth_reports_index_path)]


# ────────── [P4-4] 报告质量分级 / 增量刷新 ──────────
# next_doc/growth_advisor_improvement_plan_v2.md P4-4：默认模板报告保持
# 零成本，`report_quality_llm_enabled` 是独立于 `llm_signal_augment_enabled`
# 的另一个 opt-in 开关——后者控制"扫描阶段要不要多花一次 LLM 调用去归纳
# 新主题"，这个开关控制"生成调研报告正文时要不要多花一次 LLM 调用换取
# 更高信息密度"，两者互不影响，用户可以只开一个。

# 候选证据数比上一次生成报告时又新增达到这个数量，才提示"可以刷新了"，
# 避免证据每多 1 条就被打扰。
_DEFAULT_REPORT_REFRESH_MIN_NEW_EVIDENCE = 3


def reports_needing_refresh(paths, cfg=None) -> list[dict]:
    """返回"生成之后证据又显著增长、值得提示用户刷新一下"的报告列表。
    只看每个候选**当前挂着的那份报告**（`candidate.report_id`），已经被
    刷新过的旧报告不会重复出现。纯只读聚合，不做任何写入。"""
    min_new = getattr(cfg, "report_refresh_min_new_evidence", _DEFAULT_REPORT_REFRESH_MIN_NEW_EVIDENCE) if cfg is not None else _DEFAULT_REPORT_REFRESH_MIN_NEW_EVIDENCE
    reports_by_id = {r.report_id: r for r in list_reports(paths)}
    out = []
    for c in GrowthBacklog(paths).load_all():
        if not c.report_id:
            continue
        report = reports_by_id.get(c.report_id)
        if report is None:
            continue
        new_evidence = c.evidence_count - report.evidence_count_at_generation
        if new_evidence >= min_new:
            out.append(
                {
                    "candidate_id": c.candidate_id,
                    "title": c.title,
                    "report_id": report.report_id,
                    "evidence_count": c.evidence_count,
                    "evidence_count_at_generation": report.evidence_count_at_generation,
                    "new_evidence": new_evidence,
                }
            )
    return sorted(out, key=lambda row: -row["new_evidence"])


def refresh_growth_report(
    paths, candidate_id: str, *, llm_helper: Optional[Callable[[str], str]] = None
) -> Optional[GrowthReport]:
    """为一个候选重新生成一份调研报告（新 report_id/新文件），并把候选
    的 `report_id` 指向新报告——旧报告仍留在 `growth_reports_index.jsonl`
    历史记录里（不删除、不覆盖），只是不再是候选"当前挂着"的那份，
    `reports_needing_refresh()` 之后也不会再把它算作"待刷新"。"""
    candidate = GrowthBacklog(paths).get(candidate_id)
    if candidate is None:
        return None
    return generate_growth_report(paths, candidate, llm_helper=llm_helper)


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
        # [P4-5] 类别被静音的报告不进摘要打包，逻辑与 _maybe_dispatch_notification
        # 一致——静音是"完全不主动推送"，不是"降低频率"。
        window_reports = [r for r in window_reports if not _category_notification_muted(cfg, r.title)]

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
        - 先按 `notification_min_confidence` 过滤、再排除类别被静音
          （`category_notification_frequency` 配成 `"kanban_only"`）的
          报告；剩下的按 [P4-5] 优先级分数（置信度 × 类别历史采纳率加权，
          见 `_notification_priority_score`）取最高的一条；全部被过滤掉
          -> 不推送（"宁可不推，不为了凑数硬推"，方案第 4.2 节原文）。
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
    # [P4-5] 按类别历史采纳率算优先级分数，而不是单纯比置信度；同时把
    # 类别被静音（category_notification_frequency=="kanban_only"）的
    # 报告排除在候选之外，不管置信度多高都不推送。
    category_rates = _category_acceptance_rate(paths)

    scored: list[tuple[float, GrowthReport]] = []
    for r in reports:
        cand = candidates_by_id.get(r.candidate_id)
        conf = cand.confidence if cand is not None else 0.0
        if conf < min_conf:
            continue
        if _category_notification_muted(cfg, r.title):
            continue
        priority = _notification_priority_score(conf, category_rates.get(_category_of(r.title)))
        scored.append((priority, conf, r))
    if not scored:
        return None
    scored.sort(key=lambda t: -t[0])
    _best_priority, best_conf, best_report = scored[0]

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
    # [P4-4] report_quality_llm_enabled 独立于 llm_signal_augment_enabled：
    # 默认仍是零成本模板报告，只有显式打开这个开关才会在生成报告正文时
    # 用 llm_helper 换取更高信息密度（同样是 opt-in，不因为"恰好有" llm_helper
    # 就默认用上）。
    report_llm_helper = llm_helper if getattr(cfg, "report_quality_llm_enabled", False) else None
    reports = [generate_growth_report(paths, c, llm_helper=report_llm_helper) for c in top]

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


# ────────────────────────── P3（用户反馈追加）：诊断快照 ──────────────────────────
# 真实用户反馈："运行了一天，成长顾问里的数据都是 0"——排查下来往往不是
# bug，而是"关键词表没命中"/"证据数没达标"/"cron 没跑过"这类看不见的
# 中间状态：候选数=0 本身不区分"扫描过但没匹配到"和"压根没扫描过"。这个
# 函数把决定"为什么是 0"的关键中间量整理成一份可读快照，配合看板展示，
# 让用户自己就能判断卡在哪一步，不用非得来问。
def diagnostics_snapshot(paths, cfg, profile, memory_store) -> dict[str, Any]:
    """成长顾问的自检信息：当前配置快照、上一次信号扫描命中了哪些主题
    各多少条（只给计数，不回显记忆原文——诊断信息也要遵守"知情但克制"
    的边界）、扫描窗口内一共有多少条记忆可供扫描。纯只读聚合，不做任何
    写入，可以随时安全调用（哪怕从未跑过一次扫描）。
    """
    derived = dict(getattr(profile, "derived", {}) or {})
    focus_areas: dict[str, list[str]] = derived.get("growth_focus_areas") or {}
    last_scan_at = derived.get("growth_focus_areas_updated_at")

    entries = []
    if memory_store is not None:
        try:
            entries = memory_store.all_entries()
        except Exception:
            entries = []
    cutoff = time.time() - SIGNAL_SCAN_WINDOW_DAYS * 86400
    entries_in_window = sum(1 for e in entries if getattr(e, "created_at", 0) >= cutoff)

    # [P4-1] 关键词表按来源展示（内置/系统学到待确认/用户自定义），
    # 而不是只给一个不带来源信息的主题名列表。
    effective_keywords = _effective_topic_keywords(profile)
    topics_detail = [
        {
            "topic": topic,
            "keywords": info["keywords"],
            "source": info["source"],
            "confirmed_by_user": info["confirmed_by_user"],
            "consecutive_scan_hits": info.get("consecutive_scan_hits", 0),
            "auto_confirmed": info.get("auto_confirmed", False),
        }
        for topic, info in effective_keywords.items()
    ]

    # [P4-1] 看板"Agent 对你的了解"区块：只透出 LLM 生成的画像部分
    # （summary/tech_stack/habits），不包含 preferences（用户显式设置的
    # 偏好是另一回事，混在一起展示容易让用户误解）。
    derived_profile = dict(getattr(profile, "derived", {}) or {})
    user_profile_snapshot = {
        "summary": derived_profile.get("summary") or "",
        "tech_stack": list(derived_profile.get("tech_stack") or []),
        "habits": list(derived_profile.get("habits") or []),
        "updated_at": derived_profile.get("updated_at"),
    }

    return {
        "config": {
            "enabled": getattr(cfg, "enabled", True),
            "min_evidence_count": getattr(cfg, "min_evidence_count", None),
            "max_pending_candidates": getattr(cfg, "max_pending_candidates", None),
            "dismissed_cooldown_days": getattr(cfg, "dismissed_cooldown_days", None),
            "notification_frequency": getattr(cfg, "notification_frequency", None),
            "notification_min_confidence": getattr(cfg, "notification_min_confidence", None),
            "excluded_topics": list(getattr(cfg, "excluded_topics", []) or []),
            "llm_signal_augment_enabled": getattr(cfg, "llm_signal_augment_enabled", False),
        },
        "signal_scan": {
            "window_days": SIGNAL_SCAN_WINDOW_DAYS,
            "last_scan_at": last_scan_at,
            "topics_tracked": list(effective_keywords.keys()),
            "topics_detail": topics_detail,
            # 只给每个主题命中了多少条，不回显 entry_id/记忆原文
            "topic_hit_counts": {topic: len(ids) for topic, ids in focus_areas.items()},
        },
        "memory": {
            "total_entries": len(entries),
            "entries_in_scan_window": entries_in_window,
        },
        "user_profile": user_profile_snapshot,
        # [P4-3] 待回访候选数量，供看板在诊断区提示"有 N 个方向该回访了"，
        # 具体列表通过 GET /growth/followups 单独获取（避免每次
        # /growth/summary 都要多做一遍 accepted_at 过滤）。
        "pending_followups_count": len(pending_followups(paths, cfg)),
        # [P4-4] 待刷新报告数量，明细走 GET /growth/reports/refresh_candidates。
        "reports_needing_refresh_count": len(reports_needing_refresh(paths, cfg)),
        # [P4-5] 按类别的历史采纳率（供看板解释"为什么这条被优先推送了"），
        # 只包含有过至少一次 accept/dismiss 决策的类别。
        "category_acceptance_rate": _category_acceptance_rate(paths),
    }
