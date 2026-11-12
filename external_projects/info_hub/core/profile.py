"""兴趣画像与排序（第二阶段）。

画像由两部分组成，存放在 data/profile/profile.json：
- explicit：用户手写的关注主题与屏蔽词，优先级最高，统计重算时不会被覆盖。
- learned：由反馈事件离线统计得到的关键词与来源权重，带时间衰减，可随时重算或清空。
- llm：可选，由大模型归纳的兴趣关键词、避开关键词与一句话摘要；LLM 不可用时画像仍可工作。

排序只影响看板展示顺序，不改变抓取结果与原始数据。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from core.feedback import ACTIONS, read_feedback
from core.store import atomic_write_json, file_lock, read_json

PROFILE_VERSION = 1
HALF_LIFE_DAYS = 30.0
ACTION_WEIGHT = {"interested": 1.0, "later": 0.5, "not_interested": -1.0}
MIN_ABS_SCORE = 0.3
TOP_N = 80
MAX_TOPICS = 50
MAX_TERM_CHARS = 30
LLM_EVENT_LIMIT = 80
LLM_TERM_CHARS = 20

# 中文高频虚词/泛词与英文停用词，避免产生无意义关键词
_STOP = {
    "我们", "你们", "他们", "这个", "那个", "一个", "没有", "可以", "什么", "怎么", "如何", "进行",
    "发布", "最新", "今天", "现在", "已经", "就是", "还是", "以及", "因为", "所以", "不是", "知道",
    "一下", "关于", "为什么", "的话", "一篇", "这些", "那些", "自己", "大家", "真的", "出来",
    "the", "and", "for", "with", "from", "this", "that", "are", "was", "you", "your", "how", "what",
}


class ProfileError(RuntimeError):
    """画像相关的可预期错误（例如 LLM 输出无法解析）。"""


def profile_path(data_dir: Path) -> Path:
    return Path(data_dir) / "profile" / "profile.json"


def empty_profile() -> dict[str, Any]:
    return {
        "version": PROFILE_VERSION,
        "updated_at": None,
        "explicit": {"topics": [], "exclude": []},
        "learned": {"keywords": {}, "sources": {}, "computed_from_events": 0},
        "llm": {},
    }


def load_profile(data_dir: Path) -> dict[str, Any]:
    data = read_json(profile_path(data_dir), default=None)
    base = empty_profile()
    if isinstance(data, dict):
        for k in base:
            if k in data:
                base[k] = data[k]
    return base


def save_profile(data_dir: Path, profile: dict[str, Any]) -> None:
    path = profile_path(data_dir)
    with file_lock(path):
        atomic_write_json(path, profile)


# ---------- 分词 ----------
def tokenize(text: str) -> set[str]:
    """英文按单词；中文取 2~4 字子串。不依赖分词库。"""
    text = (text or "").lower()
    tokens: set[str] = set()
    for m in re.finditer(r"[a-z0-9][a-z0-9+#._-]+", text):
        tokens.add(m.group())
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        # 2~4 字的连续子串都作为候选词，使“大模型”“异步编程”这类三四字词也能被学到
        for n in (2, 3, 4):
            for i in range(len(run) - n + 1):
                tokens.add(run[i:i + n])
    return {t for t in tokens if len(t) >= 2 and t not in _STOP}


def _event_time(event: dict[str, Any]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(event.get("time")))
    except (TypeError, ValueError):
        return None


# ---------- 离线统计 ----------
def learn_from_events(events: list[dict[str, Any]], now: Optional[datetime] = None) -> dict[str, Any]:
    """从反馈事件计算关键词与来源权重。越新的事件影响越大（半衰期 HALF_LIFE_DAYS 天）。"""
    now = now or datetime.now().astimezone()
    kw: dict[str, dict[str, float]] = {}
    src: dict[str, dict[str, float]] = {}

    def bump(table: dict, key: str, delta: float) -> None:
        cell = table.setdefault(key, {"score": 0.0, "evidence": 0})
        cell["score"] += delta
        cell["evidence"] += 1

    for e in events:
        action = e.get("action")
        if action not in ACTION_WEIGHT:
            continue
        when = _event_time(e)
        age_days = max(0.0, (now - when).total_seconds() / 86400) if when and when.tzinfo else 0.0
        decay = 0.5 ** (age_days / HALF_LIFE_DAYS)
        reasons = set(e.get("reasons") or [])
        w = ACTION_WEIGHT[action] * decay
        if action == "not_interested" and "off_topic" in reasons:
            w *= 1.5  # 明确“话题不符”时，关键词的负向信号加强
        for token in tokenize(e.get("title", "")):
            bump(kw, token, w)
        sid = e.get("source_id") or ""
        if sid:
            bump(src, sid, w)
            if action == "not_interested" and "source_dislike" in reasons:
                bump(src, sid, -1.0 * decay)

    def prune(table: dict) -> dict:
        kept = {k: v for k, v in table.items() if abs(v["score"]) >= MIN_ABS_SCORE}
        top = sorted(kept.items(), key=lambda kv: abs(kv[1]["score"]), reverse=True)[:TOP_N]
        return {k: {"score": round(v["score"], 3), "evidence": v["evidence"]} for k, v in top}

    return {"keywords": prune(kw), "sources": prune(src), "computed_from_events": len(events)}


def rebuild_learned(data_dir: Path, events: Optional[list[dict[str, Any]]] = None,
                    now: Optional[datetime] = None) -> dict[str, Any]:
    """重算 learned 部分，保留 explicit 与 llm。"""
    if events is None:
        events = read_feedback(data_dir)
    profile = load_profile(data_dir)
    profile["learned"] = learn_from_events(events, now)
    profile["updated_at"] = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    save_profile(data_dir, profile)
    return profile


def ensure_fresh(data_dir: Path) -> dict[str, Any]:
    """读取画像；若反馈事件数量已变化，先重算 learned（即“离线学习”的按需触发）。"""
    events = read_feedback(data_dir)
    profile = load_profile(data_dir)
    if profile["learned"].get("computed_from_events") != len(events):
        profile = rebuild_learned(data_dir, events)
    return profile


def clear_learned(data_dir: Path) -> dict[str, Any]:
    profile = load_profile(data_dir)
    profile["learned"] = {"keywords": {}, "sources": {}, "computed_from_events": 0}
    profile["llm"] = {}
    profile["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    save_profile(data_dir, profile)
    return profile


# ---------- 显式偏好 ----------
def _clean_terms(values: Any) -> list[str]:
    if isinstance(values, str):
        values = re.split(r"[，,、\n]", values)
    if not isinstance(values, list):
        raise ProfileError("偏好必须是列表或逗号分隔的文本")
    out: list[str] = []
    for v in values:
        term = str(v).strip()[:MAX_TERM_CHARS]
        if term and term not in out:
            out.append(term)
    return out[:MAX_TOPICS]


def save_explicit(data_dir: Path, topics: Any, exclude: Any) -> dict[str, Any]:
    profile = load_profile(data_dir)
    profile["explicit"] = {"topics": _clean_terms(topics), "exclude": _clean_terms(exclude)}
    profile["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    save_profile(data_dir, profile)
    return profile


# ---------- LLM 归纳 ----------
def _extract_json(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ProfileError("模型输出中没有 JSON 对象")
    try:
        data = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ProfileError(f"模型输出不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise ProfileError("模型输出不是 JSON 对象")
    return data


def _short_terms(values: Any, limit: int) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for v in values:
        term = str(v).strip()[:LLM_TERM_CHARS]
        if term and term not in out:
            out.append(term)
    return out[:limit]


def llm_summarize(data_dir: Path, ask: Callable[..., str], events: list[dict[str, Any]],
                  label: str = "") -> dict[str, Any]:
    """让 LLM 根据最近反馈归纳兴趣。ask(prompt, system=...) -> str。成功后写入 profile.llm。"""
    profile = load_profile(data_dir)
    recent = sorted(events, key=lambda e: str(e.get("time", "")), reverse=True)[:LLM_EVENT_LIMIT]
    if not recent:
        raise ProfileError("还没有反馈记录，无法归纳")
    lines = []
    for e in recent:
        reasons = "、".join(r for r in (e.get("reasons") or []))
        lines.append(f"- [{ACTIONS.get(e.get('action'), e.get('action'))}] {e.get('title', '')}"
                     + (f"（原因：{reasons}）" if reasons else ""))
    explicit = profile["explicit"]
    prompt = (
        "下面是一位用户对信息条目的反馈（感兴趣 / 不感兴趣 / 稍后看）。请归纳他的兴趣。\n"
        f"用户手写的关注主题：{'、'.join(explicit['topics']) or '（无）'}\n"
        f"用户手写的屏蔽词：{'、'.join(explicit['exclude']) or '（无）'}\n"
        "反馈记录：\n" + "\n".join(lines) + "\n\n"
        "只输出一个 JSON 对象，不要任何其他文字，结构如下：\n"
        '{"interest_keywords": ["..."], "avoid_keywords": ["..."], "summary": "一两句中文描述"}\n'
        f"interest_keywords 最多 15 个、avoid_keywords 最多 10 个，每个不超过 {LLM_TERM_CHARS} 个字符，"
        "只写具体主题词，不要写“新闻”“资讯”这类泛词。"
    )
    system = "你是信息偏好分析助手，只输出合法 JSON。"
    data = _extract_json(ask(prompt, system=system))
    profile["llm"] = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "provider": label,
        "keywords": _short_terms(data.get("interest_keywords"), 15),
        "avoid": _short_terms(data.get("avoid_keywords"), 10),
        "summary": str(data.get("summary") or "").strip()[:300],
    }
    profile["updated_at"] = profile["llm"]["generated_at"]
    save_profile(data_dir, profile)
    return profile


# ---------- 排序 ----------
def score_item(profile: dict[str, Any], title: str, summary: str = "", source_id: str = "") -> tuple[float, list[str]]:
    text = f"{title} {summary}"
    low = text.lower()
    score = 0.0
    matches: list[tuple[float, str]] = []

    kw = profile.get("learned", {}).get("keywords", {})
    for token in tokenize(text):
        cell = kw.get(token)
        if cell:
            score += cell["score"]
            if cell["score"] > 0:
                matches.append((cell["score"], token))

    explicit = profile.get("explicit", {})
    for topic in explicit.get("topics", []):
        if topic.lower() in low:
            score += 1.5
            matches.append((1.5, topic))
    for term in explicit.get("exclude", []):
        if term.lower() in low:
            score -= 3.0

    llm = profile.get("llm") or {}
    for term in llm.get("keywords", []):
        if term.lower() in low:
            score += 0.8
            matches.append((0.8, term))
    for term in llm.get("avoid", []):
        if term.lower() in low:
            score -= 0.8

    if source_id:
        score += 0.5 * profile.get("learned", {}).get("sources", {}).get(source_id, {}).get("score", 0.0)

    # 去重后按权重取前 5 个作为“匹配”说明
    seen, top = set(), []
    for w, term in sorted(matches, reverse=True):
        if term not in seen:
            seen.add(term)
            top.append(term)
    return round(score, 3), top[:5]


def rank_items(profile: dict[str, Any], items: list[dict[str, Any]], source_id: str,
               last_actions: dict[str, str]) -> list[dict[str, Any]]:
    """对快照条目排序。最近一次反馈为“不感兴趣”的条目沉到最后。返回新列表，不修改输入。"""
    from core.feedback import item_key

    ranked = []
    for it in items:
        key = item_key(it.get("url", ""), it.get("title", ""))
        last = last_actions.get(key, "")
        score, matches = score_item(profile, it.get("title", "") or "", it.get("summary", "") or "", source_id)
        if last == "not_interested":
            score = -1000.0
        row = dict(it)
        row.update({"score": score, "matches": matches, "feedback": last})
        ranked.append(row)
    ranked.sort(key=lambda r: r["score"], reverse=True)
    return ranked


def last_actions(events: list[dict[str, Any]]) -> dict[str, str]:
    """每个条目最近一次反馈动作（events 按时间正序读取，后者覆盖前者）。"""
    out: dict[str, str] = {}
    for e in sorted(events, key=lambda e: str(e.get("time", ""))):
        out[e.get("item_key", "")] = e.get("action", "")
    return out


def llm_enabled(root: Path) -> bool:
    """config/settings.yaml 的 profile.llm_enabled；缺省为 True（仍需已配置模型才可用）。"""
    import yaml
    try:
        cfg = yaml.safe_load((Path(root) / "config" / "settings.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        cfg = {}
    value = (cfg.get("profile") or {}).get("llm_enabled", True)
    return bool(value)
