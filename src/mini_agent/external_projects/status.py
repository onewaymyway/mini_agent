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
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from mini_agent.external_projects.ledger import RunRecord, last_record, read_ledger
from mini_agent.external_projects.manifest import ProjectManifest, ProjectManifestError
from mini_agent.external_projects.registry import ExternalProjectRegistry

# health_check 命令探测的默认超时。project.yaml 目前没有为 health_check
# 单独暴露 timeout 配置项（如果未来发现有需要，可以在 manifest.py 里加，
# 属于第 5 节里刻意留白的"权限模型精细化"同一类问题，不提前设计）。
_HEALTH_CHECK_TIMEOUT_SEC = 30

# [2026-08-28 追加 — external_projects_kanban_integration_plan.md
# 「看板切换外部项目 tab 卡顿排查」] `probe_health()` 每次都要 fork 一个
# 新的 Python 解释器去 `import akshare/pandas` 之类的重依赖，冷启动
# 单次就要 1~5 秒，看板每次切换 tab（Streamlit 整页重跑）都会重新拉一次
# `/self/external_projects`，若不缓存，用户体验就是"点一下 tab，卡几秒
# 才出内容"，即便结果这几秒内根本不会变。这里给 probe_health() 结果加一
# 层进程内 TTL 缓存：同一个 (项目名, health_check 命令) 在 TTL 窗口内
# 只探测一次，命中缓存直接返回，不再 fork 子进程。TTL 默认 60s——比看板
# 用户正常切 tab 的间隔短得多，不会让"健康状态"明显滞后于真实情况，但
# 足以吸收"来回切几次 tab"这种短时间重复请求。
_HEALTH_CACHE_TTL_SEC = 60.0
_health_cache: Dict[Tuple[str, str], Tuple[float, Optional[bool]]] = {}


def _clear_health_cache() -> None:
    """仅供测试使用：清空探测缓存，避免用例之间互相污染。"""
    _health_cache.clear()


@dataclass
class ProjectStatusSnapshot:
    name: str
    enabled: bool
    health: str  # "healthy" | "unhealthy" | "unknown"
    health_source: str  # "health_check" | "ledger" | "none"
    last_run: Optional[RunRecord]
    manifest_error: Optional[str] = None


