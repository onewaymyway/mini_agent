"""推荐信息流（P1 骨架）：候选池 → 聚合去重 → 多信号打分 → 分区展示。

对应 next_doc/info_hub_recommendation_feed_design.md 的 P1 阶段，不含 LLM、不含探索位与多样性重排（P2）、
不含学习权重（P3）。纯文本存储，读取的是既有的快照、反馈与曝光记录；唯一新写入的是
data/feed/shown.jsonl（每次展示一批结果的位置与分量，供之后离线评估）。

流水线：
  ① 候选池：时间窗内合并（core/merged.py）；已 👍/👎 或已打开的条目不进入；⏰ 进入“稍后看”队列；命中屏蔽词剔除。
  ② 聚合去重：URL 完全相同直接合并；标题字符二元组 Jaccard ≥ 阈值（默认 0.45）的合并为一个“事件”簇。
     倒排索引分桶，只比较共享二元组的条目；出现过于频繁的二元组不参与配对，避免 O(n²)。
  ③ 多信号打分：interest / source_affinity / freshness / importance 加权，persistence_penalty 扣分。
  ④ 分区：今日精选 → 多源热点 → 关注主题 → 其余；每个簇只出现在一个分区中（天然去重）。稍后看单独列出。

所有权重与阈值可在 config/settings.yaml 的 feed 段覆盖，见 DEFAULT_CONFIG。
"""
from __future__ import annotations

import math
import re
from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from core import merged
from core.feedback import item_key, normalize_url, read_feedback
from core.store import append_jsonl, atomic_write_json, file_lock, read_json, read_jsonl

VIEW = "feed_v1"
PROCESSED_ACTIONS = ("interested", "not_interested")  # 明确处理过：不再进入推荐
MULTI_SOURCE_SATURATION = 4  # 报道来源数达到该值时“多源”分量饱和
RANK_DECAY = 10.0            # 排名分量：第 1 名为 1，第 11 名约 0.5
RISE_SATURATION = 10         # 排名上升 10 位及以上时“上升”分量饱和
SOURCE_PRIOR_WEIGHT = 0.5    # 来源反馈比率的先验（无数据时视为一半正向）
SOURCE_PRIOR_STRENGTH = 5    # 先验强度（等效样本数）

DEFAULT_CONFIG: dict[str, Any] = {
    "featured_size": 15,           # 今日精选条数
    "hot_min_sources": 3,          # 多源热点：至少被几个不同信息源报道
    "hot_size": 12,                 # 多源热点最多条数
    "topic_groups": 5,             # 关注主题分组数
    "topic_group_size": 8,         # 每个主题组最多条数
    "topic_min_group": 2,          # 主题组至少包含的簇数（少于此数的主题不单独成组）
    "later_size": 50,              # 稍后看队列展示条数
    "others_limit": 50,            # “其余”分区一次展示并记录曝光的条数
    "candidate_limit": 2000,       # 候选池上限（按首次出现时间取最近的）
    "cluster_threshold": 0.45,     # 标题二元组 Jaccard 聚合阈值（宁可漏聚，不要误并）
    "cluster_df_ratio": 0.1,       # 出现在超过该比例（且超过 60 条）标题中的二元组不参与配对
    "cold_start_factor": 0.3,      # 画像为空时，interest 权重乘以该系数（改由重要性与新鲜度主导）
    "persistence_snapshots": 12,   # 窗口内出现超过该快照数、且从未“新增”的条目开始降权
    "weights": {
        "interest": 0.35,
        "source_affinity": 0.15,
        "freshness": 0.25,
        "importance": 0.25,
        "persistence_penalty": 0.3,
        "fatigue_penalty": 0.2,    # P2：展示很多次却从未点击的条目降权；设为 0 即关闭
    },
    "explore_size": 5,             # 探索分区的条数（画像之外的内容）；设为 0 即关闭
    "diversity": {                 # P2：精选中的多样性控制
        "source_cap": 3,           # 同一信息源在精选中最多出现的条数
        "topic_repeat_penalty": 0.1,  # 同一主题每多一条，后续候选扣掉的分数
    },
    "fatigue": {                   # P2：曝光疲劳
        "shows": 3,                # 展示达到该次数仍无任何动作，开始视为弱负向
        "window_days": 7,          # 只统计最近这么多天的展示
    },
    # 新鲜度半衰期（小时）：资讯流消逝快，订阅内容保留久；未知类型用 default
    "half_life_hours": {"hot_list": 3, "feed": 12, "recommend": 24, "custom": 12, "subscription": 72,
                        "default": 12},
}


