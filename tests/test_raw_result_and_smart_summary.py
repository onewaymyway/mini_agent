"""
tests/test_raw_result_and_smart_summary.py

覆盖 [SYS-RAWSTORE] / [SYS-SMARTTRIM] 的能力：
  1. RawResultStore 基本存取（落盘版本）+ 天然去重
  2. ToolExecutor._trim_result 在规则截断路径下会留存原文并附带可读路径提示
  3. smart_summary 开启时优先走 LLM 摘要；LLM 失败时自动降级为规则截断
  4. view_raw_result 工具能按路径取回完整原文（含行号范围）；路径不存在时报错提示

[改进：next_doc/generative_capability_raw_result_and_hybrid_merge_plan.md 第1节]
RawResultStore 从"session 内内存 LRU + result_id"改为"落盘 + 完整路径"，
本文件同步改写：不再测试 LRU 驱逐（磁盘落地后清理交给
perception/raw_result_cleanup.py 的低频巡检，不在 put()/get() 路径上做同步
驱逐），改为测试落盘路径可读、天然去重、以及 view_raw_result 工具直接按
路径读取（不再依赖 configure_raw_result_store 全局单例，该函数已删除）。
"""

from __future__ import annotations

import sys
import tempfile
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


def make_store(tmp_path, session_id="test-session") -> RawResultStore:
    return RawResultStore(project_root=str(tmp_path), session_id=session_id)


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


# ── RawResultStore（落盘版本）───────────────────────────────────────────────

def test_raw_result_store_put_get_roundtrip(tmp_path):
    store = make_store(tmp_path)
    ref = store.put("hello world " * 100, tool_name="bash")
    assert Path(ref.path).exists()
    assert store.get(ref.result_id) == "hello world " * 100
    assert Path(ref.path).read_text(encoding="utf-8") == "hello world " * 100


def test_raw_result_store_dedup_same_content(tmp_path):
    store = make_store(tmp_path)
    content = "same content " * 50
    ref1 = store.put(content, tool_name="bash")
    ref2 = store.put(content, tool_name="grep")
    assert ref1.result_id == ref2.result_id
    assert ref1.path == ref2.path


def test_raw_result_store_missing_id_returns_none(tmp_path):
    store = make_store(tmp_path)
    assert store.get("does-not-exist") is None


def test_raw_result_store_separates_sessions(tmp_path):
    """不同 session_id 落盘到各自独立目录，互不干扰（对应 raw_result 落盘化的
    核心目标：多个 Agent/SubAgent 实例并存时不会互相覆盖对方的存储）。"""
    store_a = make_store(tmp_path, session_id="session-a")
    store_b = make_store(tmp_path, session_id="session-b")

    ref = store_a.put("only in session a")
    assert store_a.get(ref.result_id) == "only in session a"
    assert store_b.get(ref.result_id) is None  # session-b 看不到 session-a 的内容
    assert "session-a" in ref.path
    assert "session-b" not in ref.path


def test_raw_result_store_meta_file_written(tmp_path):
    store = make_store(tmp_path)
    ref = store.put("content", tool_name="grep")
    meta_path = Path(ref.path).with_suffix("").with_suffix(".meta.json")
    assert meta_path.exists()
    assert "grep" in meta_path.read_text(encoding="utf-8")


# ── ToolExecutor：规则截断 + 原文留存 ────────────────────────────────────────

def test_rule_trim_keeps_raw_result_retrievable(tmp_path):
    cfg = make_cfg()
    cfg.tool_trim.threshold = 100
    cfg.tool_trim.raw_store_enabled = True
    store = make_store(tmp_path)
    ex = make_executor(cfg, raw_result_store=store)

    long_text = "\n".join(f"line {i}" for i in range(500))
    trimmed = ex._trim_result("bash", long_text, {})

    assert trimmed != long_text
    assert "read_file(path=" in trimmed

    # 从提示文本里把路径抠出来，验证能取回完整原文
    start = trimmed.index('read_file(path="') + len('read_file(path="')
    end = trimmed.index('"', start)
    path = trimmed[start:end]
    assert Path(path).read_text(encoding="utf-8") == long_text


def test_rule_trim_without_raw_store_has_no_reference():
    cfg = make_cfg()
    cfg.tool_trim.threshold = 100
    ex = make_executor(cfg, raw_result_store=None)

    long_text = "\n".join(f"line {i}" for i in range(500))
    trimmed = ex._trim_result("bash", long_text, {})

    assert trimmed != long_text
    assert "read_file(path=" not in trimmed


def test_short_result_untouched_and_not_stored(tmp_path):
    cfg = make_cfg()
    cfg.tool_trim.threshold = 4000
    store = make_store(tmp_path)
    ex = make_executor(cfg, raw_result_store=store)

    short_text = "just a short result"
    trimmed = ex._trim_result("bash", short_text, {})

    assert trimmed == short_text
    assert store.stats_summary().startswith("raw result store: 0 files")


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


def test_smart_summary_used_when_enabled_and_over_threshold(tmp_path):
    cfg = make_cfg()
    cfg.tool_trim.threshold = 100
    cfg.tool_trim.smart_summary_enabled = True
    cfg.tool_trim.smart_summary_threshold = 200
    store = make_store(tmp_path)
    fake_llm = _FakeLLMClient(summary_text="only the important bits")
    ex = make_executor(cfg, llm_client=fake_llm, raw_result_store=store)

    long_text = "noise line\n" * 100  # > 200 chars
    trimmed = ex._trim_result("bash", long_text, {"command": "run tests"})

    assert fake_llm.calls, "LLM 摘要应被调用"
    assert "only the important bits" in trimmed
    assert "read_file(path=" in trimmed  # 摘要后原文依然可落盘回看


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


# ── view_raw_result 工具（现为 read_file 的路径读取别名）────────────────────

def test_view_raw_result_tool_roundtrip(tmp_path):
    from mini_agent.tools import builtin

    store = make_store(tmp_path)
    content = "\n".join(f"row {i}" for i in range(20))
    ref = store.put(content, tool_name="bash")

    full = builtin.view_raw_result(ref.path)
    assert full == content

    ranged = builtin.view_raw_result(ref.path, start_line=2, end_line=3)
    assert "row 1" in ranged
    assert "row 2" in ranged
    assert "row 4" not in ranged


def test_view_raw_result_tool_missing_path():
    from mini_agent.tools import builtin

    result = builtin.view_raw_result("/nonexistent/path/should/not/exist.txt")
    assert "error" in result


def test_view_raw_result_tool_multiple_sessions_no_cross_talk(tmp_path):
    """回归测试：对应 raw_result 落盘化要解决的核心 bug——不同 session 的
    store 先后构造不应互相覆盖，view_raw_result 按路径读取自然规避了这个问题
    （不再存在"当前活跃 store 是谁"的全局状态）。"""
    from mini_agent.tools import builtin

    store_main = make_store(tmp_path, session_id="main")
    ref_main = store_main.put("main agent result")

    # 模拟子 agent 在同一进程内后构造了自己的 store（旧 bug 场景）
    store_sub = make_store(tmp_path, session_id="sub")
    store_sub.put("sub agent result")

    # 主 agent 用自己 put() 时拿到的路径去查看，不受子 agent store 构造影响
    assert builtin.view_raw_result(ref_main.path) == "main agent result"
