"""基于画像的信息源发现（第三阶段）。

流程：
  画像（显式主题 + 高权重关键词）→ 套用搜索模板（config/discovery.yaml，URL 含 {query}）
  → 生成候选信息源（默认停用）→ 候选收件箱 → 用户确认后才写入 sources/ 并启用。

- 只使用用户在 config/discovery.yaml 中配置的站内搜索模板，不调用任何外部搜索服务。
- 候选按天数量上限生成，待确认数量也有上限；被忽略的候选不会再次生成。
- 候选状态保存在 data/discovery/state.json。
"""
from __future__ import annotations

import copy
import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import quote

import yaml

from core.store import atomic_write_json, file_lock, read_json

DISCOVERY_CONFIG = "discovery.yaml"
DEFAULT_DAILY_CAP = 3
DEFAULT_PENDING_CAP = 10
MIN_QUERY_CHARS = 2
MAX_QUERY_CHARS = 30
QUERY_MIN_SCORE = 1.0
QUERY_MIN_EVIDENCE = 2
_TEMPLATE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,15}$")


class DiscoveryError(ValueError):
    pass


def state_path(data_dir: Path) -> Path:
    return Path(data_dir) / "discovery" / "state.json"


# ---------- 模板与上限配置 ----------
def load_config(root: Path) -> dict[str, Any]:
    path = Path(root) / "config" / DISCOVERY_CONFIG
    try:
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    limits = cfg.get("limits") or {}
    return {
        "templates": cfg.get("templates") or [],
        "daily_cap": int(limits.get("daily", DEFAULT_DAILY_CAP)),
        "pending_cap": int(limits.get("pending", DEFAULT_PENDING_CAP)),
    }


def load_templates(root: Path) -> list[dict[str, Any]]:
    """读取并校验搜索模板。不合法的模板抛出 DiscoveryError，避免静默生成坏候选。"""
    templates = load_config(root)["templates"]
    if not isinstance(templates, list):
        raise DiscoveryError("config/discovery.yaml 的 templates 必须是列表")
    seen: set[str] = set()
    out = []
    for t in templates:
        if not isinstance(t, dict):
            raise DiscoveryError("搜索模板必须是对象")
        tid = str(t.get("id", ""))
        if not _TEMPLATE_ID_RE.match(tid):
            raise DiscoveryError(f"模板 id 不合法：{tid or '（空）'}（小写字母开头，只含小写字母、数字、下划线）")
        if tid in seen:
            raise DiscoveryError(f"模板 id 重复：{tid}")
        seen.add(tid)
        source = t.get("source")
        if not isinstance(source, dict):
            raise DiscoveryError(f"模板 {tid} 缺少 source 配置")
        url = str((source.get("request") or {}).get("url", ""))
        if url.count("{query}") != 1:
            raise DiscoveryError(f"模板 {tid} 的 request.url 必须且只能包含一次 {{query}}")
        out.append({"id": tid, "name": str(t.get("name") or tid), "source": source})
    return out


# ---------- 查询词选择 ----------
def profile_queries(profile: dict[str, Any], limit: int = 5) -> list[tuple[str, str]]:
    """从画像选出用于搜索的查询词，返回 [(query, 原因说明)]。显式主题优先，其次是高权重学习关键词。"""
    explicit = profile.get("explicit", {})
    exclude = {t.lower() for t in explicit.get("exclude", [])}
    exclude |= {t.lower() for t in (profile.get("llm") or {}).get("avoid", [])}

    def ok(term: str) -> bool:
        return (MIN_QUERY_CHARS <= len(term) <= MAX_QUERY_CHARS
                and term.lower() not in exclude
                and not any(x in term.lower() for x in exclude))

    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for topic in explicit.get("topics", []):
        if ok(topic) and topic.lower() not in seen:
            seen.add(topic.lower())
            out.append((topic, "显式关注主题"))
    learned = profile.get("learned", {}).get("keywords", {})
    ranked = sorted(learned.items(), key=lambda kv: -kv[1]["score"])
    for term, cell in ranked:
        if cell["score"] < QUERY_MIN_SCORE or cell["evidence"] < QUERY_MIN_EVIDENCE:
            continue
        if ok(term) and term.lower() not in seen:
            seen.add(term.lower())
            out.append((term, f"学到的偏好（权重 {cell['score']}，{cell['evidence']} 次反馈）"))
    return out[:limit]


