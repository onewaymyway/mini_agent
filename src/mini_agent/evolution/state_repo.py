"""
evolution/state_repo.py — StateRepo：自我修改的唯一写入入口（Stage 2.1）

对应 self_evolution_implementation_plan.md Stage 2.1 / 设计文档第 4.2 节
"安全更新：StateRepo.apply() 作为唯一写入入口"。

核心约束（来自设计文档 4.1 节）：
  - 全部纳入项目自身的 git 仓库管理，不再区分"agent 状态"与"项目代码"两个版本域
  - 区分点是改动对象的风险分级（Risk Tier: T0 < T1 < T2 < T3）
  - T3 是治理红线：风险分级逻辑、merge 门槛判定、worktree 隔离逻辑本身都属于 T3，
    通过 scripts/protected_paths.py（在 agent 可写范围之外）做强制判定——
    即使调用方传入了 T0/T1/T2，命中受保护路径清单一律强制升级为 T3。

设计取舍：
  - 用 subprocess 调用系统 git，不引入 GitPython 等额外依赖（设计文档明确要求）
  - apply() 是"原子写入 + 按 tier 校验 + commit"的组合操作：校验失败则不落盘、不 commit，
    不允许出现"文件已经改了但 git 没记录"的中间状态
  - commit message 使用设计文档 4.2 节的结构化格式，保证每次自我修改都能从 commit message
    反查到来源 lesson / session / 角色，这是后续"剪枝/冲突检测"和"能力地图"的数据基础
  - revert 默认使用 `git revert`（生成新 commit 撤销改动），不用 `git reset --hard`——
    "试过 X、效果不好、已回退"本身是历史的一部部分（设计文档 4.3 节）
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Union

# scripts/ 不是 mini_agent 包的一部分（详见 scripts/protected_paths.py 顶部说明），
# 需要把仓库根目录加入 sys.path 才能 import。StateRepo 的 root 通常就是仓库根目录，
# 但为了在测试场景（root 是临时目录）下也能正常工作，这里始终从"本文件所在仓库"
# 加入 sys.path，而不是从 self.root 推断——受保护路径清单判定的是"真实代码仓库"的
# 路径结构，不应该因为 StateRepo 在测试中指向了别的目录而失效或报错。
_REPO_ROOT_FOR_PROTECTED_PATHS = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT_FOR_PROTECTED_PATHS) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT_FOR_PROTECTED_PATHS))

try:
    from scripts.protected_paths import is_protected_path
except Exception:
    # 极端降级：如果连受保护路径清单都加载不了，安全的做法是"什么都不放过"，
    # 而不是静默放行——把所有路径都判定为受保护，强制需要人工介入。
    def is_protected_path(path) -> bool:  # type: ignore[misc]
        return True


VALID_TIERS: tuple[str, ...] = ("T0", "T1", "T2", "T3")

# tier 等级序号，用于"强制升级取较高者"的比较（T3 最高）。
_TIER_RANK: dict[str, int] = {t: i for i, t in enumerate(VALID_TIERS)}


class StateRepoError(Exception):
    """StateRepo 操作失败（git 调用失败、校验失败、commit 失败等）的统一异常类型。"""


@dataclass
class ValidationResult:
    """单个 tier 校验函数的返回值。校验失败必须给出明确原因，不允许静默失败。"""
    ok: bool
    reason: str = ""

    @staticmethod
    def success() -> "ValidationResult":
        return ValidationResult(ok=True)

    @staticmethod
    def failure(reason: str) -> "ValidationResult":
        return ValidationResult(ok=False, reason=reason or "validation failed (no reason given)")


@dataclass
class CommitInfo:
    """git log 单条提交的结构化信息。"""
    commit: str
    author: str
    date: str
    subject: str
    body: str = ""
    files: list[str] = field(default_factory=list)


@dataclass
class ApplyResult:
    """apply() 的返回值：成功时含 commit hash，失败时 ok=False 且带原因，便于调用方分支处理。"""
    ok: bool
    commit: str = ""
    tier: str = ""
    forced_tier: bool = False          # 是否因命中受保护路径而被强制升级
    validation_errors: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok


# 一次 apply() 的改动集合：路径（相对仓库根目录）-> 新内容（None 表示删除该文件）
ChangeSet = dict[Union[str, Path], Optional[str]]


class StateRepo:
    """
    对项目 git 仓库的封装。所有"自我修改"必须经过 apply()，
    不允许任何模块绕过它直接写文件。

    用法：
        repo = StateRepo(Path("/path/to/project"))
        result = repo.apply(
            changes={"skills/foo/SKILL.md": "---\\nname: foo\\n---\\n..."},
            message="Add foo skill",
            meta={
                "source_lessons": ["lesson_2026061501"],
                "session_id": "sess_xxx",
                "confidence": 0.82,
                "occurrence_count": 4,
                "proposed_by": "evolution-agent",
            },
            tier="T1",
        )
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self._ensure_initialized()

    # ── 初始化 ────────────────────────────────────────────────────────────────

    def _ensure_initialized(self) -> None:
        """确保 self.root 是一个可用的 git 仓库；若尚未初始化则 `git init`。"""
        if not self.root.is_dir():
            raise StateRepoError(f"StateRepo root does not exist or is not a directory: {self.root}")
        if not (self.root / ".git").exists():
            self._run_git(["init"])
        self._ensure_git_identity()

    def _ensure_git_identity(self) -> None:
        """
        某些环境（CI、临时容器、新建 worktree）没有配置全局 git user.name/user.email，
        会导致 `git commit` 直接失败。这里只在"既无全局配置也无本地配置"时，
        写入一个仅对本仓库生效的 fallback 身份，不污染用户的全局 git 配置。
        """
        for key, fallback in (("user.name", "mini_agent-evolution"),
                               ("user.email", "evolution@mini-agent.local")):
            proc = self._run_git(["config", key], check=False)
            if proc.returncode != 0 or not proc.stdout.strip():
                self._run_git(["config", key, fallback])

    # ── git 调用封装 ──────────────────────────────────────────────────────────

    def _run_git(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
        """统一的 git 子进程调用，cwd 固定为 self.root。"""
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError as e:
            raise StateRepoError(f"git executable not found: {e}") from e
        except subprocess.TimeoutExpired as e:
            raise StateRepoError(f"git command timed out: git {' '.join(args)}") from e

        if check and proc.returncode != 0:
            raise StateRepoError(
                f"git {' '.join(args)} failed (exit {proc.returncode}): {proc.stderr.strip()}"
            )
        return proc

    # ── tier 判定（T3 强制升级） ─────────────────────────────────────────────

    def resolve_tier(self, paths: list[Union[str, Path]], requested_tier: str) -> tuple[str, bool]:
        """
        计算一组改动路径的实际生效 tier。

        规则：
          - requested_tier 必须是 T0~T3 之一，否则视为非法输入抛异常
          - 若任意一个路径命中 scripts/protected_paths.is_protected_path()，
            强制升级为 T3（即使 requested_tier 更低）
          - 强制升级只升不降：即使 requested_tier 已经是 T3，也不会因为"没命中受保护路径"
            而被降级——调用方明确要求的 tier 永远是下限

        返回 (实际生效 tier, 是否发生了强制升级)。
        """
        if requested_tier not in VALID_TIERS:
            raise StateRepoError(
                f"invalid tier {requested_tier!r}, must be one of {VALID_TIERS}"
            )

        forced = any(is_protected_path(p) for p in paths)
        if not forced:
            return requested_tier, False

        effective = "T3"
        # 即使 requested_tier 本来就是 T3，也仍然标记为"命中保护路径"，
        # 因为调用方需要知道这是治理红线生效而非自愿选择。
        upgraded = _TIER_RANK[effective] > _TIER_RANK[requested_tier]
        return effective, (upgraded or requested_tier == "T3" and forced)

    # ── 核心写入入口 ──────────────────────────────────────────────────────────

    def apply(
        self,
        changes: ChangeSet,
        message: str,
        meta: dict,
        tier: str,
        validators: Optional[list[Callable[[Path, ChangeSet], "ValidationResult"]]] = None,
        auto_validators: bool = False,
    ) -> ApplyResult:
        """
        原子写入 + 按 tier 校验 + commit。

        流程：
          1. 计算实际生效 tier（受保护路径强制升级为 T3）
          2. 依次跑 validators：
             - 若显式传入 validators，使用调用方提供的列表
             - 若 auto_validators=True 且未显式传入 validators，
               按*生效* tier（而非调用方请求的 tier）从 evolution/validators.py
               自动选取对应校验函数——这是为了避免"请求 T0 但命中受保护路径被
               升级为 T3，调用方却仍然只传了 T0 的校验函数"这种 tier 与实际
               校验内容不一致的情况
          3. 任意一个校验失败 → 不落盘、不 commit，返回 ApplyResult(ok=False, validation_errors=[...])
          4. 全部通过 → 写入所有 changes（None 表示删除文件），git add -A，git commit
          5. commit message 使用结构化格式（见 _build_commit_message）

        变更路径一律按"相对 self.root 的相对路径"处理；传入绝对路径会被拒绝，
        防止越权写到仓库之外。

        注：本方法不在模块顶部 import evolution.validators，是为了避免
        state_repo.py（受保护路径清单里 evolution/ 正则规则覆盖的核心文件之一）
        与 validators.py 之间出现不必要的强耦合——validators 是"插件"，
        StateRepo 本身不应该在导入时就依赖具体校验策略的实现细节。
        """
        if not changes:
            raise StateRepoError("apply() called with empty changes")

        normalized: dict[Path, Optional[str]] = {}
        for raw_path, content in changes.items():
            rel = self._normalize_path(raw_path)
            normalized[rel] = content

        effective_tier, forced = self.resolve_tier(list(normalized.keys()), tier)

        effective_validators = validators
        if effective_validators is None and auto_validators:
            from mini_agent.evolution.validators import validators_for_tier
            effective_validators = validators_for_tier(effective_tier)

        validation_errors: list[str] = []
        for validator in (effective_validators or []):
            result = validator(self.root, normalized)
            if not result.ok:
                validation_errors.append(result.reason)

        if validation_errors:
            return ApplyResult(
                ok=False,
                tier=effective_tier,
                forced_tier=forced,
                validation_errors=validation_errors,
            )

        # ── 校验全部通过，开始真正写入 ──────────────────────────────────────
        touched_abs_paths: list[Path] = []
        try:
            for rel_path, content in normalized.items():
                abs_path = self.root / rel_path
                if content is None:
                    if abs_path.exists():
                        abs_path.unlink()
                else:
                    abs_path.parent.mkdir(parents=True, exist_ok=True)
                    abs_path.write_text(content, encoding="utf-8")
                touched_abs_paths.append(abs_path)

            self._run_git(["add", "-A", "--", *[str(p) for p in normalized.keys()]])

            full_message = self._build_commit_message(
                message=message, meta=meta, tier=effective_tier,
            )
            self._run_git(["commit", "-m", full_message, "--allow-empty-message"])

            commit_hash = self._run_git(["rev-parse", "HEAD"]).stdout.strip()
        except StateRepoError:
            raise
        except Exception as e:
            raise StateRepoError(f"apply() failed during write/commit: {e}") from e

        return ApplyResult(ok=True, commit=commit_hash, tier=effective_tier, forced_tier=forced)

    # ── 历史查询 ──────────────────────────────────────────────────────────────

    def log(self, limit: int = 20) -> list[CommitInfo]:
        """返回最近 limit 条 commit 的结构化信息（含改动文件列表）。

        实现说明：`git log --name-only --pretty=format:...` 把文件列表追加在
        格式化字段之后、且不在同一个分隔符块内（连 unit/record separator 一起用
        都无法无歧义地区分"多行 body" vs "文件列表"），单次组合查询容易在 body
        为空/非空、单 commit/多 commit 之间产生不一致的边界。为了正确性优先于
        性能（log 不是热路径，limit 默认很小），改为两步：
          1. 一次 `git log` 取 limit 条 commit 的 hash/author/date/subject/body
          2. 对每条 commit 单独跑一次 `git diff-tree --name-only` 取改动文件列表
        """
        unit_sep = "\x1f"
        rec_sep = "\x1e"
        fmt = unit_sep.join(["%H", "%an", "%ad", "%s", "%b"])
        proc = self._run_git(
            ["log", f"-n{limit}", f"--pretty=format:{fmt}{rec_sep}", "--date=iso-strict"],
            check=False,
        )
        if proc.returncode != 0:
            # 空仓库（尚无 commit）时 git log 会非零退出，视为"无历史"而非错误
            return []

        commits: list[CommitInfo] = []
        for record in proc.stdout.split(rec_sep):
            record = record.strip("\n")
            if not record.strip():
                continue
            parts = record.split(unit_sep, 4)
            if len(parts) < 5:
                continue
            h, author, date, subject, body = parts
            commits.append(CommitInfo(
                commit=h, author=author, date=date, subject=subject,
                body=body.strip("\n"), files=self._files_in_commit(h),
            ))
        return commits

    def _files_in_commit(self, commit: str) -> list[str]:
        """返回某次 commit 改动涉及的文件列表（相对仓库根目录的相对路径）。

        `--root` 是必须的：对没有父提交的根 commit（仓库的第一次 apply()），
        `git diff-tree` 默认无法确定 diff 基准，不加 `--root` 会静默返回空列表。
        """
        proc = self._run_git(
            ["diff-tree", "--no-commit-id", "--name-only", "-r", "--root", commit],
            check=False,
        )
        if proc.returncode != 0:
            return []
        return [line.strip() for line in proc.stdout.splitlines() if line.strip()]

    def diff(self, ref_a: str = "HEAD~1", ref_b: str = "HEAD", path: Optional[Union[str, Path]] = None) -> str:
        """返回两个 ref 之间的 diff 文本，可选限定单个路径。"""
        args = ["diff", ref_a, ref_b]
        if path is not None:
            args += ["--", str(self._normalize_path(path))]
        proc = self._run_git(args, check=False)
        return proc.stdout

    # ── 回退 ──────────────────────────────────────────────────────────────────

    def revert(self, commit: str) -> str:
        """
        默认使用 `git revert`（生成新 commit 撤销改动），不用 `git reset --hard`——
        "试过 X、效果不好、已回退"本身是历史的一部分（设计文档 4.3 节）。

        返回新生成的 revert commit hash。
        """
        self._run_git(["revert", "--no-edit", commit])
        return self._run_git(["rev-parse", "HEAD"]).stdout.strip()

    def checkout_file(self, commit: str, path: Union[str, Path]) -> None:
        """文件级回退：只把某个文件恢复到指定 commit 的版本，不影响仓库其他改动。

        与 revert() 的区别：revert() 撤销整次 commit 的所有改动并生成新 commit；
        checkout_file() 只回退单个文件且不自动 commit（写入工作区，调用方决定是否
        再走一次 apply() 提交）。
        """
        rel = self._normalize_path(path)
        self._run_git(["checkout", commit, "--", str(rel)])

    # ── 分支（设计文档 4.4 节："evolve 分支取代 pending 目录"） ────────────────

    def create_branch(self, name: str, base: str = "HEAD") -> None:
        """创建一个新分支但不切换（worktree 场景下通常配合 `git worktree add -b` 使用，
        这里提供给不需要 worktree、直接在主仓库内操作分支的简单场景）。"""
        self._run_git(["branch", name, base])

    def current_branch(self) -> str:
        """返回当前分支名。仓库尚无任何 commit 时（HEAD 还未指向有效引用），
        退化为读取 HEAD 符号引用本身，而不是抛异常——这是新建仓库的正常状态。"""
        proc = self._run_git(["symbolic-ref", "--short", "HEAD"], check=False)
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
        proc = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"], check=False)
        return proc.stdout.strip() if proc.returncode == 0 else ""

    def list_branches(self, prefix: str = "") -> list[str]:
        proc = self._run_git(["branch", "--list", "--format=%(refname:short)"])
        names = [n.strip() for n in proc.stdout.splitlines() if n.strip()]
        if prefix:
            names = [n for n in names if n.startswith(prefix)]
        return names

    def delete_branch(self, name: str, force: bool = True) -> None:
        flag = "-D" if force else "-d"
        self._run_git(["branch", flag, name])

    # ── 辅助 ──────────────────────────────────────────────────────────────────

    def _normalize_path(self, path: Union[str, Path]) -> Path:
        """把传入路径统一转换为相对 self.root 的相对 Path，拒绝越权的绝对路径。"""
        p = Path(path)
        if p.is_absolute():
            try:
                rel = p.relative_to(self.root)
            except ValueError as e:
                raise StateRepoError(
                    f"absolute path {p} is outside repo root {self.root}"
                ) from e
            return rel
        return p

    @staticmethod
    def _build_commit_message(message: str, meta: dict, tier: str) -> str:
        """
        构造设计文档 4.2 节的结构化 commit message：

            [T1][skill_propose] Add bash-rm-safety skill

            source_lessons: lesson_2026061501, lesson_2026061203
            session_id: sess_xxxxx
            confidence: 0.82
            occurrence_count: 4
            proposed_by: evolution-agent

        meta 字典里缺失的字段直接省略对应行（而不是写 "None"），保持 commit message 干净。
        """
        source = meta.get("source") or meta.get("entry_type") or meta.get("proposed_by") or "self_evolution"
        subject = f"[{tier}][{source}] {message}".strip()

        lines: list[str] = []

        source_lessons = meta.get("source_lessons")
        if source_lessons:
            if isinstance(source_lessons, (list, tuple)):
                lines.append(f"source_lessons: {', '.join(str(x) for x in source_lessons)}")
            else:
                lines.append(f"source_lessons: {source_lessons}")

        for key in ("session_id", "confidence", "occurrence_count", "proposed_by"):
            if key in meta and meta[key] not in (None, ""):
                lines.append(f"{key}: {meta[key]}")

        # 其余调用方自定义的 meta 字段也一并落入 commit message body，
        # 保证"任何自我修改都必须能从 commit message 反查到来源"这一要求
        # 不局限于设计文档列出的几个固定字段。
        known_keys = {"source", "entry_type", "source_lessons", "session_id",
                      "confidence", "occurrence_count", "proposed_by"}
        for key, value in meta.items():
            if key in known_keys or value in (None, ""):
                continue
            lines.append(f"{key}: {value}")

        if not lines:
            return subject
        return subject + "\n\n" + "\n".join(lines)


__all__ = [
    "StateRepo",
    "StateRepoError",
    "ValidationResult",
    "CommitInfo",
    "ApplyResult",
    "VALID_TIERS",
]
