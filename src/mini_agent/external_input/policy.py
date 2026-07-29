"""external_input/policy.py — IngestionPolicy 路由决策（P3 + P5）

设计背景见 next_doc/external_input_gateway_design.md §3.4。

P3 范围：加载 policies.yaml、按事件类型/来源匹配路由规则、落地
`notify_only`（写入 alerts.jsonl，供 /v1/inbox 展示）。

P5 范围：`goal_candidate` / `enqueue_turn` 两种落点按 §3.4/§5 明确要求
"复用现有 GoalBacklog/ResourceArbiter/InputQueue"落地——网关本身不另造
一套"要不要执行"的判断逻辑：

  - `goal_candidate`：直接调用 `GoalBacklog.add_goal()`，source 打
    `"external_input"`，并像 `soft_goal_deriver.commit_goals()` 处理
    workthread/lesson 类候选一样打上 `needs_review` 标签——外部信号
    同样没有经过 ExplorationSandbox 验证，不应该假装已经验证过。写入
    后的 Goal 自然进入既有的 Goal→Objective 拆分、`ResourceArbiter`
    门控、`GoalBacklog.has_actionable_work()` 消费链路，本模块不重复
    实现这些。
  - `enqueue_turn`：直接调用
    `InputQueue.enqueue(message, initiator="external", meta={...})`，
    这是成本最高、默认关闭（需要在 `policies.yaml` 显式配置）的落点，
    语义与 `CronScheduler`/`next_action_advisor` 提交任务完全一致，
    提交后的任务像普通一轮对话一样正常消耗 LLM、正常受
    `ResourceArbiter`/预算等既有门控约束。

调用方在没有 GoalBacklog/InputQueue 时（比如测试或诊断脚本单独调用
`run_ingestion_policy_once(paths)`）：不传 `goal_backlog`/`input_queue`
参数，命中 `goal_candidate`/`enqueue_turn` 的事件依旧记一条"跳过"计数、
游标照常推进，不会静默丢事件也不会误当成 notify_only 处理掉——这是
刻意保留的行为，见 P3 阶段实现说明。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from mini_agent.external_input.gateway import poll_external_events
from mini_agent.external_input.source import ExternalInputEvent

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.perception.goal_backlog import GoalBacklog
    from mini_agent.api.bridge import InputQueue

# goal_candidate 落点的 GoalBacklog.add_goal(source=...) 取值，与
# soft_goal_deriver 的 "agent_derived" 区分开，方便看板/诊断区分
# "agent 自己 derive 的" 和 "外部信号触发的"。
EXTERNAL_GOAL_SOURCE = "external_input"

try:
    import yaml as _yaml  # type: ignore
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

# 事件没有命中任何规则时的默认落点——§8 明确要求"默认路径永远最省钱"，
# 不会因为漏配规则就意外把高频轮询放大成高频 LLM 调用。
DEFAULT_ACTION = "notify_only"

VALID_ACTIONS = frozenset({"notify_only", "goal_candidate", "enqueue_turn"})

# P5 完成后三种 action 均已落地；`goal_candidate`/`enqueue_turn` 若调用方
# 未提供 goal_backlog/input_queue，仍走"可见地跳过"路径（见模块 docstring），
# 不属于"未实现"，因此本常量目前只用于历史参照，不再驱动分支逻辑。
_IMPLEMENTED_ACTIONS = frozenset({"notify_only", "goal_candidate", "enqueue_turn"})

# poll_external_events() 用的固定 consumer 名，跟 soft_goal_deriver 等
# 其它 system_events 消费者一样各自持有独立游标。
POLICY_CONSUMER_NAME = "external_input_policy"


class PoliciesConfigError(Exception):
    """policies.yaml 存在但内容非法（YAML 语法错误 / 顶层结构不是预期形状 /
    某条规则的 action 不是合法取值）。跟 config.py 的 SourcesConfigError
    是同一类"配置格式错误应该显式报错，而不是静默吞掉"的处理原则。"""


@dataclass
class PolicyRule:
    """一条路由规则：匹配条件 + 落点动作。对应 policies.yaml 里的一条记录。"""

    match: dict = field(default_factory=dict)
    action: str = DEFAULT_ACTION
    enqueue: dict = field(default_factory=dict)  # 仅 enqueue_turn 用得到（P5）

    @staticmethod
    def from_dict(d: dict) -> "PolicyRule":
        action = str(d.get("action", DEFAULT_ACTION)).strip()
        if action not in VALID_ACTIONS:
            raise ValueError(
                f"非法 action: {action!r}，必须是 {sorted(VALID_ACTIONS)} 之一"
            )
        match = d.get("match") or {}
        if not isinstance(match, dict):
            raise ValueError(f"match 必须是字典: {d!r}")
        return PolicyRule(match=match, action=action, enqueue=dict(d.get("enqueue") or {}))

    def matches(self, event: ExternalInputEvent) -> bool:
        """按 §3.4 示例支持的匹配维度：source_type / signal 精确匹配，
        以及 "fields.<key>" 前缀对 event.fields 里对应字段做精确匹配。
        match 为空字典视为匹配所有事件（可以用来配一条兜底规则）。
        """
        for key, expected in self.match.items():
            if key == "source_type":
                if event.source_type != expected:
                    return False
            elif key == "signal":
                if event.signal != expected:
                    return False
            elif key == "channel":
                if event.channel != expected:
                    return False
            elif key.startswith("fields."):
                field_name = key[len("fields."):]
                if event.fields.get(field_name) != expected:
                    return False
            else:
                # 未识别的匹配维度：保守起见判定不匹配，而不是忽略这个
                # 条件当作"总是满足"——后者会让规则比配置者预期的更宽松。
                return False
        return True


def load_policies(paths: "AgentPaths") -> list[PolicyRule]:
    """读取 .agent/external_input/policies.yaml，返回 PolicyRule 列表。
    容错策略与 config.py::load_sources_config 一致：文件缺失/无 PyYAML
    → 空列表（此时所有事件走默认的 notify_only）；顶层结构错误 → 抛错；
    单条规则的 action 非法 → 跳过该条。"""
    config_path = paths.external_input_policies_config
    if not config_path.exists() or not _HAS_YAML:
        return []

    try:
        raw = _yaml.safe_load(config_path.read_text(encoding="utf-8")) or []
    except Exception as exc:
        raise PoliciesConfigError(f"policies.yaml 解析失败: {exc}") from exc

    if not isinstance(raw, list):
        raise PoliciesConfigError(
            f"policies.yaml 顶层结构应为规则列表，实际读到: {type(raw).__name__}"
        )

    rules: list[PolicyRule] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            rules.append(PolicyRule.from_dict(entry))
        except ValueError:
            continue
    return rules


def decide_action(event: ExternalInputEvent, rules: list[PolicyRule]) -> PolicyRule:
    """按顺序找第一条匹配的规则；都不匹配则返回默认的 notify_only 规则。
    规则顺序即优先级（跟 policies.yaml 里书写顺序一致，第一条命中的生效），
    与项目里其它"规则列表 + 首个匹配生效"的风格（如 reminders 触发条件）
    保持一致，不引入额外的优先级/权重概念。"""
    for rule in rules:
        if rule.matches(event):
            return rule
    return PolicyRule(match={}, action=DEFAULT_ACTION)


# ── notify_only 落点：写入 alerts.jsonl ─────────────────────────────────

def _load_existing_alert_ids(p) -> set:
    """[BUGFIX] 读取 alerts.jsonl 中已经出现过的 alert_id 集合，供
    `_notify_only()` 去重用。alert_id 是 `f"alert:{source_id}:{event_id}"`
    的确定性拼接，理论上同一个外部事件不应该被写入两次——但如果 source
    自身的增量游标/去重状态出现问题（比如某个 RSS 源的条目一直留在
    feed 里、而对应 source 的增量状态又被重置），`poll()` 可能会对
    同一个 event_id 反复产生 `ExternalInputEvent`，进而反复调用
    `_notify_only()`。之前这里没有任何去重，会导致 alerts.jsonl 里
    出现 alert_id 完全相同的多条记录——看板侧按 alert_id 当 Streamlit
    组件 key 使用，直接触发 `StreamlitDuplicateElementKey` 崩溃。
    这里在写入前做一次"曾经出现过就不再写"的去重，从根源上避免这个问题；
    跟 `list_pending_alerts()` 一样，全量扫描一次小文件可以接受
    （YAGNI，量级增长后再考虑索引/滚动归档）。"""
    if not p.exists():
        return set()
    ids: set = set()
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                alert_id = d.get("alert_id")
                if alert_id:
                    ids.add(alert_id)
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.external_input.policy._load_existing_alert_ids")
    return ids


def _append_alert(paths: "AgentPaths", alert: dict) -> None:
    p = paths.external_input_alerts
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(alert, ensure_ascii=False) + "\n")
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.external_input.policy._append_alert")


def _notify_only(paths: "AgentPaths", event: ExternalInputEvent) -> None:
    """把命中 notify_only 的事件落成一条 alert 记录（§3.4 落点 1）。
    `acknowledged` 字段供 /v1/inbox 的 ack 接口标记"已读/已处理"，未确认
    的记录才会出现在看板待办中心。

    [BUGFIX] 同一个 alert_id（`source_id` + `event_id` 确定性拼接）曾经
    写入过就直接跳过，不重复追加——见 `_load_existing_alert_ids()` 的
    说明。已经 acknowledged 的旧记录也算"曾经写入过"，不会因为用户点了
    "已读"之后同一事件又重新出现一遍。"""
    alert_id = f"alert:{event.source_id}:{event.id}"
    p = paths.external_input_alerts
    if alert_id in _load_existing_alert_ids(p):
        return
    alert = {
        "alert_id": alert_id,
        "event_id": event.id,
        "source_id": event.source_id,
        "source_type": event.source_type,
        "signal": event.signal,
        "title": event.title,
        "detail": event.detail,
        "url": event.url,
        "fields": event.fields,
        "occurred_at": event.occurred_at,
        "created_at": time.time(),
        "acknowledged": False,
    }
    _append_alert(paths, alert)


def list_pending_alerts(paths: "AgentPaths", limit: Optional[int] = None) -> list[dict]:
    """读取 alerts.jsonl 中尚未 acknowledged 的记录，供 /v1/inbox 聚合展示。
    alerts.jsonl 预期体量不大（notify_only 是三个落点里成本最低、但也是
    "只有真正配了规则的事件类型才会走到"的一档，不是每次轮询都写），
    全量扫描可以接受；量级增长后可以像 events.jsonl 一样加滚动归档，
    当前不做这个优化（YAGNI）。"""
    p = paths.external_input_alerts
    if not p.exists():
        return []
    result: list[dict] = []
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if not d.get("acknowledged"):
                    result.append(d)
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.external_input.policy.list_pending_alerts")
        return []
    result.sort(key=lambda d: d.get("created_at") or 0, reverse=True)
    if limit is not None:
        result = result[:limit]
    return result


def acknowledge_alert(paths: "AgentPaths", alert_id: str) -> bool:
    """把某条 alert 标记为已处理（看板"待办中心"里点掉一条外部告警时调用）。
    alerts.jsonl 体量小，直接整体重写；跟 goal_backlog.py 类似场景的
    "小文件、低频写、整体重写"处理方式一致。返回 True 表示确实找到并标记了。
    """
    p = paths.external_input_alerts
    if not p.exists():
        return False
    lines = p.read_text(encoding="utf-8").splitlines()
    found = False
    new_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            new_lines.append(line)
            continue
        if d.get("alert_id") == alert_id and not d.get("acknowledged"):
            d["acknowledged"] = True
            found = True
        new_lines.append(json.dumps(d, ensure_ascii=False))
    if found:
        p.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return found


# ── goal_candidate 落点：写入 GoalBacklog（P5） ──────────────────────────

def _goal_candidate(paths: "AgentPaths", event: ExternalInputEvent, goal_backlog: "GoalBacklog") -> bool:
    """把命中 goal_candidate 的事件写成一个 GoalBacklog 候选（§3.4 落点 2）。

    对齐 `soft_goal_deriver.commit_goals()` 处理 workthread/lesson 类候选
    的方式：source 打 `EXTERNAL_GOAL_SOURCE`，打 `needs_review` 标签
    （外部信号同样没有经过 ExplorationSandbox 验证）。

    去重：用 `objective_outcome_tracker.normalize_title_key()` 归一化
    `event.title` 后，与当前 active Goal 的归一化标题比对，命中则跳过——
    避免同一个外部信号（比如某个 source 连续几次轮询都命中同一条新闻）
    反复堆出重复 Goal。返回是否真正写入了新 Goal。
    """
    try:
        from mini_agent.evolution.objective_outcome_tracker import normalize_title_key
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.external_input.policy._goal_candidate.normalize_title_key")
        normalize_title_key = lambda s: s.strip().lower()  # noqa: E731

    key = normalize_title_key(event.title)
    try:
        existing_keys = {normalize_title_key(g.title) for g in goal_backlog.active_goals()}
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.external_input.policy._goal_candidate.active_goals")
        existing_keys = set()
    if key in existing_keys:
        return False

    description = event.detail or f"外部输入触发（{event.source_type}/{event.signal}）：{event.title}"
    goal_backlog.add_goal(
        title=event.title,
        description=description,
        source=EXTERNAL_GOAL_SOURCE,
        priority=20,
        tags=["needs_review", "external_input"],
    )
    return True


# ── enqueue_turn 落点：直接提交 InputQueue（P5） ─────────────────────────

_DEFAULT_ENQUEUE_TEMPLATE = "收到外部输入：{title}\n{detail}\n请判断是否需要处理。"


def _enqueue_turn(
    paths: "AgentPaths",
    event: ExternalInputEvent,
    rule: PolicyRule,
    input_queue: "InputQueue",
) -> str:
    """把命中 enqueue_turn 的事件直接提交进 InputQueue（§3.4 落点 3）。

    `rule.enqueue` 支持两个可选键（对齐设计文档 §3.4 示例）：
      - `initiator`：默认 "external"
      - `task_template`：默认 `_DEFAULT_ENQUEUE_TEMPLATE`，用
        `{title}`/`{detail}` 占位符渲染消息正文；缺失的占位符按空串处理，
        不会因为 template 写漏一个字段就让整条任务提交失败。

    返回 InputQueue.enqueue() 产生的 turn_id。
    """
    initiator = str(rule.enqueue.get("initiator", "external"))
    template = rule.enqueue.get("task_template", _DEFAULT_ENQUEUE_TEMPLATE)
    try:
        message = template.format(title=event.title, detail=event.detail or "")
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.external_input.policy._enqueue_turn.format")
        message = f"收到外部输入：{event.title}\n{event.detail or ''}".strip()
    return input_queue.enqueue(
        message=message,
        initiator=initiator,
        meta={
            "source": "external_input_policy",
            "source_id": event.source_id,
            "event_id": event.id,
            "event_type": event.event_type(),
        },
    )


# ── 主入口：消费 external.* 事件并按规则路由 ────────────────────────────

@dataclass
class PolicyRunSummary:
    processed: int = 0
    notify_only: int = 0
    goal_candidate: int = 0
    goal_candidate_skipped: int = 0
    goal_candidate_deduped: int = 0
    enqueue_turn: int = 0
    enqueue_turn_skipped: int = 0
    by_channel: dict = field(default_factory=dict)
    """按 channel 统计的已处理事件数（P7），供看板/诊断观察"daemon 这一轮
    分别处理了哪些频道、各处理了多少条"，不影响路由结果本身——路由决策
    仍然是逐事件按 policies.yaml 规则匹配，channel 只是分组统计维度，
    并可作为 match 条件之一（见 PolicyRule.matches）。"""


def group_events_by_channel(
    events: list[ExternalInputEvent],
) -> "dict[str, list[ExternalInputEvent]]":
    """把一批事件按 channel 分组，组内保持原有到达顺序（P7）。

    用途见 next_doc 设计文档 §3.5："daemon 进程处理之前先分频道"——本函数
    不做任何路由决策，只是把 run_ingestion_policy_once() 拿到的事件流
    重新组织成"频道 -> 事件列表"，供该函数按频道顺序、成组地喂给
    decide_action()/落点函数，也方便看板或诊断脚本单独查看某个频道的
    待处理事件，而不用先跑一遍路由。未设置 channel（理论上不会发生，
    因为 poller.py 会用 cfg.channel 回填）的事件归入 `"default"` 频道，
    保证分组结果里不会出现空字符串 key。
    """
    grouped: dict[str, list[ExternalInputEvent]] = {}
    for event in events:
        key = event.channel or "default"
        grouped.setdefault(key, []).append(event)
    return grouped


def run_ingestion_policy_once(
    paths: "AgentPaths",
    *,
    consumer_name: str = POLICY_CONSUMER_NAME,
    goal_backlog: "Optional[GoalBacklog]" = None,
    input_queue: "Optional[InputQueue]" = None,
) -> PolicyRunSummary:
    """消费一批自上次游标之后的 external.* 事件，按 policies.yaml 路由。

    对齐 §3.4 末尾的描述："作为 autonomous_loop.tick() 里新增的一个
    poll_since(...) 消费点，跟 soft_goal_deriver 挂在同一个节拍上，不
    新增额外的调度循环"——本函数就是那个消费点的实现，调用方负责决定
    什么时候调它，本函数不自带调度。

    `goal_backlog`/`input_queue` 是 P5 新增的可选参数：
      - 都不传（P3 阶段的调用方式，测试/诊断脚本仍适用）：命中
        `goal_candidate`/`enqueue_turn` 的事件只计入 `*_skipped`，游标
        照常推进，不静默丢事件也不误当 notify_only 处理。
      - 传了对应参数：真正落地写 GoalBacklog / 提交 InputQueue（见
        `_goal_candidate()`/`_enqueue_turn()`）。
    """
    summary = PolicyRunSummary()
    try:
        rules = load_policies(paths)
    except PoliciesConfigError as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.external_input.policy.run_ingestion_policy_once")
        return summary

    events = poll_external_events(paths, consumer_name=consumer_name)
    # P7：先按 channel 分组，再逐频道、组内按原顺序处理。分组本身不改变
    # 路由结果（每个事件依旧独立 decide_action()），只是让"daemon 按频道
    # 处理"这件事在代码结构上显式可见，也顺带给出 by_channel 统计——
    # 未来如果某个频道需要频道级的特殊处理（比如权重、频道级熔断），
    # 改动点集中在这个循环里，不需要动 decide_action()/落点函数。
    grouped = group_events_by_channel(events)
    for channel, channel_events in grouped.items():
        for event in channel_events:
            summary.processed += 1
            summary.by_channel[channel] = summary.by_channel.get(channel, 0) + 1
            rule = decide_action(event, rules)
            if rule.action == "notify_only":
                _notify_only(paths, event)
                summary.notify_only += 1
            elif rule.action == "goal_candidate":
                if goal_backlog is None:
                    summary.goal_candidate_skipped += 1
                    continue
                try:
                    created = _goal_candidate(paths, event, goal_backlog)
                except Exception as exc:
                    from mini_agent.errors import log_exception
                    log_exception(exc, where="mini_agent.external_input.policy.run_ingestion_policy_once.goal_candidate")
                    created = False
                if created:
                    summary.goal_candidate += 1
                else:
                    summary.goal_candidate_deduped += 1
            elif rule.action == "enqueue_turn":
                if input_queue is None:
                    summary.enqueue_turn_skipped += 1
                    continue
                try:
                    _enqueue_turn(paths, event, rule, input_queue)
                    summary.enqueue_turn += 1
                except Exception as exc:
                    from mini_agent.errors import log_exception
                    log_exception(exc, where="mini_agent.external_input.policy.run_ingestion_policy_once.enqueue_turn")
                    summary.enqueue_turn_skipped += 1
    return summary
