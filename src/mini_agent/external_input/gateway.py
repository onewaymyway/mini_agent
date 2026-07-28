"""external_input/gateway.py — 把 ExternalInputEvent 接入 system_events（P1）

本模块目前只做 §3.3 里"归一化事件 → system_events.publish()"这一小步，
即设计文档路线图里的 P1（GatewayPoller 本体、退避熔断、sources.yaml 加载
是 P2 的范围，不在本文件）。

不新造持久化格式：external.* 事件和其余事件共用同一份 events.jsonl，
poll_since() 的游标消费模型直接复用（§3.3）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from mini_agent.external_input.source import ExternalInputEvent
from mini_agent.perception import system_events

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths

# 网关兜底去重的窗口大小：只记住最近 N 个已发布的 event.id，避免同一进程内
# 短时间重复调用 publish_event() 时把同一条事件写两遍。这是"兜底"，不是
# 权威去重——来源自己的语义去重（比如 RSS 条目是否已见过）才是主要防线，
# 见 source.py 里 ExternalInputSource.poll() 的约束 3。
_DEDUP_WINDOW = 500


class _RecentIdCache:
    """极简的 FIFO 去重缓存，只在单个 GatewayPoller 进程生命周期内有效。
    跨进程/重启后的去重完全依赖来源自己在 state 里维护的游标，本缓存
    只是同一进程内"防止手滑重复发布"的最后一道保险。"""

    def __init__(self, maxlen: int = _DEDUP_WINDOW) -> None:
        self._maxlen = maxlen
        self._seen: dict[str, None] = {}

    def seen(self, key: str) -> bool:
        return key in self._seen

    def add(self, key: str) -> None:
        self._seen[key] = None
        while len(self._seen) > self._maxlen:
            # dict 保持插入顺序，popitem(last=False) 等价的写法：
            oldest = next(iter(self._seen))
            del self._seen[oldest]


_dedup_cache = _RecentIdCache()


def publish_event(paths: "AgentPaths", event: ExternalInputEvent) -> bool:
    """把一个 ExternalInputEvent 发布到 system_events。

    Returns:
        True 表示确实发布了一条新事件；False 表示因为兜底去重命中而跳过
        （不是错误，调用方通常不需要特别处理）。
    """
    dedup_key = f"{event.source_id}:{event.id}"
    if _dedup_cache.seen(dedup_key):
        return False

    system_events.publish(
        paths,
        source=f"external:{event.source_id}",
        event_type=event.event_type(),
        tier=event.suggested_tier,
        payload=event.to_payload(),
    )
    _dedup_cache.add(dedup_key)
    return True


def publish_events(paths: "AgentPaths", events: Iterable[ExternalInputEvent]) -> int:
    """批量发布，返回实际发布成功（未被去重跳过）的条数，供 poller 侧
    统计"这一轮轮询产生了几条新事件"用于日志/健康度展示。"""
    count = 0
    for evt in events:
        if publish_event(paths, evt):
            count += 1
    return count


def poll_external_events(
    paths: "AgentPaths",
    *,
    consumer_name: str,
    event_types: list[str] | None = None,
    advance_cursor: bool = True,
) -> list[ExternalInputEvent]:
    """消费 external.* 事件的便捷封装：poll_since() 本身只支持精确匹配
    event_type（见 system_events.py::poll_since 的实现，不支持 glob），
    这里统一按 "external." 前缀做过滤，供 IngestionPolicy（P3）等消费者
    直接拿到 ExternalInputEvent 对象而不是原始 SystemEvent。

    event_types 传入的是完整 event_type（如 "external.watch.new_episode"）
    做精确子集过滤；不传则返回所有 external.* 事件。
    """
    raw = system_events.poll_since(
        paths,
        consumer_name=consumer_name,
        advance_cursor=advance_cursor,
    )
    result: list[ExternalInputEvent] = []
    for evt in raw:
        if not evt.event_type.startswith("external."):
            continue
        if event_types is not None and evt.event_type not in event_types:
            continue
        try:
            result.append(ExternalInputEvent.from_payload(evt.payload))
        except Exception:
            # payload 结构异常（理论上不应该发生，因为都是 publish_event()
            # 写入的）时跳过这一条，不让个别脏数据拖垮整个消费流程。
            continue
    return result
