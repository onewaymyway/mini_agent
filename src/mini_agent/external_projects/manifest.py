"""
external_projects/manifest.py — `project.yaml` schema 与解析

对应 `next_doc/external_projects_workspace_plan.md` §5.2 / 阶段 3 第一项。

设计要点（呼应原则一：引擎与宿主解耦）：
  - `ProjectManifest` 是 daemon 理解一个外部项目所需的**全部**信息，
    daemon 不应该、也不需要 import 外部项目自己的任何代码。
  - 校验只做"结构是否合法"（必填字段、类型、cron 表达式基本格式），
    不校验 `cmd` 指向的脚本是否存在/能否运行——那是 `registry.py`
    register 时的可选深校验，或者运行时才会暴露的问题，manifest 层
    只负责契约本身的正确性。
  - 没有引入新的第三方依赖：用标准库 + 项目已有的 `pyyaml` 依赖
    （`yaml.safe_load`），不用 pydantic——这份 schema 足够小，手写
    dataclass + 校验函数比引入模型框架更轻量，与 `Workspace`
    （`workspace.py`）保持同样的"数据类 + 显式校验"风格。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# cron 表达式最基本的形状校验：5 个空白分隔的字段。不做语义校验（比如
# 字段范围是否合法），语义留给真正触发调度时用的 cron 解析库去报错，这里
# 只挡住"完全不像 cron 表达式"的低级错误。
_CRON_FIELD_COUNT = 5


class ProjectManifestError(ValueError):
    """`project.yaml` 内容不合法（缺字段/类型错误/格式错误）。"""


@dataclass
class EntrypointSpec:
    """`project.yaml` 里 `entrypoints.<key>` 对应的一条声明。"""

    key: str
    cmd: str
    schedule: Optional[str] = None
    timeout_sec: Optional[int] = None

    @property
    def cron_expr(self) -> Optional[str]:
        """
        从 `schedule: "cron: 0 9,13 * * 1-5"` 里剥出纯 cron 表达式部分。

        约定沿用 §5.2 示例里的 `cron: <expr>` 前缀写法；`schedule` 为
        None，或不是 `cron:` 前缀（未来可能扩展 `interval:` 等），返回
        None，交给调用方决定是否是它能处理的调度形式。
        """
        if not self.schedule:
            return None
        prefix = "cron:"
        text = self.schedule.strip()
        if not text.lower().startswith(prefix):
            return None
        return text[len(prefix):].strip()


@dataclass
class HealthCheckSpec:
    cmd: str


@dataclass
class ResourceSpec:
    allowed_domains: List[str] = field(default_factory=list)
    max_concurrency: int = 1


@dataclass
class ProjectManifest:
    """一份已校验通过的 `project.yaml` 内容。"""

    name: str
    entrypoints: Dict[str, EntrypointSpec]
    health_check: Optional[HealthCheckSpec] = None
    resources: ResourceSpec = field(default_factory=ResourceSpec)
    # manifest 所在目录，即该外部项目的 Workspace root。不是 project.yaml
    # 文件本身声明的字段，而是 load_manifest() 加载时按来源路径回填，方便
    # 调用方（registry/scheduler）不用另外再传一份 root。
    source_dir: Optional[Path] = None

    def entrypoint(self, key: str) -> EntrypointSpec:
        try:
            return self.entrypoints[key]
        except KeyError as exc:
            available = ", ".join(sorted(self.entrypoints)) or "(none)"
            raise ProjectManifestError(
                f"entrypoint '{key}' 未在 project.yaml 中声明，"
                f"可用: {available}"
            ) from exc

    def scheduled_entrypoints(self) -> List[EntrypointSpec]:
        """返回所有声明了 `schedule` 的 entrypoint（供调度器/阶段 3 使用）。"""
        return [ep for ep in self.entrypoints.values() if ep.schedule]


def _require(data: Dict[str, Any], key: str, *, ctx: str) -> Any:
    if key not in data:
        raise ProjectManifestError(f"{ctx}: 缺少必填字段 '{key}'")
    return data[key]


def _require_str(data: Dict[str, Any], key: str, *, ctx: str) -> str:
    value = _require(data, key, ctx=ctx)
    if not isinstance(value, str) or not value.strip():
        raise ProjectManifestError(f"{ctx}: 字段 '{key}' 必须是非空字符串")
    return value


def _validate_cron_shape(expr: str, *, ctx: str) -> None:
    if len(expr.split()) != _CRON_FIELD_COUNT:
        raise ProjectManifestError(
            f"{ctx}: cron 表达式 '{expr}' 应为 {_CRON_FIELD_COUNT} 个"
            f"空白分隔字段（分 时 日 月 周），实际得到"
            f" {len(expr.split())} 个"
        )


def _parse_entrypoint(key: str, raw: Any) -> EntrypointSpec:
    ctx = f"entrypoints.{key}"
    if not isinstance(raw, dict):
        raise ProjectManifestError(f"{ctx}: 必须是一个映射（cmd/schedule/timeout_sec）")
    cmd = _require_str(raw, "cmd", ctx=ctx)

    schedule = raw.get("schedule")
    if schedule is not None:
        if not isinstance(schedule, str) or not schedule.strip():
            raise ProjectManifestError(f"{ctx}: 'schedule' 必须是非空字符串")
        schedule = schedule.strip()
        if schedule.lower().startswith("cron:"):
            _validate_cron_shape(schedule[len("cron:"):].strip(), ctx=ctx)

    timeout_sec = raw.get("timeout_sec")
    if timeout_sec is not None and (
        not isinstance(timeout_sec, int) or isinstance(timeout_sec, bool) or timeout_sec <= 0
    ):
        raise ProjectManifestError(f"{ctx}: 'timeout_sec' 必须是正整数")

    return EntrypointSpec(key=key, cmd=cmd, schedule=schedule, timeout_sec=timeout_sec)


def _parse_health_check(raw: Any) -> Optional[HealthCheckSpec]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ProjectManifestError("health_check: 必须是一个映射（cmd）")
    cmd = _require_str(raw, "cmd", ctx="health_check")
    return HealthCheckSpec(cmd=cmd)


def _parse_resources(raw: Any) -> ResourceSpec:
    if raw is None:
        return ResourceSpec()
    if not isinstance(raw, dict):
        raise ProjectManifestError("resources: 必须是一个映射（allowed_domains/max_concurrency）")

    allowed_domains = raw.get("allowed_domains", [])
    if not isinstance(allowed_domains, list) or not all(
        isinstance(d, str) for d in allowed_domains
    ):
        raise ProjectManifestError("resources.allowed_domains: 必须是字符串列表")

    max_concurrency = raw.get("max_concurrency", 1)
    if (
        not isinstance(max_concurrency, int)
        or isinstance(max_concurrency, bool)
        or max_concurrency < 1
    ):
        raise ProjectManifestError("resources.max_concurrency: 必须是 >= 1 的整数")

    return ResourceSpec(allowed_domains=list(allowed_domains), max_concurrency=max_concurrency)


def parse_manifest(text: str, *, source_dir: Optional[Path] = None) -> ProjectManifest:
    """从 `project.yaml` 的文本内容解析出 `ProjectManifest`，做结构校验。"""
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ProjectManifestError(f"project.yaml 不是合法的 YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise ProjectManifestError("project.yaml 顶层必须是一个映射")

    name = _require_str(data, "name", ctx="project.yaml")

    raw_entrypoints = _require(data, "entrypoints", ctx="project.yaml")
    if not isinstance(raw_entrypoints, dict) or not raw_entrypoints:
        raise ProjectManifestError("project.yaml: 'entrypoints' 必须是至少含一项的映射")
    entrypoints = {
        key: _parse_entrypoint(key, raw) for key, raw in raw_entrypoints.items()
    }

    health_check = _parse_health_check(data.get("health_check"))
    resources = _parse_resources(data.get("resources"))

    return ProjectManifest(
        name=name,
        entrypoints=entrypoints,
        health_check=health_check,
        resources=resources,
        source_dir=Path(source_dir).expanduser().resolve() if source_dir else None,
    )


def load_manifest(path: Path) -> ProjectManifest:
    """
    从磁盘加载一份 `project.yaml`。

    `path` 既可以是 `project.yaml` 文件本身，也可以是外部项目的根目录
    （此时约定读取 `<path>/project.yaml`），方便调用方直接传 `Workspace.root`
    或 `Workspace.project_yaml_path`。
    """
    path = Path(path).expanduser()
    manifest_path = path / "project.yaml" if path.is_dir() else path
    if not manifest_path.exists():
        raise ProjectManifestError(f"project.yaml 不存在: {manifest_path}")
    text = manifest_path.read_text(encoding="utf-8")
    return parse_manifest(text, source_dir=manifest_path.parent)
