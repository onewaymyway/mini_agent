"""external_input/builtin/watch.py — WatchInputSource（P4）

设计背景见 next_doc/external_input_gateway_design.md §3.2、§7 P4。

这是 External Input Gateway 的第一个内置 `ExternalInputSource` 实现：把
"实时外部事件监控"（RSS / JSON API / 网页文本 diff 抓取 + 规则匹配）整体
下沉为网关的一个 source，网关本身（poller.py/policy.py）完全不关心
"RSS 怎么解析""价格阈值怎么算"这类领域逻辑——这些都在本文件内部完成。

三种 fetcher，通过 `params["fetcher"]` 选择：

- ``rss``       抓取 RSS/Atom feed，比对 `state["seen_ids"]` 找出新条目
                （signal="new_item"），可选 `params["keywords"]` 做标题
                关键词前置过滤。
- ``json_api``  抓取 JSON API，用 `params["field_path"]`（点号路径，如
                "data.price"）取出一个标量字段，跟上次轮询的值比较：
                  - `params["mode"] == "threshold"`：数值跟
                    `params["threshold"]` 按 `params["op"]`
                    （gt/gte/lt/lte/eq）比较，命中即发一条
                    signal="threshold" 事件（同一次"命中"状态内只发一次，
                    避免连续多轮重复告警）；
                  - 否则（默认）：字段值变化即发一条
                    signal="field_changed" 事件。
- ``html_diff`` 抓取网页、去标签取纯文本、算内容摘要（sha256），跟上次
                摘要不同即认为"页面变了"，可选 `params["keywords"]` 做
                关键词前置过滤，命中发一条 signal="page_changed" 事件。

硬约束（继承自 ExternalInputSource.poll() 的约束，见 source.py）：
本文件里的三个 `_poll_*` 方法都不调用 LLM，只做纯脚本抓取 + 规则判断；
跨轮询状态全部通过 `state` dict 传递，不使用实例属性。

配置要求：由于 GatewayPoller（poller.py，P2）调用 `source.poll(cfg.params,
state)` 时不会自动注入 `cfg.id`，本 source 生成的 `ExternalInputEvent.
source_id` 取自 `params["source_id"]`——sources.yaml 里配置这个 source
时，请让 `params.source_id` 与该条目的顶层 `id` 保持一致（示例见
`docs/external_input_watch_examples.yaml` 或本文件末尾的 sources.yaml
片段注释）。缺省时回退成空字符串，不会报错，但事件的
`source=f"external:{source_id}"` 标签会失去意义，仅建议用于快速试验。
"""

from __future__ import annotations

import hashlib
import re
import time
from typing import Any, Optional
from xml.etree import ElementTree as ET

from mini_agent.external_input.source import (
    ExternalInputEvent,
    ExternalInputSource,
    register_source,
)

_DEFAULT_TIMEOUT = 10
_DEFAULT_MAX_SEEN_IDS = 500

_THRESHOLD_OPS = {
    "gt": lambda v, t: v > t,
    "gte": lambda v, t: v >= t,
    "lt": lambda v, t: v < t,
    "lte": lambda v, t: v <= t,
    "eq": lambda v, t: v == t,
}


class WatchFetchError(RuntimeError):
    """抓取阶段失败（网络错误/HTTP 非 2xx/解析失败等）。直接向上抛给
    GatewayPoller，交由其统一的退避熔断处理（§3.3），本文件不重复实现
    重试逻辑。"""


# ── Fetchers：只负责"把外部世界的一份原始数据拿回来"，不做规则判断 ──────


def _http_get(url: str, timeout: float):
    try:
        import requests  # 项目已在 requirements.txt 声明依赖
    except ImportError as exc:  # pragma: no cover - 环境应始终已安装
        raise WatchFetchError("watch source 需要 requests 库") from exc
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
    except Exception as exc:
        raise WatchFetchError(f"抓取失败 {url!r}: {exc}") from exc
    return resp


