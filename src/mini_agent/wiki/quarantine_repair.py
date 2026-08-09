"""
wiki/quarantine_repair.py — 隔离区问题页面的自动修复策略与修复循环

跟 `wiki/quarantine.py`（发现 + 记录）职责分开：本模块只管"给定一个已知
的、结构性的 frontmatter 问题，能不能自动改好它"。修复策略是一个显式的
函数注册表（`_FIXERS`），只处理"确定是数据笔误、改法唯一"的情况——不做
任何猜测性的语义修复（比如猜用户想要哪个 relation、猜漏掉的字段该填什么
值），拿不准就不动，转 `needs_human` 交给人工。

首批修复策略（来自真实故障：frontmatter.links 写成裸字符串列表而不是
`{target: ...}` 字典列表，见 next_doc 对应故障记录）：
    - `_fix_string_links`：`links` 列表里的字符串项 -> `{"target": 字符串}`
    - `_fix_links_not_wrapped_in_list`：`links` 整个字段是单个字符串/字典
      而不是列表 -> 包一层 list

每个修复动作执行后都会重新完整解析一遍页面，确认真的能通过
`parse_page()` 了才落盘；解析仍然失败就当作"这个策略没解决问题"，不写
文件、不产生副作用，交给下一个策略或者最终转 needs_human。

── LLM 兜底修复（opt-in，默认关闭）──────────────────────────────────────
规则式 `_FIXERS` 只覆盖"确定改法"的已知故障模式，遇到没见过的结构性问题
（字段缺失、类型错乱到规则策略猜不出唯一改法）只能转 `needs_human`。这类
问题往往是"人一眼能看出来该怎么改"的格式错误（缩进、字段名拼写、漏了必
填字段），交给 LLM 做一次兜底修复性价比更高，比常年堆在 needs_human 队列
里等人工强。

复用 `llm_helper` opt-in 模式（架构决策：文本理解类新功能一律走 LLM，见
`GrowthAdvisorConfig` 同款写法）：`attempt_repair_page()` / 
`run_quarantine_repair_cycle()` 都新增可选 `llm_helper` 参数（对象需实现
`.ask(prompt, *, system=...) -> str`，即 `LLMHelper` 实例），不传时行为与
改动前完全一致（零 LLM 成本）。是否要传由调用方根据
`MemoryConfig.wiki_quarantine_llm_repair_enabled`（默认关闭）决定——本模块
本身不读取全局 cfg，只认参数有没有传。

LLM 修复同样遵守"改完必须能重新通过 `parse_page()` 才落盘"的铁律：模型
输出解析失败、或者输出内容看起来不像完整页面（比如漏了 frontmatter
分隔符），都当作这次修复未成功，不写文件；不会比规则修复更"激进"。
`repaired_by` 记为 `"llm_repair"`，跟规则修复策略名区分开，供追溯时区分
"是自动确定改法修的，还是 LLM 猜的"。
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

import yaml

from mini_agent.storage.paths import AgentPaths
from mini_agent.utils.atomic_write import atomic_write_text
from mini_agent.wiki import quarantine as qz
from mini_agent.wiki.parser import _FRONTMATTER_RE, PageParseError, parse_page

if TYPE_CHECKING:
    from mini_agent.evolution.cron_scheduler import CronJob, CronScheduler

JOB_ID = "sys:wiki_quarantine_repair"

# LLM 兜底修复策略名（跟规则修复策略名同放一个命名空间，供 repaired_by 追溯）。
LLM_REPAIR_NAME = "llm_repair"

_LLM_REPAIR_SYSTEM_PROMPT = (
    "你是一个严谨的知识库数据修复工具。你会收到一个 wiki 页面的完整原始文本"
    "（包含 --- 包裹的 YAML frontmatter 和正文）和它解析失败的错误信息。"
    "请只修正导致解析失败的结构性问题（字段缺失、类型错误、YAML 语法错误、"
    "字段名拼写错误等），不要改动正文内容、不要改动看起来正常的字段值、"
    "不要臆造你不确定的信息。如果解决不了，原样返回输入。"
    "只输出修复后的完整页面文本（含 frontmatter），不要输出任何解释、"
    "不要用代码块包裹。"
)

# 传给 LLM 的原始页面文本长度上限，避免异常巨大的页面把 prompt 撑爆——
# 正常 wiki 页面远小于这个数量级，超限说明数据本身有更严重的问题，
# 不适合走 LLM 兜底，直接跳过转 needs_human。
_LLM_REPAIR_MAX_TEXT_CHARS = 12000


def _strip_code_fence(text: str) -> str:
    """防御性处理：即使 prompt 已经要求不要用代码块包裹，模型偶尔还是会加，
    这里做个宽松的兜底剥离，跟 hybrid_exec/explorer.py::_strip_code_fence
    同款写法（wiki 模块不依赖 hybrid_exec，本地复制一份避免跨包耦合）。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines)
    return cleaned.strip() + "\n"


