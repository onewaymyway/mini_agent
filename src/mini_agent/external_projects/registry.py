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
    enabled: bool = True
    registered_at: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "RegisteredProject":
        return cls(
            name=data["name"],
            path=data["path"],
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
        enabled: bool = True,
        validate: bool = True,
    ) -> RegisteredProject:
        """
        注册一个外部项目。

        `validate=True`（默认）时会先尝试 `load_manifest(path)`，确保
        这个路径下确实有一份结构合法的 `project.yaml`，避免注册表里
        混入无法使用的条目；调用方如果只是想先占个位、稍后再补
        `project.yaml`，可以传 `validate=False` 跳过。
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

        record = RegisteredProject(
            name=name,
            path=str(path),
            enabled=enabled,
            registered_at=datetime.now(timezone.utc).isoformat(),
        )
        projects[name] = record
        self._save(projects)
        return record

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
