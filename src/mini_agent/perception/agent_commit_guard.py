"""
perception/agent_commit_guard.py — agent 自动 commit 撤销感知与前瞻提醒

背景与完整方案见 next_doc/agent_commit_undo_guard_plan.md，这里只放实现。

三段式流程：
  1. 打标/记账：agent 通过 bash 工具执行 `git commit` 时，把 commit hash /
     涉及文件 / session_id / 时间记到项目级账本
     `<project_root>/.agent/agent_commits.jsonl`。
  2. 感知撤销：
       路径 A（agent 会话内）：agent 自己执行 `git reset`/`revert`/
       `commit --amend`/`rebase` 时，立即核对账本。
       路径 B（agent 会话外，用户直接在终端操作，可能很久之后）：
       没有 hook 能实时捕获 `git reset`，改用"祖先链核对"——检查账本里
       记的 commit hash 是否还在当前分支的历史里（`git merge-base
       --is-ancestor`）。触发时机是机会性节流检查（每次 bash 调用附带
       检查一次，有最短间隔限流）+ SessionStart，以及 git 侧安装的
       `post-checkout`/`post-merge`/`post-rewrite` hook 写的"待检查"
       哨兵文件（写哨兵文件不依赖本模块是否常驻，下次任意检查时机
       都会优先处理，不受节流间隔限制）。
  3. 转成提醒：确认一次撤销后，写一条 `MemoryEntry(source="revert_record")`
     lesson（跟 `/evolution revert` 共用同一个函数 `record_undo_lesson`），
     交给已有的 `evolution/lesson_to_reminder.py` 扫描，生成
     `trigger_event=pre_tool, tool_name=bash` 的 reminder，下次 agent
     准备 commit 前作为 context 注入。

设计取舍：
  - 默认开启（跟 `perception/behavior` 的"默认全关"哲学不同——这个功能
    保护的是用户自己的仓库内容，不涉及额外隐私采集，且价值在于"默认就
    有用"），配置文件里可以关闭。
  - 自包含，不依赖 `perception/behavior` 总开关；也不通过用户可编辑的
    `hooks.json` 挂载（那是给用户自定义 hook 用的），而是直接在
    `tool_executor.py` / `agent/lifecycle.py` 里各加一个内联调用点，
    参照 `auto_quarantine.py` 的接入方式（默认开启、失败静默、不阻塞
    主流程）。
  - 任何一步失败都只警告，不影响原有 git 操作/agent 主流程。
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

CONFIG_FILENAME = "agent_commit_guard_config.json"
LEDGER_FILENAME = "agent_commits.jsonl"          # <project_root>/.agent/agent_commits.jsonl
SENTINEL_FILENAME = ".commit_guard_pending_scan"  # <project_root>/.agent/.commit_guard_pending_scan

_GIT_COMMIT_RE = re.compile(r"(?:^|[;&|]\s*)git\s+(?:-C\s+\S+\s+)?commit(?:\s|$|['\"])")
_GIT_UNDO_RE = re.compile(
    r"(?:^|[;&|]\s*)git\s+(?:-C\s+\S+\s+)?"
    r"(?:reset\b|revert\b|rebase\b|commit\s+(?:--amend|-o(?:\s|$))|filter-branch\b)"
)

_DEFAULT_SCAN_INTERVAL_SEC = 600.0  # 机会性检查节流间隔（10 分钟）


# ── 配置 ──────────────────────────────────────────────────────────────────

@dataclass
class AgentCommitGuardConfig:
    """agent 自动 commit 撤销感知配置。默认开启，可显式关闭。"""

    enabled: bool = True
    # 是否在检测到 reset/revert/amend/rebase 时立即核对（路径 A）
    immediate_undo_check: bool = True
    # 是否在每次 bash 调用时做机会性节流核对（路径 B 的一种触发方式）
    opportunistic_scan_enabled: bool = True
    opportunistic_scan_interval_sec: float = _DEFAULT_SCAN_INTERVAL_SEC
    # 是否在 SessionStart 时做一次核对
    scan_on_session_start: bool = True
    # 账本最多保留多少条未确认记录（超出淘汰最旧的，避免无界增长）
    ledger_max_pending: int = 500

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AgentCommitGuardConfig":
        valid = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


def _config_path(project_root: Optional[Path] = None) -> Path:
    root = project_root or Path.cwd()
    return Path(root) / CONFIG_FILENAME


def load_config(project_root: Optional[Path] = None) -> AgentCommitGuardConfig:
    """加载配置；文件不存在时返回默认配置（默认开启）。"""
    path = _config_path(project_root)
    if not path.exists():
        return AgentCommitGuardConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return AgentCommitGuardConfig.from_dict(data)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where="mini_agent.perception.agent_commit_guard.load_config")
        return AgentCommitGuardConfig()


def save_config(cfg: AgentCommitGuardConfig, project_root: Optional[Path] = None) -> None:
    path = _config_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


# ── 账本 ──────────────────────────────────────────────────────────────────

@dataclass
class LedgerEntry:
    commit_hash: str
    files: list = field(default_factory=list)
    subject: str = ""
    session_id: str = ""
    created_at: float = field(default_factory=time.time)
    resolved: bool = False           # True = 已经核对过（无论是"仍在"还是"已撤销"）
    undone: bool = False             # True = 确认被撤销
    resolved_at: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "LedgerEntry":
        valid = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


def _ledger_path(project_root: Path) -> Path:
    return Path(project_root) / ".agent" / LEDGER_FILENAME


def _sentinel_path(project_root: Path) -> Path:
    return Path(project_root) / ".agent" / SENTINEL_FILENAME


class CommitLedger:
    """极简 jsonl 账本，读全量 / 追加 / 整体重写（条目量级不大，够用）。"""

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root)
        self.path = _ledger_path(self.project_root)

    def load_all(self) -> list[LedgerEntry]:
        if not self.path.exists():
            return []
        entries = []
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(LedgerEntry.from_dict(json.loads(line)))
                except Exception:
                    continue
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where="mini_agent.perception.agent_commit_guard.CommitLedger.load_all")
        return entries

    def append(self, entry: LedgerEntry, max_pending: int = 500) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        self._maybe_compact(max_pending)

    def _maybe_compact(self, max_pending: int) -> None:
        """未确认（resolved=False）条目超过上限时，丢弃最旧的一批，避免无界增长。
        已确认的条目本身价值有限（只是审计用），顺手也裁掉过老的，保持文件不膨胀。
        """
        entries = self.load_all()
        pending = [e for e in entries if not e.resolved]
        if len(pending) <= max_pending:
            return
        pending.sort(key=lambda e: e.created_at)
        drop = set(id(e) for e in pending[: len(pending) - max_pending])
        kept = [e for e in entries if id(e) not in drop]
        self._rewrite(kept)

    def _rewrite(self, entries: list[LedgerEntry]) -> None:
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e.to_dict(), ensure_ascii=False) + "\n")
        tmp.replace(self.path)

    def mark_resolved(self, commit_hash: str, undone: bool) -> None:
        entries = self.load_all()
        changed = False
        for e in entries:
            if e.commit_hash == commit_hash and not e.resolved:
                e.resolved = True
                e.undone = undone
                e.resolved_at = time.time()
                changed = True
        if changed:
            self._rewrite(entries)

    def pending(self) -> list[LedgerEntry]:
        return [e for e in self.load_all() if not e.resolved]


# ── git 命令识别 ─────────────────────────────────────────────────────────

def is_git_commit_command(command: str) -> bool:
    return bool(command) and bool(_GIT_COMMIT_RE.search(command))


def is_git_undo_command(command: str) -> bool:
    return bool(command) and bool(_GIT_UNDO_RE.search(command))


def _run_git(project_root: Path, args: list, timeout: float = 5.0) -> Optional[str]:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(project_root)] + args,
            text=True, stderr=subprocess.DEVNULL, timeout=timeout,
        )
        return out.strip()
    except Exception:
        return None


def _current_head(project_root: Path) -> Optional[str]:
    return _run_git(project_root, ["rev-parse", "HEAD"])


def _is_ancestor(project_root: Path, commit_hash: str, ref: str = "HEAD") -> Optional[bool]:
    """None = 无法判断（不是 git 仓库/命令失败），不应据此判定为撤销。"""
    try:
        subprocess.run(
            ["git", "-C", str(project_root), "merge-base", "--is-ancestor", commit_hash, ref],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5.0,
        )
        return True
    except subprocess.CalledProcessError:
        return False
    except Exception:
        return None


def _commit_subject(project_root: Path, commit_hash: str) -> str:
    return _run_git(project_root, ["log", "-1", "--format=%s", commit_hash]) or ""


def _changed_files(project_root: Path, commit_hash: str) -> list:
    out = _run_git(project_root, ["show", "--name-only", "--format=", commit_hash])
    if not out:
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


# ── 记账（打标） ─────────────────────────────────────────────────────────

def record_agent_commit(project_root: Path, session_id: str = "", cfg: Optional[AgentCommitGuardConfig] = None) -> None:
    """agent 通过 bash 工具执行完 `git commit` 后调用：把这次 commit 记入账本。

    失败静默——记账是锦上添花的审计功能，不能影响 commit 本身已经成功这件事。
    """
    cfg = cfg or load_config(project_root)
    if not cfg.enabled:
        return
    try:
        head = _current_head(project_root)
        if not head:
            return
        ledger = CommitLedger(project_root)
        # 幂等：同一个 hash 已经记过就不重复记
        if any(e.commit_hash == head for e in ledger.load_all()):
            return
        entry = LedgerEntry(
            commit_hash=head,
            files=_changed_files(project_root, head),
            subject=_commit_subject(project_root, head),
            session_id=session_id,
        )
        ledger.append(entry, max_pending=cfg.ledger_max_pending)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where="mini_agent.perception.agent_commit_guard.record_agent_commit")


# ── 撤销检测（路径 A + 路径 B 共用的核对逻辑） ────────────────────────────

@dataclass
class UndoEvent:
    commit_hash: str
    files: list
    subject: str
    via: str  # "agent_session" | "ancestor_check"


def scan_for_undo(project_root: Path, cfg: Optional[AgentCommitGuardConfig] = None, via: str = "ancestor_check") -> list:
    """核对账本里所有未确认的 commit，返回本次新确认的撤销事件列表。

    对每条 pending 记录做 `merge-base --is-ancestor`：
      - True  → 仍在当前分支历史里，标记 resolved=True, undone=False
      - False → 已被 reset/rebase/amend 挤出历史，标记 undone=True，
                作为一次 UndoEvent 返回
      - None（无法判断，如已经不是 git 仓库） → 保持 pending，跳过
    """
    cfg = cfg or load_config(project_root)
    if not cfg.enabled:
        return []
    events: list = []
    try:
        ledger = CommitLedger(project_root)
        for entry in ledger.pending():
            ancestor = _is_ancestor(project_root, entry.commit_hash)
            if ancestor is None:
                continue
            ledger.mark_resolved(entry.commit_hash, undone=not ancestor)
            if not ancestor:
                events.append(UndoEvent(
                    commit_hash=entry.commit_hash,
                    files=entry.files,
                    subject=entry.subject,
                    via=via,
                ))
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where="mini_agent.perception.agent_commit_guard.scan_for_undo")
    return events


def consume_pending_sentinel(project_root: Path) -> bool:
    """检查并清除 git hook 写的"待检查"哨兵文件。返回是否存在（存在即应该无视
    节流间隔，立即做一次 scan_for_undo）。"""
    path = _sentinel_path(project_root)
    if not path.exists():
        return False
    try:
        path.unlink()
    except Exception:
        pass
    return True


# ── 机会性节流检查（供 tool_executor 每次 bash 调用后调用） ────────────────

_last_scan_at: dict = {}  # project_root(str) -> 上次 scan 的 timestamp，进程内足够


def maybe_opportunistic_scan(project_root: Path, cfg: Optional[AgentCommitGuardConfig] = None) -> list:
    """节流版核对：距上次 scan 超过间隔，或存在 git hook 写的哨兵文件，才真正跑。"""
    cfg = cfg or load_config(project_root)
    if not cfg.enabled or not cfg.opportunistic_scan_enabled:
        return []
    key = str(project_root)
    now = time.time()
    forced = consume_pending_sentinel(project_root)
    if not forced:
        last = _last_scan_at.get(key, 0.0)
        if now - last < cfg.opportunistic_scan_interval_sec:
            return []
    _last_scan_at[key] = now
    return scan_for_undo(project_root, cfg, via="ancestor_check")


# ── bash 工具后置处理入口（tool_executor.py 调用点） ──────────────────────

def on_bash_post_tool(
    project_root: Path,
    command: str,
    session_id: str = "",
    memory_sink=None,
    model: str = "",
) -> None:
    """在 bash 工具执行完毕后调用一次。内部自行判断命令类型、是否需要记账/核对。

    memory_sink：Optional[MemoryBackend]，用于把确认的撤销事件写成 lesson。
    不传时只做记账/核对，不生成 lesson（比如没有可用 memory 后端的场景）。
    """
    try:
        cfg = load_config(project_root)
        if not cfg.enabled:
            return

        if is_git_commit_command(command):
            record_agent_commit(project_root, session_id=session_id, cfg=cfg)
            return

        events: list = []
        if cfg.immediate_undo_check and is_git_undo_command(command):
            events = scan_for_undo(project_root, cfg, via="agent_session")
        else:
            events = maybe_opportunistic_scan(project_root, cfg)

        if events and memory_sink is not None:
            for ev in events:
                record_undo_lesson(
                    memory_sink=memory_sink,
                    session_id=session_id,
                    model=model,
                    commit_hash=ev.commit_hash,
                    files=ev.files,
                    subject=ev.subject,
                )
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where="mini_agent.perception.agent_commit_guard.on_bash_post_tool")


def on_session_start(project_root: Path, session_id: str = "", memory_sink=None, model: str = "") -> None:
    """SessionStart 时调用一次：清掉节流限制，做一次完整核对。"""
    try:
        cfg = load_config(project_root)
        if not cfg.enabled or not cfg.scan_on_session_start:
            return
        consume_pending_sentinel(project_root)
        events = scan_for_undo(project_root, cfg, via="ancestor_check")
        if events and memory_sink is not None:
            for ev in events:
                record_undo_lesson(
                    memory_sink=memory_sink,
                    session_id=session_id,
                    model=model,
                    commit_hash=ev.commit_hash,
                    files=ev.files,
                    subject=ev.subject,
                )
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where="mini_agent.perception.agent_commit_guard.on_session_start")


# ── 共享的 lesson 记录函数 ──────────────────────────────────────────────
#
# 这个函数取代了 cli/commands/evolution.py 里原本各自为 evolution 提案
# revert 和普通 agent commit 撤销分别维护的两份几乎一样的代码
# （`_record_revert_lesson` / `_handle_outcomes` 内联逻辑）。两处都改为
# 调用这一个函数，只是 trigger/suggested_action 文案按场景各自拼接。

def record_undo_lesson(
    memory_sink,
    session_id: str,
    model: str,
    commit_hash: str,
    files: list,
    subject: str = "",
    trigger: Optional[str] = None,
    outcome: Optional[str] = None,
    suggested_action: Optional[str] = None,
    tags: Optional[list] = None,
):
    """把一次"agent 自动提交被撤销"的具体事实，写成一条
    `MemoryEntry(source="revert_record")` lesson。

    返回写入的 `MemoryEntry`（便于调用方需要时再传给
    `agent._append_memory_delta()` 之类的会话内增量记录回调），失败或
    `memory_sink` 为 None 时返回 `None`。

    失败只警告，不抛出——撤销本身已经是既成事实，记录 lesson 是锦上添花的
    审计/学习产物，不应该反过来影响任何主流程。

    走通用的 `evolution/lesson_to_reminder.py` 扫描逻辑：source="revert_record"
    与 human_feedback 同属"直接激活"档位（不需要凑够 T1 门槛的 occurrence
    次数），下次扫描即可生成对应的 pre_tool reminder。
    """
    if memory_sink is None:
        return None
    try:
        from mini_agent.perception.memory_store import MemoryEntry

        short = commit_hash[:8] if commit_hash else "?"
        file_list = "、".join(files[:10]) if files else "（未知文件）"
        subj = f"（{subject}）" if subject else ""

        entry = MemoryEntry(
            session_id=session_id or "",
            summary="",
            key_outcomes=[],
            tags=(tags or []) + ["lesson", "revert_record", "agent_commit_guard"],
            model=model or "",
            entry_type="lesson",
            trigger=trigger or (
                f"工具 `bash` 调用 `git commit` 自动提交了 {file_list}"
                f"（commit {short}{subj}），事后被用户以 git 操作撤销"
            ),
            outcome=outcome or f"用户不希望该次提交生效，已通过 git 操作撤销 commit {short}",
            root_cause="",
            suggested_action=suggested_action or (
                f"下次准备 `git commit` 时，若改动涉及 {file_list} 中的路径，"
                f"先跳过这些文件或征询用户确认，不要默认一并自动提交"
            ),
            confidence=0.85,
            occurrence_count=1,
            source="revert_record",
        )
        if entry.scope == "global" and getattr(memory_sink, "global_sink", None):
            memory_sink.global_sink.add(entry)
        else:
            memory_sink.add(entry)
        return entry
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where="mini_agent.perception.agent_commit_guard.record_undo_lesson")
        return None


# ── git hook 安装（自包含，不依赖 perception/behavior） ────────────────────
#
# 只做一件事：post-checkout / post-merge / post-rewrite 发生时，在
# `.agent/` 下 touch 一个哨兵文件。不上报任何内容、不发网络请求——
# 跟 perception/behavior 的外部上报 hook 是完全独立的两回事，commit
# guard 默认开启，不应该依赖用户是否打开了行为感知系统。

_HOOK_MARKER_BEGIN = "# >>> mini_agent agent_commit_guard >>>"
_HOOK_MARKER_END = "# <<< mini_agent agent_commit_guard <<<"


def _sentinel_touch_script() -> str:
    return f"""{_HOOK_MARKER_BEGIN}