def _llm_repair_page_text(
    raw_text: str, error_type: str, error_message: str, llm_helper: Any,
) -> Optional[str]:
    """调用 LLM 尝试修复整篇页面文本，失败（调用异常/输出为空）返回 None，
    调用方负责用 `parse_page()` 校验输出是否真的可用——这个函数只管"问一次
    模型"，不做校验、不做落盘。"""
    if len(raw_text) > _LLM_REPAIR_MAX_TEXT_CHARS:
        return None
    prompt = (
        f"页面解析失败，错误类型：{error_type}\n"
        f"错误信息：{error_message}\n\n"
        f"原始页面文本：\n{raw_text}"
    )
    try:
        text = llm_helper.ask(prompt, system=_LLM_REPAIR_SYSTEM_PROMPT)
    except Exception:  # noqa: BLE001 - LLM 调用失败（超时/额度/网络）当作这次修复未成功
        return None
    if not text or not text.strip():
        return None
    return _strip_code_fence(text)


# ────────────────────────── 修复策略注册表 ──────────────────────────
# 每个 fixer: Callable[[dict], tuple[bool, dict]]
#   输入：yaml.safe_load 出来的 frontmatter dict（未做结构校验）
#   输出：(是否改动了, 改动后的 dict)——没改动时第二个值可以原样返回输入，
#   调用方以第一个值为准判断要不要往下走。
# 修复策略只处理"确定的格式问题"，不做语义猜测。


def _fix_string_links(fm: dict) -> tuple[bool, dict]:
    """`links` 列表里的字符串项，按 shorthand 语义理解为
    `{"target": 字符串}`（等价于只声明了 target，relation 用默认值），
    这是本模块要解决的原始故障场景。"""
    links = fm.get("links")
    if not isinstance(links, list):
        return False, fm
    changed = False
    new_links = []
    for item in links:
        if isinstance(item, str):
            new_links.append({"target": item})
            changed = True
        else:
            new_links.append(item)
    if not changed:
        return False, fm
    out = dict(fm)
    out["links"] = new_links
    return True, out


def _fix_links_not_wrapped_in_list(fm: dict) -> tuple[bool, dict]:
    """`links` 整个字段写成了单个字符串或单个字典（漏了外层 `- `），
    包一层 list——同一类"忘了这是个列表"的笔误，跟上面的策略分开是因为
    触发条件（类型判断）不同，合在一起判断容易漏掉某种组合。"""
    links = fm.get("links")
    if links is None or isinstance(links, list):
        return False, fm
    if isinstance(links, (str, dict)):
        out = dict(fm)
        out["links"] = [links]
        return True, out
    return False, fm


# 执行顺序：先把 links 包成 list，再修 list 内部的字符串项——两个策略
# 独立无副作用，顺序其实不影响结果，但这样读起来更符合"先修外层结构、
# 再修内层元素"的直觉。
_FIXERS: list[tuple[str, Callable[[dict], tuple[bool, dict]]]] = [
    ("fix_links_not_wrapped_in_list", _fix_links_not_wrapped_in_list),
    ("fix_string_links", _fix_string_links),
]


@dataclass
class RepairOutcome:
    fixed: bool
    reason: str
    applied_fixers: list[str] = field(default_factory=list)


