"""
external_projects/registry.py — daemon 侧外部项目注册表

对应 `next_doc/external_projects_workspace_plan.md` §5、阶段 3 第二项。

存储位置刻意选在 `~/.mini_agent/external_projects.json`（用户级目录，
默认与 daemon 自身代码树/项目树都无关），呼应原则三"注册表与外部项目
所在路径无关"——注册表只记"有哪些外部项目、路径在哪"，本身不属于任何
一个外部项目，也不属于 mini_agent 自身仓库。

存储格式选 JSON（而不是 sqlite 等）：条目数量级是"用户注册的外部项目
个数"（十几到几十个），JSON 全量读写足够快，且方便用户直接打开文件
肉眼核对/手工修复，与 `daemon` 目录下其它同量级配置文件（如
`agent_commit_guard_config.json`）风格一致。

本模块不做任何调度/执行逻辑（那是 `scheduler.py` 和
`cli/commands/projects_cmd.py` 的范围），只负责注册表本身的增删查改，
以及"注册时顺带校验一次 manifest 合法性"（避免把一个连 `project.yaml`
都解析不了的路径注册进去）。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from mini_agent.external_projects.manifest import (
    ProjectManifest,
    ProjectManifestError,
    load_manifest,
)
from mini_agent.utils.atomic_write import atomic_write_json

DEFAULT_REGISTRY_PATH = Path.home() / ".mini_agent" / "external_projects.json"


class ExternalProjectRegistryError(ValueError):
    """注册表操作失败（重复注册/未注册/路径不合法等）。"""


@dataclass
class RegisteredProject:
    """注册表里的一条记录——只存"发现它"所需的最少信息。"""

    name: str
    path: str  # 外部项目根目录（= 该项目的 Workspace.root），存字符串便于 JSON 序列化
    # [external_projects_agent_skill_workflow_integration_plan.md 第1.4节]
    # 外部项目路径可以在磁盘任意位置（不保证挂在主项目目录下），无法从
    # `path` 反推主项目根，因此显式记录"注册这个外部项目时所在的主项目
    # 根目录"，供 `config/loader.py::load_config()` 在外部项目自己没有
    # `agent_config.json`/`providers.json` 时回退过去继承 LLM 配置。
    # 默认在 `register()` 里取注册时的 `Path.cwd()`，也可以显式传入。
    main_project_root: str = ""
    # [external_projects_cron_dispatch_plan.md 待确认问题 2] 默认 False，
    # 与 register() 的默认值保持一致——见 register() 文档字符串。
    enabled: bool = False
    registered_at: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "RegisteredProject":
        return cls(
            name=data["name"],
            path=data["path"],
            main_project_root=data.get("main_project_root", ""),
            enabled=data.get("enabled", True),
            registered_at=data.get("registered_at", ""),
        )


class ExternalProjectRegistry:
    """
    JSON 文件支撑的外部项目注册表。

    用法：
        registry = ExternalProjectRegistry()          # 默认落在
                                                        # ~/.mini_agent/external_projects.json
        registry.register("stock_watch", "/data/stock_watch")
        registry.list()                                # -> [RegisteredProject(...), ...]
        registry.unregister("stock_watch")
    """

    def __init__(self, store_path: Optional[Path] = None) -> None:
        self.store_path = Path(store_path) if store_path else DEFAULT_REGISTRY_PATH

    # ── 底层读写 ────────────────────────────────────────────────────────

    def _load(self) -> Dict[str, RegisteredProject]:
        if not self.store_path.exists():
            return {}
        try:
            raw = json.loads(self.store_path.read_text(encoding="utf-8") or "{}")
        except (json.JSONDecodeError, OSError):
            # 注册表文件损坏不应该让整个 daemon 起不来——原则三本来就要求
            # "daemon 不在场也不影响外部项目本身运行"，注册表只是可见性
            # 层，读失败时退化为"当前没有已注册项目"，而不是抛异常。
            return {}
        projects = raw.get("projects", {})
        return {name: RegisteredProject.from_dict(data) for name, data in projects.items()}

    def _save(self, projects: Dict[str, RegisteredProject]) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"projects": {name: p.to_dict() for name, p in projects.items()}}
        atomic_write_json(self.store_path, payload)

    # ── 增删查改 ────────────────────────────────────────────────────────

    def register(
        self,
        name: str,
        path: Path,
        *,
        enabled: bool = False,
        validate: bool = True,
        main_project_root: Optional[Path] = None,
    ) -> RegisteredProject:
        """
        注册一个外部项目。

        `validate=True`（默认）时会先尝试 `load_manifest(path)`，确保
        这个路径下确实有一份结构合法的 `project.yaml`，避免注册表里
        混入无法使用的条目；调用方如果只是想先占个位、稍后再补
        `project.yaml`，可以传 `validate=False` 跳过。

        `main_project_root`：[external_projects_agent_skill_workflow_
        integration_plan.md 第1.4节] 外部项目 `path` 可以在磁盘任意
        位置，不能假设它挂在主项目目录下，因此这里显式记录"注册这个
        外部项目时所在的主项目根"，供 `config/loader.py::load_config()`
        在外部项目自己没有 `agent_config.json`/`providers.json` 时
        回退过去继承 LLM 配置（provider/model/api_key）。未显式传入时
        默认取注册命令执行时的 `Path.cwd()`——`mini-agent projects
        register` 约定就是在主项目目录下执行，这个默认值覆盖最常见的
        用法；确实需要跨目录注册（比如脚本化批量注册）时可以显式传入。

        [external_projects_cron_dispatch_plan.md 待确认问题 2] `enabled`
        默认改为 `False`（opt-in）：这个字段现在同时控制"看板/CLI 展示"
        和"daemon 是否会按 project.yaml 里的 schedule 自动调度这个项目
        的 entrypoint"（见 `external_projects/scheduler.py::
        ensure_external_project_cron_jobs()`）。新注册的项目默认不会
        立刻开始自动跑定时任务，需要用户在看板上手动打开开关（或
        `mini-agent projects enable <name>`），确认过项目行为符合预期
        后再启用，避免"刚接入就开始按 cron 跑陌生代码"的意外。不影响
        该项目被 OS 原生 cron / 用户手动触发。
        """
        path = Path(path).expanduser().resolve()
        if validate:
            try:
                load_manifest(path)
            except ProjectManifestError as exc:
                raise ExternalProjectRegistryError(
                    f"注册失败，'{path}' 下的 project.yaml 不合法: {exc}"
                ) from exc

        projects = self._load()
        if name in projects:
            raise ExternalProjectRegistryError(
                f"项目 '{name}' 已注册（path={projects[name].path}），"
                f"如需更新请先 unregister 再重新 register"
            )

        from datetime import datetime, timezone

        _main_root = Path(main_project_root).expanduser().resolve() if main_project_root else Path.cwd()
        record = RegisteredProject(
            name=name,
            path=str(path),
            main_project_root=str(_main_root),
            enabled=enabled,
            registered_at=datetime.now(timezone.utc).isoformat(),
        )
        projects[name] = record
        self._save(projects)
        return record

    def find_by_path(self, path: Path) -> Optional[RegisteredProject]:
        """
        [external_projects_agent_skill_workflow_integration_plan.md
        第1.4节] 按外部项目根目录反查注册表条目——`config/loader.py::
        load_config()` 用它来找"这个外部项目当初是在哪个主项目下注册
        的"，从而回退继承主项目的 LLM 配置。找不到匹配条目（比如这个
        路径根本没注册过，只是碰巧有个 `project.yaml`）返回 `None`，
        调用方据此继续往下走"环境变量兜底"这条既有路径，不报错。
        """
        try:
            resolved = Path(path).expanduser().resolve()
        except OSError:
            return None
        for record in self._load().values():
            try:
                if Path(record.path).expanduser().resolve() == resolved:
                    return record
            except OSError:
                continue
        return None

    def unregister(self, name: str) -> None:
        projects = self._load()
        if name not in projects:
            raise ExternalProjectRegistryError(f"项目 '{name}' 未注册，无需移除")
        del projects[name]
        self._save(projects)

    def get(self, name: str) -> RegisteredProject:
        projects = self._load()
        if name not in projects:
            raise ExternalProjectRegistryError(f"项目 '{name}' 未注册")
        return projects[name]

    def list(self, *, enabled_only: bool = False) -> List[RegisteredProject]:
        projects = list(self._load().values())
        if enabled_only:
            projects = [p for p in projects if p.enabled]
        return sorted(projects, key=lambda p: p.name)

    def set_enabled(self, name: str, enabled: bool) -> RegisteredProject:
        projects = self._load()
        if name not in projects:
            raise ExternalProjectRegistryError(f"项目 '{name}' 未注册")
        projects[name].enabled = enabled
        self._save(projects)
        return projects[name]

    def load_manifest_for(self, name: str) -> ProjectManifest:
        """便捷方法：按注册表条目路径加载并返回该项目当前的 manifest。"""
        record = self.get(name)
        return load_manifest(Path(record.path))
