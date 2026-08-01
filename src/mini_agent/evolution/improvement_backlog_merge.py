"""evolution/improvement_backlog_merge.py — 改进信号聚合器（自诊断闭环深化 P1）。

设计背景见
next_doc/self_diagnosis_feedback_loop_deepening_plan.md §2 P1：
`self_maintenance.py`（工具/skill/lesson 健康）、`wiki/gap_scanner.py`（知识
缺口）、`wiki/decommission.py`（wiki 退役评估）、`perception/self_model.py`
（能力弱点）四路信号各自独立产出报告，用户需要在四个地方来回看、自己排
优先级。本模块只做"读取四份已有报告 → 按统一 schema 归一化 → 规则打分排序
→ 写汇总"，不修改任何一路信号源本身的判断逻辑，不引入 LLM。

排序打分（纯规则，具体权重系数留待实际运行数据积累后再精调，见计划文档
§4 待讨论问题 1）：
  - 新鲜度：距上次该信号更新的时间越短，分数越高。
  - 跨信号源重复：同一 subject（工具名/skill名/page_id/领域名）在多路信号
    里都出现，视为更值得优先处理，每多命中一路信号源加一次跨源加分。
  - 距上次被处理的时间：复用同一份 backlog 状态文件里记录的
    `last_seen_at`，超过一定时长仍未变化（说明既没被处理也没被信号源自己
    移除）的条目适度提权，避免被新条目持续挤到后面。

不做：不自动执行任何改进动作，只产出排序列表；不改变四路信号源各自的
存储格式或产出逻辑，本模块只读它们已经持久化的报告文件。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from mini_agent.evolution.cron_scheduler import CronJob, CronScheduler
    from mini_agent.storage.paths import AgentPaths

JOB_ID = "sys:improvement_backlog_merge"

# 跨信号源重复命中时，每多一路信号源的加分。
_CROSS_SOURCE_BONUS = 2.0
# 新鲜度打分：距最近一次更新的天数越小分数越高，超过这个天数封顶不再加分。
_FRESHNESS_WINDOW_DAYS = 14.0
# 长期滞留（既没被处理也没被信号源移除）提权阈值。
_STALE_BACKLOG_DAYS = 21.0
_STALE_BACKLOG_BONUS = 1.0

_ACTIVITY_DIGEST_SCAN_LIMIT = 200  # 只扫最近 N 条 activity_digest.jsonl 记录


@dataclass
class BacklogItem:
    """归一化后的一条改进候选。"""

    subject: str  # 统一后的主体标识，如 "tool:xxx" / "skill:xxx" / "page:xxx" / "capability:xxx"
    source: str  # 信号来源: "self_maintenance" | "gap_scanner" | "decommission" | "self_model"
    kind: str  # 该信号源内部的细分类型，如 "stale_tool"/"shallow_entity"
    summary: str
    detected_at: float
    score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "subject": self.subject,
            "source": self.source,
            "kind": self.kind,
            "summary": self.summary,
            "detected_at": self.detected_at,
            "score": round(self.score, 3),
        }


@dataclass
class BacklogMergeSummary:
    items: list[BacklogItem] = field(default_factory=list)
    sources_read: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    ran_at: float = field(default_factory=time.time)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "ran_at": self.ran_at,
            "sources_read": self.sources_read,
            "items": [i.to_dict() for i in self.items],
        }


# ── 各信号源读取（只读，不触发信号源本身重新计算）─────────────────────────────

def _read_self_maintenance_findings(paths: "AgentPaths") -> list[BacklogItem]:
    """从 activity_digest.jsonl 里最近一条 type="health_report" 记录还原候选。

    不重新跑 SelfMaintenanceModule.health_check()（那需要 skill_loader/
    memory_backend 等运行时对象，本模块作为独立的低频巡检 job 不持有这些
    对象）——只读它已经落盘的最近一次结果，与 wiki_utility_audit 读取
    usage_stats.json 而不是重新扫描全部 usage_log 是同一个"只读既有产出"
    的取舍。
    """
    items: list[BacklogItem] = []
    p = paths.workdir_dir / "activity_digest.jsonl"
    if not p.exists():
        return items
    try:
        lines = p.read_text(encoding="utf-8").splitlines()[-_ACTIVITY_DIGEST_SCAN_LIMIT:]
    except Exception:
        return items

    latest: Optional[dict] = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("type") == "health_report":
            latest = d  # 保留最后一条（时间上最近）

    if not latest:
        return items
    detected_at = float(latest.get("at", 0.0) or 0.0)
    for f in latest.get("stale_tools", []) or []:
        items.append(BacklogItem(
            subject=f"tool:{f.get('tool_name', '')}",
            source="self_maintenance",
            kind="stale_tool",
            summary=(
                f"工具 {f.get('tool_name', '')} 近期失败率 "
                f"{f.get('failure_rate', 0):.0%}"
            ),
            detected_at=detected_at,
        ))
    for f in latest.get("stale_skills", []) or []:
        items.append(BacklogItem(
            subject=f"skill:{f.get('skill_name', '')}",
            source="self_maintenance",
            kind="stale_skill",
            summary=(
                f"Skill {f.get('skill_name', '')} 已 "
                f"{f.get('last_used_days_ago', 0):.0f} 天未使用"
            ),
            detected_at=detected_at,
        ))
    for f in latest.get("conflicting_lessons", []) or []:
        items.append(BacklogItem(
            subject=f"lesson_group:{f.get('group_key', '')}",
            source="self_maintenance",
            kind="conflicting_lesson",
            summary=f"经验组 {f.get('group_key', '')} 存在可能矛盾的建议",
            detected_at=detected_at,
        ))
    # P4：skill 结果有效性审计——只把 low_effectiveness 计入待处理候选，
    # effective/inconclusive 是正面或无结论信息，不构成"需要处理的问题"。
    for f in latest.get("skill_effectiveness", []) or []:
        if f.get("verdict") != "low_effectiveness":
            continue
        items.append(BacklogItem(
            subject=f"skill:{f.get('skill_name', '')}",
            source="self_maintenance",
            kind="low_effectiveness_skill",
            summary=(
                f"Skill {f.get('skill_name', '')} 激活时任务失败率 "
                f"{f.get('active_failure_rate', 0):.0%}，"
                f"高于未激活时 {f.get('baseline_failure_rate', 0):.0%}"
            ),
            detected_at=detected_at,
        ))
    return items


def _read_gap_scanner_findings(paths: "AgentPaths") -> list[BacklogItem]:
    """直接调用 scan_gaps()——它本身是无副作用的只读扫描（不落盘缓存文件），
    跟 self_maintenance/decommission 不同，没有"最近一次结果"文件可读，
    只能现算，但现算成本低（纯文件解析，不涉及 LLM/网络）。"""
    items: list[BacklogItem] = []
    try:
        from mini_agent.wiki.gap_scanner import scan_gaps
    except Exception:
        return items
    try:
        gaps = scan_gaps(paths, max_results=20)
    except Exception:
        return items
    now = time.time()
    for g in gaps:
        items.append(BacklogItem(
            subject=f"page:{g.page_id}",
            source="gap_scanner",
            kind=g.gap_kind,
            summary=g.suggested_action or g.detail or g.gap_kind,
            detected_at=now,
        ))
    return items


def _read_decommission_findings(paths: "AgentPaths") -> list[BacklogItem]:
    items: list[BacklogItem] = []
    try:
        from mini_agent.wiki.decommission import load_last_report
    except Exception:
        return items
    try:
        report = load_last_report(paths)
    except Exception:
        report = None
    if not report:
        return items
    detected_at = float(report.get("ran_at", 0.0) or report.get("at", 0.0) or 0.0)
    for reason in report.get("blocking_reasons", []) or []:
        items.append(BacklogItem(
            subject="wiki_index:decommission",
            source="decommission",
            kind="blocking_reason",
            summary=str(reason),
            detected_at=detected_at,
        ))
    return items


def _read_self_model_findings(paths: "AgentPaths") -> list[BacklogItem]:
    """读取能力自画像的弱项（capability_snapshot 里置信度 < 0.5 的领域）。

    与 gap_scanner 一样是现算而非读缓存——AgentSelfModelBuilder 从
    capability_map 只读构建，成本低。
    """
    items: list[BacklogItem] = []
    try:
        from mini_agent.perception.self_model import AgentSelfModelBuilder
    except Exception:
        return items
    try:
        model = AgentSelfModelBuilder().build(
            project_root=paths.project_root, use_capability_map=True,
        )
    except Exception:
        return items
    snapshot = getattr(model, "capability_snapshot", {}) or {}
    now = time.time()
    for domain, confidence in snapshot.items():
        if confidence < 0.5:
            items.append(BacklogItem(
                subject=f"capability:{domain}",
                source="self_model",
                kind="weak_capability",
                summary=f"领域 {domain} 置信度仅 {confidence:.0%}",
                detected_at=now,
            ))
    return items


# ── 打分与去重合并 ────────────────────────────────────────────────────────────

def _merge_and_score(
    all_items: list[BacklogItem], prev_state: dict,
) -> list[BacklogItem]:
    """按 subject 合并多路信号，计算排序分数。"""
    grouped: dict[str, list[BacklogItem]] = {}
    for item in all_items:
        grouped.setdefault(item.subject, []).append(item)

    now = time.time()
    prev_items = {e.get("subject"): e for e in prev_state.get("items", []) if e.get("subject")}

    merged: list[BacklogItem] = []
    for subject, group in grouped.items():
        # 取组内最新的一条作为代表条目，保留其它来源的存在作为跨源信号。
        rep = max(group, key=lambda i: i.detected_at)
        sources_hit = {i.source for i in group}

        freshness_days = max(0.0, (now - rep.detected_at) / 86400.0)
        freshness_score = max(0.0, 1.0 - min(freshness_days, _FRESHNESS_WINDOW_DAYS) / _FRESHNESS_WINDOW_DAYS) * 3.0
        cross_source_score = (len(sources_hit) - 1) * _CROSS_SOURCE_BONUS

        stale_bonus = 0.0
        prev = prev_items.get(subject)
        first_seen_at = now
        if prev:
            first_seen_at = float(prev.get("first_seen_at", now) or now)
            age_days = (now - first_seen_at) / 86400.0
            if age_days >= _STALE_BACKLOG_DAYS:
                stale_bonus = _STALE_BACKLOG_BONUS

        rep.score = freshness_score + cross_source_score + stale_bonus
        merged.append(rep)

    merged.sort(key=lambda i: -i.score)
    return merged


def _state_path(paths: "AgentPaths"):
    return getattr(paths, "improvement_backlog_path", None) or (
        paths.workdir_dir / "improvement_backlog.json"
    )


def _load_prev_state(paths: "AgentPaths") -> dict:
    p = _state_path(paths)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(paths: "AgentPaths", items: list[BacklogItem]) -> None:
    now = time.time()
    prev = _load_prev_state(paths)
    prev_items = {e.get("subject"): e for e in prev.get("items", []) if e.get("subject")}

    out_items = []
    for item in items:
        d = item.to_dict()
        prior = prev_items.get(item.subject)
        d["first_seen_at"] = float(prior.get("first_seen_at", now)) if prior else now
        out_items.append(d)

    p = _state_path(paths)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"ran_at": now, "items": out_items}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def load_improvement_backlog(paths: "AgentPaths") -> list[dict]:
    """供晨报/看板只读消费：返回上一次聚合结果（按分数已排序）。"""
    state = _load_prev_state(paths)
    return state.get("items", [])


# ── 主入口 ────────────────────────────────────────────────────────────────────

def run_improvement_backlog_merge_once(paths: "AgentPaths") -> BacklogMergeSummary:
    summary = BacklogMergeSummary()
    all_items: list[BacklogItem] = []

    readers = [
        ("self_maintenance", _read_self_maintenance_findings),
        ("gap_scanner", _read_gap_scanner_findings),
        ("decommission", _read_decommission_findings),
        ("self_model", _read_self_model_findings),
    ]
    for name, reader in readers:
        try:
            found = reader(paths)
            all_items.extend(found)
            summary.sources_read.append(name)
        except Exception as exc:
            summary.errors.append(f"{name}_failed: {exc}")

    prev_state = _load_prev_state(paths)
    merged = _merge_and_score(all_items, prev_state)
    summary.items = merged

    try:
        _save_state(paths, merged)
    except Exception as exc:
        summary.errors.append(f"save_failed: {exc}")

    if merged:
        try:
            from mini_agent.evolution.self_maintenance import append_digest_record
            top = merged[:5]
            append_digest_record(paths, {
                "type": "improvement_backlog",
                "summary": f"改进信号聚合：共 {len(merged)} 项候选，Top {len(top)} 见 items",
                "items": [i.to_dict() for i in top],
            }, initiator="improvement_backlog_merge")
        except Exception as exc:
            summary.errors.append(f"digest_write_failed: {exc}")

    return summary


def ensure_improvement_backlog_merge_job(
    paths: "AgentPaths", cron_scheduler: "CronScheduler",
) -> bool:
    """daemon 启动时调用：缺失才补注册 `sys:improvement_backlog_merge`
    （零 LLM 成本，本地回调 handler，跟 candidate_queue_triage.py /
    wiki_utility_audit.py 同构）。"""
    existing_ids = {j.id for j in cron_scheduler.list_jobs()}
    newly_added = JOB_ID not in existing_ids
    cron_scheduler.ensure_job(
        job_id=JOB_ID,
        name="改进信号聚合",
        schedule="interval:86400",
        description=(
            "汇总 self_maintenance/gap_scanner/decommission/self_model 四路"
            "信号为排序过的改进候选清单，零 LLM 成本。"
        ),
        tags=["maintenance", "self_awareness"],
    )

    def _handler(job: "CronJob") -> bool:
        result = run_improvement_backlog_merge_once(paths)
        return result.ok

    cron_scheduler.register_local_handler(JOB_ID, _handler)
    return newly_added


__all__ = [
    "JOB_ID",
    "BacklogItem",
    "BacklogMergeSummary",
    "run_improvement_backlog_merge_once",
    "ensure_improvement_backlog_merge_job",
    "load_improvement_backlog",
]
