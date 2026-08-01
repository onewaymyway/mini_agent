"""evolution/candidate_queue_triage.py — 人工候选队列过期巡检（P1）。

设计背景见
next_doc/external_knowledge_feedback_loop_improvement_plan.md §3 P1：
`external_input/novelty_judge.py` 产出的 `notification/novelty_candidates.jsonl`
是唯一一条"需要人工 confirm/dismiss"的候选队列（其余候选队列——
`tech_radar_search.py`/`evolution/external_trend_capability_link.py`——都在
各自模块内部按 `STALE_CANDIDATE_TTL_SECONDS` 自我过期，不需要外部巡检）。
这个人工队列此前只有总量止损（`novelty_judge.py::MAX_RAW_CANDIDATES_TOTAL`，
且只作用于 Stage①原始队列），没有任何时间维度的过期机制：`status="pending"`
的候选会无限期挂着，旧的低价值候选一直占着人工审核视野。

本模块只做一件事：扫描 `notification_novelty_candidates.jsonl`，把超过
`STALE_PENDING_TTL_SECONDS`（默认 30 天）仍是 `pending` 状态的候选，状态改写
为 `"expired"`——不是 `"dismissed"`，刻意跟 `novelty_judge.py::dismiss_novelty_candidate()`
（人工主动忽略）区分开，保留"系统因超时自动降级" vs "人工主动忽略"两种不同
语义，供后续做阈值/召回校准时区分统计。不删除记录，只改写状态，保留可追溯性。

跟 `novelty_judge.py::_rewrite_and_find()` 同构的"读整个文件 → 改 → 整体重写"
模式，复用同一把 `ExclusiveFileLock`，避免跟 `dismiss_novelty_candidate()`/
`confirm_novelty_candidate()`（用户手动点击触发）并发写入冲突。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from mini_agent.external_input.filelock import ExclusiveFileLock

if TYPE_CHECKING:
    from mini_agent.evolution.cron_scheduler import CronJob, CronScheduler
    from mini_agent.storage.paths import AgentPaths

JOB_ID = "sys:candidate_queue_triage"

# 超过这个时长仍是 pending 状态的候选，视为"长期无人处理"，自动过期。
# 跟 sys:decision_profile_update/sys:external_trend_capability_link 的
# 7 天节拍不同——这里面对的是人工审核队列，给足够长的窗口（30 天）避免
# 误伤用户还没来得及看的候选。
STALE_PENDING_TTL_SECONDS = 30 * 24 * 3600


@dataclass
class TriageSummary:
    """一次巡检的执行摘要，供本地回调 handler 判断成功/失败、供日志使用。"""

    scanned: int = 0
    expired: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def run_candidate_queue_triage_once(paths: "AgentPaths") -> TriageSummary:
    """扫描一次 `notification_novelty_candidates.jsonl`，过期超龄 pending 候选。

    读取失败（文件不存在/单行解析失败）都按"尽量不阻塞整批"处理：文件不存在
    直接返回空摘要；单行解析失败的记录原样保留、不计入 scanned，不中断整批。
    """
    summary = TriageSummary()
    p = paths.notification_novelty_candidates
    if not p.exists():
        return summary

    now = time.time()
    with ExclusiveFileLock(p):
        try:
            raw_lines = p.read_text(encoding="utf-8").splitlines()
        except Exception as exc:
            summary.errors.append(f"read_failed: {exc}")
            return summary

        new_lines: list[str] = []
        changed = False
        for line in raw_lines:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                # 单行损坏：原样保留，不计入 scanned，不中断整批。
                new_lines.append(line)
                continue

            summary.scanned += 1
            if d.get("status") == "pending":
                created_at = float(d.get("created_at", 0.0) or 0.0)
                if created_at > 0 and now - created_at > STALE_PENDING_TTL_SECONDS:
                    d["status"] = "expired"
                    d["expired_at"] = now
                    summary.expired += 1
                    changed = True
            new_lines.append(json.dumps(d, ensure_ascii=False))

        if changed:
            try:
                p.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            except Exception as exc:
                summary.errors.append(f"write_failed: {exc}")

    return summary


def ensure_candidate_queue_triage_job(
    paths: "AgentPaths", cron_scheduler: "CronScheduler",
) -> bool:
    """daemon 启动时调用：缺失才补注册 `sys:candidate_queue_triage`（零 LLM
    成本，本地回调 handler，跟 `report_tiers.py::ensure_report_tier_jobs`/
    `tech_radar_search.py::ensure_tech_radar_search_job` 同构）。

    返回是否是本次新注册（True=新建，False=已存在直接复用，跟既有 `ensure_*`
    系列函数保持一致的返回语义，供调用方日志/自检使用）。
    """
    existing_ids = {j.id for j in cron_scheduler.list_jobs()}
    newly_added = JOB_ID not in existing_ids
    cron_scheduler.ensure_job(
        job_id=JOB_ID,
        name="候选队列过期巡检",
        schedule="interval:86400",
        description=(
            "扫描 notification/novelty_candidates.jsonl，把超过 30 天仍未处理的"
            " pending 候选标记为 expired，零 LLM 成本。"
        ),
        tags=["maintenance", "notification"],
    )

    def _handler(job: "CronJob") -> bool:
        result = run_candidate_queue_triage_once(paths)
        return result.ok

    cron_scheduler.register_local_handler(JOB_ID, _handler)
    return newly_added


__all__ = [
    "JOB_ID",
    "STALE_PENDING_TTL_SECONDS",
    "TriageSummary",
    "run_candidate_queue_triage_once",
    "ensure_candidate_queue_triage_job",
]
