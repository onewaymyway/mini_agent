"""
external_projects/status.py — 健康检查 + 账本聚合视图

对应 `next_doc/external_projects_workspace_plan.md` 阶段 4 第 3、4 项。

`project_status_snapshot()` 是 daemon 侧"看一个外部项目现在情况如何"
的唯一入口：优先探测 `health_check`（如果 project.yaml 声明了），探测
失败/未声明时退化为读账本最后一条记录——这正是 §5、原则三里反复强调
的"退化为读账本，而不是报错中断"。`aggregate_status()` 对注册表里
所有项目批量做这件事，供 daemon 侧接入现有 kanban dashboard（新增的
`GET /v1/self/external_projects` 端点，见 `api/routes.py`）直接返回。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import List, Optional

from mini_agent.external_projects.ledger import RunRecord, last_record, read_ledger
from mini_agent.external_projects.manifest import ProjectManifest, ProjectManifestError
from mini_agent.external_projects.registry import ExternalProjectRegistry

# health_check 命令探测的默认超时。project.yaml 目前没有为 health_check
# 单独暴露 timeout 配置项（如果未来发现有需要，可以在 manifest.py 里加，
# 属于第 5 节里刻意留白的"权限模型精细化"同一类问题，不提前设计）。
_HEALTH_CHECK_TIMEOUT_SEC = 30


@dataclass
class ProjectStatusSnapshot:
    name: str
    enabled: bool
    health: str  # "healthy" | "unhealthy" | "unknown"
    health_source: str  # "health_check" | "ledger" | "none"
    last_run: Optional[RunRecord]
    manifest_error: Optional[str] = None


def probe_health(manifest: ProjectManifest) -> Optional[bool]:
    """
    执行 `project.yaml` 声明的 `health_check.cmd`（若有）。

    返回 True/False 表示探测结果；未声明 `health_check` 时返回 None
    （不是"不健康"，是"没法回答这个问题"，调用方需要据此决定是否退化
    为读账本）。探测本身抛异常（命令不存在/超时等）按 False 处理，不
    向上抛出——健康检查探测失败本身就是"不健康"这个结论的一部分。
    """
    if manifest.health_check is None:
        return None
    try:
        proc = subprocess.run(
            manifest.health_check.cmd,
            shell=True,
            cwd=str(manifest.source_dir) if manifest.source_dir else None,
            timeout=_HEALTH_CHECK_TIMEOUT_SEC,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return proc.returncode == 0
    except Exception:
        return False


def project_status_snapshot(
    registry: ExternalProjectRegistry, name: str
) -> ProjectStatusSnapshot:
    """
    聚合出一个外部项目"现在情况如何"的快照：

      1. manifest 解析失败 → `health="unknown"`，`manifest_error` 说明
         原因，其余字段尽量还是给出（比如 enabled 状态依然来自注册表，
         不因为 manifest 坏了就整条隐藏）。
      2. manifest 有 `health_check` → 主动探测一次，探测结果直接作为
         `health`，`health_source="health_check"`。
      3. 没有 `health_check`，或探测本身失败到"探测不了"的程度 →
         退化为读账本最后一条记录：exit_code==0 → healthy，非 0 →
         unhealthy，账本为空 → unknown，`health_source="ledger"`（或
         账本也没有时是 `"none"`）。
    """
    record = registry.get(name)  # 未注册会在这里抛 ExternalProjectRegistryError，不吞

    try:
        manifest = registry.load_manifest_for(name)
    except ProjectManifestError as exc:
        return ProjectStatusSnapshot(
            name=name,
            enabled=record.enabled,
            health="unknown",
            health_source="none",
            last_run=None,
            manifest_error=str(exc),
        )

    last = last_record(manifest.source_dir) if manifest.source_dir else None
    probed = probe_health(manifest)

    if probed is not None:
        health = "healthy" if probed else "unhealthy"
        source = "health_check"
    elif last is not None:
        health = "healthy" if last.success else "unhealthy"
        source = "ledger"
    else:
        health = "unknown"
        source = "none"

    return ProjectStatusSnapshot(
        name=name,
        enabled=record.enabled,
        health=health,
        health_source=source,
        last_run=last,
    )


def aggregate_status(
    registry: ExternalProjectRegistry, *, recent_runs_limit: int = 5
) -> List[dict]:
    """
    对注册表里所有项目批量生成状态视图，供 HTTP 端点/CLI 直接序列化。

    单个项目聚合失败（比如 manifest 目录被移走）不应该拖垮整个视图，
    这里逐项目 try/except，出问题的项目本身仍然出现在结果里、只是标出
    错误原因，不让它消失或让整个请求 500。
    """
    results: List[dict] = []
    for r in registry.list():
        try:
            snap = project_status_snapshot(registry, r.name)
            recent = []
            try:
                manifest = registry.load_manifest_for(r.name)
                recent = [
                    rr.to_dict()
                    for rr in read_ledger(manifest.source_dir, limit=recent_runs_limit)
                ]
            except ProjectManifestError:
                recent = []
            results.append(
                {
                    "name": snap.name,
                    "path": r.path,
                    "enabled": snap.enabled,
                    "health": snap.health,
                    "health_source": snap.health_source,
                    "manifest_error": snap.manifest_error,
                    "last_run": snap.last_run.to_dict() if snap.last_run else None,
                    "recent_runs": recent,
                }
            )
        except Exception as exc:  # noqa: BLE001 - 聚合视图刻意不让单项目错误传染
            results.append(
                {
                    "name": r.name,
                    "path": r.path,
                    "enabled": r.enabled,
                    "health": "unknown",
                    "health_source": "none",
                    "manifest_error": str(exc),
                    "last_run": None,
                    "recent_runs": [],
                }
            )
    return results
