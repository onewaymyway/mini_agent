"""
wiki/lifecycle.py — 统一知识生命周期状态机
（wiki 知识库提取与组织层改进计划 O4，next_doc/wiki知识库提取与组织层改进计划.md §7）

decision/entity/experience/fact 四类内容原本各自维护自己的状态语义（decision
有 superseded_by、entity 只有粗粒度 status、experience 没有过期机制、fact 依附
在 entity 页面内没有独立状态）。本模块提供一个跨页面类型的统一入口：

    mark_page_state(paths, page_id, confidence="superseded", ...)

不要求调用方关心目标页面是 entity/decision/experience 哪一种，也支持
"页面内锚点"粒度（entity-id#fact-N）的 fact 独立状态标记。

字段设计（frontmatter，见 wiki/writer.py::update_lifecycle_fields 的说明）：
    knowledge_state: fresh | stale | superseded
    last_validated_at: ISO8601 时间戳，最近一次被确认仍然有效的时间
    validated_by: 触发确认的来源类型列表，如 ["correction_check", "grounded_hit"]

用 `knowledge_state` 而不是复用已有的数值型 `confidence` 字段，是对原计划
§7.2.1 的必要调整——parser.py 里 `confidence` 已经是 0-1 的置信度分数，语义
不同，复用会造成同名字段两种类型冲突。详见 O4 实施记录 §2。

所有函数遵循项目一贯的"失败不阻断主流程"风格：内部异常一律吞掉，返回
False/空结果，不向上抛出。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from mini_agent.storage.paths import AgentPaths
from mini_agent.wiki.indexer import discover_pages
from mini_agent.wiki.parser import WikiPage, parse_page

KNOWLEDGE_STATES = ("fresh", "stale", "superseded")

_ANCHOR_COMMENT_RE_TEMPLATE = (
    r"<!--\s*fact_id:\s*{anchor}\s*;\s*knowledge_state:\s*\w+\s*-->"
)


def _find_page(paths: AgentPaths, page_id: str) -> Optional[WikiPage]:
    """按 page_id 在全量页面里查找（不依赖 O1 的派生索引，保持实现简单——
    lifecycle 标记属于低频操作，不在检索热路径上，全量扫描成本可接受）。"""
    for md_path in discover_pages(paths):
        if md_path.stem != page_id:
            continue
        try:
            return parse_page(md_path)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.wiki.lifecycle._find_page')
            return None
    return None


def mark_page_state(
    paths: AgentPaths,
    page_id: str,
    *,
    confidence: str,
    reason: str = "",
    validated_by: str = "",
    anchor: Optional[str] = None,
) -> bool:
    """跨页面类型的统一状态更新入口（O4 §7.2.2）。

    Args:
        page_id: 目标页面 id（不含 .md 后缀），适用于 entity/decision/
            experience/topic/process 任意 page_type。
        confidence: 目标状态，取值 "fresh" | "stale" | "superseded"。
        reason: 可选，追加到正文"历史沿革"的说明文字。
        validated_by: 可选，追加进 validated_by 列表的来源标记，如
            "correction_check"。
        anchor: 传入时按"页面内锚点"粒度标记（形如 "entity-id#fact-3"，
            由 world_writer.py::queue_facts 落盘时生成），只更新对应 fact
            的内联注释标记，不改动整份页面的 knowledge_state。

    Returns:
        是否成功标记；页面不存在、锚点未命中、写入异常等情况均返回 False，
        不抛出异常。
    """
    if confidence not in KNOWLEDGE_STATES:
        return False
    page = _find_page(paths, page_id)
    if page is None:
        return False
    try:
        if anchor:
            return _mark_anchor_state(paths, page, anchor=anchor, confidence=confidence)
        from mini_agent.wiki.writer import update_lifecycle_fields

        update_lifecycle_fields(
            paths, page,
            knowledge_state=confidence,
            validated_by_append=validated_by,
            note=reason,
        )
        return True
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.wiki.lifecycle.mark_page_state')
        return False


def _mark_anchor_state(
    paths: AgentPaths, page: WikiPage, *, anchor: str, confidence: str
) -> bool:
    pattern = re.compile(_ANCHOR_COMMENT_RE_TEMPLATE.format(anchor=re.escape(anchor)))
    if not pattern.search(page.body):
        return False
    new_body = pattern.sub(
        f"<!-- fact_id: {anchor}; knowledge_state: {confidence} -->", page.body,
    )
    if new_body == page.body:
        return False
    from mini_agent.wiki.writer import replace_body

    replace_body(paths, page, body=new_body)
    return True


def touch_validated(paths: AgentPaths, page_id: str, *, validated_by: str) -> bool:
    """记一次"隐式验证"（比如被检索命中/被 grounded 引用），刷新
    `last_validated_at`、追加 `validated_by`；若当前状态是 stale，回升为
    fresh（`superseded` 不因隐式验证回升——已被明确证据推翻的知识需要
    明确的反向纠正操作才能恢复，避免被简单地重新检索命中就"洗白"）。

    独立于 O1 §4.2.2 已有的 `increment_grounded_hit_count`（只维护命中计数，
    不关心生命周期状态），两者可以在同一个调用点先后调用，互不影响。
    """
    page = _find_page(paths, page_id)
    if page is None:
        return False
    try:
        current_state = str(page.raw_frontmatter.get("knowledge_state") or "fresh")
        new_state = "fresh" if current_state == "stale" else current_state
        from mini_agent.wiki.writer import update_lifecycle_fields

        update_lifecycle_fields(
            paths, page,
            knowledge_state=new_state,
            validated_by_append=validated_by,
        )
        return True
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.wiki.lifecycle.touch_validated')
        return False


def stale_candidate_scan(
    paths: AgentPaths,
    *,
    threshold_days: int = 90,
) -> dict:
    """巡检任务（O4 §7.2.2）：对 `last_validated_at` 超过 `threshold_days`
    天、且当前仍是 `fresh` 状态的页面标记为 `stale`（不是 `superseded`，因为
    没有明确反证，只是"久未验证"）。

    没有 `last_validated_at` 的历史遗留页面（本次改动之前创建）退回用
    `created` 日期估算年龄，不因为字段缺失就永远跳出巡检范围。

    只做标记，默认不参与检索排序（是否参与排序由 wiki/search.py 的
    `lifecycle_discount_enabled` 独立开关控制，见 O4 实施记录 §3——沿用
    "先观察不影响行为，用真实数据校准后再决定是否默认开启"的执行纪律，
    与 P4 §6.5 的教训一致）。

    Returns:
        {"scanned": 已扫描页面数, "marked_stale": 本次新标记为 stale 的数量}
    """
    from mini_agent.wiki.writer import update_lifecycle_fields

    scanned = 0
    marked = 0
    now = datetime.now(timezone.utc)
    for md_path in discover_pages(paths):
        try:
            page = parse_page(md_path)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.wiki.lifecycle.stale_candidate_scan')
            continue
        scanned += 1
        state = str(page.raw_frontmatter.get("knowledge_state") or "fresh")
        if state != "fresh":
            continue
        last_validated_raw = page.raw_frontmatter.get("last_validated_at") or page.created
        if not last_validated_raw:
            continue
        try:
            last_validated = datetime.fromisoformat(
                str(last_validated_raw).replace("Z", "+00:00")
            )
            if last_validated.tzinfo is None:
                last_validated = last_validated.replace(tzinfo=timezone.utc)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.wiki.lifecycle.stale_candidate_scan')
            continue
        age_days = (now - last_validated).total_seconds() / 86400
        if age_days < threshold_days:
            continue
        try:
            update_lifecycle_fields(
                paths, page, knowledge_state="stale", validated_by_append="stale_scan",
            )
            marked += 1
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.wiki.lifecycle.stale_candidate_scan')
            continue
    return {"scanned": scanned, "marked_stale": marked}


__all__ = [
    "KNOWLEDGE_STATES",
    "mark_page_state",
    "touch_validated",
    "stale_candidate_scan",
]