def attempt_repair_page(page_path: Path, *, llm_helper: Any = None) -> RepairOutcome:
    """对单个页面尝试自动修复。只在改动后的内容能重新通过 `parse_page()`
    完整校验时才会真的落盘——半吊子的修复（改完还是解析失败）不写文件，
    避免把一种解析错误换成另一种。

    规则式 `_FIXERS`（改法唯一、零 LLM 成本）先跑；如果规则策略没能解决
    问题，且调用方传入了 `llm_helper`（可选，opt-in），再额外尝试一次
    LLM 兜底修复——两条路径共用同一个"改完必须能重新 parse_page() 才
    落盘"的校验闸门，LLM 路径不会比规则路径更"激进"。
    """
    if not page_path.exists():
        return RepairOutcome(fixed=False, reason="file_not_found")

    raw = page_path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return RepairOutcome(fixed=False, reason="no_frontmatter_block")

    rule_reason = "no_applicable_fixer"
    applied: list[str] = []
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except Exception as exc:
        # YAML 语法本身就是坏的（比如缩进错误、未转义的特殊字符），规则
        # 策略没有"确定改法"，不猜——但这类问题恰恰是 LLM 兜底最擅长处理
        # 的场景之一，不在这里直接 return，往下走到 LLM 兜底分支。
        rule_reason = f"yaml_syntax_error: {exc}"
        fm = None

    if isinstance(fm, dict):
        body = raw[m.end():]
        current = copy.deepcopy(fm)
        for name, fixer in _FIXERS:
            changed, current = fixer(current)
            if changed:
                applied.append(name)

        if applied:
            new_fm_text = yaml.safe_dump(current, allow_unicode=True, sort_keys=False).strip()
            new_text = f"---\n{new_fm_text}\n---\n{body}"
            try:
                parse_page(page_path, text=new_text)
            except PageParseError as exc:
                rule_reason = f"still_fails_after_repair: {exc}"
            except Exception as exc:  # noqa: BLE001 - 保险：任何解析异常都当作修复未成功
                rule_reason = f"still_fails_after_repair: {exc}"
            else:
                atomic_write_text(page_path, new_text)
                return RepairOutcome(fixed=True, reason="ok", applied_fixers=applied)
    elif fm is not None:
        rule_reason = "frontmatter_not_a_mapping"

    # ── 规则修复没能解决问题，尝试 LLM 兜底（仅当调用方传入了 llm_helper）──
    if llm_helper is None:
        return RepairOutcome(fixed=False, reason=rule_reason, applied_fixers=applied)

    llm_applied = applied + [LLM_REPAIR_NAME]
    candidate = _llm_repair_page_text(raw, "PageParseError", rule_reason, llm_helper)
    if candidate is None:
        return RepairOutcome(
            fixed=False, reason=f"llm_repair_no_output ({rule_reason})", applied_fixers=applied,
        )
    if not candidate.lstrip().startswith("---"):
        # 模型没有按要求返回带 frontmatter 的完整页面，直接当作修复失败，
        # 不去猜它是不是只返回了正文——避免用一个"看起来能过解析"但实际
        # 丢失了 frontmatter 结构的内容覆盖原文件。
        return RepairOutcome(
            fixed=False, reason=f"llm_repair_malformed_output ({rule_reason})", applied_fixers=applied,
        )

    try:
        parse_page(page_path, text=candidate)
    except PageParseError as exc:
        return RepairOutcome(
            fixed=False,
            reason=f"llm_repair_still_fails: {exc} ({rule_reason})",
            applied_fixers=applied,
        )
    except Exception as exc:  # noqa: BLE001 - 保险：任何解析异常都当作修复未成功
        return RepairOutcome(
            fixed=False,
            reason=f"llm_repair_still_fails: {exc} ({rule_reason})",
            applied_fixers=applied,
        )

    atomic_write_text(page_path, candidate)
    return RepairOutcome(fixed=True, reason="ok_llm_repair", applied_fixers=llm_applied)


@dataclass
class QuarantineRepairReport:
    scanned: int = 0
    newly_quarantined: int = 0
    auto_resolved: int = 0
    repair_attempted: int = 0
    repaired: int = 0
    llm_repaired: int = 0          # repaired 的子集：走 LLM 兜底才修好的数量
    still_failing: int = 0
    needs_human: int = 0
    skipped_missing_file: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "newly_quarantined": self.newly_quarantined,
            "auto_resolved": self.auto_resolved,
            "repair_attempted": self.repair_attempted,
            "repaired": self.repaired,
            "llm_repaired": self.llm_repaired,
            "still_failing": self.still_failing,
            "needs_human": self.needs_human,
            "skipped_missing_file": self.skipped_missing_file,
            "errors": self.errors,
        }


