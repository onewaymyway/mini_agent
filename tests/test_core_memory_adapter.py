"""core/memory.py + core/memory_adapter.py 的单元测试与接入点集成测试。

背景：`MemoryAdapter`/`MemorySnapshot` 自 2026-09-26 落地以来**没有任何
测试文件**（当时只有一次性验证脚本 + 手工 trace 观察）。本文件补齐，并覆盖
2026-09-29 的两处改动：

1. `self._global_memory` 与 `self._memory` 一起纳入 `agent/core.py::
   Agent.__init__()` 唯一接入点（`MemorySnapshot.scope` 区分 project/global）。
2. `MemoryAdapter.to_old` 复核后仍不实现——用测试固定"显式报错"这一行为，
   防止日后被改成返回一个看似可用的桩对象。

见 `next_doc/refactor_plan/03-sprint1.5-memory-perception-coupling-assessment.md`
"十二"。
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mini_agent.core.memory import MemorySnapshot
from mini_agent.core.memory_adapter import MemoryAdapter, trace_memory_snapshot

_TRACE = "mini_agent.core.trace"


def _memory_trace_records(caplog):
    return [r for r in caplog.records if "memory_store.adapter.to_new" in r.getMessage()]


class _FakeBackend:
    """只实现 `count` 的最小后端：转换只依赖这一个接口属性。"""

    def __init__(self, n: int) -> None:
        self._n = n
        self.count_reads = 0

    @property
    def count(self) -> int:
        self.count_reads += 1
        return self._n


# ── MemorySnapshot ───────────────────────────────────────────────────────────


def test_snapshot_old_construction_still_works_and_defaults_to_project():
    """旧的只传前两个字段的构造方式不受 `scope` 新字段影响。"""
    snap = MemorySnapshot(entry_count=3, backend_kind="MemoryStore")
    assert snap.scope == "project"
    assert MemorySnapshot() == MemorySnapshot(entry_count=0, backend_kind="unknown", scope="project")


# ── MemoryAdapter ────────────────────────────────────────────────────────────


def test_to_new_reads_count_and_class_name_only():
    backend = _FakeBackend(7)
    snap = MemoryAdapter.to_new(backend)  # type: ignore[arg-type]
    assert snap == MemorySnapshot(entry_count=7, backend_kind="_FakeBackend", scope="project")


def test_to_new_with_real_memory_store(tmp_path: Path):
    from mini_agent.config import AppConfig
    from mini_agent.perception.memory_factory import create_memory_backend

    cfg = AppConfig()
    cfg.project_root = tmp_path
    backend = create_memory_backend(cfg)
    snap = MemoryAdapter.to_new(backend)
    assert snap.entry_count == backend.count == 0
    assert snap.backend_kind == type(backend).__name__


def test_to_old_still_raises_not_implemented():
    """复核后仍不实现：显式报错，而不是返回一个检索不到任何东西的桩后端。"""
    with pytest.raises(NotImplementedError, match="to_old"):
        MemoryAdapter.to_old(MemorySnapshot())


# ── trace_memory_snapshot ────────────────────────────────────────────────────


@pytest.mark.parametrize("scope", ["project", "global"])
def test_trace_memory_snapshot_tags_scope_and_logs(caplog, scope):
    caplog.set_level(logging.DEBUG, logger=_TRACE)
    backend = _FakeBackend(4)

    snap = trace_memory_snapshot(backend, scope)  # type: ignore[arg-type]

    assert snap.scope == scope
    assert snap.entry_count == 4
    records = _memory_trace_records(caplog)
    assert len(records) == 1
    msg = records[0].getMessage()
    assert f"'scope': '{scope}'" in msg
    assert "'entry_count': 4" in msg
    assert "'backend_kind': '_FakeBackend'" in msg


def test_trace_memory_snapshot_is_read_only():
    backend = MagicMock()
    backend.count = 2
    trace_memory_snapshot(backend, "global")
    # 只读了 count 属性，没有调用任何会修改后端的方法
    assert backend.method_calls == []


def test_trace_memory_snapshot_propagates_backend_errors():
    """异常向调用方传播，由接入点统一 log_exception 兜底（不在这里吞掉）。"""

    class _Broken:
        @property
        def count(self) -> int:
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        trace_memory_snapshot(_Broken(), "project")  # type: ignore[arg-type]


# ── Agent.__init__ 唯一接入点（真实构造 Agent） ─────────────────────────────


def _make_agent(project_root: Path, *, global_enabled: bool = True):
    from mini_agent.agent import Agent
    from mini_agent.config import load_config
    from mini_agent.llm.base import LLMClient, LLMResponse, LLMUsage

    cfg = load_config(project_root=project_root)
    cfg.api_key = "test"
    cfg.stream = False
    # 记忆默认不开启，必须显式打开才会走到 Agent.__init__ 的记忆接入点
    cfg.memory.enabled = True
    cfg.memory.global_enabled = global_enabled
    client = MagicMock(spec=LLMClient)
    client.chat.return_value = LLMResponse(text="OK", tool_calls=[], usage=LLMUsage(), stop_reason="end_turn")
    return Agent(cfg=cfg, llm_client=client), cfg


@pytest.fixture()
def isolated_home(tmp_path, monkeypatch):
    """隔离真实 ~/.agent/，避免全局记忆后端读写开发机上的真实文件。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


