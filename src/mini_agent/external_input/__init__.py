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
    ——**goal_candidate 已在 P8 移除，见下**
  - evolution/autonomous_loop.py  `_tick_passive()` 新增
    `run_ingestion_policy_once()` 消费点，跟 attention_mismatch_push
    在同一处调用，不受 autonomy_level 限制（notify_only 默认档兜底，
    高成本的 enqueue_turn 仍需在 policies.yaml 显式配置才会触发）

P6（本阶段新增）范围：
  - 看板"🔌 外部输入"面板

P7（本阶段新增）范围：
  - source.py/config.py  `channel` 分类字段（ExternalInputEvent.channel /
    SourceConfig.channel），事件在被 daemon（IngestionPolicy）处理之前
    先按频道归类，缺省即 source_type
  - policy.py    `channel` 新增为 PolicyRule 匹配维度；
    `group_events_by_channel()` + `run_ingestion_policy_once()` 按频道
    分组处理，`PolicyRunSummary.by_channel` 统计每个频道处理了多少条
  - builtin/weather.py  WeatherInputSource：第二个内置 source 实现，
    基于 Open-Meteo 免费预报 API 监控天气（降雨概率/极端温度阈值），
    channel 默认 "weather"

P8（本阶段新增，架构修正）范围：
  - policy.py    **移除 `goal_candidate` 落点**——外部输入不应该被直接
    变成一个新 Goal/Objective。与 `goal_relevance.py::GoalRelevanceEngine`
    职责重叠且语义冲突：后者已经独立订阅同一批事件，只把外部信号关联/
    挂载到*已存在*的 Goal 上（`attach_external_context()`/
    `try_advance_goal()`），从不凭空创建新 Goal。`IngestionPolicy` 收窄
    为 `notify_only`/`enqueue_turn` 两档，`run_ingestion_policy_once()`
    不再接受 `goal_backlog` 参数
  - scripts/cleanup_external_goal_candidates.py  一次性清理脚本，删除
    历史由 `goal_candidate` 创建的 Goal 节点（`source="external_input"`）
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
    PolicyRunSummary,
    acknowledge_alert,
    decide_action,
    group_events_by_channel,
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
    "group_events_by_channel",
]
