"""
evolution/workspace.py — EvolutionWorkspace：进程级隔离（Stage 2.3）

对应 self_evolution_implementation_plan.md Stage 2.3 / 设计文档第 4.5 节
"副本化运行：进程级隔离"。

设计文档原文流程：

    EvolutionWorkspace.create(branch="evolve/xxx")
      → git worktree add /tmp/evolve/xxx -b evolve/xxx     # 完整代码+资产副本，共享对象库，近零成本
      → 若依赖文件有变化，建独立 venv
      → subprocess: python -m mini_agent --cwd /tmp/evolve/xxx \\
           --eval-scenarios test_cases/ \\
           --sandbox-permissions strict \\
           → 结果写入 /tmp/evolve/xxx/.agent/eval_result.json

**本阶段（Stage 2）取舍**（计划文档 2.3 节明确写明）：
  第一版可不做"自动跑 eval 场景"，只做"创建/销毁 worktree"骨架，验证
  "改动在隔离环境里能正常加载"这一最低要求。完整的 eval 场景对比
  （test_cases/ 批量执行 + 结果对比）留到 Stage 3 接入 Phase D 时实现。

`--sandbox-permissions strict` 直接复用现有 `permissions.py` 的 `--sandbox` flag
（设计文档原话："无需新发明"）——本模块 spawn 子进程时传 `--sandbox`，不新增
任何权限机制。

依赖文件变化检测使用最简策略：比较 worktree 内 requirements.txt / pyproject.toml
与主仓库当前版本是否一致，不一致才创建独立 venv（"近零成本"是设计文档对
`git worktree` 共享对象库特性的强调，本模块延续这个取舍：不需要 venv 的场景
不应该为了"保险"而强制创建，徒增等待时间）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import venv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from mini_agent.evolution.state_repo import StateRepo, StateRepoError


class EvolutionWorkspaceError(Exception):
    """EvolutionWorkspace 操作失败（worktree 创建/销毁、smoke boot 调用失败等）的统一异常类型。"""


@dataclass
class SmokeBootResult:
    """副本进程"能跑起来、完成最简对话不崩"的最低验证结果（设计文档 4.6 节 T2 行）。"""
    ok: bool
    returncode: int = -1
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    reason: str = ""


@dataclass
class EvolutionWorkspace:
    """
    一次"进化尝试"的进程级隔离环境：基于 `git worktree` 创建独立工作目录，
    共享主仓库的 git 对象库（近零磁盘/时间成本），代码改动在主进程之外验证。

    典型用法：
        repo = StateRepo(project_root)
        ws = EvolutionWorkspace.create(repo, branch="evolve/2026-06-20-bash-safety")
        try:
            result = ws.smoke_boot()
            if result.ok:
                ...  # 验证通过，后续可 merge 分支
        finally:
            ws.destroy()
    """

    repo: StateRepo
    branch: str
    path: Path
    _created: bool = field(default=False, repr=False)

    # ── 创建 / 销毁 ──────────────────────────────────────────────────────────

    @classmethod
    def create(
        cls,
        repo: StateRepo,
        branch: str,
        base: str = "HEAD",
        workspace_root: Optional[Path] = None,
    ) -> "EvolutionWorkspace":
        """
        创建一个新的 worktree，对应一个新分支 `branch`（基于 `base`）。

        若 `branch` 已存在（例如重入同一个进化尝试），直接复用该分支创建 worktree，
        不报错——"评估同一个分支两次"是正常场景（例如先 smoke_boot 失败后修复重试）。

        workspace_root 默认是系统临时目录下的 `mini_agent_evolve/`，可由调用方
        指定（例如测试场景下指向受控的临时目录，避免污染真实 /tmp）。
        """
        ws_root = workspace_root or (Path(_default_workspace_root()))
        ws_root.mkdir(parents=True, exist_ok=True)

        safe_name = _sanitize_branch_for_dirname(branch)
        target = ws_root / safe_name

        if target.exists():
            raise EvolutionWorkspaceError(
                f"workspace path already exists: {target} "
                "(call destroy() on the previous workspace first, or pass a different branch name)"
            )

        branch_exists = branch in repo.list_branches()

        # 【fresh-repo 修复】见 StateRepo.ensure_initial_commit() 文档：全新项目
        # 第一次创建 evolve 分支时，HEAD 可能还不是有效引用（仓库尚无任何 commit），
        # 此时 `git worktree add ... -b <branch> HEAD` 会直接失败。只在
        # base 仍是默认的 "HEAD" 且仓库确实没有 commit 时兜底创建一个空初始
        # commit；调用方显式传入其他 base（如某个具体 commit hash）时不做
        # 任何隐式修复，按调用方意图失败更安全。
        if not branch_exists and base == "HEAD" and not repo.has_commits():
            repo.ensure_initial_commit()

        args = ["worktree", "add", str(target)]
        if branch_exists:
            args.append(branch)
        else:
            args += ["-b", branch, base]

        try:
            repo._run_git(args)
        except StateRepoError as e:
            raise EvolutionWorkspaceError(f"git worktree add failed: {e}") from e

        return cls(repo=repo, branch=branch, path=target, _created=True)

    def destroy(self, delete_branch: bool = False) -> None:
        """
        销毁 worktree（`git worktree remove --force`），可选一并删除分支。

        设计文档 4.5 节："最后只 merge 验证通过的那个，其余直接
        `git worktree remove --force` + 删分支，不留痕迹。"——因此 destroy()
        默认只清理 worktree 本身，是否删分支由调用方根据"是否已 merge"决定。
        """
        if not self._created or not self.path.exists():
            self._created = False
            return
        try:
            self.repo._run_git(["worktree", "remove", "--force", str(self.path)], check=False)
        finally:
            # worktree remove 偶发因为残留文件句柄失败时，兜底直接删目录，
            # 避免 /tmp 下堆积无法被 git 识别、也无法手动清理的孤儿 worktree。
            if self.path.exists():
                shutil.rmtree(self.path, ignore_errors=True)
            self._created = False

        if delete_branch:
            try:
                self.repo.delete_branch(self.branch, force=True)
            except StateRepoError:
                pass  # 分支删除失败不应阻塞 destroy() 的语义（worktree 已经清理完毕）

    def __enter__(self) -> "EvolutionWorkspace":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.destroy()

    # ── 依赖隔离（按需创建 venv） ────────────────────────────────────────────

    def needs_isolated_venv(self) -> bool:
        """
        判断 worktree 内的依赖声明文件（requirements.txt / pyproject.toml）
        是否与主仓库当前版本不同。不同才需要建独立 venv——"近零成本"是
        `git worktree` 共享对象库特性的核心优势，不应该无条件抵消掉。
        """
        for dep_file in ("requirements.txt", "pyproject.toml"):
            main_file = self.repo.root / dep_file
            ws_file = self.path / dep_file
            main_content = main_file.read_text(encoding="utf-8") if main_file.exists() else None
            ws_content = ws_file.read_text(encoding="utf-8") if ws_file.exists() else None
            if main_content != ws_content:
                return True
        return False

    def ensure_venv(self) -> Path:
        """
        在 worktree 内创建独立 venv（若尚未创建），返回 venv 内 python 可执行文件路径。

        只在 needs_isolated_venv() 为 True 时才需要调用；smoke_boot() 内部会自动判断。
        """
        venv_dir = self.path / ".venv-evolve"
        py_path = venv_dir / "bin" / "python"
        if not py_path.exists():
            venv.EnvBuilder(with_pip=True, clear=True).create(str(venv_dir))
            # 安装 worktree 自身（editable），保证子进程能 `import mini_agent`
            try:
                _popen_kwargs = {
                    "args": [str(py_path), "-m", "pip", "install", "-e", ".", "--break-system-packages", "-q"],
                    "cwd": str(self.path),
                    "capture_output": True,
                    "text": True,
                    "timeout": 300,
                }
                if sys.platform == "win32":
                    _popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
                else:
                    _popen_kwargs["start_new_session"] = True
                subprocess.run(**_popen_kwargs)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.evolution.workspace.EvolutionWorkspace.ensure_venv')
                pass  # 安装失败不阻塞 workspace 创建，smoke_boot() 会在实际跑的时候反映出问题
        return py_path

    # ── 最低验证：smoke boot（设计文档 4.6 节 T2 行"副本进程 smoke test"）────

    def smoke_boot(self, timeout: float = 60.0) -> SmokeBootResult:
        """
        验证"改动在隔离环境里能正常加载"这一最低要求（Stage 2 范围内的全部目标）。

        具体做法：在 worktree 目录下，用子进程跑一次非交互的最简单 prompt
        （`python -m mini_agent --sandbox <prompt>`），不依赖真实 LLM API key 时
        允许失败但区分"进程能否正常启动并加载所有模块"与"是否真的拿到了模型回复"——
        本阶段只关心前者（模块能否正常 import、Agent 能否构造成功），
        这是设计文档 4.5 节"主进程只读 eval_result.json 做对比"思路的最小先导版本。

        更准确地说：本方法实际探测的是"`python -c 'import mini_agent...'` 在
        worktree 副本里不抛异常"——比真正拉起一次完整对话更快、更不依赖外部
        网络/API key，足以验证"代码改动没有破坏模块加载"这一 T2 校验项的核心诉求；
        真正的"完整对话不崩"+ eval 场景对比留给 Stage 3。
        """
        if not self.path.exists():
            return SmokeBootResult(ok=False, reason="workspace path does not exist (already destroyed?)")

        python_exe = sys.executable
        if self.needs_isolated_venv():
            python_exe = str(self.ensure_venv())

        probe_script = (
            "import sys; "
            "sys.path.insert(0, 'src'); "
            "import mini_agent; "
            "from mini_agent.config import AppConfig; "
            "from mini_agent.agent import Agent; "
            "from mini_agent.tools import get_default_registry; "
            "from mini_agent.skills import SkillLoader; "
            "from mini_agent.permissions import PermissionGuard; "
            "print('SMOKE_BOOT_OK')"
        )

        start = time.time()
        try:
            _popen_kwargs = {
                "args": [python_exe, "-c", probe_script],
                "cwd": str(self.path),
                "capture_output": True,
                "text": True,
                "timeout": timeout,
            }
            if sys.platform == "win32":
                _popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                _popen_kwargs["start_new_session"] = True
            proc = subprocess.run(**_popen_kwargs)
        except subprocess.TimeoutExpired as e:
            return SmokeBootResult(
                ok=False, duration_seconds=time.time() - start,
                reason=f"smoke boot timed out after {timeout}s",
                stdout=e.stdout or "", stderr=e.stderr or "",
            )
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where='mini_agent.evolution.workspace.EvolutionWorkspace.smoke_boot')
            return SmokeBootResult(ok=False, reason=f"failed to spawn smoke boot subprocess: {e}")

        duration = time.time() - start
        ok = proc.returncode == 0 and "SMOKE_BOOT_OK" in proc.stdout
        return SmokeBootResult(
            ok=ok,
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            duration_seconds=duration,
            reason="" if ok else "module import / Agent construction failed in isolated worktree",
        )

    # ── eval 结果落盘位置（骨架，供 Stage 3 填充实际内容） ───────────────────

    def eval_result_path(self) -> Path:
        """返回本 workspace 内 eval_result.json 应该落盘的路径（Stage 3 填充实际内容）。"""
        return self.path / ".agent" / "eval_result.json"

    def write_eval_result(self, data: dict) -> Path:
        """
        将 eval 结果写入 worktree 内的 .agent/eval_result.json。

        Stage 2 范围内，调用方（CLI /evolution 命令或测试）可以用这个方法写入
        smoke_boot() 的结果，作为"eval_result.json 落盘机制"已经打通的验证；
        真正的 tool 失败率 / turns / token 对比数据由 Stage 3 的 eval 命令产出。
        """
        result_path = self.eval_result_path()
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return result_path


def _default_workspace_root() -> Path:
    import tempfile
    return Path(tempfile.gettempdir()) / "mini_agent_evolve"


def _sanitize_branch_for_dirname(branch: str) -> str:
    """把分支名（可能含 `/`）转换为安全的单层目录名，例如 `evolve/2026-06-20-x` → `evolve__2026-06-20-x`。"""
    return branch.replace("/", "__").replace("\\", "__")


__all__ = [
    "EvolutionWorkspace",
    "EvolutionWorkspaceError",
    "SmokeBootResult",
]