def test_agent_init_emits_trace_for_project_and_global(tmp_path, isolated_home, caplog):
    caplog.set_level(logging.DEBUG, logger=_TRACE)
    project = tmp_path / "proj"
    project.mkdir()

    agent, cfg = _make_agent(project)

    assert cfg.memory_enabled, "前提：本测试显式开启了记忆"
    assert agent._memory is not None
    assert agent._global_memory is not None, "前提：global_enabled=True"
    scopes = sorted(
        "global" if "'scope': 'global'" in r.getMessage() else "project"
        for r in _memory_trace_records(caplog)
    )
    assert scopes == ["global", "project"]


def test_agent_init_skips_global_trace_when_global_disabled(tmp_path, isolated_home, caplog):
    caplog.set_level(logging.DEBUG, logger=_TRACE)
    project = tmp_path / "proj"
    project.mkdir()

    agent, _ = _make_agent(project, global_enabled=False)

    assert agent._global_memory is None
    records = _memory_trace_records(caplog)
    assert len(records) == 1
    assert "'scope': 'project'" in records[0].getMessage()


def test_agent_init_survives_trace_failure_and_keeps_memory_usable(tmp_path, isolated_home, monkeypatch):
    """trace 旁路抛异常时，Agent 仍能构造，且 `_memory`/`_global_memory` 不受影响。"""
    import mini_agent.core.memory_adapter as ma

    def _boom(*_a, **_k):
        raise RuntimeError("trace failed")

    monkeypatch.setattr(ma, "trace_memory_snapshot", _boom)
    project = tmp_path / "proj"
    project.mkdir()

    agent, _ = _make_agent(project)

    assert agent._memory is not None and agent._global_memory is not None
    assert agent._memory.count == 0


def test_agent_init_does_not_eagerly_load_global_memory_without_debug(tmp_path, isolated_home, caplog):
    """默认日志级别下，trace 旁路不得把全局记忆提前读盘。

    读 `MemoryStore.count` 会触发 `_ensure_loaded()` 解析整份 JSONL。全局后端
    此前是懒加载，且 Agent 在 cron 每次触发/SubAgent 构造时都会创建，因此全局
    这一路只在 `mini_agent.core.trace` 开启 DEBUG 时才计算。项目级后端原本就在
    接入点读 `count`（启动时加载），该既有行为保持不变。
    """
    caplog.set_level(logging.WARNING, logger=_TRACE)  # 结束后由 caplog 自动还原
    project = tmp_path / "proj"
    project.mkdir()

    agent, _ = _make_agent(project)

    assert agent._global_memory is not None
    assert agent._global_memory._loaded is False, "全局后端被 trace 提前加载了"
    assert agent._memory._loaded is True, "项目级后端的既有加载时机被改变了"
