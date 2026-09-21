"""world_simulator/branch_manager.py — 分叉、回滚、对比

设计依据：`world_simulator_external_project_plan.md` 第 6 节"分支/存档"
与第 5 节页面 4（对比视图）。

关键设计取舍：**"回滚重新选"不是销毁重写，而是在某个历史节点开一条
新分支**——复制该节点之前的历史到 `branches/<branch_id>/`，原时间线
（`main` 或任意已存在分支）原样保留，天然支持"对比两条时间线"。
`SimManifest.branch` 字段记录"当前活跃分支"（`advance()` 默认推进的
分支），不代表"只存在这一条分支"——已经 fork 出来的历史分支即使不是
当前活跃分支，依然可以在对比视图里被读取展示。
"""

from __future__ import annotations

import dataclasses
import json
import secrets
import string
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from world_simulator.state_model import SimManifest, SimState
from world_simulator.store import SimNotFoundError, SimStore, now_iso

try:
    from mini_agent.utils.atomic_write import atomic_write_jsonl
except ImportError:  # 独立运行降级，与 store.py 的约定一致
    def atomic_write_jsonl(path: Path, records: list) -> None:  # type: ignore[misc]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + ("\n" if records else ""),
            encoding="utf-8",
        )


_BRANCH_ID_ALPHABET = string.ascii_lowercase + string.digits


class BranchError(RuntimeError):
    pass


def _new_branch_id() -> str:
    suffix = "".join(secrets.choice(_BRANCH_ID_ALPHABET) for _ in range(5))
    return f"br_{suffix}"


def list_branches(data_dir: Path, sim_id: str) -> List[str]:
    """列出实例已存在的所有分支 id，`main` 恒排第一个。"""
    store = SimStore.for_root(data_dir, sim_id)
    if not store.exists():
        raise SimNotFoundError(f"模拟实例不存在：{sim_id}")
    branches = ["main"]
    branches_dir = store.sim_dir / "branches"
    if branches_dir.exists():
        for child in sorted(branches_dir.iterdir()):
            if child.is_dir() and (child / "state_current.json").exists():
                branches.append(child.name)
    return branches


def list_branches_detailed(data_dir: Path, sim_id: str) -> List[Dict[str, Any]]:
    """列出实例已存在的所有分支，附带足够区分彼此的元信息，供看板的
    「分支」列表展示（`list_branches()` 只返回 id 列表，看着完全一样，
    用户很难分辨哪条分支是什么时候、为什么、用什么自动挡画像分出来
    的——这个函数把这些信息一次性取齐）。

    每个元素结构：
        {
            "branch": str,                # 分支 id
            "is_current": bool,           # 是否为当前活跃分支
            "created_at": str,            # 创建时间（main 用实例创建时间）
            "source_branch": Optional[str],   # 从哪条分支分叉出来（main 为 None）
            "from_step": Optional[int],       # 分叉自哪一步（main 为 None）
            "current_step": Optional[int],    # 该分支当前推进到第几步
            "step_count": int,                # 该分支历史长度（含初始状态）
            "pilot_mode": str,                # "manual" | "autopilot"
            "autopilot_enabled": bool,
            "risk_preference": str,
            "review_mode": str,
            "allow_custom_options": bool,     # 是否允许代理跳出候选列表自选
            "principles_count": int,          # 自动挡原则/偏好条数
        }
    """
    store = SimStore.for_root(data_dir, sim_id)
    if not store.exists():
        raise SimNotFoundError(f"模拟实例不存在：{sim_id}")
    manifest = store.load_manifest()

    detailed: List[Dict[str, Any]] = []
    for b in list_branches(data_dir, sim_id):
        current = store.load_current_state(b)
        history = store.load_history(b)
        pilot_cfg = store.load_pilot_config(b)
        ap = pilot_cfg.get("autopilot") or {}
        if b == "main":
            meta: Dict[str, Any] = {"created_at": manifest.created_at, "source_branch": None, "from_step": None}
        else:
            meta = store.load_branch_meta(b) or {}
        detailed.append(
            {
                "branch": b,
                "is_current": b == manifest.branch,
                "created_at": meta.get("created_at") or "未知",
                "source_branch": meta.get("source_branch"),
                "from_step": meta.get("from_step"),
                "current_step": current.step if current else None,
                "step_count": len(history),
                "pilot_mode": pilot_cfg.get("pilot_mode", "manual"),
                "autopilot_enabled": bool(ap.get("enabled")),
                "risk_preference": ap.get("risk_preference", "balanced"),
                "review_mode": ap.get("review_mode", "silent"),
                "allow_custom_options": bool(ap.get("allow_custom_options")),
                "principles_count": len(ap.get("principles") or []),
            }
        )
    return detailed


