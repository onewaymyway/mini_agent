"""external_input/report_tiers.py — 分级汇报 Report Tiers（P3）。

设计背景见 next_doc/watchlist_notification_goal_design.md §3.2/§4.3。

职责：
1. 加载 `.agent/notification/report_tiers.yaml`（任意 N 个 tier）。
2. 为每个 tier 在 CronScheduler 里补注册一个 `sys:watchlist_report_<id>`
   job（缺失才补，见 §8 开放项 2），并注册一个**本地回调 handler**——
   触发时直接在本进程内读 pending_hits.jsonl、生成摘要、调用
   NotificationDispatcher，**不经过 InputQueue/LLM turn**（零 LLM 成本，
   见 §7）。
3. 消费 `pending_hits.jsonl` 里 `tier == 自己` 且 `consumed == false`
   的记录，按 watchlist_id 分组生成 Markdown 摘要，通过
   NotificationDispatcher 发送，发送后（对齐 §3.4："发送成功后整体
   重写标记 consumed: true"）标记为已消费。

并发与健壮性要点（吸收 §9.1 #1 / §9.3 #9 / §9.2 #7）：
- 读取 + 改 + 整体重写 pending_hits.jsonl 全程持有跟 WatchlistMatcher
  追加写共享的同一把 `ExclusiveFileLock`，避免并发写入被覆盖丢失。
- 每个 watchlist_id 分组在摘要里最多列 `MAX_ITEMS_PER_GROUP` 条，超出
  部分显示"及其余 N 条"，避免单条通知本身过长。
- 高频 tier（interval <= HIGH_FREQ_THRESHOLD_SECONDS）连续空转达到
  `EMPTY_RUN_THROTTLE_AFTER` 次后，退化到 `EMPTY_RUN_THROTTLE_INTERVAL`
  秒才真正读一次文件，一旦有新命中立即恢复原有频率——纯粹是省一点空转
  的文件 IO，不是消息层面的丢弃。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from mini_agent.external_input.filelock import ExclusiveFileLock

if TYPE_CHECKING:
    from mini_agent.evolution.cron_scheduler import CronJob, CronScheduler
    from mini_agent.storage.paths import AgentPaths

try:
    import yaml as _yaml  # type: ignore
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

JOB_ID_PREFIX = "sys:watchlist_report_"

# 内置默认四档（§3.2），仅在 report_tiers.yaml 不存在时用作兜底样例，
# 不强制写入用户目录——是否落地这份默认配置由部署脚本/文档指引决定。
DEFAULT_TIERS: list[dict] = [
    {"id": "minute_1", "schedule": "interval:60", "notify_channels": ["kanban"]},
    {"id": "minute_30", "schedule": "interval:1800", "notify_channels": ["kanban"]},
    {"id": "hourly", "schedule": "interval:3600", "notify_channels": ["kanban"]},
    {"id": "daily", "schedule": "cron:0 22 * * *", "notify_channels": ["kanban", "email"]},
]

# §9.3 #9：单个 watchlist_id 分组在一次摘要里最多列出的条数。
MAX_ITEMS_PER_GROUP = 20

# §9.2 #7：高频 tier 的空转节流阈值（仅对 interval 调度生效，cron 调度
# 本身频率通常已经较低，不做节流）。
HIGH_FREQ_THRESHOLD_SECONDS = 300
EMPTY_RUN_THROTTLE_AFTER = 5
EMPTY_RUN_THROTTLE_INTERVAL = 300  # 连续空转达到阈值后，退化到 5 分钟才读一次


@dataclass
class ReportTier:
    id: str
    schedule: str
    notify_channels: list[str] = field(default_factory=lambda: ["kanban"])

    @staticmethod
    def from_dict(d: dict) -> "ReportTier":
        tier_id = str(d.get("id", "")).strip()
        if not tier_id:
            raise ValueError(f"report_tiers.yaml 条目缺少 id 字段: {d!r}")
        schedule = str(d.get("schedule", "")).strip()
        if not schedule:
            raise ValueError(f"report_tiers.yaml 条目 {tier_id!r} 缺少 schedule 字段")
        channels = [str(c) for c in (d.get("notify_channels") or ["kanban"])]
        return ReportTier(id=tier_id, schedule=schedule, notify_channels=channels or ["kanban"])

    @property
    def job_id(self) -> str:
        return f"{JOB_ID_PREFIX}{self.id}"


class ReportTiersConfigError(Exception):
    """report_tiers.yaml 存在但内容非法。"""


def load_report_tiers_config(paths: "AgentPaths") -> list[ReportTier]:
    p = paths.notification_report_tiers_config
    if not p.exists() or not _HAS_YAML:
        return []
    try:
        raw = _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise ReportTiersConfigError(f"report_tiers.yaml 解析失败: {exc}") from exc
    if not isinstance(raw, dict):
        raise ReportTiersConfigError("report_tiers.yaml 顶层结构必须是一个字典（tiers: [...]）")
    entries = raw.get("tiers") or []
    if not isinstance(entries, list):
        raise ReportTiersConfigError("report_tiers.yaml 的 tiers 字段必须是一个列表")
    tiers: list[ReportTier] = []
    seen_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            tier = ReportTier.from_dict(entry)
        except Exception as exc:
            from mini_agent.errors import log_exception
            log_exception(exc, where="mini_agent.external_input.report_tiers.load_report_tiers_config")
            continue
        if tier.id in seen_ids:
            continue
        seen_ids.add(tier.id)
        tiers.append(tier)
    return tiers


# ── tier 空转节流状态（§9.2 #7，纯运维态，读写失败不影响主流程） ──────────

def _load_tier_state(paths: "AgentPaths") -> dict:
    p = paths.notification_tier_state
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _save_tier_state(paths: "AgentPaths", state: dict) -> None:
    p = paths.notification_tier_state
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.external_input.report_tiers._save_tier_state")


def _is_high_freq_interval(schedule: str) -> Optional[int]:
    """schedule 形如 "interval:60" 且间隔 <= 阈值时返回间隔秒数，否则 None。"""
    if not schedule.startswith("interval:"):
        return None
    try:
        seconds = int(schedule.split(":", 1)[1])
    except (ValueError, IndexError):
        return None
    return seconds if seconds <= HIGH_FREQ_THRESHOLD_SECONDS else None


def _should_throttle(paths: "AgentPaths", tier: ReportTier) -> bool:
    """高频 tier 连续空转达到阈值后，本次是否应该跳过（节流）。"""
    interval = _is_high_freq_interval(tier.schedule)
    if interval is None:
        return False
    state = _load_tier_state(paths)
    entry = state.get(tier.id) or {}
    consecutive_empty = int(entry.get("consecutive_empty", 0))
    last_run_at = float(entry.get("last_run_at", 0.0))
    if consecutive_empty < EMPTY_RUN_THROTTLE_AFTER:
        return False
    return (time.time() - last_run_at) < EMPTY_RUN_THROTTLE_INTERVAL


def _record_tier_run(paths: "AgentPaths", tier: ReportTier, had_hits: bool) -> None:
    state = _load_tier_state(paths)
    entry = state.get(tier.id) or {}
    if had_hits:
        entry["consecutive_empty"] = 0
    else:
        entry["consecutive_empty"] = int(entry.get("consecutive_empty", 0)) + 1
    entry["last_run_at"] = time.time()
    state[tier.id] = entry
    _save_tier_state(paths, state)


# ── pending_hits.jsonl 消费 ───────────────────────────────────────────────

def _read_and_mark_consumed(paths: "AgentPaths", tier_id: str) -> list[dict]:
    """在独占锁保护下：读取整个 pending_hits.jsonl，取出 tier==tier_id 且
    未消费的记录，整体重写（把这些记录标记 consumed=true），返回取出的记录。
    跟 WatchlistMatcher 的追加写共享同一把 <path>.lock（§9.1 #1）。"""
    p = paths.external_input_pending_hits
    if not p.exists():
        return []
    matched: list[dict] = []
    with ExclusiveFileLock(p):
        lines = p.read_text(encoding="utf-8").splitlines()
        records = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:
                continue
        changed = False
        for rec in records:
            if rec.get("tier") == tier_id and not rec.get("consumed", False):
                matched.append(dict(rec))
                rec["consumed"] = True
                changed = True
        if changed:
            text = "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
            p.write_text(text + ("\n" if text else ""), encoding="utf-8")
    return matched