# 看板可调的展示数量：键 -> (最小值, 最大值, 中文名)。页面保存的值优先于 settings.yaml 与默认值。
DISPLAY_LIMITS: dict[str, tuple[int, int, str]] = {
    "featured_size": (1, 40, "今日精选条数"),
    "explore_size": (0, 40, "探索条数"),
    "hot_size": (0, 40, "多源热点最多条数"),
    "topic_groups": (0, 12, "关注主题分组数"),
    "topic_group_size": (1, 30, "每个主题组最多条数"),
    "later_size": (1, 200, "稍后看展示条数"),
    "others_limit": (1, 300, "其余分区展示条数"),
}


def clamp_display(values: dict[str, Any]) -> dict[str, int]:
    """只保留可调的键，并限制在合法范围内；非数字的值被丢弃。"""
    out: dict[str, int] = {}
    for key, value in (values or {}).items():
        if key not in DISPLAY_LIMITS or isinstance(value, bool):
            continue
        try:
            n = int(value)
        except (TypeError, ValueError):
            continue
        lo, hi, _ = DISPLAY_LIMITS[key]
        out[key] = min(hi, max(lo, n))
    return out


def apply_display(cfg: dict[str, Any], overrides: Optional[dict[str, Any]]) -> dict[str, Any]:
    """把看板保存的展示数量覆盖到配置上（返回新字典，不改动入参）。"""
    out = dict(cfg)
    out.update(clamp_display(overrides or {}))
    return out


# ---------- 配置 ----------
def load_config(raw: Optional[dict[str, Any]]) -> dict[str, Any]:
    """合并 settings.yaml 的 feed 段与默认值；非法值回落默认，不抛异常（看板不应因配置崩溃）。"""
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_CONFIG.items()}
    user = (raw or {}).get("feed") or {}
    if not isinstance(user, dict):
        return cfg
    for key, value in user.items():
        if key in cfg and isinstance(cfg[key], dict) and isinstance(value, dict):
            cfg[key].update({k: v for k, v in value.items() if isinstance(v, (int, float))})
        elif key in cfg and not isinstance(cfg[key], dict) and isinstance(value, (int, float)):
            cfg[key] = value
    return cfg


# ---------- 文本与聚合 ----------
_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)


def _norm_text(text: str) -> str:
    return _NON_WORD.sub("", (text or "").lower())


def bigrams(title: str) -> set[str]:
    """标题的字符二元组（去掉标点与空白、统一小写）。中英文通用，不依赖分词库。"""
    s = _norm_text(title)
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def cluster_items(items: list[dict[str, Any]], threshold: float = 0.45, df_ratio: float = 0.1,
                  min_df_cap: int = 60) -> list[list[int]]:
    """把条目（按下标）聚成事件簇。返回若干下标列表，每个列表是一个簇，簇内下标升序。

    1) URL 规范化后相同 → 直接合并；2) 标题二元组 Jaccard ≥ threshold → 合并。
    二元组倒排索引：出现在超过 max(min_df_cap, n*df_ratio) 条标题里的二元组不参与配对（它们几乎不能区分事件）。
    """
    n = len(items)
    uf = _UnionFind(n)
    first_by_url: dict[str, int] = {}
    for i, it in enumerate(items):
        u = normalize_url(it.get("url") or "")
        if u:
            if u in first_by_url:
                uf.union(i, first_by_url[u])
            else:
                first_by_url[u] = i

    grams = [bigrams(it.get("title") or "") for it in items]
    postings: dict[str, list[int]] = {}
    for i, g in enumerate(grams):
        for b in g:
            postings.setdefault(b, []).append(i)
    cap = max(min_df_cap, int(n * df_ratio))
    for i, g in enumerate(grams):
        cands: set[int] = set()
        for b in g:
            plist = postings[b]
            if len(plist) <= cap:
                cands.update(plist)
        for j in cands:
            if j <= i or uf.find(i) == uf.find(j):
                continue
            if jaccard(grams[i], grams[j]) >= threshold:
                uf.union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)
    return [sorted(v) for v in groups.values()]


# ---------- 时间 ----------
def _parse_time(value: Any) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.astimezone()


def age_text(now: datetime, first_seen: Any) -> str:
    dt = _parse_time(first_seen)
    if dt is None:
        return ""
    minutes = max(1, int((now - dt).total_seconds() // 60))
    if minutes < 60:
        return f"{minutes} 分钟前出现"
    if minutes < 48 * 60:
        return f"{minutes // 60} 小时前出现"
    return f"{minutes // (24 * 60)} 天前出现"


# ---------- 分量 ----------
def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))


