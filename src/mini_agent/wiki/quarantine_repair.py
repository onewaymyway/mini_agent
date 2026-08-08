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
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

import yaml

from mini_agent.storage.paths import AgentPaths
from mini_agent.utils.atomic_write import atomic_write_text
from mini_agent.wiki import quarantine as qz
from mini_agent.wiki.parser import _FRONTMATTER_RE, PageParseError, parse_page

if TYPE_CHECKING:
    from mini_agent.evolution.cron_scheduler import CronJob, CronScheduler

JOB_ID = "sys:wiki_quarantine_repair"


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


def attempt_repair_page(page_path: Path) -> RepairOutcome:
    """对单个页面尝试自动修复。只在改动后的内容能重新通过 `parse_page()`
    完整校验时才会真的落盘——半吊子的修复（改完还是解析失败）不写文件，
    避免把一种解析错误换成另一种。
    """
    if not page_path.exists():
        return RepairOutcome(fixed=False, reason="file_not_found")

    raw = page_path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return RepairOutcome(fixed=False, reason="no_frontmatter_block")

    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except Exception as exc:
        # YAML 语法本身就是坏的（比如缩进错误、未转义的特殊字符），
        # 这类问题没有"确定改法"，不猜，交给人工。
        return RepairOutcome(fixed=False, reason=f"yaml_syntax_error: {exc}")

    if not isinstance(fm, dict):
        return RepairOutcome(fixed=False, reason="frontmatter_not_a_mapping")

    body = raw[m.end():]
    applied: list[str] = []
    current = copy.deepcopy(fm)
    for name, fixer in _FIXERS:
        changed, current = fixer(current)
        if changed:
            applied.append(name)

    if not applied:
        return RepairOutcome(fixed=False, reason="no_applicable_fixer")

    new_fm_text = yaml.safe_dump(current, allow_unicode=True, sort_keys=False).strip()
    new_text = f"---\n{new_fm_text}\n---\n{body}"

    try:
        parse_page(page_path, text=new_text)
    except PageParseError as exc:
        return RepairOutcome(
            fixed=False,
            reason=f"still_fails_after_repair: {exc}",
            applied_fixers=applied,
        )
    except Exception as exc:  # noqa: BLE001 - 保险：任何解析异常都当作修复未成功
        return RepairOutcome(
            fixed=False,
            reason=f"still_fails_after_repair: {exc}",
            applied_fixers=applied,
        )

    atomic_write_text(page_path, new_text)
    return RepairOutcome(fixed=True, reason="ok", applied_fixers=applied)


@dataclass
class QuarantineRepairReport:
    scanned: int = 0
    newly_quarantined: int = 0
    auto_resolved: int = 0
    repair_attempted: int = 0
    repaired: int = 0
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
            "still_failing": self.still_failing,
            "needs_human": self.needs_human,
            "skipped_missing_file": self.skipped_missing_file,
            "errors": self.errors,
        }


def run_quarantine_repair_cycle(
    paths: AgentPaths, *, max_repair_attempts: int = qz.DEFAULT_MAX_REPAIR_ATTEMPTS
) -> QuarantineRepairReport:
    """完整的一轮"发现 + 修复"：先全量扫描 wiki/ 更新隔离区（含自愈确认），
    再对 pending 状态、尝试次数未超限的记录逐个尝试自动修复。

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
            outcome = attempt_repair_page(page_path)
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
    paths: "AgentPaths", cron_scheduler: "CronScheduler",
) -> bool:
    """daemon 启动时调用：缺失才补注册 `sys:wiki_quarantine_repair`
    （零 LLM 成本，本地回调 handler，跟 `wiki_utility_audit.py` 同构）。"""
    existing_ids = {j.id for j in cron_scheduler.list_jobs()}
    newly_added = JOB_ID not in existing_ids
    cron_scheduler.ensure_job(
        job_id=JOB_ID,
        name="wiki 问题页面自动修复",
        schedule="interval:21600",
        description=(
            "全量扫描 wiki/ 页面，把解析失败的页面记入隔离区（_quarantine.json），"
            "并对已知的格式性问题（如 frontmatter.links 写成裸字符串）尝试自动"
            "修复；超过重试上限的转人工处理，零 LLM 成本（每 6 小时）。"
        ),
        tags=["maintenance", "wiki"],
    )

    def _handler(job: "CronJob") -> bool:
        result = run_quarantine_repair_cycle(paths)
        return result.ok

    cron_scheduler.register_local_handler(JOB_ID, _handler)
    return newly_added


__all__ = [
    "JOB_ID",
    "RepairOutcome",
    "QuarantineRepairReport",
    "attempt_repair_page",
    "run_quarantine_repair_cycle",
    "ensure_wiki_quarantine_repair_job",
]
