"""
tests/test_agent_commit_guard.py — agent_commit_guard 单元测试

用真实的临时 git 仓库（subprocess 调 git）覆盖：
  1. git commit 命令识别 + 记账
  2. 正常情况下（未撤销）scan_for_undo 不产生 UndoEvent
  3. reset 之后 scan_for_undo 能识别出撤销，并生成 revert_record lesson
  4. 配置默认开启 / 可显式关闭
  5. git hook 安装 + 哨兵文件消费
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from mini_agent.perception import agent_commit_guard as guard


# ── Fixtures ────────────────────────────────────────────────────────────────

def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo)] + list(args), check=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "test@example.com")
    _git(r, "config", "user.name", "test")
    (r / "a.txt").write_text("hello")
    _git(r, "add", "a.txt")
    _git(r, "commit", "-q", "-m", "init")
    return r


class _FakeMemorySink:
    """极简 MemoryBackend 假实现，只记录 add() 被调用了什么。"""
    def __init__(self):
        self.entries = []

    def add(self, entry):
        self.entries.append(entry)


# ── 命令识别 ────────────────────────────────────────────────────────────────

def test_is_git_commit_command():
    assert guard.is_git_commit_command("git commit -m 'wip'")
    assert guard.is_git_commit_command("cd /tmp && git commit -am x")
    assert not guard.is_git_commit_command("git status")
    assert not guard.is_git_commit_command("git commit-graph write")


def test_is_git_undo_command():
    assert guard.is_git_undo_command("git reset --hard HEAD~1")
    assert guard.is_git_undo_command("git revert --no-edit HEAD")
    assert guard.is_git_undo_command("git commit --amend -m x")
    assert guard.is_git_undo_command("git rebase -i HEAD~3")
    assert not guard.is_git_undo_command("git commit -m 'reset the counter in code'")


# ── 配置 ───────────────────────────────────────────────────────────────────

def test_config_default_enabled(tmp_path):
    cfg = guard.load_config(tmp_path)
    assert cfg.enabled is True


def test_config_can_be_disabled(tmp_path):
    cfg = guard.AgentCommitGuardConfig(enabled=False)
    guard.save_config(cfg, tmp_path)
    loaded = guard.load_config(tmp_path)
    assert loaded.enabled is False


# ── 记账 + 撤销核对 ───────────────────────────────────────────────────────

def test_record_and_no_undo(repo):
    guard.record_agent_commit(repo, session_id="s1")
    ledger = guard.CommitLedger(repo)
    pending = ledger.pending()
    assert len(pending) == 1

    events = guard.scan_for_undo(repo)
    assert events == []
    # 核对完之后应该已经 resolved，且没有被判定为撤销
    entries = ledger.load_all()
    assert entries[0].resolved is True
    assert entries[0].undone is False


def test_detects_reset_undo_and_records_lesson(repo):
    # agent 自动提交一个文件
    (repo / "b.txt").write_text("secret stuff")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-q", "-m", "agent auto commit")
    guard.record_agent_commit(repo, session_id="s1")

    ledger = guard.CommitLedger(repo)
    assert len(ledger.pending()) == 1
    committed_hash = ledger.pending()[0].commit_hash

    # 用户（在 agent 之外）reset 掉这次提交
    _git(repo, "reset", "--hard", "HEAD~1")

    events = guard.scan_for_undo(repo)
    assert len(events) == 1
    assert events[0].commit_hash == committed_hash
    assert "b.txt" in events[0].files

    # 再跑一次应该没有新事件（已经 resolved）
    assert guard.scan_for_undo(repo) == []

    sink = _FakeMemorySink()
    entry = guard.record_undo_lesson(
        memory_sink=sink, session_id="s1", model="test-model",
        commit_hash=events[0].commit_hash, files=events[0].files,
        subject=events[0].subject,
    )
    assert entry is not None
    assert len(sink.entries) == 1
    assert sink.entries[0].source == "revert_record"
    assert "b.txt" in sink.entries[0].trigger


def test_on_bash_post_tool_end_to_end(repo):
    """模拟 tool_executor 的调用方式：先 commit 触发记账，再 reset 触发立即核对+写 lesson。"""
    (repo / "c.txt").write_text("oops")
    _git(repo, "add", "c.txt")
    _git(repo, "commit", "-q", "-m", "cleanup commit")

    sink = _FakeMemorySink()
    guard.on_bash_post_tool(
        project_root=repo, command="git commit -am 'cleanup commit'",
        session_id="s1", memory_sink=sink, model="m",
    )
    assert len(guard.CommitLedger(repo).pending()) == 1

    _git(repo, "reset", "--hard", "HEAD~1")

    guard.on_bash_post_tool(
        project_root=repo, command="git reset --hard HEAD~1",
        session_id="s1", memory_sink=sink, model="m",
    )
    assert len(sink.entries) == 1
    assert sink.entries[0].source == "revert_record"


def test_recheck_window_catches_later_undo(repo, monkeypatch):
    """修复回归测试：第一次核对判定"仍在历史里"之后不应永久冻结——
    如果撤销发生在第一次机会性核对之后（复查窗口内），下一次 scan
    应该仍然能发现它。"""
    (repo / "d.txt").write_text("later undo")
    _git(repo, "add", "d.txt")
    _git(repo, "commit", "-q", "-m", "will be reset later")
    guard.record_agent_commit(repo, session_id="s1")

    ledger = guard.CommitLedger(repo)
    committed_hash = ledger.pending()[0].commit_hash

    # 第一次核对：这时还没做任何撤销操作，应该判定"仍在历史里"
    assert guard.scan_for_undo(repo) == []
    entry = ledger.load_all()[0]
    assert entry.resolved is True
    assert entry.undone is False
    assert entry.checked_count == 1
    # 还在复查窗口内，不是终态
    assert entry.is_finalized() is False
    assert entry.commit_hash == committed_hash

    # 用户这时候才在终端里 reset 掉
    _git(repo, "reset", "--hard", "HEAD~1")

    # 复查窗口内的下一次 scan 应该能抓到这次滞后的撤销
    events = guard.scan_for_undo(repo)
    assert len(events) == 1
    assert events[0].commit_hash == committed_hash
    entry = ledger.load_all()[0]
    assert entry.undone is True
    assert entry.is_finalized() is True

    # undone 是终态，再跑一次不会再产生事件
    assert guard.scan_for_undo(repo) == []


def test_recheck_window_expires_and_finalizes(repo, monkeypatch):
    """超过复查窗口之后，"仍在历史里"的记录应该真正结案，不再被复查
    （即使之后真的被撤销也检测不到——这是窗口机制刻意的取舍）。"""
    (repo / "e.txt").write_text("stale")
    _git(repo, "add", "e.txt")
    _git(repo, "commit", "-q", "-m", "stale commit")
    guard.record_agent_commit(repo, session_id="s1")

    assert guard.scan_for_undo(repo) == []
    ledger = guard.CommitLedger(repo)
    entry = ledger.load_all()[0]
    assert entry.is_finalized() is False

    # 模拟已经过了复查窗口
    future = entry.created_at + guard.RECHECK_WINDOW_SEC + 1
    assert entry.is_finalized(now=future) is True

    _git(repo, "reset", "--hard", "HEAD~1")
    monkeypatch.setattr(guard.time, "time", lambda: future)
    assert len(ledger.recheckable()) == 0

    # 窗口过期后，scan_for_undo 不会再去核对这条记录（即使此刻它已被 reset 掉）
    assert guard.scan_for_undo(repo) == []


def test_disabled_config_short_circuits(repo):
    guard.save_config(guard.AgentCommitGuardConfig(enabled=False), repo)
    guard.record_agent_commit(repo, session_id="s1")
    assert guard.CommitLedger(repo).pending() == []


# ── git hook 安装 + 哨兵文件 ─────────────────────────────────────────────

def test_install_hooks_and_sentinel(repo):
    written = guard.install_undo_scan_git_hooks(repo)
    assert len(written) == 3
    for p in written:
        assert p.exists()
        assert guard._HOOK_MARKER_BEGIN in p.read_text(encoding="utf-8")

    # 再装一次不应该重复追加
    written2 = guard.install_undo_scan_git_hooks(repo)
    for p in written2:
        content = p.read_text(encoding="utf-8")
        assert content.count(guard._HOOK_MARKER_BEGIN) == 1

    # 模拟 post-checkout 触发：直接调用哨兵脚本效果（写文件），再验证消费逻辑
    sentinel = guard._sentinel_path(repo)
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.touch()
    assert guard.consume_pending_sentinel(repo) is True
    assert not sentinel.exists()
    assert guard.consume_pending_sentinel(repo) is False


def test_opportunistic_scan_respects_throttle_and_sentinel(repo):
    guard._last_scan_at.clear()
    cfg = guard.AgentCommitGuardConfig(opportunistic_scan_interval_sec=9999)
    guard.save_config(cfg, repo)

    (repo / "d.txt").write_text("x")
    _git(repo, "add", "d.txt")
    _git(repo, "commit", "-q", "-m", "auto")
    guard.record_agent_commit(repo, session_id="s1")
    _git(repo, "reset", "--hard", "HEAD~1")

    # 第一次调用会跑（进程内还没有 last_scan 记录）
    events1 = guard.maybe_opportunistic_scan(repo)
    assert len(events1) == 1

    # 制造第二条待撤销记录，但节流间隔很长，不哨兵触发的话不应该再跑
    (repo / "e.txt").write_text("y")
    _git(repo, "add", "e.txt")
    _git(repo, "commit", "-q", "-m", "auto2")
    guard.record_agent_commit(repo, session_id="s1")
    _git(repo, "reset", "--hard", "HEAD~1")

    events2 = guard.maybe_opportunistic_scan(repo)
    assert events2 == []  # 被节流挡住

    # 哨兵文件存在时应无视节流立即跑
    guard._sentinel_path(repo).parent.mkdir(parents=True, exist_ok=True)
    guard._sentinel_path(repo).touch()
    events3 = guard.maybe_opportunistic_scan(repo)
    assert len(events3) == 1


# ── /commit-guard CLI 命令（阶段 4） ─────────────────────────────────────────

class _FakeCfg:
    def __init__(self, project_root):
        self.project_root = project_root
        self.model = "test-model"


class _FakeAgent:
    def __init__(self, project_root, memory_sink=None):
        self.cfg = _FakeCfg(project_root)
        self.session_id = "s1"
        self._memory = memory_sink


def test_cli_status_on_off(repo, capsys):
    from mini_agent.cli.commands.commit_guard import handle_commit_guard_cmd

    agent = _FakeAgent(repo)
    handle_commit_guard_cmd(["status"], agent)
    handle_commit_guard_cmd(["off"], agent)
    assert guard.load_config(repo).enabled is False
    handle_commit_guard_cmd(["on"], agent)
    assert guard.load_config(repo).enabled is True


def test_cli_install_hooks_and_ledger(repo):
    from mini_agent.cli.commands.commit_guard import handle_commit_guard_cmd

    agent = _FakeAgent(repo)
    handle_commit_guard_cmd(["install-hooks"], agent)
    for name in ("post-checkout", "post-merge", "post-rewrite"):
        assert (repo / ".git" / "hooks" / name).exists()

    (repo / "f.txt").write_text("z")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "auto")
    guard.record_agent_commit(repo, session_id="s1")

    handle_commit_guard_cmd(["ledger"], agent)  # 只验证不抛异常
    assert len(guard.CommitLedger(repo).load_all()) == 1


def test_cli_scan_writes_lesson_when_memory_sink_present(repo):
    from mini_agent.cli.commands.commit_guard import handle_commit_guard_cmd

    (repo / "g.txt").write_text("secret")
    _git(repo, "add", "g.txt")
    _git(repo, "commit", "-q", "-m", "auto")
    guard.record_agent_commit(repo, session_id="s1")
    _git(repo, "reset", "--hard", "HEAD~1")

    sink = _FakeMemorySink()
    agent = _FakeAgent(repo, memory_sink=sink)
    handle_commit_guard_cmd(["scan"], agent)

    assert len(sink.entries) == 1
    assert sink.entries[0].source == "revert_record"


def test_cli_clear_removes_ledger_file(repo):
    from mini_agent.cli.commands.commit_guard import handle_commit_guard_cmd

    guard.record_agent_commit(repo, session_id="s1")
    assert guard._ledger_path(repo).exists()
    handle_commit_guard_cmd(["clear"], _FakeAgent(repo))
    assert not guard._ledger_path(repo).exists()