def fork_branch(
    data_dir: Path,
    sim_id: str,
    *,
    from_step: int,
    source_branch: str = "main",
    branch_id: Optional[str] = None,
    switch: bool = True,
) -> str:
    """在 `source_branch` 的第 `from_step` 步开一条新分支。

    新分支的历史 = `source_branch` 历史里 step <= from_step 的部分（不含
    `from_step` 之后已经发生的事），当前状态即该分支历史的最后一条。
    `switch=True`（默认）时把 `manifest.branch` 切到新分支，对应"回滚
    重新选之后继续在新分支上推进"这个最常见的用法；`switch=False` 用于
    "只是想留一份存档/用于对比，不打算立刻切过去推进"的场景。

    分叉出来的新分支，最后一个节点（第 `from_step` 步）的
    `chosen_option_id`/`chosen_by`/`chosen_reason` 会被清空，即使原
    分支上这个节点已经记录过"选了什么"——道理很简单：新分支存在的意义
    就是"这个节点之后要重新决定"，如果还留着旧分支的选择记录，会让人
    以为"回滚了但其实什么都没变"，`from_step=0`（回到最开始）时这个
    问题最明显：不清空的话，新分支的起点看起来"已经做过一次选择"，
    没法真正重新开始。`options` 本身（候选方向列表）不受影响，原样
    带过来供重新选择。
    """
    store = SimStore.for_root(data_dir, sim_id)
    manifest = store.load_manifest()

    source_history = store.load_history(source_branch)
    if not source_history:
        raise BranchError(f"分支 {source_branch!r} 没有历史，无法分叉")
    cutoff = [s for s in source_history if s.step <= from_step]
    if not cutoff:
        raise BranchError(f"分支 {source_branch!r} 没有 step <= {from_step} 的历史节点")

    # 清空最后一个节点的"已选择"记录（见上方 docstring）；`replace()`
    # 产出一个新对象，不会连带改到 `source_history`/原分支磁盘上的数据。
    cutoff = cutoff[:-1] + [
        dataclasses.replace(cutoff[-1], chosen_option_id=None, chosen_by=None, chosen_reason=None)
    ]

    new_branch = branch_id or _new_branch_id()
    if new_branch == "main":
        raise BranchError("分支 id 不能是 'main'（保留名）")
    if new_branch in list_branches(data_dir, sim_id):
        raise BranchError(f"分支 {new_branch!r} 已存在")

    atomic_write_jsonl(
        store.state_history_path(new_branch), [s.to_dict() for s in cutoff]
    )
    try:
        from mini_agent.utils.atomic_write import atomic_write_json
    except ImportError:
        def atomic_write_json(path: Path, data, *, flock: bool = False) -> None:  # type: ignore[misc]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    atomic_write_json(store.state_current_path(new_branch), cutoff[-1].to_dict())

    # 自动挡配置：新分支继承 source_branch 当前那一份，但落盘成自己
    # 独立的一份文件（`save_pilot_config` 整份重写，不是引用/链接）——
    # 之后不管哪条分支上再调整自动挡设置，都只会改到自己这一份，两条
    # 分支各自用不同的自动挡画像跑模拟互不干扰，这正是"分支管理"要
    # 支持的用法：同一个起点分岔出去，一条手动挡、一条自动挡（甚至
    # 自动挡画像都不同）。
    pilot_cfg = store.load_pilot_config(source_branch)
    store.save_pilot_config(new_branch, pilot_cfg["pilot_mode"], pilot_cfg["autopilot"])

    # 分支元信息（创建时间/来源分支/分叉自哪一步）：只用于列表展示
    # （`list_branches_detailed`），不参与任何推进/对比逻辑，所以落盘
    # 失败也不应该让整个分叉操作失败——这里不做特殊 try/except，
    # 走到这一步前面的落盘都已经成功，说明存储是可写的，正常情况下
    # 这一步不会单独失败。
    store.save_branch_meta(
        new_branch,
        {"created_at": now_iso(), "source_branch": source_branch, "from_step": from_step},
    )

    if switch:
        manifest.branch = new_branch
        manifest.current_step = cutoff[-1].step
        # 镜像新分支自己的自动挡配置到 manifest 顶层字段，道理同
        # `switch_branch`/`engine.set_pilot_config` 的注释：既有代码
        # 读的是 manifest.pilot_mode/manifest.autopilot，切过去之后
        # 这两个字段要立刻反映"新分支自己的配置"，而不是继续显示切换
        # 前那条分支的配置。
        manifest.pilot_mode = pilot_cfg["pilot_mode"]
        manifest.autopilot = dict(pilot_cfg["autopilot"])
        store.save_manifest(manifest)

    return new_branch