def run_quarantine_repair_cycle(
    paths: AgentPaths,
    *,
    max_repair_attempts: int = qz.DEFAULT_MAX_REPAIR_ATTEMPTS,
    llm_helper: Any = None,
) -> QuarantineRepairReport:
    """完整的一轮"发现 + 修复"：先全量扫描 wiki/ 更新隔离区（含自愈确认），
    再对 pending 状态、尝试次数未超限的记录逐个尝试自动修复。

    `llm_helper` 可选（opt-in，默认 None）：传入时，规则修复策略兜底失败
    的页面会额外尝试一次 LLM 修复（见 `attempt_repair_page()`）；是否要传
    由调用方根据 `MemoryConfig.wiki_quarantine_llm_repair_enabled` 决定，
    不传时零 LLM 成本，行为与改动前完全一致。

    这是 `sys:wiki_quarantine_repair` cron job 的 handler 主体，也可以被
    CLI `/wiki quarantine repair` 手动触发，两处共用同一份逻辑。
    """
    report = QuarantineRepairReport()

    scan = qz.scan_and_record(paths)
    report.scanned = scan.scanned
    report.newly_quarantined = scan.newly_quarantined
    report.auto_resolved = scan.auto_resolved
    report.errors.extend(scan.errors)

    records = qz.load_quarantine(paths)
    pending = [
        r for r in records.values()
        if r.status == qz.STATUS_PENDING and r.repair_attempts < max_repair_attempts
    ]

    for rec in pending:
        page_path = Path(rec.page_path)
        report.repair_attempted += 1
        if not page_path.exists():
            report.skipped_missing_file += 1
            continue

        try:
            outcome = attempt_repair_page(page_path, llm_helper=llm_helper)
        except Exception as exc:  # noqa: BLE001 - 单个页面修复失败不阻断整轮循环
            from mini_agent.errors import log_exception

            log_exception(exc, where="mini_agent.wiki.quarantine_repair.run_quarantine_repair_cycle")
            outcome = RepairOutcome(fixed=False, reason=f"unexpected_error: {exc}")

        all_records = qz.load_quarantine(paths)
        current = all_records.get(str(page_path))
        if current is None:
            continue  # 记录在本轮循环期间被别处摘除了，跳过

        if outcome.fixed:
            current.status = qz.STATUS_REPAIRED
            current.repaired_at = time.time()
            current.repaired_by = ",".join(outcome.applied_fixers)
            report.repaired += 1
            if LLM_REPAIR_NAME in outcome.applied_fixers:
                report.llm_repaired += 1
        else:
            current.repair_attempts += 1
            current.last_attempt_at = time.time()
            current.last_attempt_error = outcome.reason
            if current.repair_attempts >= max_repair_attempts:
                current.status = qz.STATUS_NEEDS_HUMAN
                report.needs_human += 1
            else:
                report.still_failing += 1
        all_records[str(page_path)] = current
        qz.save_quarantine(paths, all_records)

    return report


def ensure_wiki_quarantine_repair_job(
    paths: "AgentPaths",
    cron_scheduler: "CronScheduler",
    *,
    llm_helper_provider: Optional[Callable[[], Any]] = None,
) -> bool:
    """daemon 启动时调用：缺失才补注册 `sys:wiki_quarantine_repair`
    （本地回调 handler，跟 `wiki_utility_audit.py` 同构）。

    `llm_helper_provider` 可选：不传或调用返回 None 时，本 job 保持零 LLM
    成本（只跑规则修复），跟改动前行为一致。传入时（调用方通常根据
    `MemoryConfig.wiki_quarantine_llm_repair_enabled` 决定要不要传），规则
    修复兜底失败的页面会额外尝试一次 LLM 修复——惰性获取（handler 触发时
    才调用 provider），daemon 启动时 agent 可能还没就绪也不影响注册本身，
    跟 `ensure_external_trend_capability_link_job` 同款写法。
    """
    existing_ids = {j.id for j in cron_scheduler.list_jobs()}
    newly_added = JOB_ID not in existing_ids
    cron_scheduler.ensure_job(
        job_id=JOB_ID,
        name="wiki 问题页面自动修复",
        schedule="interval:21600",
        description=(
            "全量扫描 wiki/ 页面，把解析失败的页面记入隔离区（_quarantine.json），"
            "并对已知的格式性问题（如 frontmatter.links 写成裸字符串）尝试自动"
            "修复；规则修复兜底失败时，如已开启 wiki_quarantine_llm_repair_enabled"
            "则额外尝试一次 LLM 修复；超过重试上限的转人工处理（每 6 小时）。"
        ),
        tags=["maintenance", "wiki"],
    )

    def _handler(job: "CronJob") -> bool:
        helper = llm_helper_provider() if llm_helper_provider else None
        result = run_quarantine_repair_cycle(paths, llm_helper=helper)
        return result.ok

    cron_scheduler.register_local_handler(JOB_ID, _handler)
    return newly_added


__all__ = [
    "JOB_ID",
    "LLM_REPAIR_NAME",
    "RepairOutcome",
    "QuarantineRepairReport",
    "attempt_repair_page",
    "run_quarantine_repair_cycle",
    "ensure_wiki_quarantine_repair_job",
]
