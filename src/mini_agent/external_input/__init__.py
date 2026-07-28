"""external_input — External Input Gateway（外部输入网关）

设计文档：next_doc/external_input_gateway_design.md
实现进度：见该文档末尾"实现状态"一节。

P1（本阶段）范围：
  - source.py    ExternalInputEvent / ExternalInputSource / registry
  - gateway.py   把 ExternalInputEvent 接入 system_events.publish()

尚未实现（后续阶段，见设计文档 §7 路线图）：
  - poller.py    GatewayPoller 独立轮询调度 + 退避熔断（P2）
  - policy.py    IngestionPolicy 路由决策（P3）
  - config.py    sources.yaml / policies.yaml 加载（P2/P3）
  - builtin/watch.py  WatchInputSource（P4）
"""

from mini_agent.external_input.gateway import (
    poll_external_events,
    publish_event,
    publish_events,
)
from mini_agent.external_input.source import (
    ExternalInputEvent,
    ExternalInputSource,
    get_source_class,
    register_source,
    registered_source_types,
)

__all__ = [
    "ExternalInputEvent",
    "ExternalInputSource",
    "register_source",
    "get_source_class",
    "registered_source_types",
    "publish_event",
    "publish_events",
    "poll_external_events",
]