def probe_health(manifest: ProjectManifest, *, use_cache: bool = True) -> Optional[bool]:
    """
    执行 `project.yaml` 声明的 `health_check.cmd`（若有）。

    返回 True/False 表示探测结果；未声明 `health_check` 时返回 None
    （不是"不健康"，是"没法回答这个问题"，调用方需要据此决定是否退化
    为读账本）。探测本身抛异常（命令不存在/超时等）按 False 处理，不
    向上抛出——健康检查探测失败本身就是"不健康"这个结论的一部分。

    `use_cache=True`（默认）时，命中 `_HEALTH_CACHE_TTL_SEC` 内的缓存
    会直接返回，不再 fork 子进程；`aggregate_status()` 的看板高频轮询
    场景应使用默认值，CLI `mini-agent projects status` 这类"用户主动
    要一个当下准确结果"的场景可以传 `use_cache=False` 强制重新探测。
    """
    if manifest.health_check is None:
        return None

    cache_key = (manifest.name, manifest.health_check.cmd)
    now = time.monotonic()
    if use_cache:
        cached = _health_cache.get(cache_key)
        if cached is not None and (now - cached[0]) < _HEALTH_CACHE_TTL_SEC:
            return cached[1]

    try:
        proc = subprocess.run(
            manifest.health_check.cmd,
            shell=True,
            cwd=str(manifest.source_dir) if manifest.source_dir else None,
            timeout=_HEALTH_CHECK_TIMEOUT_SEC,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        result: Optional[bool] = proc.returncode == 0
    except Exception:
        result = False

    _health_cache[cache_key] = (now, result)
    return result


def project_status_snapshot(
    registry: ExternalProjectRegistry, name: str, *, use_cache: bool = True
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
    probed = probe_health(manifest, use_cache=use_cache)

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
    registry: ExternalProjectRegistry, *, recent_runs_limit: int = 5, use_cache: bool = True
) -> List[dict]:
    """
    对注册表里所有项目批量生成状态视图，供 HTTP 端点/CLI 直接序列化。

    单个项目聚合失败（比如 manifest 目录被移走）不应该拖垮整个视图，
    这里逐项目 try/except，出问题的项目本身仍然出现在结果里、只是标出
    错误原因，不让它消失或让整个请求 500。

    `use_cache`：透传给 `probe_health()`（见其 docstring）。看板高频
    轮询用默认 True 吃 TTL 缓存；这个函数本身仍是同步阻塞的（未缓存命中
    时依然会 fork 子进程逐项目探测），调用方如果在 asyncio 事件循环里
    用，必须自己 `asyncio.to_thread()` 包一层——不在这里改成 async，是
    因为 CLI（`mini-agent projects status`）也直接同步调用它，没有事件
    循环可言。
    """
    results: List[dict] = []
    for r in registry.list():
        try:
            snap = project_status_snapshot(registry, r.name, use_cache=use_cache)
            manifest = None
            entrypoints: List[dict] = []
            kanban_view: Optional[dict] = None
            try:
                manifest = registry.load_manifest_for(r.name)
                recent = [
                    rr.to_dict()
                    for rr in read_ledger(manifest.source_dir, limit=recent_runs_limit)
                ]
                entrypoints = [
                    {
                        "key": ep.key,
                        "cmd": ep.cmd,
                        "schedule": ep.schedule,
                        # [external_projects_kanban_integration_plan.md 阶段6]
                        # 供看板在「▶️ 触发」按钮旁按声明渲染参数输入框，
                        # 不用用户去猜 cmd 后面该拼什么位置参数。
                        "params": [
                            {
                                "name": p.name,
                                "required": p.required,
                                "default": p.default,
                                "help": p.help,
                            }
                            for p in ep.params
                        ],
                    }
                    for ep in manifest.entrypoints.values()
                ]
                if manifest.kanban_view is not None:
                    # [external_projects_generic_kanban_view_refactor_plan.md
                    # 阶段B] 把通用看板视图的契约随聚合状态一起下发，供前端
                    # 判断"这个项目有没有声明看板视图"而不必额外发一次请求去
                    # 探测——与 entrypoints/params 字段是同一个模式。
                    kv = manifest.kanban_view
                    kanban_view = {
                        "data_file": kv.data_file,
                        "id_field": kv.id_field,
                        "title_field": kv.title_field,
                        "state_field": kv.state_field,
                        "states": [
                            {"value": s.value, "label": s.label, "collapsed": s.collapsed}
                            for s in kv.states
                        ],
                        "metric_fields": [
                            {"field": m.field, "label": m.label, "format": m.format}
                            for m in kv.metric_fields
                        ],
                        "detail_list_field": kv.detail_list_field,
                        "change_state": (
                            {
                                "entrypoint": kv.change_state.entrypoint,
                                "id_param": kv.change_state.id_param,
                                "state_param": kv.change_state.state_param,
                                "note_param": kv.change_state.note_param,
                            }
                            if kv.change_state is not None
                            else None
                        ),
                    }
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
                    # [external_projects_kanban_integration_plan.md 阶段5]
                    # 供看板"手动触发"直接列出按钮，不用用户手填 entrypoint
                    # key。manifest 解析失败时留空列表——manifest_error
                    # 字段已经说明了原因，这里不重复报错。
                    "entrypoints": entrypoints,
                    # [external_projects_generic_kanban_view_refactor_plan.md
                    # 阶段B]
                    "kanban_view": kanban_view,
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
                    "entrypoints": [],
                    "kanban_view": None,
                }
            )
    return results