def source_affinity(sid: str, *, positives: dict[str, int], negatives: dict[str, int],
                    learned_sources: dict[str, Any]) -> float:
    """来源亲和度 0~1：反馈比率（带先验平滑）与画像里来源权重的 sigmoid 各占一半。无数据时为 0.5。"""
    p = positives.get(sid, 0)
    n = negatives.get(sid, 0)
    ratio = (p + SOURCE_PRIOR_WEIGHT * SOURCE_PRIOR_STRENGTH) / (p + n + SOURCE_PRIOR_STRENGTH)
    learned = (learned_sources.get(sid) or {}).get("score", 0.0)
    return round(0.5 * ratio + 0.5 * _sigmoid(float(learned)), 4)


def freshness(first_seen: Any, now: datetime, half_life_hours: float) -> float:
    """指数衰减：距首次出现每过一个半衰期减半。"""
    dt = _parse_time(first_seen)
    if dt is None or half_life_hours <= 0:
        return 0.0
    age_h = max(0.0, (now - dt).total_seconds() / 3600.0)
    return round(0.5 ** (age_h / half_life_hours), 4)


def importance(n_sources: int, best_rank: Optional[int], max_rise: int) -> float:
    """重要性 0~1：多源同报（对数）、窗口内最高排名、排名上升，三者加权 0.4 / 0.4 / 0.2。"""
    multi = min(1.0, math.log2(1 + n_sources) / math.log2(1 + MULTI_SOURCE_SATURATION))
    rank_part = 1.0 / (1.0 + (best_rank - 1) / RANK_DECAY) if best_rank else 0.0
    rise_part = min(1.0, max(0, max_rise) / RISE_SATURATION)
    return round(0.4 * multi + 0.4 * rank_part + 0.2 * rise_part, 4)


def persistence_penalty(members: list[dict[str, Any]], snapshots: int) -> float:
    """只靠“一直挂在榜上”出现的簇降权（热榜霸屏）。任一成员被标为“新增”则不降权。"""
    if any(m.get("is_new") for m in members):
        return 0.0
    seen = max(int(m.get("seen_count") or 0) for m in members)
    if snapshots <= 0:
        return 0.0
    return round(min(1.0, max(0.0, (seen - snapshots) / snapshots)), 4)


def quantile_normalize(values: list[float]) -> list[float]:
    """把原始兴趣分按分布归一化到 0~1（平均秩，同分同值）。全部相同时返回 0，表示没有区分度。"""
    n = len(values)
    if n == 0 or max(values) - min(values) < 1e-9:
        return [0.0] * n
    ordered = sorted(values)
    out = []
    for v in values:
        lo, hi = bisect_left(ordered, v), bisect_right(ordered, v)
        out.append(round(((lo + hi - 1) / 2) / (n - 1), 4))
    return out


def learned_score(comps: dict[str, Any], weights: dict[str, float]) -> float:
    """P3 线性打分：学习到的权重与各分量的点积（单调，因此只改变排序，不改变分量本身）。"""
    names = ("interest", "source_affinity", "freshness", "importance", "persistence_penalty", "fatigue_penalty")
    return round(sum(weights[n] * float(comps.get(n, 0.0) or 0.0) for n in names), 4)


def profile_informative(profile: dict[str, Any]) -> bool:
    """画像里是否有任何可用于排序的信号；没有则视为冷启动。"""
    explicit = profile.get("explicit") or {}
    learned = profile.get("learned") or {}
    llm = profile.get("llm") or {}
    return bool(explicit.get("topics") or learned.get("keywords") or learned.get("sources")
                or llm.get("keywords"))


# ---------- 反馈与曝光 ----------
def _event_index(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """每个条目最近一次反馈事件（按时间取最新）。"""
    latest: dict[str, dict[str, Any]] = {}
    for e in sorted(events, key=lambda e: str(e.get("time", ""))):
        latest[e.get("item_key", "")] = e
    return latest


def _open_state(data_dir: Path) -> tuple[set[str], dict[str, int]]:
    """返回 (已打开过的条目 key 集合, 各信息源被打开的次数)。"""
    from core.evaluation import exposure_path
    opened: set[str] = set()
    opens_by_source: dict[str, int] = {}
    for row in read_jsonl(exposure_path(data_dir)):
        if row.get("kind") == "open" and row.get("item_key"):
            opened.add(row["item_key"])
            sid = row.get("source_id") or ""
            if sid:
                opens_by_source[sid] = opens_by_source.get(sid, 0) + 1
    return opened, opens_by_source


def _feedback_by_source(events: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, int]]:
    pos: dict[str, int] = {}
    neg: dict[str, int] = {}
    for e in events:
        sid = e.get("source_id") or ""
        if not sid:
            continue
        if e.get("action") == "interested":
            pos[sid] = pos.get(sid, 0) + 1
        elif e.get("action") == "not_interested":
            neg[sid] = neg.get(sid, 0) + 1
    return pos, neg