def fetch_rss(url: str, timeout: float = _DEFAULT_TIMEOUT) -> list[dict]:
    """抓取 RSS 2.0 / Atom feed，归一化成 [{"id","title","link"}, ...]。

    用标准库 xml.etree 解析（项目未依赖 feedparser 之类的第三方库），
    RSS 用 <item>，Atom 用 <entry>，两者的子标签名不同（guid/id、
    link 是文本还是 href 属性）在这里统一抹平。
    """
    resp = _http_get(url, timeout)
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as exc:
        raise WatchFetchError(f"RSS/Atom 解析失败 {url!r}: {exc}") from exc

    items: list[dict] = []
    for elem in root.iter():
        tag = elem.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue
        entry_id: Optional[str] = None
        title = ""
        link = ""
        for child in elem:
            ctag = child.tag.rsplit("}", 1)[-1]
            if ctag in ("guid", "id"):
                entry_id = (child.text or "").strip() or entry_id
            elif ctag == "title":
                title = (child.text or "").strip()
            elif ctag == "link":
                link = (child.get("href") or child.text or "").strip() or link
        if not entry_id:
            entry_id = link or title
        if not entry_id:
            continue
        items.append({"id": entry_id, "title": title, "link": link})
    return items


def fetch_json_api(url: str, timeout: float = _DEFAULT_TIMEOUT) -> Any:
    """抓取 JSON API，返回解析后的 Python 对象。"""
    resp = _http_get(url, timeout)
    try:
        return resp.json()
    except Exception as exc:
        raise WatchFetchError(f"JSON 解析失败 {url!r}: {exc}") from exc


_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def fetch_html_text(url: str, timeout: float = _DEFAULT_TIMEOUT) -> str:
    """抓取网页并抽取出纯文本（去 script/style/标签、压缩空白），
    供 html_diff 模式做内容摘要比对。这是一个足够朴素的实现——目标是
    "页面主要内容有没有变"，不追求精确的可见文本还原。"""
    resp = _http_get(url, timeout)
    text = _TAG_RE.sub(" ", resp.text)
    text = _ANY_TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def get_by_path(data: Any, path: str) -> Any:
    """按点号路径（如 "data.price" 或 "items.0.value"）从嵌套的
    dict/list 结构里取值，取不到返回 None 而不是抛异常——字段路径配置
    错误应该表现成"没有变化"，而不是让整个 source 线程崩掉。"""
    cur = data
    for part in path.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


# ── RuleEngine：领域内的规则判断（新增/字段变化/关键词/阈值） ───────────


class RuleEngine:
    """§1.2/§3.2 里说的"新增/字段变化/关键词/阈值匹配"规则判断，全部
    是纯函数式的小工具方法，不持有状态（状态由调用方通过 state dict
    传递）。拆成独立类主要是为了让每种判断可以单独测试、未来也方便被
    其它 source 复用（不是 watch 专属的领域逻辑）。"""

    @staticmethod
    def find_new_items(items: list[dict], seen_ids: set[str]) -> list[dict]:
        return [it for it in items if it.get("id") and it["id"] not in seen_ids]

    @staticmethod
    def keyword_hits(text: str, keywords: list[str]) -> list[str]:
        text_low = (text or "").lower()
        return [kw for kw in keywords if kw and kw.lower() in text_low]

    @staticmethod
    def threshold_hit(value: Any, op: str, threshold: Any) -> bool:
        cmp = _THRESHOLD_OPS.get(op)
        if cmp is None or value is None or threshold is None:
            return False
        try:
            return bool(cmp(float(value), float(threshold)))
        except (TypeError, ValueError):
            return False


# ── WatchInputSource：ExternalInputSource 的第一个内置实现 ──────────────


