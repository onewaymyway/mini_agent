"""
history/extraction_trigger.py — 轻量级抽取候选窗口探测器
（wiki 提取层与组织层改进计划 E1 §1.2.1）

把"是否该抽取"从"是否该压缩"中解耦出来：规则驱动、零 LLM 成本的候选窗口
探测，命中即交给巩固循环异步跑"仅抽取、不压缩"的 LLM 调用（见
history_manager.py::HistoryManager.maybe_trigger_extraction）。

只做"值得看一眼"的粗筛，不做语义判断——真正的抽取质量判断交给 LLM 抽取
本身（decision_extraction.py::DecisionCandidate.is_meaningful /
world_extraction.py 的 EntityCandidate/FactCandidate.is_meaningful 校验）。

本模块操作的是 raw history（RawHistory.entries，append-only、永不删减的
完整事件日志），而不是 compact 会清空重置的 active history——这正是
"抽取时机与 compact 解耦"的关键：raw history 的坐标不会因为 compact
发生而失效，`last_extracted_index` 可以稳定地持久化为一个单调递增的游标
（见 storage/paths.py::AgentPaths.extraction_cursor_path）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from mini_agent.history.entry import is_turn_boundary

# 连接词密度触发的默认关键词表（计划原文 §1.2.1）。
_DEFAULT_CONNECTIVE_KEYWORDS = (
    "因为", "所以", "决定", "改为", "放弃", "取代", "而不是",
)

# 连接词命中次数 / 文本长度（每 100 字符）达到此密度视为"值得看一眼"。
# 阈值本身没有理论依据，属于计划 §1.4 提到的"需要用真实数据校准"的参数，
# 默认先偏保守（不轻易触发），线上跑 extraction_trigger_log.jsonl 一段
# 时间后再调整。
_DEFAULT_CONNECTIVE_DENSITY_THRESHOLD = 0.6  # 每 100 字符至少 0.6 次命中

# 改进计划第 4 节：独立的"实体密度"触发信号，和上面的连接词密度并列、
# 互不干扰——connective_density 本质上是"决策/纠正语境探测器"，对纯描述性的
# 世界知识（"这个项目用 FastAPI + PostgreSQL"）天然低敏感，永远攒不够密度。
# 这里用一组很粗的正则去抓"看起来像专有名词/版本号/路径"的候选词，
# 不做真正 NER（成本要保持零 LLM），宁可粗一点多触发几次。
import re as _re

_ENTITY_TERM_PATTERNS = (
    # CamelCase / PascalCase 词（如 FastAPI、PostgreSQL）
    _re.compile(r"\b[A-Z][a-zA-Z]*[A-Z][a-zA-Z]*\b"),
    # 名称+版本号（如 Python3.11、React 18、node v20）
    _re.compile(r"\b[A-Za-z][A-Za-z._-]{1,20}\s?v?\d+(?:\.\d+){0,3}\b"),
    # 路径/配置项风格token（含 / 或 . 且长度 >=4，避免命中普通小数）
    _re.compile(r"\b[\w-]+(?:[./][\w-]+){1,}\b"),
    # 全大写缩写（3 位以上，如 API、SDK、ORM）
    _re.compile(r"\b[A-Z]{3,}\b"),
)

_DEFAULT_MIN_NEW_ENTITY_TERMS = 3


def _extract_entity_terms(entries: list[dict]) -> set[str]:
    """从条目文本里用一组粗正则抓候选实体词，不做语义判断。"""
    text = "".join(_extract_text(e) for e in entries)
    if not text:
        return set()
    terms: set[str] = set()
    for pattern in _ENTITY_TERM_PATTERNS:
        for m in pattern.finditer(text):
            token = m.group(0).strip()
            if token:
                terms.add(token)
    return terms


def load_known_entity_names(paths) -> set[str]:
    """从已有 wiki entity 页面粗略取一份"已知名字"集合，用于过滤掉
    `_extract_entity_terms()` 里已经在 wiki 里出现过的词，避免对已经记录过的
    实体反复触发。只用页面 id 的原始 slug 分词做近似匹配（不追求精确），
    读取失败（wiki 目录不存在等）返回空集合，调用方据此退化为"所有候选词
    都算新词"，宁可多触发也不要因为读取失败永久停止触发。
    """
    try:
        from mini_agent.wiki.indexer import discover_pages
        from mini_agent.wiki.parser import parse_page
    except ImportError:
        return set()
    names: set[str] = set()
    try:
        for p in discover_pages(paths):
            if "entities" not in str(p):
                continue
            try:
                page = parse_page(p)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.history.extraction_trigger.load_known_entity_names')
                continue
            names.add(page.id.lower())
            names.update(part.lower() for part in page.id.split("-") if len(part) > 2)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.history.extraction_trigger.load_known_entity_names')
        return set()
    return names


def _entity_density_new_terms(
    entries: list[dict], known_entity_names: frozenset[str]
) -> set[str]:
    terms = _extract_entity_terms(entries)
    if not known_entity_names:
        return terms
    return {t for t in terms if t.lower() not in known_entity_names}


@dataclass
class ExtractionWindowCandidate:
    """一次候选抽取窗口的探测结果。"""

    start_index: int          # raw history 起始条目 index（含）
    end_index: int             # 结束 index（不含）——不再总是 len(raw_entries)，
                                # 见 `max_window_chars`：超过预算时会被提前截断，
                                # 剩余部分留到下次继续扫描。
    trigger_reason: str        # "connective_density" | "entity_density" | "turn_count"
                                # | "size_cap" | "session_end"
    signal_score: float        # 触发强度，用于排队优先级（本模块不做排队，仅透传）
    truncated: bool = False    # 本次候选窗口是否因为超出 max_window_chars 被截断
                                # （即游标之后其实还有更多未纳入本窗口的新增内容）


def _entries_char_len(entries: list[dict]) -> int:
    """粗略估算一段 raw entries 的字符总量（与 compact._compact_chunked() 里
    `turn_chars = sum(len(str(m.get("content", ""))) for m in turn_msgs)` 是
    同一套估算口径，只取 content 字段的字符串化长度，不做精确 token 计数——
    这里只需要一个足够便宜、足够单调的信号来做窗口预算切分，不需要精确。
    """
    return sum(len(str(e.get("content", ""))) for e in entries)


def _extract_text(msg: dict) -> str:
    """从一条 raw history 条目里提取纯文本内容，兜底处理 list content。

    与 history/triggers.py::_extract_text、history/compact_audit.py::
    _extract_text 是同一逻辑的独立实现——这三处都只是"给一段轻量启发式
    扫描用的纯文本提取"，不构成需要抽公共模块的复杂度，保持各自独立、
    互不依赖是项目里已有的既定风格。
    """
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return " ".join(parts)
    return ""


def _connective_density(entries: list[dict], keywords: tuple[str, ...]) -> float:
    """连接词命中次数 / 文本长度（每 100 字符），文本为空时返回 0。"""
    text = "".join(_extract_text(e) for e in entries)
    if not text:
        return 0.0
    hits = sum(text.count(kw) for kw in keywords)
    return hits / len(text) * 100


def _cap_window_by_chars(
    raw_entries: list[dict], start_index: int, end_index: int, max_chars: int
) -> tuple[int, bool]:
    """在 `[start_index, end_index)` 范围内，按字符预算从 `start_index` 起
    尽量多地纳入条目，返回 `(capped_end_index, truncated)`。

    与 compaction.py::_compact_chunked() 的 chunk 切分策略同一个思路：
    - 至少纳入一条（即使这一条本身已经超过预算——单条极端巨大是"内容
      本身超限"，交给 history_manager.py 里 `_dispatch_extraction_window()`
      的递归二分兜底，不是这里要解决的问题；这里只负责"别让窗口无限
      累积变大"）。
    - `truncated=True` 表示预算不够纳入 `end_index` 之前的全部新增内容，
      调用方应把这次候选窗口标记为提前截断（游标之后还有剩余内容，留到
      下次继续扫描，不会丢失）。
    """
    total_chars = 0
    cursor = start_index
    for i in range(start_index, end_index):
        entry_chars = _entries_char_len([raw_entries[i]])
        if cursor > start_index and total_chars + entry_chars > max_chars:
            break
        total_chars += entry_chars
        cursor = i + 1
    return cursor, cursor < end_index


def scan_for_extraction_window(
    raw_entries: list[dict],
    *,
    last_extracted_index: int,
    min_window_turns: int = 6,
    connective_keywords: tuple[str, ...] = _DEFAULT_CONNECTIVE_KEYWORDS,
    connective_density_threshold: float = _DEFAULT_CONNECTIVE_DENSITY_THRESHOLD,
    known_entity_names: frozenset[str] = frozenset(),
    min_new_entity_terms: int = _DEFAULT_MIN_NEW_ENTITY_TERMS,
    max_window_chars: Optional[int] = None,
) -> Optional[ExtractionWindowCandidate]:
    """规则驱动、零 LLM 成本的候选窗口探测（计划 §1.2.1 触发规则 1/2，
    改进计划第 4 节新增规则 1b；`max_window_chars` 为
    next_doc/extraction_window_oversize_chunking_fix.md §7 补充的窗口预算
    上限）。

    触发规则（满足任一即返回候选窗口，按下面的顺序判断）：
    0. **窗口预算上限**（`max_window_chars`，若传入且 > 0）：游标之后新增
       内容的字符数一旦超过这个预算，不再等待连接词/实体/轮次三条规则
       是否命中，直接就地按预算截断、返回一个 `trigger_reason="size_cap"`
       的候选窗口——这是从源头避免"长期没有触发（比如只在跑工具、用户
       输入轮次很少但工具输出本身很大）→未处理条目持续累积→最终一次性
       打包成远超模型上下文的超大窗口"这个问题（history_manager.py 里的
       递归二分只是超限后的事后兜底，这里是提前预防）。触发后不会丢内容：
       `end_index` 只是被限制在预算内，剩余部分保留在 raw_entries 里，
       下次扫描时 `last_extracted_index` 已经推进到这里，会继续被看到。
    1. **连接词密度**：`last_extracted_index` 之后新增条目文本中，
       "因为/所以/决定/改为/放弃/取代/而不是"等词的密度超过阈值。
       这条规则本质上是"决策/纠正语境探测器"。
    1b. **实体密度**（改进计划第 4 节）：新增条目里出现的"看起来像专有名词/
       版本号/路径"的候选词（不在 `known_entity_names` 里的"新词"）数量达到
       `min_new_entity_terms`。这条规则是 1 的补充，专门覆盖 1 抓不到的
       纯描述性世界知识（"这个项目用 FastAPI + PostgreSQL"这类没有转折词的
       陈述句）——两条规则触发原因分开记录在 `trigger_reason` 里，方便后续
       统计各自覆盖的知识类型是否真的不同，而不是重叠冗余。
    2. **轮次计数**：新增条目里的真实用户输入轮次（`is_turn_boundary`）
       达到 `min_window_turns`，无论连接词密度如何——避免长期空转、话题
       平淡但确实积累了内容的 session 永远不被抽取。

    第 3 条"session 结束兜底"不在本函数里实现（本函数只看"新增了多少"，
    不感知"session 是否结束"这一外部事件），由调用方
    （agent/lifecycle.py::close()）在 session 结束时以
    `force=True` 的方式单独处理，见 history_manager.py::
    maybe_trigger_extraction 的 force 参数（`force=True` 路径不经过本函数，
    因此不受 `max_window_chars` 约束——session 结束时的兜底仍然是"一次性
    收尾剩余全部内容"，超限风险由 history_manager.py 的递归二分兜底吸收）。

    `last_extracted_index` 越界（大于 `len(raw_entries)`，比如 cursor
    文件损坏/手动改坏）时视为"没有新内容"，返回 None，不抛异常。
    """
    if last_extracted_index < 0:
        last_extracted_index = 0
    if last_extracted_index >= len(raw_entries):
        return None

    full_end_index = len(raw_entries)
    new_entries_probe = raw_entries[last_extracted_index:full_end_index]
    if not new_entries_probe:
        return None

    end_index = full_end_index
    if max_window_chars is not None and max_window_chars > 0:
        end_index, truncated = _cap_window_by_chars(
            raw_entries, last_extracted_index, full_end_index, max_window_chars
        )
        if truncated:
            capped_entries = raw_entries[last_extracted_index:end_index]
            return ExtractionWindowCandidate(
                start_index=last_extracted_index,
                end_index=end_index,
                trigger_reason="size_cap",
                signal_score=float(_entries_char_len(capped_entries)),
                truncated=True,
            )

    new_entries = raw_entries[last_extracted_index:end_index]
    if not new_entries:
        return None

    density = _connective_density(new_entries, connective_keywords)
    if density >= connective_density_threshold:
        return ExtractionWindowCandidate(
            start_index=last_extracted_index,
            end_index=end_index,
            trigger_reason="connective_density",
            signal_score=density,
        )

    new_terms = _entity_density_new_terms(new_entries, known_entity_names)
    if len(new_terms) >= min_new_entity_terms:
        return ExtractionWindowCandidate(
            start_index=last_extracted_index,
            end_index=end_index,
            trigger_reason="entity_density",
            signal_score=float(len(new_terms)),
        )

    turn_count = sum(1 for e in new_entries if is_turn_boundary(e))
    if turn_count >= min_window_turns:
        return ExtractionWindowCandidate(
            start_index=last_extracted_index,
            end_index=end_index,
            trigger_reason="turn_count",
            signal_score=float(turn_count),
        )

    return None


def _read_json_dict(path) -> dict:
    if not path.exists():
        return {}
    try:
        import json
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.history.extraction_trigger._read_json_dict')
        return {}


def load_extraction_cursor(paths) -> int:
    """读取持久化的 `last_extracted_index`（`AgentPaths.extraction_cursor_path`）。

    文件不存在/损坏/字段缺失均返回 0（视为"还没抽取过任何内容"），不抛
    异常——游标读取属于非关键路径，读取失败不应阻断本轮触发判断，只是
    退化为"从头再扫一次"（`scan_for_extraction_window` 本身对重复扫描是
    幂等的：已经被抽取过的内容不会因为再扫一次而重复入队，因为决定"是否
    入队"的是本模块的密度/轮次判断，不会对同一段内容产生两次候选——除非
    游标真的丢失，这种情况下宁可多做一次扫描，也不能因为读游标失败而
    永久停止抽取）。
    """
    data = _read_json_dict(paths.extraction_cursor_path)
    try:
        return max(0, int(data.get("last_extracted_index", 0)))
    except (TypeError, ValueError):
        return 0


def save_extraction_cursor(paths, last_extracted_index: int) -> None:
    """原子写入 `last_extracted_index`。写入失败静默跳过（非关键路径）。"""
    import json
    import os
    import tempfile

    path = paths.extraction_cursor_path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"last_extracted_index": last_extracted_index}, f)
                f.flush()
                os.fsync(f.fileno())
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.history.extraction_trigger.save_extraction_cursor')
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return
        os.replace(tmp, path)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.history.extraction_trigger.save_extraction_cursor')
        pass


def log_extraction_trigger_event(
    paths, candidate: ExtractionWindowCandidate, *, dispatched: bool
) -> None:
    """把一次候选窗口命中记录追加到 `extraction_trigger_log.jsonl`（计划
    §1.4）：先只记录、不发起 LLM 调用的校准阶段，靠这份日志判断触发器
    "从不命中"还是"过于敏感"，用真实数据调整阈值再打开 `dispatched=True`
    的实际抽取开关。纯观测、append-only，写入失败静默跳过。
    """
    import json
    import time

    try:
        path = paths.extraction_trigger_log
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": time.time(),
            "start_index": candidate.start_index,
            "end_index": candidate.end_index,
            "trigger_reason": candidate.trigger_reason,
            "signal_score": candidate.signal_score,
            "dispatched": dispatched,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.history.extraction_trigger.log_extraction_trigger_event')
        pass


__all__ = [
    "ExtractionWindowCandidate",
    "scan_for_extraction_window",
    "load_extraction_cursor",
    "save_extraction_cursor",
    "log_extraction_trigger_event",
]
