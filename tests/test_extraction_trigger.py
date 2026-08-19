"""
tests/test_extraction_trigger.py — wiki 提取层与组织层改进计划 E1 单元测试

覆盖：
  - history/extraction_trigger.py::scan_for_extraction_window 对连接词密度
    /轮次计数/零信号三种场景的判定，以及游标越界时的降级行为
  - load_extraction_cursor/save_extraction_cursor 的读写与容错
  - log_extraction_trigger_event 的 append 格式
  - history_manager.py::HistoryManager.maybe_trigger_extraction 的完整链路：
    默认关闭时不产生任何副作用；开启但 dispatch 关闭时只记录候选窗口、
    不发起 LLM 调用、不推进 cursor；dispatch 开启时发起 LLM 调用、解析
    结果入队、推进 cursor（幂等：同一段不会被抽两次）
"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, "src")

from mini_agent.history.entry import make_user_input, make_assistant_reply, make_tool_result
from mini_agent.history.extraction_trigger import (
    ExtractionWindowCandidate,
    load_extraction_cursor,
    log_extraction_trigger_event,
    save_extraction_cursor,
    scan_for_extraction_window,
)
from mini_agent.history_manager import HistoryManager
from mini_agent.storage.paths import AgentPaths


@pytest.fixture
def paths(tmp_path):
    p = AgentPaths(tmp_path)
    p.ensure_wiki_dirs()
    return p


def _turn(text: str) -> list[dict]:
    return [
        make_user_input(text),
        make_assistant_reply([{"type": "text", "text": "ok"}]),
    ]


# ── scan_for_extraction_window ───────────────────────────────────────────

def test_scan_returns_none_when_no_new_entries():
    entries = [*_turn("hello")]
    assert scan_for_extraction_window(entries, last_extracted_index=len(entries)) is None


def test_scan_returns_none_when_cursor_out_of_range():
    entries = [*_turn("hello")]
    assert scan_for_extraction_window(entries, last_extracted_index=999) is None


def test_scan_detects_connective_density():
    entries = [
        make_user_input("因为旧方案有性能问题，所以我们决定改为新的批处理实现，而不是继续用逐条写入。"),
    ]
    candidate = scan_for_extraction_window(entries, last_extracted_index=0, min_window_turns=100)
    assert candidate is not None
    assert candidate.trigger_reason == "connective_density"
    assert candidate.start_index == 0
    assert candidate.end_index == len(entries)


def test_scan_detects_turn_count_without_connective_density():
    entries: list[dict] = []
    for i in range(6):
        entries.extend(_turn(f"随便聊聊第 {i} 件事，没有任何取舍或转折。"))
    candidate = scan_for_extraction_window(
        entries, last_extracted_index=0, min_window_turns=6,
        connective_density_threshold=999.0,  # 密度阈值故意设极高，只测轮次信号
    )
    assert candidate is not None
    assert candidate.trigger_reason == "turn_count"


def test_scan_returns_none_when_neither_signal_hits():
    entries = [*_turn("今天天气不错。")]
    candidate = scan_for_extraction_window(
        entries, last_extracted_index=0, min_window_turns=6,
    )
    assert candidate is None


def test_scan_detects_entity_density_for_descriptive_content():
    # 改进计划第 4 节：纯描述性内容（没有转折/决策连接词）也应该能触发，
    # 覆盖 connective_density 天然抓不到的世界知识场景。
    entries = [
        make_user_input(
            "这个项目用 FastAPI 和 PostgreSQL，部署在 AWS 上，配置文件在 config/app.yaml"
        ),
    ]
    candidate = scan_for_extraction_window(
        entries, last_extracted_index=0, min_window_turns=100,
    )
    assert candidate is not None
    assert candidate.trigger_reason == "entity_density"


def test_scan_entity_density_filters_known_entity_names():
    entries = [
        make_user_input("这个项目用 FastAPI，部署在 AWS 上。"),
    ]
    # 三个候选词（FastAPI/AWS + 可能的其它匹配）都已经在 known_entity_names 里，
    # 不应该再触发。
    candidate = scan_for_extraction_window(
        entries, last_extracted_index=0, min_window_turns=100,
        known_entity_names=frozenset({"fastapi", "aws"}),
    )
    assert candidate is None


def test_scan_connective_density_takes_priority_over_entity_density():
    # 两个信号都能命中时，connective_density 检查在前，优先返回它。
    entries = [
        make_user_input(
            "因为 FastAPI 在 AWS 上部署有性能问题，所以我们决定改为 PostgreSQL 连接池方案。"
        ),
    ]
    candidate = scan_for_extraction_window(entries, last_extracted_index=0, min_window_turns=100)
    assert candidate is not None
    assert candidate.trigger_reason == "connective_density"


# ── max_window_chars 窗口预算上限 ───────────────────────────────────────────
# next_doc/extraction_window_oversize_chunking_fix.md §7 后续优化：窗口预算
# 上限应该在"长期不触发三条规则、内容持续累积"时提前截断，而不是等超限了
# 才靠 history_manager.py 里的递归二分事后补救。


def test_scan_size_cap_truncates_when_budget_exceeded():
    # 构造一堆不含连接词/实体特征、轮次也不够 min_window_turns 的平淡内容，
    # 单靠原有三条规则永远不会触发；但总字符数超过 max_window_chars。
    entries: list[dict] = []
    for i in range(4):
        entries.extend(_turn("啊" * 50))
    total_chars = sum(len(str(e.get("content", ""))) for e in entries)
    budget = total_chars // 2  # 预算只够纳入大约一半内容

    candidate = scan_for_extraction_window(
        entries, last_extracted_index=0, min_window_turns=100,
        connective_density_threshold=999.0,
        max_window_chars=budget,
    )
    assert candidate is not None
    assert candidate.trigger_reason == "size_cap"
    assert candidate.truncated is True
    assert candidate.start_index == 0
    assert 0 < candidate.end_index < len(entries)


def test_scan_size_cap_always_includes_at_least_one_entry():
    # 预算小到连第一条都放不下时，仍然至少纳入一条（不能返回空窗口），
    # 单条极端巨大的兜底交给 history_manager.py 的递归二分处理。
    entries = [*_turn("一段很长的内容" * 20)]
    candidate = scan_for_extraction_window(
        entries, last_extracted_index=0, min_window_turns=100,
        max_window_chars=1,
    )
    assert candidate is not None
    assert candidate.trigger_reason == "size_cap"
    assert candidate.end_index >= 1


def test_scan_size_cap_does_not_trigger_when_within_budget():
    # 预算充足时，size_cap 不应该抢在其它规则前面误触发；这里内容本身
    # 也不满足其它规则，所以应该整体返回 None。
    entries = [*_turn("今天天气不错。")]
    candidate = scan_for_extraction_window(
        entries, last_extracted_index=0, min_window_turns=6,
        max_window_chars=1_000_000,
    )
    assert candidate is None


def test_scan_size_cap_defers_to_connective_density_when_within_budget():
    # 预算足够容纳全部新增内容时，正常走原有三条规则，size_cap 不介入、
    # 不影响 end_index（仍然是全部新增内容的末尾）。
    entries = [
        make_user_input("因为旧方案有性能问题，所以我们决定改为新的批处理实现。"),
    ]
    candidate = scan_for_extraction_window(
        entries, last_extracted_index=0, min_window_turns=100,
        max_window_chars=1_000_000,
    )
    assert candidate is not None
    assert candidate.trigger_reason == "connective_density"
    assert candidate.end_index == len(entries)


# ── cursor 持久化 ─────────────────────────────────────────────────────────

def test_load_cursor_defaults_to_zero_when_missing(paths):
    assert load_extraction_cursor(paths) == 0


def test_save_and_load_cursor_round_trip(paths):
    save_extraction_cursor(paths, 42)
    assert load_extraction_cursor(paths) == 42
    save_extraction_cursor(paths, 100)
    assert load_extraction_cursor(paths) == 100


def test_load_cursor_tolerates_corrupt_file(paths):
    paths.extraction_cursor_path.parent.mkdir(parents=True, exist_ok=True)
    paths.extraction_cursor_path.write_text("not json", encoding="utf-8")
    assert load_extraction_cursor(paths) == 0


def test_log_extraction_trigger_event_appends(paths):
    candidate = ExtractionWindowCandidate(0, 5, "turn_count", 6.0)
    log_extraction_trigger_event(paths, candidate, dispatched=False)
    log_extraction_trigger_event(paths, candidate, dispatched=True)

    lines = paths.extraction_trigger_log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["trigger_reason"] == "turn_count"
    assert first["dispatched"] is False
    second = json.loads(lines[1])
    assert second["dispatched"] is True


# ── HistoryManager.maybe_trigger_extraction ─────────────────────────────

class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeLLMClient:
    def __init__(self, text: str) -> None:
        self._text = text
        self.calls = 0

    def chat_with_retry(self, **kwargs):
        self.calls += 1
        return _FakeResponse(self._text)


def _cfg(paths, **compress_overrides) -> SimpleNamespace:
    compress = SimpleNamespace(
        extraction_trigger_enabled=False,
        extraction_trigger_dispatch_enabled=False,
        extraction_trigger_min_window_turns=1,
        max_message_chars_for_compact=10000,
        entity_digest_enabled=False,
        extract_decisions=True,
        extract_world_model=True,
        strategy="turn_aligned",
    )
    for k, v in compress_overrides.items():
        setattr(compress, k, v)
    return SimpleNamespace(project_root=str(paths.project_root), compress=compress)


def _seed_history_manager(cfg) -> HistoryManager:
    hist = HistoryManager(cfg)
    hist.append_user("因为旧方案有性能问题，所以我们决定改为新的批处理实现。")
    hist.append_assistant(_FakeAssistantResponse())
    return hist


class _FakeAssistantResponse:
    text = "好的，已经改用批处理实现。"
    tool_calls = []


def test_maybe_trigger_extraction_noop_when_disabled(paths):
    cfg = _cfg(paths, extraction_trigger_enabled=False)
    hist = _seed_history_manager(cfg)
    llm = _FakeLLMClient("{}")

    hist.maybe_trigger_extraction(llm_client=llm)

    assert llm.calls == 0
    assert not paths.extraction_trigger_log.exists()
    assert load_extraction_cursor(paths) == 0


def test_maybe_trigger_extraction_logs_only_when_dispatch_disabled(paths):
    cfg = _cfg(paths, extraction_trigger_enabled=True, extraction_trigger_dispatch_enabled=False)
    hist = _seed_history_manager(cfg)
    llm = _FakeLLMClient("{}")

    hist.maybe_trigger_extraction(llm_client=llm)

    assert llm.calls == 0  # 校准阶段不发起 LLM 调用
    assert paths.extraction_trigger_log.exists()
    # cursor 不推进：下次仍会看到同一段新增内容
    assert load_extraction_cursor(paths) == 0


def test_maybe_trigger_extraction_dispatches_and_queues_candidates(paths):
    cfg = _cfg(paths, extraction_trigger_enabled=True, extraction_trigger_dispatch_enabled=True)
    hist = _seed_history_manager(cfg)

    llm_text = json.dumps({
        "decisions": [{
            "topic": "批处理 vs 逐条写入",
            "options_considered": ["逐条写入", "批处理"],
            "chosen": "批处理",
            "rejected_because": {"逐条写入": "性能问题"},
            "related_entities": [],
        }],
        "entities": [],
        "facts": [],
        "compact_summary": "",
    })
    llm = _FakeLLMClient(llm_text)

    hist.maybe_trigger_extraction(llm_client=llm)

    assert llm.calls == 1
    # cursor 应该推进到当前 raw 长度
    assert load_extraction_cursor(paths) == len(hist.raw_history.entries)

    # 决策候选应该已经入队
    pending_path = paths.decision_candidates_pending_path
    assert pending_path.exists()
    rows = [json.loads(line) for line in pending_path.read_text(encoding="utf-8").splitlines()]
    assert any("批处理" in r.get("candidate", {}).get("chosen", "") for r in rows)


def test_maybe_trigger_extraction_is_idempotent_after_dispatch(paths):
    cfg = _cfg(paths, extraction_trigger_enabled=True, extraction_trigger_dispatch_enabled=True)
    hist = _seed_history_manager(cfg)
    llm = _FakeLLMClient(json.dumps({
        "decisions": [], "entities": [], "facts": [], "compact_summary": "",
    }))

    hist.maybe_trigger_extraction(llm_client=llm)
    assert llm.calls == 1
    cursor_after_first = load_extraction_cursor(paths)

    # 没有新增内容时，再次调用不应该产生新的 LLM 调用（cursor 已经追上）
    hist.maybe_trigger_extraction(llm_client=llm)
    assert llm.calls == 1
    assert load_extraction_cursor(paths) == cursor_after_first


def test_maybe_trigger_extraction_force_ignores_rule_thresholds(paths):
    # min_window_turns 设得很高、且内容里没有连接词——常规规则判定应该不命中，
    # 但 force=True 应该无视规则直接触发。
    cfg = _cfg(
        paths, extraction_trigger_enabled=True, extraction_trigger_dispatch_enabled=True,
        extraction_trigger_min_window_turns=1000,
    )
    hist = HistoryManager(cfg)
    hist.append_user("今天天气不错。")
    llm = _FakeLLMClient(json.dumps({
        "decisions": [], "entities": [], "facts": [], "compact_summary": "",
    }))

    hist.maybe_trigger_extraction(llm_client=llm, force=False)
    assert llm.calls == 0  # 常规规则不命中

    hist.maybe_trigger_extraction(llm_client=llm, force=True)
    assert llm.calls == 1  # session 结束兜底命中
