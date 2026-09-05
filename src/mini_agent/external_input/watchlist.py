"""external_input/watchlist.py — WatchlistMatcher（P2）。

设计背景见 next_doc/watchlist_notification_goal_design.md §4.1。
纯规则、零 LLM 成本：对每条 external.* 事件，逐条比对 watchlist.yaml 里
已启用的关键词项，命中后去重、写入 pending_hits.jsonl（tier 取该
watchlist 项的 report_tier），完全不做任何 Goal 相关性判断——那是
GoalRelevanceEngine（P4/P5）的职责，两者完全独立、各自订阅 external.*
事件、各自持有独立游标。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from mini_agent.external_input.filelock import ExclusiveFileLock
from mini_agent.external_input.gateway import poll_external_events
from mini_agent.external_input.source import ExternalInputEvent

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths

try:
    import yaml as _yaml  # type: ignore
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

WATCHLIST_CONSUMER_NAME = "watchlist_matcher"

# §9.2 #6：去重窗口默认值 —— 同一话题（归一化标题相同）在这个时间窗口内
# 只计入一次，避免多个 RSS 源转载同一条新闻反复触发通知。可在
# watchlist.yaml 单条项目上用 dedup_window_seconds 覆盖。
DEFAULT_DEDUP_WINDOW_SECONDS = 24 * 3600


@dataclass
class WatchlistItem:
    id: str
    keywords: list[str]
    report_tier: str
    match_type: str = "keyword"
    notify_channels: list[str] = field(default_factory=list)
    source_channels: list[str] = field(default_factory=list)
    enabled: bool = True
    dedup_window_seconds: int = DEFAULT_DEDUP_WINDOW_SECONDS

    @staticmethod
    def from_dict(d: dict) -> "WatchlistItem":
        item_id = str(d.get("id", "")).strip()
        if not item_id:
            raise ValueError(f"watchlist.yaml 条目缺少 id 字段: {d!r}")
        keywords = [str(k) for k in (d.get("keywords") or []) if str(k).strip()]
        report_tier = str(d.get("report_tier", "")).strip()
        if not report_tier:
            raise ValueError(f"watchlist.yaml 条目 {item_id!r} 缺少 report_tier 字段")
        scope = d.get("scope") or {}
        dedup = d.get("dedup_window_seconds", DEFAULT_DEDUP_WINDOW_SECONDS)
        try:
            dedup = int(dedup)
        except (TypeError, ValueError):
            dedup = DEFAULT_DEDUP_WINDOW_SECONDS
        return WatchlistItem(
            id=item_id,
            keywords=keywords,
            report_tier=report_tier,
            match_type=str(d.get("match_type", "keyword")),
            notify_channels=[str(c) for c in (d.get("notify_channels") or [])],
            source_channels=[str(c) for c in (scope.get("source_channels") or [])],
            enabled=bool(d.get("enabled", True)),
            dedup_window_seconds=dedup if dedup > 0 else DEFAULT_DEDUP_WINDOW_SECONDS,
        )

    def matches(self, event: ExternalInputEvent) -> bool:
        if not self.enabled:
            return False
        if self.source_channels and event.channel not in self.source_channels:
            return False
        # match_type=regex 先占位，不实现（对齐 §3.1 注释），非 keyword 一律不匹配。
        if self.match_type != "keyword":
            return False
        haystack = f"{event.title}\n{event.detail}".lower()
        return any(kw.lower() in haystack for kw in self.keywords if kw)


class WatchlistConfigError(Exception):
    """watchlist.yaml 存在但内容非法（YAML 语法错误 / 顶层结构不是预期形状）。
    单条记录缺字段不算这一类——那种情况按"跳过这一条、其余照常加载"处理。"""


def load_watchlist_config(paths: "AgentPaths") -> list[WatchlistItem]:
    p = paths.external_input_watchlist_config
    if not p.exists() or not _HAS_YAML:
        return []
    try:
        raw = _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise WatchlistConfigError(f"watchlist.yaml 解析失败: {exc}") from exc
    if not isinstance(raw, dict):
        raise WatchlistConfigError("watchlist.yaml 顶层结构必须是一个字典（watchlist: [...]）")
    entries = raw.get("watchlist") or []
    if not isinstance(entries, list):
        raise WatchlistConfigError("watchlist.yaml 的 watchlist 字段必须是一个列表")
    items: list[WatchlistItem] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            items.append(WatchlistItem.from_dict(entry))
        except Exception as exc:
            from mini_agent.errors import log_exception
            log_exception(exc, where="mini_agent.external_input.watchlist.load_watchlist_config")
            continue
    return items


def build_watchlist_profile_snapshot(paths: "AgentPaths", *, max_items: int = 10) -> str:
    """[response to user feedback: 画像信息来源不够全] 为
    `UserProfileManager.generate()` 准备一份"用户主动配置要关注的
    话题/关键词"快照，跟 `build_goal_tree_profile_snapshot()` 同一个
    定位——零成本、不引入 LLM、任一环节异常直接返回空串。

    背景：`watchlist.yaml` 是用户显式配置的"我要关注这些话题"（见
    `WatchlistMatcher`），这是比"从历史会话摘要里反推用户关心什么"更
    直接、更权威的信号，但画像生成此前完全没有用到这份数据——用户的
    真实关注点如果还没在某次对话里被提起过，画像就永远看不到。这里
    只取已启用（`enabled=True`）条目的 id + 关键词，不展开匹配命中的
    具体内容（那是 pending_hits.jsonl 的职责，跟"用户是谁/关心什么"
    这层画像信息无关，也避免把大量新闻类文本喂进 profile 生成的 prompt）。
    """
    try:
        items = load_watchlist_config(paths)
    except Exception:
        return ""

    enabled = [it for it in items if it.enabled]
    if not enabled:
        return ""

    lines = ["Topics/keywords the user has explicitly configured to watch (from watchlist.yaml):"]
    for it in enabled[:max_items]:
        kw = "、".join(it.keywords) if it.keywords else it.id
        lines.append(f"- [{it.id}] {kw}")

    if len(lines) <= 1:
        return ""
    return "\n".join(lines)


def _normalize_title_key(title: str) -> str:
    try:
        from mini_agent.evolution.objective_outcome_tracker import normalize_title_key
        return normalize_title_key(title)
    except Exception:
        return title.strip().lower()


@dataclass
class WatchlistMatchSummary:
    scanned: int = 0
    matched: int = 0
    deduped: int = 0
    written: int = 0


def _load_pending_hits(p) -> list[dict]:
    if not p.exists():
        return []
    result = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            result.append(json.loads(line))
        except Exception:
            continue
    return result


def _append_pending_hit(paths: "AgentPaths", hit: dict) -> None:
    """在独占锁保护下追加一条命中记录（§9.1 #1：跟 report_tiers 的
    "读+改+整体重写"共享同一把 <path>.lock，避免并发写丢数据）。"""
    p = paths.external_input_pending_hits
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with ExclusiveFileLock(p):
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(hit, ensure_ascii=False) + "\n")
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.external_input.watchlist._append_pending_hit")


def run_watchlist_matcher_once(
    paths: "AgentPaths",
    *,
    consumer_name: str = WATCHLIST_CONSUMER_NAME,
) -> WatchlistMatchSummary:
    """消费一批自上次游标之后的 external.* 事件，按 watchlist.yaml 匹配。

    跟 IngestionPolicy 一样挂在 AutonomousLoop.tick() 的 maintenance 档位里，
    各自独立游标——不是"先匹配关注词，命中的才去判断 Goal 相关性"这种串联
    关系（见 §2 关键设计取舍）。
    """
    summary = WatchlistMatchSummary()
    try:
        items = load_watchlist_config(paths)
    except WatchlistConfigError as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.external_input.watchlist.run_watchlist_matcher_once")
        return summary
    if not items:
        # watchlist 为空：仍然要推进游标，避免以后配置了 watchlist 之后突然
        # "回放"一大批历史事件——跟 IngestionPolicy 空 rules 时的处理方式一致。
        poll_external_events(paths, consumer_name=consumer_name)
        return summary

    events = poll_external_events(paths, consumer_name=consumer_name)
    summary.scanned = len(events)
    if not events:
        return summary

    # 去重需要看最近写过的记录（跨 item 共享同一份 pending_hits.jsonl）。
    existing = _load_pending_hits(paths.external_input_pending_hits)
    now = time.time()
    # {(watchlist_id, normalized_title_key): matched_at}，只保留窗口内的最近一条即可。
    recent: dict[tuple, float] = {}
    for rec in existing:
        key = (rec.get("watchlist_id"), rec.get("_title_key"))
        ts = rec.get("matched_at") or 0
        if key not in recent or ts > recent[key]:
            recent[key] = ts

    for event in events:
        title_key = _normalize_title_key(event.title)
        for item in items:
            if not item.matches(event):
                continue
            summary.matched += 1
            dedup_key = (item.id, title_key)
            last_ts = recent.get(dedup_key)
            if last_ts is not None and (now - last_ts) < item.dedup_window_seconds:
                summary.deduped += 1
                continue
            hit = {
                "id": f"hit:{item.id}:{event.id}",
                "tier": item.report_tier,
                "source": "watchlist",
                "watchlist_id": item.id,
                "title": event.title,
                "detail": event.detail,
                "url": event.url,
                "notify_channels": item.notify_channels,
                "matched_at": now,
                "consumed": False,
                "_title_key": title_key,
            }
            _append_pending_hit(paths, hit)
            recent[dedup_key] = now
            summary.written += 1

    return summary