# ---------- P2：偏好（不再显示的信息源） ----------
def prefs_path(data_dir: Path) -> Path:
    return Path(data_dir) / "feed" / "prefs.json"


def load_prefs(data_dir: Path) -> dict[str, Any]:
    data = read_json(prefs_path(data_dir), default=None)
    data = data if isinstance(data, dict) else {}
    hidden = data.get("hidden_sources", [])
    return {"hidden_sources": [str(x) for x in hidden if isinstance(x, str) and x],
            "learning_enabled": bool(data.get("learning_enabled", False)),  # P3：默认关闭
            "llm_enabled": bool(data.get("llm_enabled", False)),            # P4：默认关闭
            "display": clamp_display(data.get("display") or {})}            # 看板设置的展示数量


def save_prefs(data_dir: Path, prefs: dict[str, Any]) -> None:
    path = prefs_path(data_dir)
    current = load_prefs(data_dir)
    current.update({k: v for k, v in prefs.items() if k in current})
    with file_lock(path):
        atomic_write_json(path, {"hidden_sources": sorted(set(current["hidden_sources"])),
                                 "learning_enabled": bool(current["learning_enabled"]),
                                 "llm_enabled": bool(current["llm_enabled"]),
                                 "display": current["display"]})


def set_source_hidden(data_dir: Path, source_id: str, hidden: bool) -> dict[str, Any]:
    prefs = load_prefs(data_dir)
    current = set(prefs["hidden_sources"])
    (current.add if hidden else current.discard)(source_id)
    prefs["hidden_sources"] = sorted(current)
    save_prefs(data_dir, prefs)
    return prefs


# ---------- P2：曝光计数与疲劳 ----------
def shown_counts(data_dir: Path, now: datetime, window_days: float) -> dict[str, int]:
    """窗口内每个条目被展示过的次数（来自 shown.jsonl，append-only）。"""
    since = now - timedelta(days=window_days)
    counts: dict[str, int] = {}
    for row in read_jsonl(shown_path(data_dir)):
        if row.get("kind") != "feed_impression":
            continue
        when = _parse_time(row.get("time"))
        if when is None or when < since:
            continue
        for it in row.get("items") or []:
            key = it.get("item_key")
            if key:
                counts[key] = counts.get(key, 0) + 1
    return counts


def fatigue_value(count: int, shows: int) -> float:
    """展示次数达到 shows 后开始线性增长，展示 2×shows−1 次时达到 1（shows=3 时即第 5 次）。未达到则为 0。"""
    if shows <= 0 or count < shows:
        return 0.0
    return round(min(1.0, (count - shows + 1) / shows), 4)