def _build_summary_markdown(tier_id: str, hits: list[dict]) -> tuple[str, str]:
    """按 watchlist_id 分组生成标题 + Markdown 正文。"""
    groups: dict[str, list[dict]] = {}
    for hit in hits:
        groups.setdefault(hit.get("watchlist_id", "unknown"), []).append(hit)

    title = f"关注对象命中汇报（{tier_id}，共 {len(hits)} 条）"
    lines = [f"# {title}", ""]
    for wid, items in groups.items():
        lines.append(f"## {wid}（{len(items)} 条）")
        shown = items[:MAX_ITEMS_PER_GROUP]
        for item in shown:
            url_part = f" [链接]({item['url']})" if item.get("url") else ""
            lines.append(f"- {item.get('title', '(无标题)')}{url_part}")
        remaining = len(items) - len(shown)
        if remaining > 0:
            lines.append(f"- ……及其余 {remaining} 条，详见 pending_hits.jsonl")
        lines.append("")
    return title, "\n".join(lines)


def consume_tier_once(paths: "AgentPaths", tier: ReportTier) -> bool:
    """消费一个 tier 自上次以来的命中记录，生成摘要并 dispatch。

    返回值仅表示"本次调用是否正常完成"（供 CronScheduler 记 run_count 用），
    不代表"是否真的发出了通知"——没有新记录时直接跳过发送，属于正常结果，
    同样返回 True（对齐 §4.3"没有新记录就直接跳过，不发送空消息"）。
    """
    try:
        if _should_throttle(paths, tier):
            return True
        hits = _read_and_mark_consumed(paths, tier.id)
        if not hits:
            _record_tier_run(paths, tier, had_hits=False)
            return True
        _record_tier_run(paths, tier, had_hits=True)

        title, body = _build_summary_markdown(tier.id, hits)

        from mini_agent.notification.dispatcher import NotificationDispatcher, NotificationMessage
        dispatcher = NotificationDispatcher(paths)
        message = NotificationMessage(
            title=title,
            body=body,
            source="watchlist_report",
            meta={"tier": tier.id, "hit_count": len(hits)},
        )
        dispatcher.dispatch(message, channels=tier.notify_channels)
        return True
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.external_input.report_tiers.consume_tier_once")
        return False