def switch_branch(data_dir: Path, sim_id: str, branch_id: str) -> SimManifest:
    """把实例的"当前活跃分支"切到 `branch_id`（必须已存在）。

    连带把 `manifest.pilot_mode`/`manifest.autopilot` 刷新成 `branch_id`
    自己的自动挡配置——每条分支的自动挡设置是独立存储的（见
    `SimStore.load_pilot_config`），切换分支时如果不刷新这两个镜像
    字段，`autopilot.py`/看板会继续显示、继续使用切换前那条分支的
    配置，等于"切了分支但自动挡画像没跟着切"，不符合"每个分支一份
    独立配置"的预期。
    """
    if branch_id not in list_branches(data_dir, sim_id):
        raise BranchError(f"分支不存在：{branch_id}")
    store = SimStore.for_root(data_dir, sim_id)
    manifest = store.load_manifest()
    current = store.load_current_state(branch_id)
    if current is None:
        raise BranchError(f"分支 {branch_id!r} 缺少当前状态，数据可能已损坏")
    pilot_cfg = store.load_pilot_config(branch_id)
    manifest.branch = branch_id
    manifest.current_step = current.step
    manifest.pilot_mode = pilot_cfg["pilot_mode"]
    manifest.autopilot = pilot_cfg["autopilot"]
    store.save_manifest(manifest)
    return manifest


def delete_branch(data_dir: Path, sim_id: str, branch_id: str) -> None:
    """删除一条已存在的分支。

    约束（都是"防止手滑删掉正在用的东西"）：
    - 不能删除 `main`——`main` 是实例本体的时间线，不是"branches/ 目录
      下的一条分支"，删了等于删掉整个实例；想清空实例请走实例删除
      的入口，不归这个函数管。
    - 不能删除当前活跃分支（`manifest.branch`）——删除前必须先
      `switch_branch` 切到别的分支，避免删完之后 `manifest.branch`
      指向一个已经不存在的分支，后续读取直接报错。
    删除是不可逆的物理删除（`shutil.rmtree`），不做"回收站"式的软删除。
    """
    if branch_id == "main":
        raise BranchError("不能删除 main 分支")
    store = SimStore.for_root(data_dir, sim_id)
    manifest = store.load_manifest()
    if branch_id == manifest.branch:
        raise BranchError("不能删除当前活跃分支，请先切换到其他分支")
    if branch_id not in list_branches(data_dir, sim_id):
        raise BranchError(f"分支不存在：{branch_id}")
    store.delete_branch_dir(branch_id)


def load_branch_timeline(data_dir: Path, sim_id: str, branch: str) -> List[SimState]:
    """读取某条分支的完整历史，供对比视图使用。"""
    store = SimStore.for_root(data_dir, sim_id)
    return store.load_history(branch)