@register_source("watch")
class WatchInputSource(ExternalInputSource):
    source_type = "watch"

    def poll(self, params: dict, state: dict) -> tuple[list[ExternalInputEvent], dict]:
        state = dict(state or {})
        fetcher = params.get("fetcher", "rss")
        if fetcher == "rss":
            return self._poll_rss(params, state)
        if fetcher == "json_api":
            return self._poll_json_api(params, state)
        if fetcher == "html_diff":
            return self._poll_html_diff(params, state)
        raise WatchFetchError(
            f"未知 fetcher: {fetcher!r}，必须是 rss/json_api/html_diff 之一"
        )

    # -- rss --------------------------------------------------------------

    def _poll_rss(self, params: dict, state: dict) -> tuple[list[ExternalInputEvent], dict]:
        url = params["url"]
        timeout = params.get("timeout", _DEFAULT_TIMEOUT)
        items = fetch_rss(url, timeout=timeout)

        seen_ids = set(state.get("seen_ids") or [])
        new_items = RuleEngine.find_new_items(items, seen_ids)

        keywords = params.get("keywords")
        source_id = params.get("source_id", "")
        events: list[ExternalInputEvent] = []
        for it in new_items:
            title = it.get("title", "")
            if keywords:
                if not RuleEngine.keyword_hits(title, keywords):
                    continue
            events.append(
                ExternalInputEvent(
                    id=it["id"],
                    source_id=source_id,
                    source_type="watch",
                    signal="new_item",
                    title=title or it["id"],
                    url=it.get("link") or None,
                    fields={"fetcher": "rss"},
                    suggested_tier=params.get("suggested_tier", "tick"),
                )
            )

        all_ids = list(seen_ids | {it["id"] for it in items if it.get("id")})
        max_seen = int(params.get("max_seen_ids", _DEFAULT_MAX_SEEN_IDS))
        if len(all_ids) > max_seen:
            all_ids = all_ids[-max_seen:]
        state["seen_ids"] = all_ids
        return events, state

    # -- json_api -----------------------------------------------------------

    def _poll_json_api(self, params: dict, state: dict) -> tuple[list[ExternalInputEvent], dict]:
        url = params["url"]
        timeout = params.get("timeout", _DEFAULT_TIMEOUT)
        path = params["field_path"]
        data = fetch_json_api(url, timeout=timeout)
        value = get_by_path(data, path)

        source_id = params.get("source_id", "")
        mode = params.get("mode", "field_change")
        events: list[ExternalInputEvent] = []

        if mode == "threshold":
            op = params.get("op", "lt")
            threshold = params.get("threshold")
            hit = RuleEngine.threshold_hit(value, op, threshold)
            already_hit = bool(state.get("threshold_hit"))
            if hit and not already_hit:
                events.append(
                    ExternalInputEvent(
                        id=f"{url}#{path}:{op}:{threshold}:{time.time()}",
                        source_id=source_id,
                        source_type="watch",
                        signal="threshold",
                        title=params.get("title", f"{path} {op} {threshold}"),
                        detail=f"当前值: {value}",
                        url=url,
                        fields={"value": value, "path": path, "op": op, "threshold": threshold},
                        suggested_tier=params.get("suggested_tier", "tick"),
                    )
                )
            state["threshold_hit"] = hit
        else:
            prev_value = state.get("last_value") if "last_value" in state else None
            has_prev = "last_value" in state
            if has_prev and value != prev_value:
                events.append(
                    ExternalInputEvent(
                        id=f"{url}#{path}:{value}:{time.time()}",
                        source_id=source_id,
                        source_type="watch",
                        signal="field_changed",
                        title=params.get("title", f"{path} 发生变化"),
                        detail=f"{prev_value!r} -> {value!r}",
                        url=url,
                        fields={"old": prev_value, "new": value, "path": path},
                        suggested_tier=params.get("suggested_tier", "tick"),
                    )
                )
            state["last_value"] = value

        return events, state

    # -- html_diff ------------------------------------------------------------

    def _poll_html_diff(self, params: dict, state: dict) -> tuple[list[ExternalInputEvent], dict]:
        url = params["url"]
        timeout = params.get("timeout", _DEFAULT_TIMEOUT)
        text = fetch_html_text(url, timeout=timeout)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

        source_id = params.get("source_id", "")
        prev_digest = state.get("digest")
        events: list[ExternalInputEvent] = []

        if prev_digest is not None and digest != prev_digest:
            keywords = params.get("keywords")
            hits: list[str] = []
            if keywords:
                hits = RuleEngine.keyword_hits(text, keywords)
                should_emit = bool(hits)
            else:
                should_emit = True
            if should_emit:
                events.append(
                    ExternalInputEvent(
                        id=f"{url}#{digest}",
                        source_id=source_id,
                        source_type="watch",
                        signal="page_changed",
                        title=params.get("title", "页面内容发生变化"),
                        url=url,
                        fields={"matched_keywords": hits},
                        suggested_tier=params.get("suggested_tier", "tick"),
                    )
                )

        state["digest"] = digest
        return events, state
