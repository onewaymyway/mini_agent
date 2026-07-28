"""external_input/source.py — 外部输入来源的统一扩展点（P1）

设计背景见 next_doc/external_input_gateway_design.md §3.1。

这是 External Input Gateway 里"来源"这一层的全部内容：一个标准化的事件
表示（ExternalInputEvent）+ 一个来源必须实现的最小接口（ExternalInputSource）
+ 一个按 source_type 注册/查找实现类的 registry。

新增一种外部输入来源，只需要：

    from mini_agent.external_input.source import ExternalInputSource, register_source

    @register_source("my_source")
    class MySource(ExternalInputSource):
        source_type = "my_source"

        def poll(self, params, state):
            ...
            return events, new_state

不需要碰调度（poller.py，P2）、路由（policy.py，P3）这些通用逻辑。
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

# 合法 suggested_tier 取值，直接复用 system_events.py 的 tier 语义
# （instant | tick | cron），不新造一套分级词汇。
VALID_SUGGESTED_TIERS = frozenset({"instant", "tick", "cron"})


@dataclass
class ExternalInputEvent:
    """一次外部输入的标准化表示，供网关归一化发布 / 后续路由规则统一处理。

    字段设计对齐 §3.1：id 用于来源内部去重，source_id/source_type 用于定位
    是哪个具体来源实例产生的，signal 是具体信号名（供 IngestionPolicy 匹配），
    fields 是留给路由规则做结构化匹配的自由字段（比如 watch source 可以塞
    fields={"priority": "high"}）。
    """

    id: str
    source_id: str
    source_type: str
    signal: str
    title: str
    detail: str = ""
    url: Optional[str] = None
    fields: dict = field(default_factory=dict)
    occurred_at: float = field(default_factory=time.time)
    suggested_tier: str = "tick"

    def __post_init__(self) -> None:
        if self.suggested_tier not in VALID_SUGGESTED_TIERS:
            raise ValueError(
                f"非法 suggested_tier: {self.suggested_tier!r}，"
                f"必须是 {sorted(VALID_SUGGESTED_TIERS)} 之一"
            )

    def event_type(self) -> str:
        """映射到 system_events.py 里的 event_type 命名："external.<source_type>.<signal>"。"""
        return f"external.{self.source_type}.{self.signal}"

    def to_payload(self) -> dict:
        """发布到 system_events 时塞进 payload 的结构化内容（event_id/tier 等
        由 system_events.publish() 自己生成，不重复放进 payload）。"""
        return {
            "id": self.id,
            "source_id": self.source_id,
            "source_type": self.source_type,
            "signal": self.signal,
            "title": self.title,
            "detail": self.detail,
            "url": self.url,
            "fields": self.fields,
            "occurred_at": self.occurred_at,
        }

    @staticmethod
    def from_payload(payload: dict) -> "ExternalInputEvent":
        """从 system_events 里读回来的 payload 还原成 ExternalInputEvent，
        供路由决策 / 看板展示复用同一个数据结构，不用各处手写字典取值。"""
        return ExternalInputEvent(
            id=payload.get("id", ""),
            source_id=payload.get("source_id", ""),
            source_type=payload.get("source_type", ""),
            signal=payload.get("signal", ""),
            title=payload.get("title", ""),
            detail=payload.get("detail", ""),
            url=payload.get("url"),
            fields=payload.get("fields") or {},
            occurred_at=float(payload.get("occurred_at", 0.0)),
            # tier 校验在这里不适用（这是回填历史数据，可能是任何合法 tier），
            # 直接读 payload 里没有的话给个安全默认值。
            suggested_tier=payload.get("suggested_tier", "tick"),
        )


class ExternalInputSource(ABC):
    """所有外部输入来源的统一接口。

    子类只需要关心"怎么拿到信号"，不需要关心调度节奏（poller.py）、去重兜底、
    发布到事件总线、路由决策——这些都由网关统一处理（§3.1、§3.3）。

    硬约束（与 system_events.py 的三条硬约束一脉相承，不是可选项）：

    1. **禁止在 poll() 里调用 LLM/Agent**。保持轮询成本可控、可预测，
       "产生事件"和"消耗 LLM"之间必须隔着 IngestionPolicy 这层路由决策。
    2. **state 是唯一允许的跨轮询记忆**。不要在实例属性里存跨轮询状态——
       同一个 source 可能被网关以无状态的方式反复实例化/调用，只有
       poll() 返回的 new_state 会被网关落盘保存并在下次调用时传回。
    3. **只返回确实是新增/变化的事件**。语义去重（比如"这条 RSS 条目
       之前见过"）是来源自己的职责；网关只做兜底的 event.id 级去重，
       不重复实现来源特定的去重逻辑。
    """

    source_type: str

    @abstractmethod
    def poll(self, params: dict, state: dict) -> tuple[list[ExternalInputEvent], dict]:
        """单次轮询。

        Args:
            params: 该 source 实例的配置（来自 sources.yaml 里这个 source 的
                params 字段），比如 RSS 地址、关键词规则等来源特定配置。
            state: 上一次 poll() 返回的 state（首次调用为空字典 {}），
                用于去重游标 / ETag 等跨轮询增量状态。

        Returns:
            (events, new_state)：本次新增/变化的事件列表（可以为空列表，
            代表本次轮询没有新信号），以及需要网关落盘保存的新 state。
        """


# ── Registry：source_type -> 实现类 ─────────────────────────────────────

_REGISTRY: dict[str, type[ExternalInputSource]] = {}


def register_source(source_type: str):
    """装饰器：`@register_source("watch")` 把一个 ExternalInputSource 子类
    注册进全局 registry，供 GatewayPoller（P2）按配置里的 type 字符串找到
    对应实现类，不需要在网关代码里写死 import/if-elif 分支。"""

    def _wrap(cls: type[ExternalInputSource]) -> type[ExternalInputSource]:
        if not issubclass(cls, ExternalInputSource):
            raise TypeError(f"{cls!r} 不是 ExternalInputSource 的子类，无法注册")
        cls.source_type = source_type
        _REGISTRY[source_type] = cls
        return cls

    return _wrap


def get_source_class(source_type: str) -> type[ExternalInputSource]:
    """按注册名查找实现类；找不到时抛 KeyError（调用方通常是加载
    sources.yaml 配置的地方，应该在那一层给出"配置了未知 source type"
    这样明确的错误信息，而不是让 KeyError 原样冒泡）。"""
    try:
        return _REGISTRY[source_type]
    except KeyError as exc:
        raise KeyError(
            f"未注册的 ExternalInputSource 类型: {source_type!r}，"
            f"已注册的类型有: {sorted(_REGISTRY)}"
        ) from exc


def registered_source_types() -> list[str]:
    """列出当前已注册的所有 source_type，供看板"🔌 外部输入"面板（P6）
    和诊断命令展示可用来源。"""
    return sorted(_REGISTRY)


def _reset_registry_for_tests() -> None:
    """仅供测试使用：清空 registry，避免不同测试用例之间的注册状态互相污染。"""
    _REGISTRY.clear()