# ---------- 主流程 ----------
def build_feed(*, data_dir: Path, raw_dir: Path, sources: list[dict[str, Any]], hours: float,
               cfg: Optional[dict[str, Any]] = None, profile: Optional[dict[str, Any]] = None,
               now: Optional[datetime] = None, include_processed: bool = False,
               hidden_sources: Optional[set[str]] = None,
               shown: Optional[dict[str, int]] = None,
               learned_weights: Optional[dict[str, float]] = None,
               exclude: Optional[set[str]] = None,
               merge_judge: Optional[Callable[[list[dict[str, Any]], list[list[int]]], list[list[int]]]] = None,
               ) -> dict[str, Any]:
    """构造推荐信息流。只读（不写 feed 目录，曝光记录由调用方显式 record_impressions）。

    sources：[{id, name, kind}]；profile：兴趣画像（缺省时从 data_dir 读取并按需重算）；
    hidden_sources：“不再显示”的信息源；shown：{item_key: 窗口内展示次数}（缺省从 shown.jsonl 计算）。
    返回 {generated_at, since, window_hours, stats, sections, impressions, others_limit, config...}。
    """
    from core.profile import ensure_fresh, score_item

    cfg = cfg or load_config(None)
    now = now or datetime.now().astimezone()
    names = {s["id"]: s.get("name", s["id"]) for s in sources}
    kinds = {s["id"]: s.get("kind", "") for s in sources}
    ids = list(names)
    merged_res = merged.merge_sources(raw_dir, ids, hours, now=now) if ids else {
        "items": [], "since": now.isoformat(timespec="seconds"), "sources": {}}
    profile = profile if profile is not None else ensure_fresh(data_dir)
    hidden = set(hidden_sources or ())
    excluded_keys = set(exclude or ())  # “换一批”：已展示过的条目（按条目 key）不再进入候选
    if shown is None:
        shown = shown_counts(data_dir, now, cfg["fatigue"]["window_days"])

    events = read_feedback(data_dir)
    latest = _event_index(events)
    opened, opens_by_src = _open_state(data_dir)
    pos_by_src, neg_by_src = _feedback_by_source(events)
    for sid, n in opens_by_src.items():
        pos_by_src[sid] = pos_by_src.get(sid, 0) + n  # 打开计为正向信号
    learned_sources = (profile.get("learned") or {}).get("sources") or {}
    blocked_terms = [t.lower() for t in (profile.get("explicit") or {}).get("exclude", []) if t]

    stats = {"raw_items": len(merged_res["items"]), "processed": 0, "blocked": 0, "hidden_sources": 0,
             "excluded": 0, "later": 0, "candidates": 0, "clusters": 0, "cold_start": not profile_informative(profile),
             "truncated": False}

    pool: list[dict[str, Any]] = []
    later_rows: list[dict[str, Any]] = []
    for it in merged_res["items"]:
        key = item_key(it.get("url") or "", it.get("title") or "")
        last = latest.get(key) or {}
        action = last.get("action", "")
        text = f"{it.get('title') or ''} {it.get('summary') or ''}".lower()
        row = dict(it)
        row.update({"item_key": key, "source_name": names.get(it.get("source_id"), it.get("source_id")),
                    "source_kind": kinds.get(it.get("source_id"), ""), "processed": False,
                    "processed_by": "", "shown_count": shown.get(key, 0)})
        if key in excluded_keys:
            stats["excluded"] += 1
            continue
        if it.get("source_id") in hidden:
            stats["hidden_sources"] += 1
            continue
        if any(term in text for term in blocked_terms):
            stats["blocked"] += 1
            continue
        if key in opened:
            row["processed"], row["processed_by"] = True, "opened"
        elif action in PROCESSED_ACTIONS:
            row["processed"], row["processed_by"] = True, action
        elif action == "later":
            later_rows.append({**row, "later_time": last.get("time", "")})
            continue
        if row["processed"]:
            stats["processed"] += 1
            if not include_processed:
                continue
        pool.append(row)

    # 稍后看：同一条目只出现一次，按加入时间倒序
    seen_later: set[str] = set()
    later_rows.sort(key=lambda r: str(r.get("later_time", "")), reverse=True)
    later_list = []
    for r in later_rows:
        if r["item_key"] in seen_later:
            continue
        seen_later.add(r["item_key"])
        later_list.append(_plain_card(r, now))
    stats["later"] = len(later_list)
    later_list = later_list[:cfg["later_size"]]

    # 候选上限：按首次出现时间取最近的
    pool.sort(key=lambda r: str(r.get("first_seen") or ""), reverse=True)
    if len(pool) > cfg["candidate_limit"]:
        pool = pool[:cfg["candidate_limit"]]
        stats["truncated"] = True
    stats["candidates"] = len(pool)

    groups = cluster_items(pool, threshold=cfg["cluster_threshold"], df_ratio=cfg["cluster_df_ratio"])
    if merge_judge is not None:  # P4：只使用已缓存的“同一事件”结论合并簇，不在此调用模型
        groups = merge_judge(pool, groups)
    stats["clusters"] = len(groups)

    raw_interest: list[float] = []
    partial: list[dict[str, Any]] = []
    for idxs in groups:
        members = [pool[i] for i in idxs]
        best_raw, best_matches, best_member = None, [], None
        for m in members:
            score, matches = score_item(profile, m.get("title") or "", m.get("summary") or "",
                                        m.get("source_id") or "")
            if best_raw is None or score > best_raw:
                best_raw, best_matches, best_member = score, matches, m
        raw_interest.append(float(best_raw or 0.0))
        partial.append({"members": members, "interest_raw": float(best_raw or 0.0),
                        "matches": best_matches, "best_member": best_member})

    interest_norm = quantile_normalize(raw_interest)
    weights = cfg["weights"]
    interest_factor = cfg["cold_start_factor"] if stats["cold_start"] else 1.0
    cards: list[dict[str, Any]] = []
    for p, inter in zip(partial, interest_norm):
        members = p["members"]
        src_ids = {m.get("source_id") for m in members}
        n_sources = len(src_ids)
        ranks = [m.get("rank") for m in members if isinstance(m.get("rank"), int) and m.get("rank") > 0]
        best_rank = min(ranks) if ranks else None
        rises = [m["first_rank"] - m["rank"] for m in members
                 if isinstance(m.get("first_rank"), int) and isinstance(m.get("rank"), int)]
        max_rise = max(rises) if rises else 0
        # 新鲜度取簇内最早出现的成员；半衰期取该成员所属信息源类型
        earliest = min(members, key=lambda m: str(m.get("first_seen") or "9999"))
        half_life = cfg["half_life_hours"].get(earliest.get("source_kind") or "",
                                               cfg["half_life_hours"].get("default", 12))
        fr = freshness(earliest.get("first_seen"), now, float(half_life))
        sa = sum(source_affinity(sid, positives=pos_by_src, negatives=neg_by_src,
                                 learned_sources=learned_sources) for sid in src_ids) / max(1, len(src_ids))
        imp = importance(n_sources, best_rank, max_rise)
        pen = persistence_penalty(members, cfg["persistence_snapshots"])
        fat = max(fatigue_value(m.get("shown_count", 0), cfg["fatigue"]["shows"]) for m in members)
        comps = {
            "interest": round(inter, 4),
            "source_affinity": round(sa, 4),
            "freshness": fr,
            "importance": imp,
            "persistence_penalty": pen,
            "fatigue_penalty": fat,
        }
        if learned_weights:  # P3：权重已通过留出集验证且用户开启时，用学习到的线性权重排序
            score = learned_score(comps, learned_weights)
        else:
            score = (weights["interest"] * interest_factor * inter
                     + weights["source_affinity"] * sa
                     + weights["freshness"] * fr
                     + weights["importance"] * imp
                     - weights["persistence_penalty"] * pen
                     - weights.get("fatigue_penalty", 0.0) * fat)
        rep = _representative(members)
        cards.append(_cluster_card(rep, members, names, p, comps, round(score, 4), best_rank, max_rise, now))

    sections = _assign_sections(cards, cfg)
    sections["later"] = later_list
    impressions = _impression_order(sections, cfg["others_limit"])
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "since": merged_res.get("since"),
        "window_hours": hours,
        "stats": stats,
        "sections": sections,
        "impressions": impressions,
        "others_limit": cfg["others_limit"],
        "scoring": "learned" if learned_weights else "hand",
        "source_states": merged_res.get("sources", {}),
    }


