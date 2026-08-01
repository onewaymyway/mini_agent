"""evolution/wiki_utility_audit.py — wiki 页面利用率回溯（P2）。

设计背景见
next_doc/external_knowledge_feedback_loop_improvement_plan.md §3 P2：
`wiki/gap_scanner.py` 只从"内容薄不薄"（浅层实体/孤儿页面/陈旧专题页）判断
一个页面该不该被关注，完全不知道"这个页面有没有真的被用上"——一个内容详实
但从未被检索命中过的页面，和一个刚写完就被频繁引用的页面，在现有机制下待遇
完全一样。

本模块消费 `wiki/search.py::wiki_shelf_search()` 新增的埋点
（`AgentPaths.wiki_usage_log_path`，每次检索返回前追加一条
`{ts, query, page_ids, grounded_page_ids, stage_reached}` 记录），周期性
聚合成"近期利用率"统计：

  - 每个 page_id 在回溯窗口（默认 30 天）内：
      hit_count       —— 出现在候选列表（page_ids）里的次数
      grounded_count  —— 被 LLM 精排标注为"回答主要依据"（grounded_page_ids）
                          的次数，比单纯候选命中信号更强
      last_used_at    —— 最近一次命中的时间戳
  - 落盘到 `AgentPaths.wiki_dir / "usage_stats.json"`，供后续
    `wiki/gap_scanner.py`/`wiki/decommission.py` 消费（本阶段只产出统计，
    不改动 gap_scan/decommission 的判断逻辑本身——把"统计"和"策略"分成两个
    可以独立验证的阶段，见计划文档 P2 小节的阶段划分说明，避免在还没看到
    真实利用率分布之前就仓促决定权重怎么定）。

同时顺带做 usage_log.jsonl 的低频修剪（超过 `LOG_RETENTION_SECONDS` 的记录
丢弃，同一个 job 里做，不新开一个 job，风格对齐 `sys:digest_trim`）——这是
一个持续追加写的日志文件，不加修剪会无限增长。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.evolution.cron_scheduler import CronJob, CronScheduler
    from mini_agent.storage.paths import AgentPaths

JOB_ID = "sys:wiki_utility_audit"

# 统计回溯窗口：只统计最近 30 天的命中，让统计结果反映"近期"利用率而不是
# 历史总量（一个页面半年前很热门、最近完全没人用，不该被算作"仍然有用"）。
AUDIT_WINDOW_SECONDS = 30 * 24 * 3600

# usage_log.jsonl 保留窗口：比统计回溯窗口长一档（90 天），保留一些历史余量
# 供人工回看趋势，但也不是无限保留。
LOG_RETENTION_SECONDS = 90 * 24 * 3600


@dataclass
class PageUsageStat:
    page_id: str
    hit_count: int = 0
    grounded_count: int = 0
    last_used_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "page_id": self.page_id,
            "hit_count": self.hit_count,
            "grounded_count": self.grounded_count,
            "last_used_at": self.last_used_at,
        }


@dataclass
class AuditSummary:
    log_lines_scanned: int = 0
    log_lines_kept: int = 0
    pages_with_usage: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _usage_stats_path(paths: "AgentPaths"):
    return paths.wiki_dir / "usage_stats.json"


def run_wiki_utility_audit_once(paths: "AgentPaths") -> AuditSummary:
    """读一次 `usage_log.jsonl`，聚合出窗口内每个 page_id 的利用率统计，
    写入 `wiki/usage_stats.json`；同时重写 `usage_log.jsonl`，丢弃超过
    `LOG_RETENTION_SECONDS` 的旧记录。日志文件不存在时视为"还没有任何检索
    发生过"，直接返回空摘要，不报错（跟 `sys:digest_trim` 对空日志的处理
    风格一致）。"""
    summary = AuditSummary()
    log_path = paths.wiki_usage_log_path
    if not log_path.exists():
        return summary

    now = time.time()
    try:
        raw_lines = log_path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        summary.errors.append(f"read_failed: {exc}")
        return summary

    stats: dict[str, PageUsageStat] = {}
    kept_lines: list[str] = []
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue

        summary.log_lines_scanned += 1
        ts = float(rec.get("ts", 0.0) or 0.0)
        if now - ts > LOG_RETENTION_SECONDS:
            continue  # 超出保留窗口，直接从 usage_log.jsonl 里丢弃
        kept_lines.append(line)
        summary.log_lines_kept += 1

        if now - ts > AUDIT_WINDOW_SECONDS:
            continue  # 保留在日志里，但不计入本次统计窗口

        grounded_ids = set(rec.get("grounded_page_ids") or [])
        for pid in rec.get("page_ids") or []:
            stat = stats.setdefault(pid, PageUsageStat(page_id=pid))
            stat.hit_count += 1
            stat.last_used_at = max(stat.last_used_at, ts)
            if pid in grounded_ids:
                stat.grounded_count += 1

    summary.pages_with_usage = len(stats)

    try:
        log_path.write_text(
            ("\n".join(kept_lines) + "\n") if kept_lines else "", encoding="utf-8"
        )
    except Exception as exc:
        summary.errors.append(f"log_trim_failed: {exc}")

    try:
        out = {
            "generated_at": now,
            "window_seconds": AUDIT_WINDOW_SECONDS,
            "pages": {pid: s.to_dict() for pid, s in stats.items()},
        }
        _usage_stats_path(paths).write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        summary.errors.append(f"stats_write_failed: {exc}")

    return summary


def load_wiki_usage_stats(paths: "AgentPaths") -> dict[str, dict]:
    """供后续 `wiki/gap_scanner.py`/`wiki/decommission.py` 消费：只读加载
    最近一次审计产出的 `page_id -> stat dict` 映射。文件不存在/解析失败都
    返回空 dict（消费方应把"没有统计数据"和"统计为 0"同等对待，不阻塞
    既有逻辑——这也是本阶段刻意不改动 gap_scan/decommission 判断逻辑的
    原因之一：调用方目前还没有人读取这份数据）。"""
    p = _usage_stats_path(paths)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("pages", {})
    except Exception:
        return {}


def ensure_wiki_utility_audit_job(
    paths: "AgentPaths", cron_scheduler: "CronScheduler",
) -> bool:
    """daemon 启动时调用：缺失才补注册 `sys:wiki_utility_audit`（零 LLM
    成本，本地回调 handler，跟 `candidate_queue_triage.py` 同构）。"""
    existing_ids = {j.id for j in cron_scheduler.list_jobs()}
    newly_added = JOB_ID not in existing_ids
    cron_scheduler.ensure_job(
        job_id=JOB_ID,
        name="wiki 利用率审计",
        schedule="interval:604800",
        description=(
            "聚合 wiki/usage_log.jsonl 近 30 天检索命中记录为每页利用率统计"
            "（usage_stats.json），并修剪超过 90 天的日志记录，零 LLM 成本。"
        ),
        tags=["maintenance", "wiki"],
    )

    def _handler(job: "CronJob") -> bool:
        result = run_wiki_utility_audit_once(paths)
        return result.ok

    cron_scheduler.register_local_handler(JOB_ID, _handler)
    return newly_added


__all__ = [
    "JOB_ID",
    "AUDIT_WINDOW_SECONDS",
    "LOG_RETENTION_SECONDS",
    "PageUsageStat",
    "AuditSummary",
    "run_wiki_utility_audit_once",
    "load_wiki_usage_stats",
    "ensure_wiki_utility_audit_job",
]
