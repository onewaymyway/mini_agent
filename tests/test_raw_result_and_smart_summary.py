"""
tests/test_raw_result_and_smart_summary.py

覆盖 [SYS-RAWSTORE] / [SYS-SMARTTRIM] 的新增能力：
  1. RawResultStore 基本存取 + LRU/总字符数淘汰
  2. ToolExecutor._trim_result 在规则截断路径下会留存原文并附带 result_id 提示
  3. smart_summary 开启时优先走 LLM 摘要；LLM 失败时自动降级为规则截断
  4. view_raw_result 工具能取回完整原文（含行号范围）；result_id 不存在时报错提示
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from mini_agent.perception.raw_result_store import RawResultStore
from mini_agent.tool_executor import ToolExecutor
from mini_agent.config import load_config


def make_cfg():
    cfg = load_config()
    cfg.api_key = "test"
    cfg.stream = False
    cfg.tool_trim.enabled = True  # 该 loader 默认 enabled=False（无配置文件时的既有行为），测试里显式开启
    return cfg


def make_executor(cfg, llm_client=None, raw_result_store=None) -> ToolExecutor:
    from mini_agent.permissions import PermissionGuard
    from mini_agent.tools import get_default_registry
    from mini_agent.config import SessionStats

    return ToolExecutor(
        cfg=cfg,
        registry=get_default_registry(),
        guard=PermissionGuard(auto_approve=True, sandbox=False, project_root=cfg.project_root),
        stats=SessionStats(),
        llm_client=llm_client,
        raw_result_store=raw_result_store,
    )


# ── RawResultStore ───────────────────────────────────────────────────────────

def test_raw_result_store_put_get_roundtrip():
    store = RawResultStore(max_entries=10, max_total_chars=1_000_000)
    rid = store.put("hello world " * 100, tool_name="bash")
    assert store.get(rid) == "hello world " * 100


def test_raw_result_store_dedup_same_content():
    store = RawResultStore(max_entries=10, max_total_chars=1_000_000)
    content = "same content " * 50
    rid1 = store.put(content, tool_name="bash")
    rid2 = store.put(content, tool_name="grep")
    assert rid1 == rid2


def test_raw_result_store_missing_id_returns_none():
    store = RawResultStore()
    assert store.get("does-not-exist") is None


def test_raw_result_store_evicts_by_entry_count():
    store = RawResultStore(max_entries=2, max_total_chars=1_000_000)
    id1 = store.put("a" * 100)
    id2 = store.put("b" * 100)
    id3 = store.put("c" * 100)
    assert store.get(id1) is None      # 最早的被淘汰
    assert store.get(id2) == "b" * 100
    assert store.get(id3) == "c" * 100


def test_raw_result_store_evicts_by_total_chars():
    store = RawResultStore(max_entries=100, max_total_chars=250)
    id1 = store.put("a" * 100)
    id2 = store.put("b" * 100)
    id3 = store.put("c" * 100)  # 总量超 250，触发淘汰最旧的 id1
    assert store.get(id1) is None
    assert store.get(id2) == "b" * 100
    assert store.get(id3) == "c" * 100


# ── ToolExecutor：规则截断 + 原文留存 ────────────────────────────────────────

def test_rule_trim_keeps_raw_result_retrievable():
    cfg = make_cfg()
    cfg.tool_trim.threshold = 100
    cfg.tool_trim.raw_store_enabled = True
    store = RawResultStore()
    ex = make_executor(cfg, raw_result_store=store)

    long_text = "\n".join(f"line {i}" for i in range(500))
    trimmed = ex._trim_result("bash", long_text, {})

    assert trimmed != long_text
    assert "view_raw_result" in trimmed
    assert "result_id=" in trimmed

    # 从提示文本里把 result_id 抠出来，验证能取回完整原文
    marker = 'result_id=\\"'.replace("\\", "")
    start = trimmed.index('result_id="') + len('result_id="')
    end = trimmed.index('"', start)
    result_id = trimmed[start:end]
    assert store.get(result_id) == long_text


def test_rule_trim_without_raw_store_has_no_reference():
    cfg = make_cfg()
    cfg.tool_trim.threshold = 100
    ex = make_executor(cfg, raw_result_store=None)

    long_text = "\n".join(f"line {i}" for i in range(500))
    trimmed = ex._trim_result("bash", long_text, {})

    assert trimmed != long_text
    assert "view_raw_result" not in trimmed


def test_short_result_untouched_and_not_stored():
    cfg = make_cfg()
    cfg.tool_trim.threshold = 4000
    store = RawResultStore()
    ex = make_executor(cfg, raw_result_store=store)

    short_text = "just a short result"
    trimmed = ex._trim_result("bash", short_text, {})

    assert trimmed == short_text
    assert store.stats_summary().startswith("raw result store: 0/")


# ── ToolExecutor：智能摘要 ───────────────────────────────────────────────────

class _FakeUsage:
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text
        self.tool_calls = []
        self.usage = _FakeUsage()
        self.stop_reason = "end_turn"
        self.reasoning = ""
        self.raw = None


class _FakeLLMClient:
    """假 LLM 客户端：记录调用参数，返回固定摘要文本。"""

    def __init__(self, summary_text: str = "extracted summary", raise_error: bool = False):
        self.summary_text = summary_text
        self.raise_error = raise_error
        self.calls: list[dict] = []

    def chat_with_retry(self, messages, system, tools=None, **kwargs):
        self.calls.append({"messages": messages, "system": system})
        if self.raise_error:
            raise RuntimeError("simulated LLM failure")
        return _FakeResponse(self.summary_text)


def test_smart_summary_used_when_enabled_and_over_threshold():
    cfg = make_cfg()
    cfg.tool_trim.threshold = 100
    cfg.tool_trim.smart_summary_enabled = True
    cfg.tool_trim.smart_summary_threshold = 200
    store = RawResultStore()
    fake_llm = _FakeLLMClient(summary_text="only the important bits")
    ex = make_executor(cfg, llm_client=fake_llm, raw_result_store=store)

    long_text = "noise line\n" * 100  # > 200 chars
    trimmed = ex._trim_result("bash", long_text, {"command": "run tests"})

    assert fake_llm.calls, "LLM 摘要应被调用"
    assert "only the important bits" in trimmed
    assert "view_raw_result" in trimmed  # 摘要后原文依然可留存回看


def test_smart_summary_falls_back_to_rule_trim_on_llm_error():
    cfg = make_cfg()
    cfg.tool_trim.threshold = 100
    cfg.tool_trim.smart_summary_enabled = True
    cfg.tool_trim.smart_summary_threshold = 200
    fake_llm = _FakeLLMClient(raise_error=True)
    ex = make_executor(cfg, llm_client=fake_llm, raw_result_store=None)

    long_text = "noise line\n" * 100
    trimmed = ex._trim_result("bash", long_text, {"command": "run tests"})

    # LLM 失败后应走规则截断路径，不应抛异常，也不包含摘要占位文本
    assert "LLM-extracted summary" not in trimmed
    assert len(trimmed) < len(long_text)


def test_smart_summary_not_used_below_its_own_threshold():
    """长度超过 trim threshold 但未超过 smart_summary_threshold 时，走规则截断，不调用 LLM。"""
    cfg = make_cfg()
    cfg.tool_trim.threshold = 100
    cfg.tool_trim.smart_summary_enabled = True
    cfg.tool_trim.smart_summary_threshold = 10_000
    fake_llm = _FakeLLMClient()
    ex = make_executor(cfg, llm_client=fake_llm, raw_result_store=None)

    long_text = "line\n" * 50  # > 100 但 < 10000
    ex._trim_result("bash", long_text, {})

    assert not fake_llm.calls


def test_smart_summary_skips_llm_when_original_too_large_for_summarizer():
    cfg = make_cfg()
    cfg.tool_trim.threshold = 100
    cfg.tool_trim.smart_summary_enabled = True
    cfg.tool_trim.smart_summary_threshold = 200
    cfg.tool_trim.smart_summary_max_input_chars = 300
    fake_llm = _FakeLLMClient()
    ex = make_executor(cfg, llm_client=fake_llm, raw_result_store=None)

    long_text = "x" * 1000  # 超过 smart_summary_max_input_chars，应直接降级
    ex._trim_result("bash", long_text, {})

    assert not fake_llm.calls


# ── view_raw_result 工具 ─────────────────────────────────────────────────────

def test_view_raw_result_tool_roundtrip():
    from mini_agent.tools import builtin

    store = RawResultStore()
    builtin.configure_raw_result_store(store)
    try:
        content = "\n".join(f"row {i}" for i in range(20))
        rid = store.put(content, tool_name="bash")

        full = builtin.view_raw_result(rid)
        assert full == content

        ranged = builtin.view_raw_result(rid, start_line=2, end_line=3)
        assert "row 1" in ranged
        assert "row 2" in ranged
        assert "row 4" not in ranged
    finally:
        builtin.configure_raw_result_store(None)


def test_view_raw_result_tool_missing_id():
    from mini_agent.tools import builtin

    builtin.configure_raw_result_store(RawResultStore())
    try:
        result = builtin.view_raw_result("nonexistent")
        assert "error" in result
    finally:
        builtin.configure_raw_result_store(None)


def test_view_raw_result_tool_store_not_configured():
    from mini_agent.tools import builtin

    builtin.configure_raw_result_store(None)
    result = builtin.view_raw_result("whatever")
    assert "not enabled" in result