# ---------- 实例化 ----------
def candidate_id(template_id: str, query: str) -> str:
    digest = hashlib.sha1(f"{template_id}|{query}".encode("utf-8")).hexdigest()[:6]
    return f"disc_{template_id}_{digest}"


def instantiate(template: dict[str, Any], query: str) -> dict[str, Any]:
    source = copy.deepcopy(template["source"])
    source["request"] = dict(source.get("request") or {})
    source["request"]["url"] = source["request"]["url"].replace("{query}", quote(query, safe=""))
    source["id"] = candidate_id(template["id"], query)
    source["name"] = f"{template['name']}：{query}"
    source["enabled"] = False  # 候选默认停用，确认后才启用
    tags = list(source.get("tags") or [])
    if "发现" not in tags:
        tags.append("发现")
    source["tags"] = tags
    return source


# ---------- 候选状态 ----------
def load_state(data_dir: Path) -> dict[str, Any]:
    data = read_json(state_path(data_dir), default=None)
    if not isinstance(data, dict):
        data = {}
    data.setdefault("candidates", {})
    return data


def _save(data_dir: Path, state: dict[str, Any]) -> None:
    path = state_path(data_dir)
    with file_lock(path):
        atomic_write_json(path, state)


def list_candidates(data_dir: Path) -> list[dict[str, Any]]:
    rows = list(load_state(data_dir)["candidates"].values())
    return sorted(rows, key=lambda c: c.get("created_at", ""), reverse=True)


def generate(data_dir: Path, profile: dict[str, Any], templates: list[dict[str, Any]],
             validate: Callable[[dict[str, Any]], list[str]], *, daily_cap: int, pending_cap: int,
             now: Optional[datetime] = None) -> dict[str, Any]:
    """按画像生成候选。返回 {"added": [...], "skipped": {原因: 数量}}。"""
    now = now or datetime.now().astimezone()
    today = now.date().isoformat()
    state = load_state(data_dir)
    cands: dict[str, Any] = state["candidates"]
    pending = [c for c in cands.values() if c.get("status") == "pending"]
    today_count = sum(1 for c in cands.values() if str(c.get("created_at", "")).startswith(today))
    skipped: dict[str, int] = {}
    added: list[dict[str, Any]] = []

    if not templates:
        return {"added": [], "skipped": {"no_template": 1}}
    queries = profile_queries(profile)
    if not queries:
        return {"added": [], "skipped": {"no_query": 1}}

    for query, reason in queries:
        for tpl in templates:
            if len(pending) >= pending_cap or today_count >= daily_cap:
                break
            cid = candidate_id(tpl["id"], query)
            if cid in cands:  # 包括已忽略、已加入的，不重复生成
                skipped["exists"] = skipped.get("exists", 0) + 1
                continue
            source = instantiate(tpl, query)
            errors = validate(source)
            if errors:
                skipped["invalid"] = skipped.get("invalid", 0) + 1
                continue
            cand = {"id": cid, "template_id": tpl["id"], "template_name": tpl["name"], "query": query,
                    "reason": reason, "url": source["request"]["url"], "status": "pending",
                    "created_at": now.isoformat(timespec="seconds"), "decided_at": None, "source": source}
            cands[cid] = cand
            added.append(cand)
            pending.append(cand)
            today_count += 1
        if len(pending) >= pending_cap or today_count >= daily_cap:
            break
    if added:
        _save(data_dir, state)
    return {"added": added, "skipped": skipped}


def decide(data_dir: Path, cid: str, status: str, now: Optional[datetime] = None) -> dict[str, Any]:
    """标记候选为 accepted / rejected。accepted 的写入由调用方完成。"""
    if status not in ("accepted", "rejected"):
        raise DiscoveryError("未知状态")
    state = load_state(data_dir)
    cand = state["candidates"].get(cid)
    if cand is None:
        raise DiscoveryError("候选不存在")
    if cand["status"] != "pending":
        raise DiscoveryError(f"候选已处理（{cand['status']}）")
    cand["status"] = status
    cand["decided_at"] = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    _save(data_dir, state)
    return cand


def confirmed_source(cand: dict[str, Any]) -> dict[str, Any]:
    """用户确认后写入 sources/ 的内容：启用。"""
    return dict(cand["source"], enabled=True)