def _representative(members: list[dict[str, Any]]) -> dict[str, Any]:
    """簇的展示成员：优先有摘要的，其次标题更短的（短标题通常更干净），再按首次出现时间。"""
    return min(members, key=lambda m: (0 if m.get("summary") else 1, len(m.get("title") or ""),
                                       str(m.get("first_seen") or "")))


def _plain_card(row: dict[str, Any], now: datetime) -> dict[str, Any]:
    return {
        "key": row["item_key"], "title": row.get("title") or "（无标题）", "url": row.get("url") or "",
        "summary": row.get("summary") or "", "source_id": row.get("source_id") or "",
        "source_name": row.get("source_name") or "", "first_seen": row.get("first_seen"),
        "later_time": row.get("later_time", ""), "age": age_text(now, row.get("first_seen")),
        "sources": [{"source_id": row.get("source_id") or "", "name": row.get("source_name") or "",
                     "title": row.get("title") or "", "url": row.get("url") or "",
                     "first_seen": row.get("first_seen"), "is_new": bool(row.get("is_new")),
                     "item_key": row["item_key"]}],
        "n_sources": 1, "score": None, "components": {}, "matches": [],
        "reason": "已加入稍后看", "section": "later", "processed": bool(row.get("processed")),
        "processed_by": row.get("processed_by", ""), "is_new": bool(row.get("is_new")),
    }