def merge_branch(
    data_dir: Path, sim_id: str, *, source: str, target: str, from_step: int
) -> int:
    """"合并"分支（第八轮批次六，`next_doc/world_simulator_c_category_
    precision_upgrade_improvement_plan.md` 第 7 节）：把 `target`
    分支从 `from_step` **之后**的历史，替换成 `source` 分支从
    `from_step` 之后的历史。

    **退化为"指针切换"，不是真正的字段级三路合并**（原因见上游盘点
    文档 4.6 节——两条分支的 `vars` 可能已经不可调和地分歧，自动合并
    大概率产生语义错误的结果）：本质是"以 `source` 为准覆盖 `target`
    的后续部分"，不是合并两边都有价值的内容。

    **前置条件（硬性，不满足直接拒绝）**：`from_step` 及之前，两条
    分支的历史必须**完全一致**（逐步比较 `step` 序号和 `to_dict()`
    内容）——不一致说明两条分支在这之前就已经分歧，"以谁为准"是一个
    需要人工判断的语义问题，本函数不猜、直接拒绝执行并报错，避免
    产生一份看起来"合并成功"但实际语义不明的历史。

    Args:
        source: 提供 `from_step` 之后历史的分支（"以它为准"）。
        target: 历史被替换的分支（`from_step` 之后的部分会被覆盖，
            `from_step` 及之前保持不变——反正前置条件已经要求这部分
            和 `source` 完全一致）。
        from_step: 两条分支历史一致性的分界点（含），也是合并后
            "从这一步开始采用 source 的后续内容"的分界点。

    Returns:
        合并后 `target` 分支历史最后一个状态的 `step` 序号。

    Raises:
        BranchError: `source`/`target` 相同、任一分支不存在、
            `from_step` 为负数、任一分支在 `from_step` 之前没有历史、
            或两条分支在 `from_step` 之前的历史不一致。
    """
    if source == target:
        raise BranchError("source 和 target 不能是同一条分支")
    if from_step < 0:
        raise BranchError("from_step 不能是负数")

    existing_branches = list_branches(data_dir, sim_id)
    if source not in existing_branches:
        raise BranchError(f"分支不存在：{source}")
    if target not in existing_branches:
        raise BranchError(f"分支不存在：{target}")

    store = SimStore.for_root(data_dir, sim_id)
    source_history = store.load_history(source)
    target_history = store.load_history(target)

    source_prefix = [s for s in source_history if s.step <= from_step]
    target_prefix = [s for s in target_history if s.step <= from_step]
    if not source_prefix or not target_prefix:
        raise BranchError(
            f"两条分支都必须有 step <= {from_step} 的历史节点才能合并"
            f"（source 有 {len(source_prefix)} 条，target 有 {len(target_prefix)} 条）"
        )

    source_prefix_steps = [s.step for s in source_prefix]
    target_prefix_steps = [s.step for s in target_prefix]
    if source_prefix_steps != target_prefix_steps:
        raise BranchError(
            f"两条分支在 from_step={from_step} 之前的历史节点步序不一致，"
            f"无法合并（source: {source_prefix_steps}，target: {target_prefix_steps}）"
        )
    for s_state, t_state in zip(source_prefix, target_prefix):
        if s_state.to_dict() != t_state.to_dict():
            raise BranchError(
                f"两条分支在第 {s_state.step} 步的历史内容不一致，无法合并"
                "（from_step 及之前必须完全一致，可能两条分支已经严重分歧）"
            )

    source_suffix = [s for s in source_history if s.step > from_step]
    merged_history = target_prefix + source_suffix

    atomic_write_jsonl(
        store.state_history_path(target), [s.to_dict() for s in merged_history]
    )

    try:
        from mini_agent.utils.atomic_write import atomic_write_json
    except ImportError:
        def atomic_write_json(path: Path, data, *, flock: bool = False) -> None:  # type: ignore[misc]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    last_state = merged_history[-1]
    atomic_write_json(store.state_current_path(target), last_state.to_dict())

    # 如果 target 恰好是当前活跃分支，manifest.current_step 需要跟着
    # 刷新，否则界面会继续显示合并前的旧步数（同 `switch_branch()`
    # "切换后镜像字段必须跟着更新"的一贯取舍；`pilot_mode`/`autopilot`
    # 是每条分支独立存储的配置，合并历史不影响这两个字段，不需要动）。
    manifest = store.load_manifest()
    if manifest.branch == target:
        manifest.current_step = last_state.step
        store.save_manifest(manifest)

    return last_state.step


