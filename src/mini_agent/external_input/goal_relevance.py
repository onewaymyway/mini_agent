"""external_input/goal_relevance.py — GoalRelevanceEngine（P4：Stage①）。

设计背景见 next_doc/watchlist_notification_goal_design.md §4.2/§3.6。

Stage①（本文件，P4 范围）：纯规则层，零 LLM 成本，每个 tick 都跑。对每条
`external.*` 事件，与 `goal_backlog.active_goals()`（只看 level=goal 且
status=active，不含 Objective）逐一计算一个廉价的 token 重合度分数，超过
一个很低的阈值（默认宽松，只为过滤掉明显八竿子打不着的组合）即写入
`goal_relevance_candidates.jsonl`，交给 Stage②（P5，LLM 批量判定）消费。

这一层的设计原则是"宁可让 Stage② LLM 多判几个'不相关'，也不要在这一层
就误杀掉真正相关的事件"——跟 `WatchlistMatcher` 完全独立，各自订阅
`external.*` 事件、各自持有独立游标，不是"先匹配关注词，命中的才判断
Goal 相关性"这种串联关系（见 §2 关键设计取舍）。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from mini_agent.external_input.filelock import ExclusiveFileLock
from mini_agent.external_input.gateway import poll_external_events
from mini_agent.external_input.source import ExternalInputEvent

if TYPE_CHECKING:
    from mini_agent.perception.goal_backlog import GoalBacklog, GoalNode
    from mini_agent.storage.paths import AgentPaths

CANDIDATE_CONSUMER_NAME = "goal_relevance_candidate"

# §8 开放项 1：先给一个宽松默认阈值，跑一段时间观察 Stage② 的"相关判定
# 命中率"再调整，不是精确计算出来的值。
DEFAULT_PREFILTER_THRESHOLD = 0.12

# §9.2 #5：候选队列总量止损上限——超过这个数直接丢弃本轮新候选并计数，
# 不无限堆积一个 jsonl 文件。
MAX_CANDIDATES_TOTAL = 500

_TOKEN_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _tokenize(text: str) -> set[str]:
    """跟 `normalize_title_key` 同款归一化风格：小写、去标点、按空格切分。
    这里额外过滤掉单字符 token（中文场景下按字切分噪声太大、英文场景下
    单字母基本没有区分度），不追求精确分词，只求便宜且不容易漏判。"""
    s = (text or "").lower().strip()
    s = _TOKEN_RE.sub(" ", s)
    return {tok for tok in s.split() if len(tok) > 1}


def _overlap_score(a_tokens: set[str], b_tokens: set[str]) -> float:
    """token 重合度：交集大小 / 两者中较小的那个集合大小（不是 Jaccard，
    刻意选"对短文本更宽松"的分母，因为 Goal 标题通常比事件详情短很多，
    用交集/并集会把重合度算得过低，容易在这一层就误杀真正相关的组合）。"""
    if not a_tokens or not b_tokens:
        return 0.0
    inter = len(a_tokens & b_tokens)
    denom = min(len(a_tokens), len(b_tokens))
    return inter / denom if denom else 0.0


@dataclass
class GoalRelevanceCandidateSummary:
    scanned_events: int = 0
    scanned_goals: int = 0
    candidates_written: int = 0
    candidates_skipped_existing: int = 0
    candidates_discarded_over_cap: int = 0


def _load_candidate_ids(p) -> set[str]:
    if not p.exists():
        return set()
    ids: set[str] = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        cid = rec.get("id")
        if cid:
            ids.add(cid)
    return ids


def _count_candidates(p) -> int:
    if not p.exists():
        return 0
    return sum(1 for line in p.read_text(encoding="utf-8").splitlines() if line.strip())


def _append_candidates(paths: "AgentPaths", records: list[dict]) -> None:
    if not records:
        return
    p = paths.external_input_goal_relevance_candidates
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with ExclusiveFileLock(p):
            with open(p, "a", encoding="utf-8") as f:
                for rec in records:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.external_input.goal_relevance._append_candidates")


def run_goal_relevance_candidate_once(
    paths: "AgentPaths",
    *,
    consumer_name: str = CANDIDATE_CONSUMER_NAME,
    goal_backlog: "Optional[GoalBacklog]" = None,
    threshold: float = DEFAULT_PREFILTER_THRESHOLD,
) -> GoalRelevanceCandidateSummary:
    """消费一批自上次游标之后的 external.* 事件，与当前 active Goal 逐一
    计算重合度分数，超过阈值即写入候选队列。

    `goal_backlog` 未传入时（测试/诊断场景）内部自行 `load_goal_backlog()`
    读一份只读快照——本函数不修改 GoalBacklog 任何字段，纯读取。
    """
    summary = GoalRelevanceCandidateSummary()

    if goal_backlog is None:
        try:
            from mini_agent.perception.goal_backlog import load_goal_backlog
            goal_backlog = load_goal_backlog(paths)
        except Exception as exc:
            from mini_agent.errors import log_exception
            log_exception(exc, where="mini_agent.external_input.goal_relevance.run_goal_relevance_candidate_once.load_goal_backlog")
            goal_backlog = None

    goals: list["GoalNode"] = list(goal_backlog.active_goals()) if goal_backlog is not None else []
    summary.scanned_goals = len(goals)

    events = poll_external_events(paths, consumer_name=consumer_name)
    summary.scanned_events = len(events)
    if not events or not goals:
        return summary

    candidates_path = paths.external_input_goal_relevance_candidates
    existing_ids = _load_candidate_ids(candidates_path)
    current_total = len(existing_ids)

    goal_tokens_cache: dict[str, set[str]] = {
        g.id: _tokenize(f"{g.title}\n{g.description}") for g in goals
    }

    new_records: list[dict] = []
    now = time.time()
    for event in events:
        event_tokens = _tokenize(f"{event.title}\n{event.detail}")
        if not event_tokens:
            continue
        for goal in goals:
            cand_id = f"cand:{event.id}:{goal.id}"
            # §9.1 #2：同一 (event_id, goal_id) 已经写过（无论 judged 与否）
            # 就跳过，避免游标重放（daemon 重启等）时重复写入同一组合。
            if cand_id in existing_ids:
                summary.candidates_skipped_existing += 1
                continue
            score = _overlap_score(event_tokens, goal_tokens_cache.get(goal.id, set()))
            if score < threshold:
                continue
            if current_total + len(new_records) >= MAX_CANDIDATES_TOTAL:
                summary.candidates_discarded_over_cap += 1
                continue
            new_records.append({
                "id": cand_id,
                "event_id": event.id,
                "goal_id": goal.id,
                "event_title": event.title,
                "event_detail": event.detail,
                "goal_title": goal.title,
                "goal_description": goal.description,
                "prefilter_score": round(score, 4),
                "judged": False,
                "created_at": now,
            })
            existing_ids.add(cand_id)

    if new_records:
        _append_candidates(paths, new_records)
        summary.candidates_written = len(new_records)

    return summary


__all__ = [
    "GoalRelevanceCandidateSummary",
    "run_goal_relevance_candidate_once",
    "DEFAULT_PREFILTER_THRESHOLD",
    "MAX_CANDIDATES_TOTAL",
    "CANDIDATE_CONSUMER_NAME",
]