def _cluster_card(rep: dict[str, Any], members: list[dict[str, Any]], names: dict[str, str],
                  p: dict[str, Any], comps: dict[str, Any], score: float, best_rank: Optional[int],
                  max_rise: int, now: datetime) -> dict[str, Any]:
    earliest = min(members, key=lambda m: str(m.get("first_seen") or "9999"))
    src_ids = list(dict.fromkeys(m.get("source_id") for m in sorted(members, key=lambda m: str(m.get("first_seen") or ""))))
    n_sources = len(src_ids)
    matches = p["matches"] if p["interest_raw"] > 0 else []
    card = {
        "key": rep["item_key"],
        "title": rep.get("title") or "（无标题）",
        "url": rep.get("url") or "",
        "summary": rep.get("summary") or "",
        "source_id": rep.get("source_id") or "",
        "source_name": rep.get("source_name") or "",
        "first_seen": earliest.get("first_seen"),
        "age": age_text(now, earliest.get("first_seen")),
        "is_new": any(m.get("is_new") for m in members),
        "rank": best_rank,
        "rise": max_rise,
        "n_sources": n_sources,
        "sources": [{"source_id": m.get("source_id"), "name": names.get(m.get("source_id"), m.get("source_id")),
                     "title": m.get("title") or "", "url": m.get("url") or "",
                     "first_seen": m.get("first_seen"), "is_new": bool(m.get("is_new")),
                     "item_key": item_key(m.get("url") or "", m.get("title") or "")}
                    for m in sorted(members, key=lambda m: str(m.get("first_seen") or ""))],
        "score": score,
        "components": comps,
        "matches": matches,
        "processed": all(m.get("processed") for m in members),
        "processed_by": rep.get("processed_by", ""),
        "section": "",
    }
    card["reason"] = build_reason(card)
    return card


def build_reason(card: dict[str, Any]) -> str:
    """一句话理由：命中关注主题 · 多源报道 · 出现时间 · 排名上升 · 一直在榜。缺省时给出保底说法。"""
    parts: list[str] = []
    if card.get("matches"):
        parts.append("命中关注主题『" + "、".join(card["matches"][:2]) + "』")
    if card.get("n_sources", 1) >= 2:
        parts.append(f"{card['n_sources']} 个来源报道")
    if card.get("age"):
        parts.append(card["age"])
    if (card.get("rise") or 0) >= 1:
        parts.append(f"排名上升 {card['rise']} 位")
    if (card.get("components") or {}).get("persistence_penalty", 0) > 0:
        parts.append("一直在榜，已降权")
    return " · ".join(parts) if parts else "综合排序靠前"


def _pick_featured(ranked: list[dict[str, Any]], n: int, source_cap: int, topic_penalty: float) -> list[dict[str, Any]]:
    """精选的贪心多样性挑选（MMR 思路）：每次选“扣掉同主题重复惩罚后”分最高的；同一信息源不超过 source_cap。"""
    chosen: list[dict[str, Any]] = []
    src_count: dict[str, int] = {}
    topic_count: dict[str, int] = {}
    pool = list(ranked)
    while pool and len(chosen) < n:
        best, best_val = None, None
        for c in pool:
            src = c.get("source_id") or ""
            if source_cap > 0 and src_count.get(src, 0) >= source_cap:
                continue
            topic = c["matches"][0] if c.get("matches") else ""
            val = c["score"] - (topic_penalty * topic_count.get(topic, 0) if topic else 0.0)
            if best_val is None or val > best_val:
                best, best_val = c, val
        if best is None:
            break
        chosen.append(best)
        pool.remove(best)
        src_count[best.get("source_id") or ""] = src_count.get(best.get("source_id") or "", 0) + 1
        topic = best["matches"][0] if best.get("matches") else ""
        if topic:
            topic_count[topic] = topic_count.get(topic, 0) + 1
    return chosen


def _explore_size(cfg: dict[str, Any]) -> int:
    """探索分区的条数：直接取配置的 explore_size（0 即关闭）。实际条数还受候选数量限制。"""
    try:
        return max(0, int(cfg.get("explore_size", 0)))
    except (TypeError, ValueError):
        return 0


