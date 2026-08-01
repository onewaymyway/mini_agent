"""evolution/self_model_snapshot.py — 能力自画像时间序列快照
（自诊断闭环深化 P3）。

设计背景见
next_doc/self_diagnosis_feedback_loop_deepening_plan.md §2 P3：
`perception/self_model.py::AgentSelfModelBuilder` 目前是即时计算、不落盘
的读取式接口——每次调用都从 `consolidation.build_capability_map()` 现算
一份 `capability_snapshot`，但从不保存"上一次是什么样"，导致没有办法回答
"这次改动之后，能力弱点清单是变短了还是变长了"这种最基本的趋势问题。

本模块只做两件事：
  1. 周期性（与 P1 `sys:improvement_backlog_merge` 同频，日频）把
     `capability_snapshot` 连同时间戳追加写入 `self_model_history.jsonl`，
     不改动 `AgentSelfModelBuilder` 本身的即时计算逻辑。
  2. 提供一个只读 diff 函数，比较任意两次快照之间"置信度 < 0.5 的弱项
     清单"的增减，供 P1 的 backlog 汇总或月度回顾（`monthly_trend_
     retrospective.py`）复用，而不是新建一个独立的回顾入口。

不做：不引入预测/趋势外推，只做历史快照存档与两两 diff；不据此自动触发
任何改进动作。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.evolution.cron_scheduler import CronJob, CronScheduler
    from mini_agent.storage.paths import AgentPaths

JOB_ID = "sys:self_model_snapshot"

# 弱项判定阈值，与 AgentSelfModel.render() 里的 weak 判定（confidence < 0.5）
# 保持一致，避免两处标准不同产生误导。
_WEAK_CONFIDENCE_THRESHOLD = 0.5

# 历史文件保留窗口：跟 wiki_utility_audit.py 的 usage_log 保留策略（90 天）
# 对齐，日频快照 90 天足够支撑月度回顾使用，不需要无限保留。
_HISTORY_RETENTION_SECONDS = 90 * 24 * 3600


@dataclass
class SnapshotRecord:
    at: float
    capability_snapshot: dict[str, float]

    def to_dict(self) -> dict:
        return {"at": self.at, "capability_snapshot": self.capability_snapshot}


@dataclass
class DomainDelta:
    domain: str
    old_confidence: Optional[float]
    new_confidence: Optional[float]
    delta: Optional[float]  # None 表示该领域只在其中一侧出现，无法算差值

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "old_confidence": self.old_confidence,
            "new_confidence": self.new_confidence,
            "delta": round(self.delta, 3) if self.delta is not None else None,
        }


@dataclass
class SnapshotDiff:
    old_at: Optional[float]
    new_at: float
    weak_domains_old: list[str] = field(default_factory=list)
    weak_domains_new: list[str] = field(default_factory=list)
    deltas: list[DomainDelta] = field(default_factory=list)

    @property
    def weak_count_change(self) -> Optional[int]:
        if self.old_at is None:
            return None
        return len(self.weak_domains_new) - len(self.weak_domains_old)

    def to_dict(self) -> dict:
        return {
            "old_at": self.old_at,
            "new_at": self.new_at,
            "weak_domains_old": self.weak_domains_old,
            "weak_domains_new": self.weak_domains_new,
            "weak_count_change": self.weak_count_change,
            "deltas": [d.to_dict() for d in self.deltas],
        }


@dataclass
class SnapshotRunSummary:
    snapshot: Optional[SnapshotRecord] = None
    diff: Optional[SnapshotDiff] = None
    errors: list[str] = field(default_factory=list)
    ran_at: float = field(default_factory=time.time)

    @property
    def ok(self) -> bool:
        return not self.errors


# ── 快照计算 ──────────────────────────────────────────────────────────────────

def _compute_capability_snapshot(paths: "AgentPaths") -> dict[str, float]:
    """跟 improvement_backlog_merge.py::_read_self_model_findings() 同样的
    现算方式（纯只读、无 LLM/网络调用，成本低）。"""
    from mini_agent.perception.self_model import AgentSelfModelBuilder

    model = AgentSelfModelBuilder().build(
        project_root=paths.project_root, use_capability_map=True,
    )
    return dict(getattr(model, "capability_snapshot", {}) or {})


def _weak_domains(snapshot: dict[str, float]) -> list[str]:
    return sorted(d for d, c in snapshot.items() if c < _WEAK_CONFIDENCE_THRESHOLD)


# ── 历史存储 ──────────────────────────────────────────────────────────────────

def _history_path(paths: "AgentPaths"):
    return getattr(paths, "self_model_history_path", None) or (
        paths.workdir_dir / "self_model_history.jsonl"
    )


def _append_snapshot(paths: "AgentPaths", record: SnapshotRecord) -> None:
    p = _history_path(paths)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(record.to_dict(), ensure_ascii=False))
        f.write("\n")


def _trim_history(paths: "AgentPaths", now: float) -> None:
    p = _history_path(paths)
    if not p.exists():
        return
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except Exception:
        return
    cutoff = now - _HISTORY_RETENTION_SECONDS
    kept: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            kept.append(line)  # 损坏行原样保留，不中断整体修剪
            continue
        if float(d.get("at", 0.0) or 0.0) >= cutoff:
            kept.append(line)
    try:
        p.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    except Exception:
        pass


def load_snapshot_history(paths: "AgentPaths") -> list[SnapshotRecord]:
    """只读加载全部历史快照（按时间升序），供月度回顾等下游模块复用。"""
    p = _history_path(paths)
    if not p.exists():
        return []
    records: list[SnapshotRecord] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            records.append(SnapshotRecord(
                at=float(d.get("at", 0.0) or 0.0),
                capability_snapshot=d.get("capability_snapshot", {}) or {},
            ))
    except Exception:
        return []
    records.sort(key=lambda r: r.at)
    return records


def find_snapshot_near(records: list[SnapshotRecord], target_at: float) -> Optional[SnapshotRecord]:
    """在历史记录里找时间戳最接近 target_at 且不晚于 target_at 的快照
    （"回看 N 天前大致是什么样"，不要求精确命中）。找不到早于 target_at 的
    记录时返回 None（说明历史还不够长，无法做这次 diff，不强行拿更晚的
    记录冒充"N 天前"）。"""
    candidates = [r for r in records if r.at <= target_at]
    if not candidates:
        return None
    return max(candidates, key=lambda r: r.at)


def diff_snapshots(old: Optional[SnapshotRecord], new: SnapshotRecord) -> SnapshotDiff:
    old_snapshot = old.capability_snapshot if old else {}
    new_snapshot = new.capability_snapshot

    domains = sorted(set(old_snapshot) | set(new_snapshot))
    deltas: list[DomainDelta] = []
    for d in domains:
        old_c = old_snapshot.get(d)
        new_c = new_snapshot.get(d)
        delta = (new_c - old_c) if (old_c is not None and new_c is not None) else None
        deltas.append(DomainDelta(domain=d, old_confidence=old_c, new_confidence=new_c, delta=delta))
    deltas.sort(key=lambda d: (d.delta is None, d.delta if d.delta is not None else 0.0))

    return SnapshotDiff(
        old_at=(old.at if old else None),
        new_at=new.at,
        weak_domains_old=(_weak_domains(old_snapshot) if old else []),
        weak_domains_new=_weak_domains(new_snapshot),
        deltas=deltas,
    )


# ── 主入口 ────────────────────────────────────────────────────────────────────

def run_self_model_snapshot_once(paths: "AgentPaths", *, lookback_days: float = 7.0) -> SnapshotRunSummary:
    summary = SnapshotRunSummary()
    now = time.time()

    try:
        snapshot_dict = _compute_capability_snapshot(paths)
    except Exception as exc:
        summary.errors.append(f"compute_snapshot_failed: {exc}")
        return summary

    record = SnapshotRecord(at=now, capability_snapshot=snapshot_dict)
    summary.snapshot = record

    try:
        history = load_snapshot_history(paths)
        baseline = find_snapshot_near(history, now - lookback_days * 86400.0)
        summary.diff = diff_snapshots(baseline, record)
    except Exception as exc:
        summary.errors.append(f"diff_failed: {exc}")

    try:
        _append_snapshot(paths, record)
        _trim_history(paths, now)
    except Exception as exc:
        summary.errors.append(f"persist_failed: {exc}")

    if summary.diff is not None and summary.diff.old_at is not None:
        try:
            from mini_agent.evolution.self_maintenance import append_digest_record
            change = summary.diff.weak_count_change
            append_digest_record(paths, {
                "type": "self_model_snapshot_diff",
                "summary": (
                    f"能力自画像回看（对比 ~{lookback_days:.0f} 天前）："
                    f"弱项数量 {'增加' if (change or 0) > 0 else '减少' if (change or 0) < 0 else '不变'}"
                    f"（{len(summary.diff.weak_domains_old)} → {len(summary.diff.weak_domains_new)}）"
                ),
                **summary.diff.to_dict(),
            }, initiator="self_model_snapshot")
        except Exception as exc:
            summary.errors.append(f"digest_write_failed: {exc}")

    return summary


def ensure_self_model_snapshot_job(
    paths: "AgentPaths", cron_scheduler: "CronScheduler",
) -> bool:
    """daemon 启动时调用：缺失才补注册 `sys:self_model_snapshot`（零 LLM
    成本，本地回调 handler，与 P1 `sys:improvement_backlog_merge` 同频）。"""
    existing_ids = {j.id for j in cron_scheduler.list_jobs()}
    newly_added = JOB_ID not in existing_ids
    cron_scheduler.ensure_job(
        job_id=JOB_ID,
        name="能力自画像快照",
        schedule="interval:86400",
        description=(
            "将 capability_snapshot 按时间戳存档，并与约 7 天前的快照做 diff，"
            "回看能力弱项清单是否在收敛，零 LLM 成本。"
        ),
        tags=["maintenance", "self_awareness"],
    )

    def _handler(job: "CronJob") -> bool:
        result = run_self_model_snapshot_once(paths)
        return result.ok

    cron_scheduler.register_local_handler(JOB_ID, _handler)
    return newly_added


__all__ = [
    "JOB_ID",
    "SnapshotRecord",
    "DomainDelta",
    "SnapshotDiff",
    "SnapshotRunSummary",
    "run_self_model_snapshot_once",
    "ensure_self_model_snapshot_job",
    "load_snapshot_history",
    "find_snapshot_near",
    "diff_snapshots",
]
