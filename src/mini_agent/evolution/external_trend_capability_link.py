"""evolution/external_trend_capability_link.py — 外部知识接入自我改进候选生成（P4）。

设计背景见 next_doc/external_knowledge_wiki_and_self_improvement_plan.md
§3 P4：P1-P3 已经把"外部世界正在发生什么"沉淀进了 wiki
（`source_kind` 为 `external_watch`/`external_search` 的专题页/实体页），
但 `evolution/soft_goal_deriver.py` 现有的四路信号采集
（`_from_capability_map`/`_from_work_index`/`_from_lesson_review`/
`_from_unexplored_capabilities`）全部来自系统内部状态，没有一路桥接
"这条外部知识是否值得作为一个改进方向"。

本模块新增 cron job `sys:external_trend_capability_link`
（`interval:604800`，每周一次，节奏对齐 `sys:decision_profile_update`）：

  1. 读取 P1-P3 沉淀的外部知识页面（`source_kind` 属于
     `EXTERNAL_KNOWLEDGE_SOURCE_KINDS`）。
  2. 读取 `evolution/consolidation.py::load_capability_map()` 的能力评估
     结果，筛出 `confidence < CONFIDENCE_LOW` 或 `total_calls` 极少的
     条目（阈值复用 `soft_goal_deriver.py` 里已有的同名常量，避免两处
     漂移）。
  3. 用 LLM 做一次轻量匹配，产出"外部动态 × 能力薄弱点"候选草稿——
     每条候选必须能同时追溯到具体的 wiki 页面 id 与 capability_map
     的能力条目名称，不是凭感觉生成的建议。
  4. 落点只有两处，且都不直接创建 Goal / 不自动修改代码：
       a. 结构化候选写入 `AgentPaths.external_trend_capability_link_state_path`
          （`candidates` 字段），供 `_from_external_knowledge()` 消费；
       b. 人类可读草稿写入
          `AgentPaths.external_trend_capability_candidates_path`
          （`.agent/wiki/external_trend_capability_candidates.md`），
          格式与 `decision_profile_builder.py::_write_profile_md()` 一致，
          人工审核后再决定是否实施。
     `evolution/soft_goal_deriver.py::_from_external_knowledge()`
     从 (a) 读取候选、转换为既有的 `_DeriveCandidate`，进入既有的
     derive_candidates()/commit_goals() 流程——仍然遵循"autonomous 档位
     下才 derive、其余档位只记录不生成"的既有规则，不改变整体风险模型，
     也不是"跳过既有安全阀直接建 Goal"。

去重：已经产出过的"外部页面 id + 能力域"组合记录在状态文件里，
`STALE_CANDIDATE_TTL_SECONDS`（默认 14 天）内不重复产出同一组合，避免
每周对同一批未处理的外部知识反复刷屏。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths

JOB_ID = "sys:external_trend_capability_link"

# 与 wiki/stats.py 里的 source_kind 取值约定保持一致，只消费 P1/P3 产出的
# 外部知识页面，不包括对话来源的 world_model / decision 等页面。
EXTERNAL_KNOWLEDGE_SOURCE_KINDS = ("external_watch", "external_search")

# 复用 soft_goal_deriver.py 的同名常量语义（低置信度 / 几乎未探索），
# 避免两处各自维护一份阈值容易漂移——这里不做 import（soft_goal_deriver
# 不应反向依赖本模块），只保持数值一致，两边各自的模块注释里互相指向对方。
CONFIDENCE_LOW = 0.35
MIN_CALLS_FOR_KNOWN = 2

# 同一("外部页面 id","能力域") 组合的去重窗口：14 天内不重复产出候选草稿。
STALE_CANDIDATE_TTL_SECONDS = 14 * 86400

# 单次 run 最多处理的外部知识页面数量，避免 wiki 页面很多时一次性塞进
# 一个巨大的 LLM prompt。
MAX_PAGES_PER_RUN = 30


@dataclass
class TrendCapabilityCandidate:
    capability_domain: str
    wiki_page_ids: list = field(default_factory=list)   # list[str]
    rationale: str = ""
    produced_at: float = 0.0

    def dedupe_key(self) -> str:
        return self.capability_domain + "|" + ",".join(sorted(self.wiki_page_ids))

    def to_dict(self) -> dict:
        return {
            "capability_domain": self.capability_domain,
            "wiki_page_ids": list(self.wiki_page_ids),
            "rationale": self.rationale,
            "produced_at": self.produced_at,
        }

    @staticmethod
    def from_dict(d: dict) -> "TrendCapabilityCandidate":
        return TrendCapabilityCandidate(
            capability_domain=str(d.get("capability_domain", "")),
            wiki_page_ids=list(d.get("wiki_page_ids", [])),
            rationale=str(d.get("rationale", "")),
            produced_at=float(d.get("produced_at", 0.0)),
        )


@dataclass
class LinkSummary:
    external_pages_scanned: int = 0
    weak_capabilities_scanned: int = 0
    llm_called: bool = False
    candidates_produced: int = 0
    candidates_skipped_duplicate: int = 0


def _load_external_knowledge_pages(paths: "AgentPaths") -> list:
    """扫描 wiki 全量页面，筛出 source_kind 属于 P1/P3 外部知识的页面。
    复用 wiki/indexer.py::discover_pages() + wiki/parser.py::parse_page()，
    与 wiki/stats.py::compute_stats() 同款用法，不新造扫描逻辑。"""
    pages = []
    try:
        from mini_agent.wiki.indexer import discover_pages
        from mini_agent.wiki.parser import parse_page

        for md_path in discover_pages(paths):
            try:
                page = parse_page(md_path)
            except Exception as exc:
                from mini_agent.errors import log_exception
                log_exception(exc, where="mini_agent.evolution.external_trend_capability_link._load_external_knowledge_pages.parse")
                continue
            source_kind = str(page.raw_frontmatter.get("source_kind") or "")
            if source_kind in EXTERNAL_KNOWLEDGE_SOURCE_KINDS:
                pages.append(page)
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.evolution.external_trend_capability_link._load_external_knowledge_pages")
    return pages[:MAX_PAGES_PER_RUN]


def _load_weak_capabilities(paths: "AgentPaths") -> list:
    """筛出 capability_map 中 confidence 低或 total_calls 极少的条目，
    与 soft_goal_deriver.py 的 `_from_capability_map()`/
    `_from_unexplored_capabilities()` 用同一批数据源，只是这里合并两类
    信号（不区分"试过效果不好"与"几乎没试过"），因为 P4 只需要知道
    "哪些能力域薄弱"，不需要区分薄弱的具体原因。"""
    entries = []
    try:
        from mini_agent.evolution.consolidation import load_capability_map
        all_entries = load_capability_map(paths)
        for e in all_entries:
            if e.confidence < CONFIDENCE_LOW or e.total_calls < MIN_CALLS_FOR_KNOWN:
                entries.append(e)
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.evolution.external_trend_capability_link._load_weak_capabilities")
    return entries


def _load_state(paths: "AgentPaths") -> dict:
    p = paths.external_trend_capability_link_state_path
    if not p.exists():
        return {"last_scan_at": 0.0, "candidates": [], "produced_keys": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        data.setdefault("candidates", [])
        data.setdefault("produced_keys", {})
        return data
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.evolution.external_trend_capability_link._load_state")
        return {"last_scan_at": 0.0, "candidates": [], "produced_keys": {}}


def _save_state(paths: "AgentPaths", state: dict) -> None:
    p = paths.external_trend_capability_link_state_path
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.evolution.external_trend_capability_link._save_state")


def _extract_json_array(text: str) -> str:
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return "[]"
    return text[start : end + 1]


def _llm_match_trends_to_capabilities(pages: list, capabilities: list, llm_helper) -> list[dict]:
    """要求 LLM 只做匹配归纳：每条候选必须同时给出 capability_domain（必须
    真实来自 capabilities 列表）与 wiki_page_ids（必须真实来自 pages 列表），
    不满足的候选事后过滤掉（不完全信任 LLM 自称的引用是否真实存在）。"""
    page_entries = [
        {"id": p.id, "excerpt": (p.body or "")[:300]}
        for p in pages
    ]
    cap_entries = [
        {"domain": c.capability_name, "confidence": round(c.confidence, 2), "total_calls": c.total_calls}
        for c in capabilities
    ]
    prompt = (
        "以下是两批数据：wiki 里沉淀的外部技术动态摘要（pages），以及 agent 自身"
        "能力评估中偏薄弱的能力域列表（capabilities，confidence 低或几乎没试过）。"
        "请找出其中确实相关的组合——某条外部技术动态如果能帮助改善某个薄弱能力域，"
        "才产出一条候选；找不到明确关联时不要强行凑数。每条候选给出："
        "capability_domain（必须是 capabilities 里真实存在的 domain）、"
        "wiki_page_ids（引用的 page id 列表，必须真实来自 pages 输入，"
        "至少 1 个）、rationale（一句话说明为什么这条外部动态与该能力域相关）。"
        "只返回 JSON 数组，不要其他文字。\n"
        f"pages: {json.dumps(page_entries, ensure_ascii=False)}\n"
        f"capabilities: {json.dumps(cap_entries, ensure_ascii=False)}"
    )
    raw = llm_helper.ask(prompt)
    try:
        parsed = json.loads(_extract_json_array(raw))
    except Exception:
        return []

    valid_page_ids = {p["id"] for p in page_entries}
    valid_domains = {c["domain"] for c in cap_entries}
    out = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        domain = str(item.get("capability_domain", "")).strip()
        page_ids = [pid for pid in (item.get("wiki_page_ids") or []) if pid in valid_page_ids]
        if domain not in valid_domains or not page_ids:
            continue  # 证据不完整，不落地，即使 LLM 自己声称满足
        out.append({
            "capability_domain": domain,
            "wiki_page_ids": page_ids,
            "rationale": str(item.get("rationale", "")).strip(),
        })
    return out


def _write_candidates_md(paths: "AgentPaths", candidates: list[TrendCapabilityCandidate]) -> None:
    lines = [
        "---",
        "title: 外部技术趋势 x 能力薄弱点候选",
        "source_kind: external_trend_capability_candidates",
        f"updated: {time.strftime('%Y-%m-%d', time.localtime())}",
        "tags: [external-trend-capability-link]",
        "---",
        "",
        "# 外部技术趋势 × 自身能力薄弱点候选",
        "",
        "> 本文档由 `sys:external_trend_capability_link` 周期性归纳生成，只是"
        "供人工审核的草稿——不会自动创建 Goal，也不会自动修改代码。每条候选"
        "都能追溯到具体的 wiki 页面与 capability_map 能力条目。",
        "",
    ]
    if not candidates:
        lines.append("_本轮未产出新的候选。_")
    for c in sorted(candidates, key=lambda x: -x.produced_at):
        lines.append(f"## {c.capability_domain}")
        lines.append(f"- 依据（wiki 页面）：{', '.join(c.wiki_page_ids)}")
        lines.append(f"- 理由：{c.rationale}")
        lines.append(f"- 产出时间：{time.strftime('%Y-%m-%d', time.localtime(c.produced_at))}")
        lines.append("")

    paths.wiki_dir.mkdir(parents=True, exist_ok=True)
    paths.external_trend_capability_candidates_path.write_text("\n".join(lines), encoding="utf-8")


def run_external_trend_capability_link_once(
    paths: "AgentPaths", *, llm_helper=None,
) -> LinkSummary:
    """cron 触发入口：外部知识页面 × 能力薄弱点 → LLM 匹配 → 写状态文件 +
    人类可读草稿。没有 llm_helper 时直接跳过（本层匹配依赖 LLM 做语义
    关联判断，规则层无法替代）。"""
    summary = LinkSummary()
    if llm_helper is None:
        return summary

    pages = _load_external_knowledge_pages(paths)
    capabilities = _load_weak_capabilities(paths)
    summary.external_pages_scanned = len(pages)
    summary.weak_capabilities_scanned = len(capabilities)
    if not pages or not capabilities:
        return summary  # 任一路数据为空，匹配没有意义

    state = _load_state(paths)
    produced_keys: dict = state.get("produced_keys", {})
    now = time.time()

    raw_candidates = _llm_match_trends_to_capabilities(pages, capabilities, llm_helper)
    summary.llm_called = True

    kept: list[TrendCapabilityCandidate] = [
        TrendCapabilityCandidate.from_dict(d) for d in state.get("candidates", [])
        if now - float(d.get("produced_at", 0.0)) < STALE_CANDIDATE_TTL_SECONDS
    ]
    kept_keys = {c.dedupe_key() for c in kept}

    for item in raw_candidates:
        candidate = TrendCapabilityCandidate(
            capability_domain=item["capability_domain"],
            wiki_page_ids=item["wiki_page_ids"],
            rationale=item["rationale"],
            produced_at=now,
        )
        key = candidate.dedupe_key()
        last_produced = produced_keys.get(key, 0.0)
        if now - float(last_produced) < STALE_CANDIDATE_TTL_SECONDS:
            summary.candidates_skipped_duplicate += 1
            continue
        if key in kept_keys:
            summary.candidates_skipped_duplicate += 1
            continue
        kept.append(candidate)
        kept_keys.add(key)
        produced_keys[key] = now
        summary.candidates_produced += 1

    state["last_scan_at"] = now
    state["candidates"] = [c.to_dict() for c in kept]
    state["produced_keys"] = produced_keys
    _save_state(paths, state)

    _write_candidates_md(paths, kept)
    return summary


def load_external_trend_candidates(paths: "AgentPaths") -> list[TrendCapabilityCandidate]:
    """供 `evolution/soft_goal_deriver.py::_from_external_knowledge()` 消费：
    只读加载状态文件里未过期（14 天内）的候选，读取失败静默返回空列表。"""
    state = _load_state(paths)
    now = time.time()
    out = []
    for d in state.get("candidates", []):
        if now - float(d.get("produced_at", 0.0)) < STALE_CANDIDATE_TTL_SECONDS:
            out.append(TrendCapabilityCandidate.from_dict(d))
    return out


def ensure_external_trend_capability_link_job(
    paths: "AgentPaths",
    cron_scheduler,
    *,
    llm_helper_provider,
    schedule: str = "interval:604800",
) -> bool:
    """daemon 启动时调用：缺失才补注册 `sys:external_trend_capability_link`
    job，并注册本地回调 handler，跟 P1/P3 的 `ensure_*_job` 同构。默认每周
    一次，节奏对齐 `sys:decision_profile_update`（计划 §3 P4/§4）。

    改进计划 §4 规定新增 job 默认建议先以 disabled 状态接入——job 首次
    创建时调用一次 `disable()`，已存在的 job 不受影响。"""
    existing_ids = {j.id for j in cron_scheduler.list_jobs()}
    newly_added = JOB_ID not in existing_ids
    cron_scheduler.ensure_job(
        job_id=JOB_ID,
        name="外部技术趋势 x 能力薄弱点关联",
        schedule=schedule,
        description=(
            "读取 P1-P3 沉淀的外部知识 wiki 页面（source_kind=external_watch/"
            "external_search）与 capability_map 中偏薄弱的能力域，用 LLM 做"
            "轻量匹配，产出一份结构化候选草稿（供 soft_goal_deriver 消费）"
            "与一份人类可读草稿文档，不自动创建 Goal、不自动修改代码。"
        ),
        tags=["evolution", "wiki", "external_trend_capability_link"],
    )
    if newly_added:
        cron_scheduler.disable(JOB_ID)

    def _handler(job, _paths=paths) -> bool:
        helper = llm_helper_provider() if llm_helper_provider else None
        if helper is None:
            return False
        run_external_trend_capability_link_once(_paths, llm_helper=helper)
        return True

    cron_scheduler.register_local_handler(JOB_ID, _handler)
    return newly_added


__all__ = [
    "JOB_ID",
    "EXTERNAL_KNOWLEDGE_SOURCE_KINDS",
    "TrendCapabilityCandidate",
    "LinkSummary",
    "run_external_trend_capability_link_once",
    "load_external_trend_candidates",
    "ensure_external_trend_capability_link_job",
]