def _assign_sections(cards: list[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """每个簇只归入一个分区。挑选顺序：精选（带多样性）→ 探索 → 多源热点 → 关注主题 → 其余。"""
    ranked = sorted(cards, key=lambda c: c["score"], reverse=True)
    used: set[str] = set()
    div = cfg.get("diversity") or {}

    featured = _pick_featured(ranked, cfg["featured_size"], int(div.get("source_cap", 3)),
                              float(div.get("topic_repeat_penalty", 0.1)))
    for c in featured:
        c["section"] = "featured"
        used.add(c["key"])

    # 探索位：画像之外（没有命中关注主题）但重要且新鲜的簇；标注为“探索”，防止回音室
    rest = [c for c in ranked if c["key"] not in used]
    # 探索不抢占“多源热点”的条目：多源事件留给热点分区
    explore = sorted((c for c in rest if not c["matches"] and c["n_sources"] < cfg["hot_min_sources"]),
                     key=lambda c: c["components"].get("importance", 0) * c["components"].get("freshness", 0),
                     reverse=True)[:_explore_size(cfg)]
    for c in explore:
        c["section"] = "explore"
        used.add(c["key"])

    rest = [c for c in ranked if c["key"] not in used]
    hot_pool = sorted((c for c in rest if c["n_sources"] >= cfg["hot_min_sources"]),
                      key=lambda c: (c["n_sources"], c["score"]), reverse=True)[:cfg["hot_size"]]
    for c in hot_pool:
        c["section"] = "hot"
        used.add(c["key"])

    rest = [c for c in ranked if c["key"] not in used]
    topic_of: dict[str, list[dict[str, Any]]] = {}
    for c in rest:
        if c["matches"]:
            topic_of.setdefault(c["matches"][0], []).append(c)
    eligible = [(t, cs) for t, cs in topic_of.items() if len(cs) >= cfg["topic_min_group"]]
    eligible.sort(key=lambda tc: (len(tc[1]), max(c["score"] for c in tc[1])), reverse=True)
    topics: list[dict[str, Any]] = []
    for topic, cs in eligible[:cfg["topic_groups"]]:
        chosen = sorted(cs, key=lambda c: c["score"], reverse=True)[:cfg["topic_group_size"]]
        for c in chosen:
            c["section"] = "topics"
            used.add(c["key"])
        topics.append({"topic": topic, "cards": chosen})

    others = [c for c in ranked if c["key"] not in used]
    for c in others:
        c["section"] = "others"
    return {"featured": featured, "explore": explore, "hot": hot_pool, "topics": topics, "others": others}


def _impression_order(sections: dict[str, Any], others_limit: int) -> list[dict[str, Any]]:
    """按屏幕上的先后顺序给出位置：精选 → 多源热点 → 关注主题 → 探索 → 其余（只计入默认展示的前 others_limit 条）。"""
    order: list[dict[str, Any]] = []

    def add(card: dict[str, Any], section: str) -> None:
        order.append({"item_key": card["key"], "position": len(order), "section": section,
                      "score": card.get("score"), "components": card.get("components") or {},
                      "first_seen": card.get("first_seen")})

    for c in sections["featured"]:
        add(c, "featured")
    for c in sections["hot"]:
        add(c, "hot")
    for group in sections["topics"]:
        for c in group["cards"]:
            add(c, "topics")
    for c in sections["explore"]:
        add(c, "explore")
    for c in sections["others"][:others_limit]:
        add(c, "others")
    return order


# ---------- 曝光记录 ----------
def shown_path(data_dir: Path) -> Path:
    return Path(data_dir) / "feed" / "shown.jsonl"


def record_impressions(data_dir: Path, *, window_hours: float, impressions: list[dict[str, Any]],
                       now: Optional[datetime] = None) -> None:
    """每次展示一批结果写一行：时间、视图 feed_v1、时间窗、各条目的位置、分区与分量。append-only。"""
    row = {
        "kind": "feed_impression",
        "time": (now or datetime.now().astimezone()).isoformat(timespec="seconds"),
        "view": VIEW,
        "window_hours": window_hours,
        "items": [dict(x) for x in impressions[:200]],
    }
    path = shown_path(data_dir)
    with file_lock(path):
        append_jsonl(path, row)


def public_config_summary(cfg: dict[str, Any]) -> dict[str, Any]:
    """供看板展示当前生效的关键参数（只读）。"""
    keys = ("featured_size", "hot_min_sources", "hot_size", "topic_groups", "topic_group_size",
            "cluster_threshold", "cold_start_factor", "persistence_snapshots")
    out = {k: cfg[k] for k in keys}
    out["weights"] = dict(cfg["weights"])
    return out


__all__ = [
    "VIEW", "DEFAULT_CONFIG", "DISPLAY_LIMITS", "load_config", "apply_display", "clamp_display", "bigrams", "jaccard", "cluster_items", "freshness",
    "importance", "persistence_penalty", "quantile_normalize", "source_affinity", "build_feed",
    "build_reason", "record_impressions", "shown_path", "public_config_summary",
]
