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

import json
import secrets
import string
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from world_simulator.state_model import SimManifest, SimState
from world_simulator.store import SimNotFoundError, SimStore

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
    """
    store = SimStore.for_root(data_dir, sim_id)
    manifest = store.load_manifest()

    source_history = store.load_history(source_branch)
    if not source_history:
        raise BranchError(f"分支 {source_branch!r} 没有历史，无法分叉")
    cutoff = [s for s in source_history if s.step <= from_step]
    if not cutoff:
        raise BranchError(f"分支 {source_branch!r} 没有 step <= {from_step} 的历史节点")

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

    if switch:
        manifest.branch = new_branch
        manifest.current_step = cutoff[-1].step
        store.save_manifest(manifest)

    return new_branch


def switch_branch(data_dir: Path, sim_id: str, branch_id: str) -> SimManifest:
    """把实例的"当前活跃分支"切到 `branch_id`（必须已存在）。"""
    if branch_id not in list_branches(data_dir, sim_id):
        raise BranchError(f"分支不存在：{branch_id}")
    store = SimStore.for_root(data_dir, sim_id)
    manifest = store.load_manifest()
    current = store.load_current_state(branch_id)
    if current is None:
        raise BranchError(f"分支 {branch_id!r} 缺少当前状态，数据可能已损坏")
    manifest.branch = branch_id
    manifest.current_step = current.step
    store.save_manifest(manifest)
    return manifest


def load_branch_timeline(data_dir: Path, sim_id: str, branch: str) -> List[SimState]:
    """读取某条分支的完整历史，供对比视图使用。"""
    store = SimStore.for_root(data_dir, sim_id)
    return store.load_history(branch)


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
