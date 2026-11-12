"""推荐信息流的 LLM 增强（P4，见 next_doc/info_hub_recommendation_feed_design.md 第 6 节）。

默认关闭。只有同时满足才会调用模型：① config/settings.yaml 的 feed.llm.enabled 为 true 或页面上开启；
② 已配置可用的模型（core/llm.py）；③ 用户在页面上点击“运行”（页面渲染不会自动调用）。

两类用途：
1. 摘要：为前列卡片（精选 / 多源热点 / 关注主题）生成主题标签与一句话摘要，显示在卡片上。
2. 补聚：对“差一点就能聚成同一事件”的近似对（字符二元组 Jaccard 落在 [pair_low, 聚合阈值) 之间），
   让模型判断是否为同一事件。结论只写入缓存；聚合时只使用缓存里已有的结论，页面渲染永不调用模型。

约束：
- 结果按条目 / 条目对缓存（data/feed/llm_cache.json），同一对象不重复调用；
- 每日调用上限（data/feed/llm_usage.json，按日期计数），达到上限即停止，并如实报告；
- 标题会发送给所配置的模型（隐私提示在页面上明确给出）。

提示词沿用 info_hub 现有做法：内联在模块中（与 core/profile.py 的 LLM 归纳一致）。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from core.feed import bigrams, jaccard, _representative
from core.store import atomic_write_json, file_lock, read_json

DEFAULT_LLM: dict[str, Any] = {
    "enabled": False,       # 配置层面的默认开关；页面开关（prefs.llm_enabled）另行控制
    "daily_limit": 40,      # 每日模型调用次数上限
    "batch_size": 8,        # 每次调用包含的条目 / 条目对数
    "top_n": 12,            # 摘要覆盖的前列卡片数
    "pair_enabled": True,   # 是否做补聚判断
    "pair_low": 0.25,       # 近似对的下限（Jaccard）
    "pair_max": 40,         # 每次最多判断的近似对数
}

SUMMARY_SYSTEM = "你是信息整理助手，只输出合法 JSON。"
PAIR_SYSTEM = "你是新闻事件判断助手，只输出合法 JSON。"


def llm_config(raw: Optional[dict[str, Any]]) -> dict[str, Any]:
    cfg = dict(DEFAULT_LLM)
    user = ((raw or {}).get("feed") or {}).get("llm") or {}
    if isinstance(user, dict):
        for k, v in user.items():
            if k in cfg and (isinstance(v, (int, float)) or isinstance(v, bool)):
                cfg[k] = v
    return cfg


# ---------- 存储：缓存与用量 ----------
def cache_path(data_dir: Path) -> Path:
    return Path(data_dir) / "feed" / "llm_cache.json"


def usage_path(data_dir: Path) -> Path:
    return Path(data_dir) / "feed" / "llm_usage.json"


def load_cache(data_dir: Path) -> dict[str, Any]:
    data = read_json(cache_path(data_dir), default=None)
    if not isinstance(data, dict):
        data = {}
    data.setdefault("items", {})
    data.setdefault("pairs", {})
    return data


def save_cache(data_dir: Path, cache: dict[str, Any]) -> None:
    path = cache_path(data_dir)
    with file_lock(path):
        atomic_write_json(path, cache)


def _day(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


def used_today(data_dir: Path, now: datetime) -> int:
    data = read_json(usage_path(data_dir), default=None)
    if not isinstance(data, dict):
        return 0
    return int(data.get(_day(now), 0) or 0)


def charge(data_dir: Path, n: int, now: datetime) -> int:
    path = usage_path(data_dir)
    with file_lock(path):
        data = read_json(path, default=None)
        if not isinstance(data, dict):
            data = {}
        today = _day(now)
        data = {k: v for k, v in data.items() if k == today}  # 只保留当天，避免无限增长
        data[today] = int(data.get(today, 0)) + int(n)
        atomic_write_json(path, data)
        return data[today]


# ---------- 规划：哪些还没有结论 ----------
def pair_key(a: str, b: str) -> str:
    return "|".join(sorted([a, b]))


def _all_cards(sections: dict[str, Any]) -> list[dict[str, Any]]:
    out = list(sections.get("featured", [])) + list(sections.get("explore", [])) + list(sections.get("hot", []))
    for g in sections.get("topics", []):
        out += g["cards"]
    return out + list(sections.get("others", []))


def summary_targets(sections: dict[str, Any], cfg: dict[str, Any], cache: dict[str, Any]) -> list[dict[str, Any]]:
    """前 top_n 张卡片中，还没有摘要的那些（按展示顺序）。"""
    front = (list(sections.get("featured", [])) + list(sections.get("hot", []))
             + [c for g in sections.get("topics", []) for c in g["cards"]])[: int(cfg["top_n"])]
    return [c for c in front if c["key"] not in cache["items"]]


def near_pairs(cards: list[dict[str, Any]], cfg: dict[str, Any], threshold: float) -> list[tuple[dict, dict]]:
    """近似对：标题二元组 Jaccard 在 [pair_low, threshold) 之间的卡片对（最多 pair_max 对，按相似度降序）。"""
    grams = [(c, bigrams(c["title"])) for c in cards]
    scored = []
    for i in range(len(grams)):
        for j in range(i + 1, len(grams)):
            s = jaccard(grams[i][1], grams[j][1])
            if cfg["pair_low"] <= s < threshold:
                scored.append((s, grams[i][0], grams[j][0]))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [(a, b) for _, a, b in scored[: int(cfg["pair_max"])]]


def pending_pairs(pairs: list[tuple[dict, dict]], cache: dict[str, Any]) -> list[tuple[dict, dict]]:
    return [(a, b) for a, b in pairs if pair_key(a["key"], b["key"]) not in cache["pairs"]]


def plan(sections: dict[str, Any], cfg: dict[str, Any], cache: dict[str, Any], threshold: float,
         data_dir: Path, now: datetime) -> dict[str, Any]:
    """本次运行的预计：需要几次调用、今天还剩多少额度。不调用模型。"""
    targets = summary_targets(sections, cfg, cache)
    pairs = pending_pairs(near_pairs(_all_cards(sections), cfg, threshold), cache) if cfg["pair_enabled"] else []
    bs = max(1, int(cfg["batch_size"]))
    summary_calls = -(-len(targets) // bs) if targets else 0
    pair_calls = -(-len(pairs) // bs) if pairs else 0
    used = used_today(data_dir, now)
    return {
        "summary_targets": [c["key"] for c in targets],
        "pending_pairs": [(a["key"], b["key"]) for a, b in pairs],
        "calls_estimate": summary_calls + pair_calls,
        "used_today": used,
        "daily_limit": int(cfg["daily_limit"]),
        "remaining": max(0, int(cfg["daily_limit"]) - used),
    }


# ---------- 模型调用 ----------
def _extract_list(text: str) -> list[Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned)
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start < 0 or end <= start:
        raise ValueError("模型输出中没有 JSON 数组")
    data = json.loads(cleaned[start:end + 1])
    if not isinstance(data, list):
        raise ValueError("模型输出不是数组")
    return data


def _summary_prompt(batch: list[dict[str, Any]]) -> str:
    lines = []
    for i, c in enumerate(batch):
        srcs = "、".join(dict.fromkeys(s.get("name", "") for s in c.get("sources", []) if s.get("name")))
        lines.append(f"{i}. 标题：{c['title']}　来源：{srcs or c.get('source_name', '')}"
                     + (f"　摘要：{c['summary'][:120]}" if c.get("summary") else ""))
    return ("下面是一组新闻条目。请为每条给出一个 2~4 字的主题标签，以及一句不超过 40 字的中文摘要，"
            "只依据给出的标题与摘要，不要编造信息。\n" + "\n".join(lines) + "\n\n"
            "只输出一个 JSON 数组，不要任何其他文字，每个元素形如："
            '{"id": 0, "topic": "...", "summary": "..."}')


def _pair_prompt(batch: list[tuple[dict, dict]]) -> str:
    lines = [f"{i}. A：{a['title']}　|　B：{b['title']}" for i, (a, b) in enumerate(batch)]
    return ("下面每一行是两条新闻标题。判断 A 与 B 是否报道的是同一件具体事件（同一时间、同一主体、同一事情）。"
            "只有非常确定时才回答 true；同一主题但不同事件，回答 false。\n" + "\n".join(lines) + "\n\n"
            "只输出一个 JSON 数组，不要任何其他文字，每个元素形如："
            '{"id": 0, "same": true}')


def run(data_dir: Path, ask: Callable[..., str], sections: dict[str, Any], cfg: dict[str, Any],
        threshold: float, now: datetime) -> dict[str, Any]:
    """执行一轮：摘要 + 补聚判断，严格遵守每日上限。结果写入缓存。"""
    cache = load_cache(data_dir)
    report = {"summaries_done": 0, "pairs_done": 0, "calls": 0, "errors": [], "stopped_by_limit": False}
    budget = max(0, int(cfg["daily_limit"]) - used_today(data_dir, now))
    bs = max(1, int(cfg["batch_size"]))
    by_key = {c["key"]: c for c in _all_cards(sections)}

    def take() -> bool:
        nonlocal budget
        if budget <= 0:
            report["stopped_by_limit"] = True
            return False
        budget -= 1
        report["calls"] += 1
        charge(data_dir, 1, now)
        return True

    targets = summary_targets(sections, cfg, cache)
    for i in range(0, len(targets), bs):
        if not take():
            break
        batch = targets[i:i + bs]
        try:
            items = _extract_list(ask(_summary_prompt(batch), system=SUMMARY_SYSTEM))
        except Exception as exc:  # noqa: BLE001 - 单批失败不影响其余
            report["errors"].append(f"摘要批次失败：{exc}")
            continue
        for it in items:
            if not isinstance(it, dict) or not isinstance(it.get("id"), int) or not 0 <= it["id"] < len(batch):
                continue
            card = batch[it["id"]]
            cache["items"][card["key"]] = {
                "topic": str(it.get("topic") or "").strip()[:12],
                "summary": str(it.get("summary") or "").strip()[:80],
                "at": now.isoformat(timespec="seconds"),
            }
            report["summaries_done"] += 1
        save_cache(data_dir, cache)

    if cfg["pair_enabled"]:
        pairs = pending_pairs(near_pairs(_all_cards(sections), cfg, threshold), cache)
        for i in range(0, len(pairs), bs):
            if not take():
                break
            batch = pairs[i:i + bs]
            try:
                items = _extract_list(ask(_pair_prompt(batch), system=PAIR_SYSTEM))
            except Exception as exc:  # noqa: BLE001
                report["errors"].append(f"补聚批次失败：{exc}")
                continue
            for it in items:
                if not isinstance(it, dict) or not isinstance(it.get("id"), int) or not 0 <= it["id"] < len(batch):
                    continue
                a, b = batch[it["id"]]
                cache["pairs"][pair_key(a["key"], b["key"])] = {
                    "same": bool(it.get("same")), "at": now.isoformat(timespec="seconds")}
                report["pairs_done"] += 1
            save_cache(data_dir, cache)
    report["cached_items"] = len(cache["items"])
    report["cached_pairs"] = len(cache["pairs"])
    return report


# ---------- 聚合时使用（只读缓存，永不调用模型） ----------
def cached_merge_judge(data_dir: Path) -> Callable[[list[dict[str, Any]], list[list[int]]], list[list[int]]]:
    """返回一个合并函数：把缓存中判定为“同一事件”的簇合并起来。没有缓存结论的对保持不动。"""
    cache = load_cache(data_dir)
    same = {k for k, v in cache["pairs"].items() if v.get("same")}

    def merge(pool: list[dict[str, Any]], groups: list[list[int]]) -> list[list[int]]:
        if not same:
            return groups
        reps = [(gi, _representative([pool[i] for i in g])["item_key"]) for gi, g in enumerate(groups)]
        parent = list(range(len(groups)))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for x in range(len(reps)):
            for y in range(x + 1, len(reps)):
                if pair_key(reps[x][1], reps[y][1]) in same:
                    rx, ry = find(reps[x][0]), find(reps[y][0])
                    if rx != ry:
                        parent[max(rx, ry)] = min(rx, ry)
        merged: dict[int, list[int]] = {}
        for gi, g in enumerate(groups):
            merged.setdefault(find(gi), []).extend(g)
        return [sorted(v) for v in merged.values()]

    return merge


def attach_labels(sections: dict[str, Any], data_dir: Path) -> None:
    """把缓存中的主题与摘要挂到卡片上（只读，不调用模型）。"""
    items = load_cache(data_dir)["items"]
    for c in _all_cards(sections):
        hit = items.get(c["key"])
        if hit:
            c["llm_topic"] = hit.get("topic", "")
            c["llm_summary"] = hit.get("summary", "")
