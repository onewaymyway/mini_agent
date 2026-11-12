"""推荐效果评估与候选采纳率（第四阶段）。

数据来源（均为 append-only 纯文本）：
- 曝光 impression：某次列表展示了哪些条目、用的是哪种排序（ranked / plain）。
- 打开 open：用户在看板上加载正文或展开内嵌页面，即一次“点击”。
- 反馈 feedback：感兴趣 / 不感兴趣 / 稍后看，记录了发生时的排序视图。
- 候选 candidates：被加入（accepted）或忽略（rejected）的发现候选。

指标只做统计与展示；候选策略的调整只在明确的阈值下自动发生，并在结果中说明原因，可通过配置关闭。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional

from core.feedback import feedback_path, read_feedback
from core.store import append_jsonl, file_lock, read_jsonl

DEFAULT_WINDOW_DAYS = 30
MIN_DECISIONS = 5
MIN_ADOPTION_RATE = 0.2


def exposure_path(data_dir: Path) -> Path:
    return Path(data_dir) / "feedback" / "exposures.jsonl"


def _now_iso(now: Optional[datetime] = None) -> str:
    return (now or datetime.now().astimezone()).isoformat(timespec="seconds")


def record_impression(data_dir: Path, *, source_id: str, snapshot_id: str, view: str,
                      item_keys: list[str], now: Optional[datetime] = None) -> None:
    if view not in ("ranked", "plain"):
        raise ValueError("view 必须是 ranked 或 plain")
    path = exposure_path(data_dir)
    with file_lock(path):
        append_jsonl(path, {"kind": "impression", "time": _now_iso(now), "source_id": source_id,
                            "snapshot_id": snapshot_id, "view": view,
                            "item_keys": list(dict.fromkeys(item_keys))[:60]})


def record_open(data_dir: Path, *, source_id: str, item_key: str, view: str, mode: str,
                now: Optional[datetime] = None) -> None:
    path = exposure_path(data_dir)
    with file_lock(path):
        append_jsonl(path, {"kind": "open", "time": _now_iso(now), "source_id": source_id,
                            "item_key": item_key, "view": view if view in ("ranked", "plain") else "",
                            "mode": mode})


MAX_DWELL_SECONDS = 3600


def record_dwell(data_dir: Path, *, source_id: str, item_key: str, view: str, seconds: int,
                 now: Optional[datetime] = None) -> None:
    """记录一次停留时长增量（插件在页面隐藏/关闭时发送，可多次累加）。"""
    seconds = int(seconds)
    if not 0 < seconds <= MAX_DWELL_SECONDS:
        raise ValueError(f"停留秒数需在 1–{MAX_DWELL_SECONDS} 之间")
    path = exposure_path(data_dir)
    with file_lock(path):
        append_jsonl(path, {"kind": "dwell", "time": _now_iso(now), "source_id": source_id,
                            "item_key": item_key, "view": view if view in ("ranked", "plain") else "",
                            "seconds": seconds})


def _in_window(time_str: Any, since: datetime) -> bool:
    try:
        return datetime.fromisoformat(str(time_str)) >= since
    except (TypeError, ValueError):
        return False


def _rate(num: int, den: int) -> Optional[float]:
    return round(num / den, 3) if den else None


def template_adoption(candidates: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """按模板统计采纳情况。"""
    out: dict[str, dict[str, Any]] = {}
    for c in candidates:
        tid = c.get("template_id", "")
        cell = out.setdefault(tid, {"name": c.get("template_name", tid), "generated": 0,
                                    "accepted": 0, "rejected": 0, "pending": 0})
        cell["generated"] += 1
        status = c.get("status", "pending")
        if status in ("accepted", "rejected", "pending"):
            cell[status] += 1
    for cell in out.values():
        decided = cell["accepted"] + cell["rejected"]
        cell["decided"] = decided
        cell["adoption_rate"] = _rate(cell["accepted"], decided)
    return out


def paused_templates(candidates: Iterable[dict[str, Any]], min_decisions: int = MIN_DECISIONS,
                     min_rate: float = MIN_ADOPTION_RATE) -> dict[str, str]:
    """采纳率持续偏低的模板 → 暂停生成的原因。只看已做出决定（加入/忽略）的候选。"""
    reasons: dict[str, str] = {}
    for tid, cell in template_adoption(candidates).items():
        if cell["decided"] >= min_decisions and cell["adoption_rate"] is not None and cell["adoption_rate"] < min_rate:
            reasons[tid] = (f"采纳率 {cell['adoption_rate']:.0%}（{cell['decided']} 次决定）低于 {min_rate:.0%}，"
                            "已暂停生成；可在 config/discovery.yaml 调整阈值或停用该模板")
    return reasons


def _view_stats(impressions: list[dict[str, Any]], opens: list[dict[str, Any]],
                feedback: list[dict[str, Any]], view: str,
                dwells: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    shown: set[tuple[str, str]] = set()
    for imp in impressions:
        if imp.get("view") == view:
            for k in imp.get("item_keys", []):
                shown.add((imp.get("snapshot_id", ""), k))
    shown_keys = {k for _, k in shown}
    opened = {o["item_key"] for o in opens if o.get("view") == view and o.get("item_key") in shown_keys}
    fb = [e for e in feedback if e.get("view") == view]
    n = len(fb)
    per_item: dict[str, int] = {}
    for d in dwells or []:
        if d.get("view") == view:
            per_item[d["item_key"]] = per_item.get(d["item_key"], 0) + int(d.get("seconds") or 0)
    avg_dwell = round(sum(per_item.values()) / len(per_item), 1) if per_item else None
    return {
        "avg_dwell_seconds": avg_dwell,
        "dwell_items": len(per_item),
        "impressions": len(shown),
        "opened_items": len(opened),
        "ctr": _rate(len(opened), len(shown_keys)),
        "feedback": n,
        "interested_rate": _rate(sum(1 for e in fb if e["action"] == "interested"), n),
        "not_interested_rate": _rate(sum(1 for e in fb if e["action"] == "not_interested"), n),
        "later_rate": _rate(sum(1 for e in fb if e["action"] == "later"), n),
    }


def compute_metrics(data_dir: Path, candidates: list[dict[str, Any]], *, days: int = DEFAULT_WINDOW_DAYS,
                    now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or datetime.now().astimezone()
    since = now - timedelta(days=days)
    exposures = [x for x in read_jsonl(exposure_path(data_dir)) if _in_window(x.get("time"), since)]
    impressions = [x for x in exposures if x.get("kind") == "impression"]
    opens = [x for x in exposures if x.get("kind") == "open"]
    dwells = [x for x in exposures if x.get("kind") == "dwell"]
    feedback = [e for e in read_feedback(data_dir) if _in_window(e.get("time"), since)]
    views = {v: _view_stats(impressions, opens, feedback, v, dwells) for v in ("ranked", "plain")}

    adoption = template_adoption(candidates)
    by_reason: dict[str, dict[str, int]] = {}
    for c in candidates:
        kind = "explicit" if str(c.get("reason", "")).startswith("显式") else "learned"
        cell = by_reason.setdefault(kind, {"accepted": 0, "rejected": 0})
        if c.get("status") in cell:
            cell[c["status"]] += 1
    reason_rates = {k: {**v, "adoption_rate": _rate(v["accepted"], v["accepted"] + v["rejected"])}
                    for k, v in by_reason.items()}
    return {
        "window_days": days,
        "views": views,
        "ranked_vs_plain": {
            "ctr_delta": _delta(views["ranked"]["ctr"], views["plain"]["ctr"]),
            "not_interested_delta": _delta(views["ranked"]["not_interested_rate"],
                                           views["plain"]["not_interested_rate"]),
            "dwell_delta": _delta(views["ranked"]["avg_dwell_seconds"], views["plain"]["avg_dwell_seconds"]),
        },
        "candidates": {"by_template": adoption, "by_reason": reason_rates,
                       "paused_templates": paused_templates(candidates)},
    }


def _delta(a: Optional[float], b: Optional[float]) -> Optional[float]:
    return round(a - b, 3) if a is not None and b is not None else None


def recommendations(metrics: dict[str, Any]) -> list[str]:
    """把指标转为可读建议（只给建议，不自动改动排序配置）。"""
    out: list[str] = []
    rv = metrics["views"]["ranked"]
    pv = metrics["views"]["plain"]
    if rv["feedback"] + pv["feedback"] < 10:
        out.append("反馈样本不足（不足 10 条），暂不足以比较排序效果，请继续积累反馈。")
    else:
        d = metrics["ranked_vs_plain"]
        if d["not_interested_delta"] is not None and d["not_interested_delta"] > 0.05:
            out.append("按兴趣排序后的“不感兴趣”比例反而更高，建议检查画像关键词是否误判，或暂时关闭排序。")
        elif d["not_interested_delta"] is not None and d["not_interested_delta"] < -0.05:
            out.append("按兴趣排序后“不感兴趣”比例明显下降，排序对当前的筛选有帮助，可考虑设为默认。")
        if d["ctr_delta"] is not None and d["ctr_delta"] > 0.05:
            out.append("按兴趣排序后点击率更高。")
        elif d["ctr_delta"] is not None and d["ctr_delta"] < -0.05:
            out.append("按兴趣排序后点击率更低，排序可能把用户不点的内容放到了前面。")
    for tid, reason in metrics["candidates"]["paused_templates"].items():
        out.append(f"模板 {tid}：{reason}")
    if not metrics["candidates"]["by_template"]:
        out.append("还没有生成过候选，暂无采纳率数据。")
    return out
