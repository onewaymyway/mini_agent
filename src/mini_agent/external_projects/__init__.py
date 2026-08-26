"""
mini_agent/external_projects — 外部项目契约、注册表与调度支持

设计依据：`next_doc/external_projects_workspace_plan.md` 阶段 3。

本包只做三件事，且刻意保持薄：
  - manifest.py  : 解析/校验每个外部项目自己的 `project.yaml`（原则一：
                    daemon 只需要理解这份契约，不需要理解项目内部实现）
  - registry.py  : daemon 侧的外部项目注册表（原则三：声明式注册 +
                    被动可读状态，注册表本身与 daemon 代码树无关）
  - scheduler.py : 按 `project.yaml` 里的 `schedule` 计算/触发到期的
                    entrypoint（原则二：调度只是可选加成，未被调度到的
                    entrypoint 依然可以被 OS cron / 用户手动独立执行）

状态账本（`run_status.jsonl` 读写、聚合视图）见 `ledger.py`（读写）与
`status.py`（健康检查探测 + 聚合），对应阶段 4。
"""

from mini_agent.external_projects.manifest import (
    EntrypointSpec,
    HealthCheckSpec,
    ProjectManifest,
    ProjectManifestError,
    ResourceSpec,
    load_manifest,
)
from mini_agent.external_projects.registry import ExternalProjectRegistry
from mini_agent.external_projects.ledger import RunRecord, read_ledger, record_run, track_run
from mini_agent.external_projects.status import (
    ProjectStatusSnapshot,
    aggregate_status,
    probe_health,
    project_status_snapshot,
)

__all__ = [
    "EntrypointSpec",
    "HealthCheckSpec",
    "ProjectManifest",
    "ProjectManifestError",
    "ResourceSpec",
    "load_manifest",
    "ExternalProjectRegistry",
    "RunRecord",
    "read_ledger",
    "record_run",
    "track_run",
    "ProjectStatusSnapshot",
    "aggregate_status",
    "probe_health",
    "project_status_snapshot",
]
