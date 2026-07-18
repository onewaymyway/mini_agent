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


@dataclass
class ExtractionWindowCandidate:
    """一次候选抽取窗口的探测结果。"""

    start_index: int          # raw history 起始条目 index（含）
    end_index: int             # 结束 index（不含），即调用时刻的 len(raw_entries)
    trigger_reason: str        # "connective_density" | "turn_count" | "session_end"
    signal_score: float        # 触发强度，用于排队优先级（本模块不做排队，仅透传）


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


def scan_for_extraction_window(
    raw_entries: list[dict],
    *,
    last_extracted_index: int,
    min_window_turns: int = 6,
    connective_keywords: tuple[str, ...] = _DEFAULT_CONNECTIVE_KEYWORDS,
    connective_density_threshold: float = _DEFAULT_CONNECTIVE_DENSITY_THRESHOLD,
) -> Optional[ExtractionWindowCandidate]:
    """规则驱动、零 LLM 成本的候选窗口探测（计划 §1.2.1 触发规则 1/2）。

    触发规则（满足任一即返回候选窗口）：
    1. **连接词密度**：`last_extracted_index` 之后新增条目文本中，
       "因为/所以/决定/改为/放弃/取代/而不是"等词的密度超过阈值。
    2. **轮次计数**：新增条目里的真实用户输入轮次（`is_turn_boundary`）
       达到 `min_window_turns`，无论连接词密度如何——避免长期空转、话题
       平淡但确实积累了内容的 session 永远不被抽取。

    第 3 条"session 结束兜底"不在本函数里实现（本函数只看"新增了多少"，
    不感知"session 是否结束"这一外部事件），由调用方
    （agent/lifecycle.py::close()）在 session 结束时以
    `force=True` 的方式单独处理，见 history_manager.py::
    maybe_trigger_extraction 的 force 参数。

    `last_extracted_index` 越界（大于 `len(raw_entries)`，比如 cursor
    文件损坏/手动改坏）时视为"没有新内容"，返回 None，不抛异常。
    """
    if last_extracted_index < 0:
        last_extracted_index = 0
    if last_extracted_index >= len(raw_entries):
        return None

    new_entries = raw_entries[last_extracted_index:]
    if not new_entries:
        return None

    end_index = len(raw_entries)

    density = _connective_density(new_entries, connective_keywords)
    if density >= connective_density_threshold:
        return ExtractionWindowCandidate(
            start_index=last_extracted_index,
            end_index=end_index,
            trigger_reason="connective_density",
            signal_score=density,
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
    except Exception:
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
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return
        os.replace(tmp, path)
    except Exception:
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
    except Exception:
        pass


__all__ = [
    "ExtractionWindowCandidate",
    "scan_for_extraction_window",
    "load_extraction_cursor",
    "save_extraction_cursor",
    "log_extraction_trigger_event",
]