@dataclasses.dataclass
class ExploreRouteResult:
    """`explore_branches()` 里单条候选路线的探索结果（第十二轮方案第 1
    节 Exploration Mode）。"""

    route_label: str
    """这条路线的展示用标签——取自 `choice_option_id` 对应候选选项的
    `label`，或 `custom_option["label"]`，供调用方（`app.py`）展示，
    不参与任何比较逻辑。"""

    ok: bool
    """这条路线是否探索成功（`fork_branch()` + `advance()` 都没有
    抛异常）。"""

    branch: Optional[str] = None
    """成功时，新生成的分支 id；失败时为 `None`（失败路线不留下半
    成品分支，见 `explore_branches()` docstring）。"""

    error: Optional[str] = None
    """失败时的原因说明；成功时为 `None`。"""


def explore_branches(
    cfg,
    workspace_root: Path,
    data_dir: Path,
    sim_id: str,
    *,
    from_step: int,
    routes: List[Dict[str, Any]],
    source_branch: str = "main",
) -> Dict[str, ExploreRouteResult]:
    """批量分支探索（第十二轮方案第 1 节，对应参考文档"决策的价值不是
    改变一个指标，而是选择一个未来世界"）。

    给定 `from_step`/`source_branch` 这一个共同的历史起点，和一组候选
    "路线"，对每条路线依次：`fork_branch(from_step=..., source_branch=
    ..., switch=False)` 开一条独立分支 → 临时把活跃分支切过去 →
    `engine.advance()` 推进一步（喂入这条路线对应的
    `choice_option_id`/`custom_option`）→ 切回原来的活跃分支。

    只做向前一步，不做多步递归自动探索——分支数量会随步数指数增长，
    超出这个函数要解决的问题范围；用户如果想让某条探索出来的分支
    继续往前跑，用已有的 `fast_forward()` 在那条分支上单独操作即可。

    每条路线的推进互相独立、互不影响：某条路线 `advance()` 失败
    （LLM 报错/校验失败）不影响其它路线的探索结果，也不留下一条
    半成品分支——失败时会把已经为这条路线创建的分支删除
    （`delete_branch()`）。

    这个函数是纯粹在已有 `fork_branch`/`advance`/`delete_branch`/
    `switch_branch` 之上的编排，不重复实现这几个函数已有的逻辑。

    Args:
        routes: 每一项是一条候选路线，形如
            `{"choice_option_id": "..."}` 或
            `{"custom_option": {"label": ..., "description": ...}}`——
            与 `engine.advance()` 的同名参数一一对应，两者互斥（同时
            给出时以 `custom_option` 为准，与 `advance()` 一致，不
            额外报错）；可选 `route_label` 覆盖展示用标签（不给的话
            从 `choice_option_id` 对应的当前候选选项标签或
            `custom_option["label"]` 推导）。
        from_step / source_branch: 所有路线共享的分叉起点。

    Returns:
        `{候选路线在 routes 里的序号（字符串形式）: ExploreRouteResult}`
        ——用序号而不是分支 id 做 key，因为失败路线没有分支 id；
        调用方（`app.py`）需要按 `routes` 原始顺序展示结果时，用这个
        序号即可。

    Raises:
        BranchError: `routes` 为空——给出明确提示而不是静默不做任何事。
    """
    if not routes:
        raise BranchError("候选路线为空，没有可探索的内容")

    from world_simulator.engine import SimEngineError, advance as engine_advance
    from world_simulator.engine.errors import SimAlreadyEndedError, SimPausedError

    store = SimStore.for_root(data_dir, sim_id)
    original_manifest = store.load_manifest()
    original_branch = original_manifest.branch

    # 先取一份 from_step 那一步的候选选项列表，用于给没有显式
    # route_label 的 choice_option_id 路线推导展示标签——只是展示
    # 用途，取不到（比如 from_step 对应节点没有 options）不影响探索
    # 本身，静默退化成用 choice_option_id 原样当标签。
    option_labels: Dict[str, str] = {}
    try:
        source_history = load_branch_timeline(data_dir, sim_id, source_branch)
        anchor = next((s for s in source_history if s.step == from_step), None)
        if anchor is not None:
            option_labels = {opt.id: opt.label for opt in (anchor.options or [])}
    except Exception:  # noqa: BLE001 — 标签推导失败不该拖垮整个探索
        option_labels = {}

    results: Dict[str, ExploreRouteResult] = {}
    for idx, route in enumerate(routes):
        key = str(idx)
        choice_option_id = route.get("choice_option_id")
        custom_option = route.get("custom_option")
        route_label = str(
            route.get("route_label")
            or (custom_option or {}).get("label")
            or option_labels.get(choice_option_id)
            or choice_option_id
            or f"路线 {idx + 1}"
        )

        new_branch: Optional[str] = None
        try:
            new_branch = fork_branch(
                data_dir, sim_id,
                from_step=from_step, source_branch=source_branch, switch=True,
            )
            engine_advance(
                cfg, workspace_root, data_dir, sim_id,
                choice_option_id=choice_option_id,
                custom_option=custom_option,
                chosen_by="user",
            )
        except (BranchError, SimEngineError, SimAlreadyEndedError, SimPausedError) as exc:
            if new_branch is not None:
                # 已经切到了这条失败路线的分支，先切回去，才能删除它
                # （`delete_branch()` 不允许删除当前活跃分支）。
                switch_branch(data_dir, sim_id, original_branch)
                delete_branch(data_dir, sim_id, new_branch)
            results[key] = ExploreRouteResult(route_label=route_label, ok=False, error=str(exc))
            continue
        except Exception as exc:  # noqa: BLE001 — 单条路线的未预期异常不该拖垮其它路线
            if new_branch is not None:
                switch_branch(data_dir, sim_id, original_branch)
                delete_branch(data_dir, sim_id, new_branch)
            results[key] = ExploreRouteResult(route_label=route_label, ok=False, error=str(exc))
            continue

        results[key] = ExploreRouteResult(route_label=route_label, ok=True, branch=new_branch)

    # 无论每条路线成功与否，探索结束后都切回探索开始前的活跃分支——
    # 探索是"生成多个可能世界供查看/对比"，不代表用户想切走当前
    # 正在看的分支（同 `autopilot.run_comparison_experiment()` 的
    # 既有取舍）。
    current_manifest = store.load_manifest()
    if current_manifest.branch != original_branch:
        switch_branch(data_dir, sim_id, original_branch)

    return results


def compare_timelines(
    data_dir: Path, entries: List[Tuple[str, str]]
) -> Dict[str, Any]:
    """并排对比若干条时间线（可以是同一实例的不同分支，也可以是不同
    实例），服务方案第 5 节页面 4"对比视图"（决策推演分析场景）。

    Args:
        entries: `(sim_id, branch)` 元组列表，通常是 2 条（也允许更多）。

    Returns: `{"label": [...每条时间线的 (SimManifest, List[SimState])]}`
        形式的简单结构，不做字段对齐——不同实例/分支的 `vars` schema
        可能完全不同（甚至来自不同模板），对齐/差异高亮留给展示层
        （`app.py`）按需处理，本函数只负责把数据取齐。
    """
    result = []
    for sim_id, branch in entries:
        store = SimStore.for_root(data_dir, sim_id)
        manifest = store.load_manifest()
        history = store.load_history(branch)
        result.append({"sim_id": sim_id, "branch": branch, "manifest": manifest, "history": history})
    return {"lines": result}
