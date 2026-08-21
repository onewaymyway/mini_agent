"""notification/reports_store.py — watchlist_report 汇报记录的独立存储。

跟 `external_input/policy.py` 里 alerts.jsonl 的读写逻辑刻意保持同构（同样
是"小文件、低频写、整体重写"），但物理上是两份完全独立的文件：

  - `.agent/external_input/alerts.jsonl`  ← 只装网关 notify_only 告警
  - `.agent/notification/reports.jsonl`  ← 只装 watchlist_report 分级汇报

分开存储的原因：这两类东西对用户来说语义不同（"外部世界发生了一件需要
你判断的事" vs "你关注的东西按周期打包汇总了一份清单"），过去共用
alerts.jsonl + /v1/inbox 聚合展示，导致：
  1. 汇报的完整正文（NotificationMessage.body，含命中明细）没有专门的
     展示入口，只藏在共享文件的 detail 字段里；
  2. 两者在"全局待办中心"里混在同一个列表，用户分不清哪条是需要处理的
     告警、哪条只是周期性汇总。

现在 KanbanChannel 直接写这份独立文件，看板"关注与通知"tab 用专门的
/v1/notifications/* 端点读取/ack，不再出现在 /v1/inbox 里。
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths


def append_report(paths: "AgentPaths", record: dict) -> None:
    p = paths.notification_reports
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.notification.reports_store.append_report")


def _load_pending_sorted(paths: "AgentPaths") -> list[dict]:
    p = paths.notification_reports
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
        log_exception(exc, where="mini_agent.notification.reports_store._load_pending_sorted")
        return []
    result.sort(key=lambda d: d.get("created_at") or 0, reverse=True)
    return result


def list_pending_reports(
    paths: "AgentPaths", limit: Optional[int] = None, offset: int = 0,
    category: Optional[str] = None,
) -> list[dict]:
    """读取 reports.jsonl 中尚未 acknowledged 的汇报，供
    /v1/notifications/pending 分页端点使用。每条记录都带完整 `detail`
    （汇报正文，含命中明细），供看板"📋 待处理汇报"面板展开显示——这是
    跟 alerts.jsonl 共用时缺失的能力。每条记录额外附带一个只读的
    `category` 字段（`categorize_report()` 算出来的，不落盘、不是存储
    schema 的一部分），供看板按分类筛选/分组展示；`category` 参数非空
    时只返回该分类下的记录，分页（limit/offset）在筛选之后计算。"""
    result = _load_pending_sorted(paths)
    for d in result:
        d["category"] = categorize_report(d)
    if category:
        result = [d for d in result if d["category"] == category]
    if offset:
        result = result[offset:]
    if limit is not None:
        result = result[:limit]
    return result


def count_pending_reports(paths: "AgentPaths", category: Optional[str] = None) -> int:
    result = _load_pending_sorted(paths)
    if category:
        result = [d for d in result if categorize_report(d) == category]
    return len(result)


def count_pending_reports_by_category(paths: "AgentPaths") -> dict[str, int]:
    """按分类统计未读汇报数量，供看板渲染分类 tab 上的计数角标，不需要
    额外拉取一次全量列表来现算。"""
    counts = {c: 0 for c in ALL_CATEGORIES}
    for d in _load_pending_sorted(paths):
        counts[categorize_report(d)] = counts.get(categorize_report(d), 0) + 1
    return counts


def acknowledge_report(paths: "AgentPaths", report_id: str) -> bool:
    """把某条汇报标记为已读。整体重写，跟 policy.py::acknowledge_alert
    的处理方式一致。"""
    return acknowledge_reports(paths, {report_id}) > 0


# [看板"关注与通知"批量处理 + 分类展示] 每条汇报的 `source` 字段（见
# notification/channels/kanban.py 写入的 record）取值来自各个模块拼装
# NotificationMessage 时传入的 `source`，语义各不相同但数量有限、稳定
# （见各 `NotificationMessage(...)` 调用点），所以用一张静态映射表把它们
# 归到几个用户能理解的大类，而不是引入一个新的、需要各模块协同维护的
# "category" 字段——旧数据（reports.jsonl 里已经落盘、映射表加入之前
# 写入的记录）也能直接归类，不需要迁移。未收录的 source 归入"其他"，
# 新增一个 NotificationMessage 调用点忘记加映射也不会报错或丢失展示，
# 只是先落到"其他"分类里。
_SOURCE_CATEGORY_MAP: dict[str, str] = {
    # 执行失败/异常 —— 系统性故障、任务失败、卡死回收
    "objective_failed": "执行失败",
    "goal_cycle": "执行失败",
    "objective_circuit_breaker": "执行失败",
    "cron_circuit_breaker": "执行失败",
    "workflow_circuit_breaker": "执行失败",
    "recovery_burst": "执行失败",
    "cron_skip_alert": "执行失败",
    "scheduler_heartbeat_stuck": "执行失败",
    # 关注提醒 —— 需要留意但不是"失败"，比如执行阶段健康度、饱和度提示
    "goal_cycle_phase_health": "关注提醒",
    "growth_advisor_pursuit_saturation": "关注提醒",
    "goal_cycle_converge_spec_draft": "关注提醒",
    # 关注汇报 —— 关注对象/成长顾问按周期打包的信息类汇总
    "watchlist_report": "关注汇报",
    "growth_weekly_digest": "关注汇报",
    "growth_report": "关注汇报",
    "cycle_patrol": "关注汇报",
    "capability_learning": "关注汇报",
}
CATEGORY_OTHER = "其他"
ALL_CATEGORIES = ["执行失败", "关注提醒", "关注汇报", CATEGORY_OTHER]


def categorize_report(record: dict) -> str:
    """把一条汇报记录归到 `ALL_CATEGORIES` 里的一个分类，供看板筛选/
    分组展示使用。纯函数，不做任何 IO。"""
    return _SOURCE_CATEGORY_MAP.get(record.get("source") or "", CATEGORY_OTHER)


def acknowledge_reports(paths: "AgentPaths", report_ids: "set[str] | list[str]") -> int:
    """批量把多条汇报标记为已读，返回实际标记成功的条数。
    整体重写一次（跟单条 `acknowledge_report` 之前逐条重写文件相比，
    批量场景下只重写一次，避免"勾选 20 条点批量已读"触发 20 次整文件
    读写）。`report_ids` 不存在或已经是已读状态的条目会被跳过，不计入
    返回值，也不会报错。"""
    ids = set(report_ids)
    if not ids:
        return 0
    p = paths.notification_reports
    if not p.exists():
        return 0
    lines = p.read_text(encoding="utf-8").splitlines()
    matched = 0
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
        if d.get("report_id") in ids and not d.get("acknowledged"):
            d["acknowledged"] = True
            matched += 1
        new_lines.append(json.dumps(d, ensure_ascii=False))
    if matched:
        p.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return matched
