"""
mini_agent/workspace.py — Workspace：显式的执行上下文根

设计依据：`next_doc/external_projects_workspace_plan.md` 原则一
（引擎与宿主解耦）。

背景：skill / workflow / memory / session 等子系统目前都通过
`AgentPaths(cfg.project_root)`（见 `storage/paths.py`）从同一个根目录
派生各自的存储路径，`project_root` 本身则隐式等于"当前交互式会话所在
目录"。这个约定在"一次会话一个项目根"的交互式场景下没问题，但当我们
需要让同一套引擎驱动"外部项目"（有自己独立路径、独立数据、独立生命
周期的领域系统，如 A 股监控分析系统）时，需要一个可以显式构造、传递、
互不干扰的根上下文对象——这就是 `Workspace`。

`Workspace` 不是一套新的路径体系，而是对"以哪个目录为根"这件事的显式
包装：所有具体路径计算仍然复用已有的 `AgentPaths`；skill 分层搜索的
"本地优先、全局兜底"约定，沿用 `workflow/resource_bundle.py` 里已经
验证过的"列表顺序 = 加载顺序，后面的目录同名覆盖前面的"规则，本模块
只是把这个约定从"workflow 私有资源包"这一个使用场景，提升成任何驱动
方式（REPL、daemon、headless CLI、未来的外部项目）都能复用的通用能力。

当前阶段（阶段 1）范围：
  - 只提供"显式构造 Workspace + 从中派生各子系统路径/skill 搜索路径"
    的能力。
  - 不改变任何现有默认行为：不显式构造/传入 Workspace 时，各子系统
    继续走原来的隐式 `project_root` 推导逻辑，一行代码都不用改。
  - `project.yaml` 契约的解析（`resources` 声明、`entrypoints` 等）是
    阶段 3 的范围，本模块只预留 `project_yaml_path` 属性占位，不实现
    解析逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from mini_agent.storage.paths import AgentPaths


@dataclass
class Workspace:
    """
    一个执行上下文的根。

    用法示例：
        ws = Workspace(root="/data/stock_watch")
        ws.apply_to(cfg)                       # 让 AppConfig 落到这个根下
        loader = ws.build_skill_loader()        # 分层 skill 搜索（本地优先）

        # 两个 Workspace 互不污染：
        ws_a = Workspace(root="/data/project_a")
        ws_b = Workspace(root="/data/project_b")
        assert ws_a.memory_store_path != ws_b.memory_store_path
    """

    root: Path
    # 全局内置 skills 兜底目录（通常对应交互式模式下的 `cfg.skills_dir`）。
    # None 表示不叠加全局目录，只用该 Workspace 私有的 skills 目录。
    global_skills_dir: Optional[Path] = None

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser().resolve()
        if self.global_skills_dir is not None:
            self.global_skills_dir = Path(self.global_skills_dir).expanduser()

    # ── 底层路径管理器 ─────────────────────────────────────────────────────

    @property
    def paths(self) -> AgentPaths:
        """复用已有的 `AgentPaths`，不重新实现一遍路径拼接逻辑。"""
        return AgentPaths(self.root)

    # ── 子系统路径派生（对应 external_projects_workspace_plan.md 5.1 节结构）──

    @property
    def skills_dir(self) -> Path:
        """该 Workspace 私有的 skills 目录：`<root>/skills/`。"""
        return self.root / "skills"

    @property
    def skills_search_dirs(self) -> List[Path]:
        """
        分层 skill 搜索路径，本地优先、全局兜底。

        列表顺序即 `SkillLoader` 的加载顺序——`SkillLoader._discover()`
        按目录顺序遍历，同名 skill 后发现的覆盖先发现的，所以本地目录
        必须排在全局目录之后。这与 `workflow/resource_bundle.py::
        WorkflowResourceBundle._build_skill_loader()` 已经在用的约定
        完全一致，这里只是把它变成通用、可独立于 workflow 上下文复用的
        能力。
        """
        dirs: List[Path] = []
        if self.global_skills_dir is not None:
            dirs.append(self.global_skills_dir)
        dirs.append(self.skills_dir)
        return dirs

    @property
    def workflows_dir(self) -> Path:
        return self.root / "workflows"

    @property
    def memory_store_path(self) -> Path:
        """项目级 memory 文件路径：`<root>/.agent/memory.jsonl`。"""
        return self.paths.workdir_memory

    @property
    def sessions_dir(self) -> Path:
        return self.paths.sessions_dir

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def run_status_path(self) -> Path:
        """阶段 4（状态账本）预留位置：`<root>/.agent/run_status.jsonl`。"""
        return self.root / ".agent" / "run_status.jsonl"

    @property
    def backlog_path(self) -> Path:
        """改进积压账本位置：`<root>/.agent/improvement_backlog.jsonl`。

        对应 `next_doc/stock_watch_continuous_improvement_plan.md`
        阶段 1，与 `run_status_path` 对称——都是外部项目自己写、
        daemon/review session 被动读的账本，只是记录的内容不同
        （执行成败 vs 待优化的软问题）。
        """
        return self.root / ".agent" / "improvement_backlog.jsonl"

    @property
    def project_yaml_path(self) -> Path:
        """阶段 3（`project.yaml` 契约）预留位置，本阶段不解析内容。"""
        return self.root / "project.yaml"

    @property
    def hybrid_exec_scripts_dir(self) -> Path:
        """hybrid_exec 脚本仓库目录：`<root>/.agent/hybrid_exec/scripts/`。

        对应 `next_doc/hybrid_exec_improvement_directions.md` A1：把
        `hybrid_exec.default_executor()` 内部拼死的
        `project_root / ".agent" / "hybrid_exec" / "scripts"` 路径提升为
        `Workspace` 上显式声明的属性，让"外部项目用 `Workspace` 对象一路
        传下去"这条线不再依赖"裸路径字符串恰好和 `Workspace.root` 对齐"
        这种巧合式兼容。与 `ScriptRepository` 构造时使用的路径保持一致。
        """
        return self.root / ".agent" / "hybrid_exec" / "scripts"

    @property
    def hybrid_exec_runs_dir(self) -> Path:
        """hybrid_exec 执行记录目录：`<root>/.agent/hybrid_exec/runs/`。

        与 `RunRecorder` 构造时使用的路径保持一致，见
        `hybrid_exec_scripts_dir` 的说明。
        """
        return self.root / ".agent" / "hybrid_exec" / "runs"

    @property
    def hybrid_exec_playbooks_dir(self) -> Path:
        """hybrid_exec SKILL 档（playbook）仓库目录：
        `<root>/.agent/hybrid_exec/playbooks/`。

        与 `enable_skill_tier=True` 时 `PlaybookRepository` 构造使用的
        路径保持一致，见 `hybrid_exec_scripts_dir` 的说明。
        """
        return self.root / ".agent" / "hybrid_exec" / "playbooks"

    # ── 构造/接入辅助 ──────────────────────────────────────────────────────

    def build_skill_loader(self, **kwargs):
        """按 `skills_search_dirs` 构造一个分层 `SkillLoader`。"""
        from mini_agent.skills import SkillLoader

        return SkillLoader(self.skills_search_dirs, **kwargs)

    def apply_to(self, cfg) -> None:
        """
        把这个 Workspace 的根应用到一份 `AppConfig` 上。

        只设置 `cfg.project_root`——`MemoryConfig.store_path` /
        `SessionConfig.dir` 等字段保持"None = 从 project_root 派生"的
        既有约定不变（见 `config/models.py` 对应字段注释），这样已有的
        `AgentPaths(cfg.project_root)` 派生逻辑天然生效，不需要在这里
        重复计算一遍路径。如果调用方已经在 cfg 上显式设置了
        `memory.store_path` / `session.dir`（有意覆盖默认路径），这个
        方法不会碰它们。
        """
        cfg.project_root = self.root

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"Workspace(root={self.root!s})"