def ensure_report_tier_jobs(paths: "AgentPaths", cron_scheduler: "CronScheduler") -> list[str]:
    """daemon 启动时调用：按 report_tiers.yaml 为每个 tier 补注册
    sys:watchlist_report_<id> job（缺失才补，见 §8 开放项 2），并注册
    本地回调 handler（零 LLM 成本，见模块 docstring）。返回本次实际
    新注册的 job_id 列表（供日志/自检使用）。"""
    try:
        tiers = load_report_tiers_config(paths)
    except ReportTiersConfigError as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.external_input.report_tiers.ensure_report_tier_jobs")
        return []

    newly_added: list[str] = []
    existing_ids = {j.id for j in cron_scheduler.list_jobs()}
    for tier in tiers:
        if tier.job_id not in existing_ids:
            newly_added.append(tier.job_id)
        cron_scheduler.ensure_job(
            job_id=tier.job_id,
            name=f"关注对象分级汇报（{tier.id}）",
            schedule=tier.schedule,
            description=(
                f"消费 pending_hits.jsonl 中 tier={tier.id} 的未读命中，"
                f"生成摘要并通过 NotificationDispatcher 发送，零 LLM 成本。"
            ),
            tags=["notification", "watchlist_report"],
        )

        def _handler(job: "CronJob", _tier: ReportTier = tier) -> bool:
            return consume_tier_once(paths, _tier)

        cron_scheduler.register_local_handler(tier.job_id, _handler)
    return newly_added


__all__ = [
    "ReportTier",
    "ReportTiersConfigError",
    "load_report_tiers_config",
    "consume_tier_once",
    "ensure_report_tier_jobs",
    "DEFAULT_TIERS",
    "JOB_ID_PREFIX",
]