mkdir -p "$(git rev-parse --show-toplevel 2>/dev/null)/.agent" 2>/dev/null
touch "$(git rev-parse --show-toplevel 2>/dev/null)/.agent/{SENTINEL_FILENAME}" 2>/dev/null
{_HOOK_MARKER_END}
"""


def install_undo_scan_git_hooks(repo_path: Path) -> list:
    """在 `<repo_path>/.git/hooks/` 下追加 post-checkout / post-merge /
    post-rewrite 脚本片段（若脚本已存在则追加，不覆盖用户原有内容）。

    返回写入/追加过的 hook 文件路径列表。这三个事件覆盖了"用户在 agent
    之外做了可能撤销 commit 的操作"里，会触发 git hook 的那部分场景
    （`git reset` 本身仍然不会触发任何 hook，仍然依赖机会性节流核对/
    SessionStart 兜底）。
    """
    hooks_dir = Path(repo_path) / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for hook_name in ("post-checkout", "post-merge", "post-rewrite"):
        path = hooks_dir / hook_name
        existing = path.read_text(encoding="utf-8") if path.exists() else "#!/bin/sh\n"
        if _HOOK_MARKER_BEGIN in existing:
            written.append(path)
            continue
        content = existing.rstrip("\n") + "\n" + _sentinel_touch_script()
        path.write_text(content, encoding="utf-8")
        try:
            import stat
            path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        except Exception:
            pass
        written.append(path)
    return written
