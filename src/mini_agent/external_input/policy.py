"""external_input/policy.py — IngestionPolicy 路由决策（P3）

设计背景见 next_doc/external_input_gateway_design.md §3.4。

本阶段（P3）范围：加载 policies.yaml、按事件类型/来源匹配路由规则、
落地 `notify_only`（写入 alerts.jsonl，供 /v1/inbox 展示）。

`goal_candidate` / `enqueue_turn` 两种落点在 §3.4 里明确要求"复用现有
GoalBacklog/ResourceArbiter/InputQueue"而不是网关自己另造门控——这两个
落点留到 P5 跟对应子系统的接入一起做（当前遇到会记一条诊断日志并跳过，
不会静默丢事件也不会误当成 notify_only 处理掉）。
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

try:
    import yaml as _yaml  # type: ignore
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

# 事件没有命中任何规则时的默认落点——§8 明确要求"默认路径永远最省钱"，
# 不会因为漏配规则就意外把高频轮询放大成高频 LLM 调用。
DEFAULT_ACTION = "notify_only"

VALID_ACTIONS = frozenset({"notify_only", "goal_candidate", "enqueue_turn"})

# P3 阶段已经实现落地逻辑的 action；其余合法 action 会被识别但暂不执行。
_IMPLEMENTED_ACTIONS = frozenset({"notify_only"})

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
    的记录才会出现在看板待办中心。"""
    alert = {
        "alert_id": f"alert:{event.source_id}:{event.id}",
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


# ── 主入口：消费 external.* 事件并按规则路由 ────────────────────────────

@dataclass
class PolicyRunSummary:
    processed: int = 0
    notify_only: int = 0
    goal_candidate_skipped: int = 0
    enqueue_turn_skipped: int = 0


def run_ingestion_policy_once(
    paths: "AgentPaths",
    *,
    consumer_name: str = POLICY_CONSUMER_NAME,
) -> PolicyRunSummary:
    """消费一批自上次游标之后的 external.* 事件，按 policies.yaml 路由。

    对齐 §3.4 末尾的描述："作为 autonomous_loop.tick() 里新增的一个
    poll_since(...) 消费点，跟 soft_goal_deriver 挂在同一个节拍上，不
    新增额外的调度循环"——本函数就是那个消费点的实现，调用方（P3 阶段
    是测试/诊断命令直接调，后续 tick() 接入只是多一行调用）负责决定
    什么时候调它，本函数不自带调度。
    """
    summary = PolicyRunSummary()
    try:
        rules = load_policies(paths)
    except PoliciesConfigError as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.external_input.policy.run_ingestion_policy_once")
        return summary

    events = poll_external_events(paths, consumer_name=consumer_name)
    for event in events:
        summary.processed += 1
        rule = decide_action(event, rules)
        if rule.action == "notify_only":
            _notify_only(paths, event)
            summary.notify_only += 1
        elif rule.action == "goal_candidate":
            # P5 落地：goal_candidate 复用 soft_goal_deriver 同款模式。
            # 目前只记一条诊断日志、不丢事件也不误当 notify_only 处理，
            # 避免"配置了还没实现的 action"被悄悄降级成别的行为。
            summary.goal_candidate_skipped += 1
        elif rule.action == "enqueue_turn":
            # P5 落地：复用 InputQueue.enqueue(initiator="external", ...)。
            summary.enqueue_turn_skipped += 1
    return summary
