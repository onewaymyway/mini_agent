"""world_simulator/engine/management.py — 实例状态/设置管理 +
查询/删除。

从原单体 `engine.py` 拆分而来（阶段三十，4.18 节剩余部分），纯粹的
内部重组，行为不变。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from world_simulator.engine.errors import SimEngineError
from world_simulator.state_model import SimManifest, SimState
from world_simulator.store import SimNotFoundError, SimStore, list_sim_ids


def set_status(data_dir: Path, sim_id: str, status: str) -> SimManifest:
    """设置实例状态：`active` | `paused` | `ended`。"""
    if status not in ("active", "paused", "ended"):
        raise SimEngineError(f"非法状态：{status}")
    store = SimStore.for_root(data_dir, sim_id)
    manifest = store.load_manifest()
    manifest.status = status
    store.save_manifest(manifest)
    return manifest


def set_pilot_config(
    data_dir: Path, sim_id: str, *, pilot_mode: str, autopilot: Optional[Dict[str, Any]] = None
) -> SimManifest:
    """更新**当前活跃分支**的推进模式（手动挡/自动挡）与自动挡配置。

    每条分支有自己独立的一份自动挡配置（`SimStore.pilot_config_path`），
    互不影响：在分支 A 上调整"风险偏好"不会波及分支 B。这里只更新
    `manifest.branch` 指向的这一条分支的配置文件；`manifest.pilot_mode`/
    `manifest.autopilot` 这两个顶层字段仍然同步写一份作为"当前活跃分支
    配置"的镜像——`autopilot.py`、看板列表等既有代码读的就是这两个
    顶层字段，镜像它们可以在不改动那些读取逻辑的前提下，让"配置已经
    是按分支存储"这件事对它们透明。分支切换/分叉时（`branch_manager`）
    也会同步刷新这份镜像，保证它始终等于"当前活跃分支自己的配置"。

    不校验 `autopilot` 字段内部结构（`principles`/`risk_preference`/
    `review_mode`），非法值会在真正调用 `advance_step` workflow 时体现
    为"skill 读不懂这段画像"而不是这里报错——阶段四范围内暂不引入
    额外的 schema 校验。
    """
    if pilot_mode not in ("manual", "autopilot"):
        raise SimEngineError(f"非法推进模式：{pilot_mode}")
    store = SimStore.for_root(data_dir, sim_id)
    manifest = store.load_manifest()
    autopilot_cfg = dict(autopilot or {})
    store.save_pilot_config(manifest.branch, pilot_mode, autopilot_cfg)
    manifest.pilot_mode = pilot_mode
    manifest.autopilot = autopilot_cfg
    store.save_manifest(manifest)
    return manifest


def update_settings(data_dir: Path, sim_id: str, **updates: Any) -> SimManifest:
    """更新实例的 `settings`（`options_count`/`time_granularity`），
    只合并传入的字段，不清空其它已有设置。

    对应"创建时选的候选方向数量/时间粒度，之后模拟过程中还想改"的场景
    （比如模拟推进到后期想从"1 年一步"切到"1 个月一步"看得更细）；
    改了之后从下一次 `advance()` 调用开始生效——`advance()` 每次都
    重新从磁盘读 `manifest.settings`，不需要额外的"生效"逻辑。
    """
    store = SimStore.for_root(data_dir, sim_id)
    manifest = store.load_manifest()
    manifest.settings = {**manifest.settings, **updates}
    store.save_manifest(manifest)
    return manifest


def rename_simulation(data_dir: Path, sim_id: str, new_title: str) -> SimManifest:
    """修改一个模拟实例的标题（`manifest.title`）。

    纯展示层的重命名，不涉及历史/分支数据，也不影响 `intent`（创建时的
    原始一句话意图，作为"这个实例最初想模拟什么"的留档，重命名不应该
    连带改掉）——只改 `title` 这一个字段，同 `update_settings()` 的
    "只合并/只改传入字段"取舍一致。
    """
    title = new_title.strip()
    if not title:
        raise SimEngineError("标题不能为空")
    store = SimStore.for_root(data_dir, sim_id)
    manifest = store.load_manifest()
    manifest.title = title
    store.save_manifest(manifest)
    return manifest


def delete_simulation(data_dir: Path, sim_id: str) -> None:
    """彻底删除一个模拟实例（`data/<sim_id>/` 整个目录，含所有分支）。

    对应方案第 5 节页面 5「存档管理」的"删除实例"操作。这是一个不可逆
    操作——不做软删除/回收站，理由：模拟实例本身就是"存档"语义（分叉/
    回滚已经覆盖了"不想要这条时间线了"的场景，见 `branch_manager.py`），
    真正点了"删除"通常是想彻底清掉一个不再需要的实例，加一层回收站会
    让"删除"这个操作本身语义变得含糊；调用方（`app.py`）在 UI 层要求
    二次确认来弥补"不可逆"的风险。
    """
    store = SimStore.for_root(data_dir, sim_id)
    if not store.exists():
        raise SimNotFoundError(f"模拟实例不存在：{sim_id}")
    import shutil

    shutil.rmtree(store.sim_dir)


def get_simulation(data_dir: Path, sim_id: str) -> tuple[SimManifest, SimState, list]:
    """返回 (manifest, current_state, history)，供 CLI/看板展示。"""
    store = SimStore.for_root(data_dir, sim_id)
    manifest = store.load_manifest()
    current = store.load_current_state(manifest.branch)
    history = store.load_history(manifest.branch)
    if current is None:
        raise SimEngineError(f"模拟实例缺少当前状态：{sim_id}")
    return manifest, current, history


def list_simulations(data_dir: Path) -> list:
    """返回所有实例的 manifest 列表，按 `created_at` 倒序（最新的排在
    最前面）。

    `sim_id` 本身是 `{template}_{随机后缀}`（见 `_new_sim_id()`），不
    包含时间信息，目录名字典序并不等价于创建时间顺序，因此这里显式
    按 `created_at`（`now_iso()` 生成的 ISO 8601 字符串，可直接按字符
    串倒序比较）排序，而不是依赖 `list_sim_ids()` 的目录遍历顺序。
    `created_at` 缺失或解析异常的历史脏数据（理论上不应该出现，
    `materialize_simulation()` 落盘时总会写入）统一排到最后，不让
    异常数据影响其它正常实例的排序，也不让整个列表页因此报错。
    """
    manifests = []
    for sim_id in list_sim_ids(Path(data_dir)):
        store = SimStore.for_root(data_dir, sim_id)
        try:
            manifests.append(store.load_manifest())
        except SimNotFoundError:
            continue
    manifests.sort(key=lambda m: m.created_at or "", reverse=True)
    return manifests
