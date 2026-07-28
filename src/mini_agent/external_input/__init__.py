"""external_input — External Input Gateway（外部输入网关）

设计文档：next_doc/external_input_gateway_design.md
实现进度：见该文档末尾"实现状态"一节。

P1（本阶段）范围：
  - source.py    ExternalInputEvent / ExternalInputSource / registry
  - gateway.py   把 ExternalInputEvent 接入 system_events.publish()

P2（本阶段新增）范围：
  - config.py    sources.yaml 加载（SourceConfig / load_sources_config）
  - poller.py    GatewayPoller 独立轮询调度线程 + 退避熔断 + 健康事件

P3（本阶段新增）范围：
  - policy.py    IngestionPolicy 路由（policies.yaml 加载 + 匹配 + notify_only 落地）
  - /v1/inbox 新增 external_alert 聚合（api/routes.py，见该文件改动）

尚未实现（后续阶段，见设计文档 §7 路线图）：
  - goal_candidate / enqueue_turn 落点的真正执行（P5，当前会被识别但跳过）
  - builtin/watch.py  WatchInputSource（P4）
"""

from mini_agent.external_input.config import SourceConfig, load_sources_config
from mini_agent.external_input.gateway import (
    poll_external_events,
    publish_event,
    publish_events,
)
from mini_agent.external_input.poller import GatewayPoller
from mini_agent.external_input.policy import (
    PolicyRule,
    acknowledge_alert,
    decide_action,
    list_pending_alerts,
    load_policies,
    run_ingestion_policy_once,
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
    "SourceConfig",
    "load_sources_config",
    "GatewayPoller",
    "PolicyRule",
    "load_policies",
    "decide_action",
    "list_pending_alerts",
    "acknowledge_alert",
    "run_ingestion_policy_once",
]
