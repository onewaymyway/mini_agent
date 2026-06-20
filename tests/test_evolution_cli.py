"""
tests/test_evolution_cli.py — Stage 2.4 验证

对应 self_evolution_implementation_plan.md Stage 2.4：
  /evolution log|show|diff|revert slash 命令；revert 后自动生成
  source="revert_record" 的 lesson（设计文档 4.3 节）。
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from mini_agent.config import AppConfig, SessionConfig, MemoryConfig, SessionStats
from mini_agent.agent import Agent
from mini_agent.session import Session
from mini_agent.cli.commands.evolution import handle_evolution_cmd
from mini_agent.evolution.state_repo import StateRepo
from mini_agent.ui.terminal import term, _Msg


@pytest.fixture
def captured_output(monkeypatch):
    """
    捕获 /evolution 命令通过 mini_agent.ui.terminal.term 渲染的输出文本。

    `term` 是模块级单例，内部用后台线程异步消费一个消息队列来串行渲染
    （详见 ui/terminal.py 顶部说明），它持有的 `rich.Console` 在模块导入时
    就已经绑定了 `sys.stdout`，因此 pytest 的 `capsys` 无法捕获到这部分输出
    （`capsys` patch `sys.stdout` 的时机晚于 Console 持有引用的时机，且渲染
    本身是异步的，存在竞态）。

    这里复用 terminal.py 自身在"进入输入模式"前用来排空队列的 noop+join
    哨兵技巧：先 monkeypatch `term._console` 为一个写入 StringIO 的 Console，
    调用结束后投递一个 `_noop` 消息并 `queue.join()`，确保渲染线程已经把
    所有消息处理完毕，再读取 StringIO 内容——避免任何 sleep/轮询，且与
    生产代码使用的是同一种同步原语。
    """
    buf = io.StringIO()
    test_console = Console(file=buf, width=120, force_terminal=False, highlight=False)
    monkeypatch.setattr(term, "_console", test_console)

    def get_output() -> str:
        term._q.put(_Msg("_noop", None))
        term._q.join()
        return buf.getvalue()

    return get_output


def make_minimal_agent(tmp_path: Path, memory_enabled: bool = True) -> Agent:
    """与 test_session_end_reflection.py 相同的最小化 Agent stub 构造模式。"""
    cfg = AppConfig(
        auto_approve=True, project_root=tmp_path, model="test-model",
        session=SessionConfig(auto_save=False),
        memory=MemoryConfig(enabled=memory_enabled),
    )
    agent = Agent.__new__(Agent)
    agent.cfg = cfg
    agent.stats = SessionStats()
    agent._history = []
    agent._memory = MagicMock() if memory_enabled else None
    agent._global_memory = None
    agent._session = Session(
        id="sess_test1", title="t", created_at="", updated_at="",
        provider="anthropic", model="test-model", stats={}, history=[],
    )
    agent._append_memory_delta = MagicMock()
    return agent


@pytest.fixture
def agent(tmp_path) -> Agent:
    return make_minimal_agent(tmp_path)


def _seed_commit(project_root: Path, **meta_overrides) -> str:
    repo = StateRepo(project_root)
    meta = {
        "source_lessons": ["lesson_1"],
        "session_id": "sess_x",
        "confidence": 0.8,
        "occurrence_count": 3,
        "proposed_by": "evolution-agent",
    }
    meta.update(meta_overrides)
    result = repo.apply(
        changes={"skills/foo/SKILL.md": "---\nname: foo\ndescription: test\n---\nbody"},
        message="Add foo skill",
        meta=meta,
        tier="T1",
    )
    assert result.ok
    return result.commit


# ── 基本路由 ──────────────────────────────────────────────────────────────────

def test_no_agent_does_not_raise(captured_output):
    handle_evolution_cmd(["log"], agent=None)  # 不应抛异常


def test_unknown_subcommand_shows_usage(agent, captured_output):
    handle_evolution_cmd(["bogus"], agent)
    out = captured_output()
    assert "Usage" in out or "usage" in out.lower()


def test_default_subcommand_is_log(agent, tmp_path, captured_output):
    """不带参数的 /evolution 应等价于 /evolution log。"""
    _seed_commit(tmp_path)
    handle_evolution_cmd([], agent)
    out = captured_output()
    assert "Self-Evolution History" in out


# ── /evolution log ───────────────────────────────────────────────────────────

def test_log_no_commits_shows_info_message(agent, captured_output):
    handle_evolution_cmd(["log"], agent)
    out = captured_output()
    assert "No self-evolution commits yet" in out


def test_log_shows_commits_table(agent, tmp_path, captured_output):
    commit = _seed_commit(tmp_path)
    handle_evolution_cmd(["log"], agent)
    out = captured_output()
    assert commit[:8] in out


def test_log_with_count_argument(agent, tmp_path, captured_output):
    repo = StateRepo(tmp_path)
    for i in range(5):
        repo.apply(changes={f"f{i}.txt": "x"}, message=f"commit {i}", meta={}, tier="T0")
    handle_evolution_cmd(["log", "2"], agent)
    out = captured_output()
    assert "commit 4" in out
    assert "commit 0" not in out  # 只显示最近 2 条


def test_log_invalid_count_shows_error(agent, captured_output):
    handle_evolution_cmd(["log", "not-a-number"], agent)
    out = captured_output()
    assert "Invalid count" in out


# ── /evolution show ──────────────────────────────────────────────────────────

def test_show_missing_arg_shows_usage(agent, captured_output):
    handle_evolution_cmd(["show"], agent)
    out = captured_output()
    assert "Usage" in out


def test_show_unknown_commit_shows_error(agent, tmp_path, captured_output):
    _seed_commit(tmp_path)
    handle_evolution_cmd(["show", "0000000"], agent)
    out = captured_output()
    assert "Commit not found" in out


def test_show_valid_commit_displays_details(agent, tmp_path, captured_output):
    commit = _seed_commit(tmp_path)
    handle_evolution_cmd(["show", commit[:8]], agent)
    out = captured_output()
    assert commit in out
    assert "Add foo skill" in out
    assert "source_lessons: lesson_1" in out
    assert "skills/foo/SKILL.md" in out


def test_show_accepts_short_prefix(agent, tmp_path, captured_output):
    commit = _seed_commit(tmp_path)
    handle_evolution_cmd(["show", commit[:6]], agent)
    out = captured_output()
    assert commit in out


# ── /evolution diff ───────────────────────────────────────────────────────────

def test_diff_missing_arg_shows_usage(agent, captured_output):
    handle_evolution_cmd(["diff"], agent)
    out = captured_output()
    assert "Usage" in out


def test_diff_shows_content_for_non_root_commit(agent, tmp_path, captured_output):
    repo = StateRepo(tmp_path)
    repo.apply(changes={"a.txt": "v1"}, message="v1", meta={}, tier="T0")
    result2 = repo.apply(changes={"a.txt": "v2"}, message="v2", meta={}, tier="T0")
    handle_evolution_cmd(["diff", result2.commit[:8]], agent)
    out = captured_output()
    assert "v1" in out
    assert "v2" in out


def test_diff_root_commit_shows_no_diff_message(agent, tmp_path, captured_output):
    commit = _seed_commit(tmp_path)
    handle_evolution_cmd(["diff", commit[:8]], agent)
    out = captured_output()
    assert "No diff found" in out


# ── /evolution revert ────────────────────────────────────────────────────────

def test_revert_missing_arg_shows_usage(agent, captured_output):
    handle_evolution_cmd(["revert"], agent)
    out = captured_output()
    assert "Usage" in out


def test_revert_unknown_commit_shows_error(agent, tmp_path, captured_output):
    _seed_commit(tmp_path)
    handle_evolution_cmd(["revert", "0000000"], agent)
    out = captured_output()
    assert "Commit not found" in out


def test_revert_success_undoes_change(agent, tmp_path, captured_output):
    commit = _seed_commit(tmp_path)
    assert (tmp_path / "skills/foo/SKILL.md").exists()

    handle_evolution_cmd(["revert", commit[:8]], agent)

    out = captured_output()
    assert "Reverted" in out
    assert not (tmp_path / "skills/foo/SKILL.md").exists()


def test_revert_writes_lesson_with_revert_record_source(agent, tmp_path):
    commit = _seed_commit(tmp_path)
    handle_evolution_cmd(["revert", commit[:8]], agent)

    agent._memory.add.assert_called_once()
    entry = agent._memory.add.call_args[0][0]
    assert entry.source == "revert_record"
    assert entry.entry_type == "lesson"
    assert entry.confidence == 0.9
    assert commit[:8] in entry.trigger


def test_revert_calls_append_memory_delta(agent, tmp_path):
    commit = _seed_commit(tmp_path)
    handle_evolution_cmd(["revert", commit[:8]], agent)
    agent._append_memory_delta.assert_called_once()


def test_revert_with_memory_disabled_does_not_raise(tmp_path, captured_output):
    agent = make_minimal_agent(tmp_path, memory_enabled=False)
    commit = _seed_commit(tmp_path)
    handle_evolution_cmd(["revert", commit[:8]], agent)
    out = captured_output()
    assert "Reverted" in out  # revert 本身仍然成功，只是不写 lesson


def test_revert_memory_failure_does_not_raise(agent, tmp_path, captured_output):
    commit = _seed_commit(tmp_path)
    agent._memory.add.side_effect = RuntimeError("disk full")
    handle_evolution_cmd(["revert", commit[:8]], agent)
    out = captured_output()
    assert "Reverted" in out  # revert 本身已经成功
    assert "failed to record revert lesson" in out  # 但应该有警告


def test_revert_shows_diff_before_reverting(agent, tmp_path, captured_output):
    repo = StateRepo(tmp_path)
    repo.apply(changes={"a.txt": "v1"}, message="v1", meta={}, tier="T0")
    result2 = repo.apply(changes={"a.txt": "v2"}, message="v2", meta={}, tier="T0")
    handle_evolution_cmd(["revert", result2.commit[:8]], agent)
    out = captured_output()
    assert "About to revert" in out
