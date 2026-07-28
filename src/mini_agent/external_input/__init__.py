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

P4（本阶段新增）范围：
  - builtin/watch.py  WatchInputSource（rss/json_api/html_diff 三种 fetcher
    + RuleEngine 新增/字段变化/关键词/阈值匹配），ExternalInputSource 的
    第一个内置实现；GatewayPoller 构造时自动尝试 import 完成注册

P5（本阶段新增）范围：
  - policy.py    goal_candidate 落点（写入 GoalBacklog，对齐
    soft_goal_deriver 的 needs_review 处理方式）+ enqueue_turn 落点
    （直接提交 InputQueue.enqueue(initiator="external", ...)）真正落地
  - evolution/autonomous_loop.py  `_tick_passive()` 新增
    `run_ingestion_policy_once()` 消费点，跟 attention_mismatch_push
    在同一处调用，不受 autonomy_level 限制（notify_only 默认档兜底，
    高成本的 enqueue_turn 仍需在 policies.yaml 显式配置才会触发）

尚未实现（后续阶段，见设计文档 §7 路线图）：
  - 看板"🔌 外部输入"面板（P6）
"""

from mini_agent.external_input.config import SourceConfig, load_sources_config
from mini_agent.external_input.gateway import (
    poll_external_events,
    publish_event,
    publish_events,
)
from mini_agent.external_input.poller import GatewayPoller
from mini_agent.external_input.policy import (
    EXTERNAL_GOAL_SOURCE,
    PolicyRule,
    PolicyRunSummary,
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
    "PolicyRunSummary",
    "load_policies",
    "decide_action",
    "list_pending_alerts",
    "acknowledge_alert",
    "run_ingestion_policy_once",
    "EXTERNAL_GOAL_SOURCE",
]
