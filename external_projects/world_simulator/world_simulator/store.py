"""world_simulator/store.py — 模拟实例的状态持久化

设计依据：`world_simulator_external_project_plan.md` 2.6 节 + 第 6 节。

落盘用 `mini_agent.utils.atomic_write` 的既有原子写工具（tmp + rename），
不重新发明原子写；没有 mini_agent 环境时退化为普通写文件（不追求原子性，
换来"这个包能在没装 mini_agent 的环境下独立跑"，与 `entrypoints/_common.py`
的降级约定一致）。

目录约定（对应方案第 3 节）：
    data/<sim_id>/
        manifest.json          # SimManifest
        state_current.json     # 当前分支（manifest.branch）的最新 SimState
        state_history.jsonl     # 当前分支的历史（按 step 顺序追加）
        branches/<branch_id>/
            state_current.json
            state_history.jsonl

阶段一只实现 `main` 分支的读写；`branches/` 目录结构已经按方案预留，
`branch_manager.py`（阶段三）实现时不需要改这里的路径约定。
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from world_simulator.state_model import SimManifest, SimState

try:
    from mini_agent.utils.atomic_write import atomic_write_json, atomic_write_jsonl
except ImportError:  # 独立运行、未装 mini_agent 时的降级实现
    def atomic_write_json(path: Path, data, *, flock: bool = False) -> None:  # type: ignore[misc]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def atomic_write_jsonl(path: Path, records: list) -> None:  # type: ignore[misc]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + ("\n" if records else ""),
            encoding="utf-8",
        )


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class SimNotFoundError(RuntimeError):
    pass


@dataclass
class SimStore:
    """单个模拟实例的存储句柄，围绕 `data/<sim_id>/` 展开。"""

    data_dir: Path
    sim_id: str

    @classmethod
    def for_root(cls, data_dir: Path, sim_id: str) -> "SimStore":
        return cls(data_dir=Path(data_dir), sim_id=sim_id)

    # ── 路径 ──────────────────────────────────────────────────────────

    @property
    def sim_dir(self) -> Path:
        return self.data_dir / self.sim_id

    @property
    def manifest_path(self) -> Path:
        return self.sim_dir / "manifest.json"

    def branch_dir(self, branch: str) -> Path:
        if branch == "main":
            return self.sim_dir
        return self.sim_dir / "branches" / branch

    def state_current_path(self, branch: str = "main") -> Path:
        return self.branch_dir(branch) / "state_current.json"

    def state_history_path(self, branch: str = "main") -> Path:
        return self.branch_dir(branch) / "state_history.jsonl"

    def pilot_config_path(self, branch: str = "main") -> Path:
        return self.branch_dir(branch) / "pilot_config.json"

    # ── manifest ─────────────────────────────────────────────────────

    def exists(self) -> bool:
        return self.manifest_path.exists()

    def load_manifest(self) -> SimManifest:
        if not self.manifest_path.exists():
            raise SimNotFoundError(f"模拟实例不存在：{self.sim_id}")
        data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        return SimManifest.from_dict(data)

    def save_manifest(self, manifest: SimManifest) -> None:
        manifest.updated_at = now_iso()
        atomic_write_json(self.manifest_path, manifest.to_dict())

    # ── state ────────────────────────────────────────────────────────

    def load_current_state(self, branch: str = "main") -> Optional[SimState]:
        path = self.state_current_path(branch)
        if not path.exists():
            return None
        return SimState.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def load_history(self, branch: str = "main") -> List[SimState]:
        path = self.state_history_path(branch)
        if not path.exists():
            return []
        states = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            states.append(SimState.from_dict(json.loads(line)))
        return states

    def append_state(self, state: SimState, branch: str = "main") -> None:
        """把一个新状态追加进历史，并同步为"当前状态"。

        `state_history.jsonl` 用 `atomic_write_jsonl` 整体重写（先读出
        已有历史、追加新条目、整体落盘），不是真正的"追加写"——阶段一
        实例数据量小（一次模拟通常几十到几百步），整体重写的开销可以
        接受，换来"不需要单独处理 jsonl 追加写的原子性"这个简化；如果
        后续实例历史变得很长，可以在不改变本方法签名的前提下换成真正
        的追加写（`state_history_path` 打开文件 `a` 模式），调用方不受
        影响。
        """
        history = self.load_history(branch)
        history.append(state)
        atomic_write_jsonl(
            self.state_history_path(branch),
            [s.to_dict() for s in history],
        )
        atomic_write_json(self.state_current_path(branch), state.to_dict())

    # ── 自动挡配置（按分支独立存储）──────────────────────────────────

    def load_pilot_config(self, branch: str = "main") -> Dict[str, Any]:
        """读取某条分支自己的推进模式/自动挡配置。

        每条分支的 `pilot_config.json` 落在各自的分支目录下（`main` 就是
        `sim_dir/pilot_config.json`），与 `state_current.json`/
        `state_history.jsonl` 同级——分支既然已经是"独立目录"，配置跟着
        放在一起最自然，也顺带保证"删分支目录"（`delete_branch_dir`）
        天然把该分支的自动挡配置一并删掉，不需要额外清理。

        兼容旧数据：早期版本把 `pilot_mode`/`autopilot` 直接存在
        `manifest.json` 顶层（全实例共用一份，不区分分支）。`main`
        分支如果还没有独立的 `pilot_config.json`，就退回读 manifest
        顶层字段，读到什么用什么；其余分支（后引入功能之后才可能
        存在）没有独立文件时直接给默认值，不存在"退回 manifest"这一说，
        因为它们创建时（`branch_manager.fork_branch`）就必然会写好自己
        的 `pilot_config.json`。
        """
        path = self.pilot_config_path(branch)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return {
                "pilot_mode": str(data.get("pilot_mode", "manual")),
                "autopilot": dict(data.get("autopilot") or {}),
            }
        if branch == "main":
            try:
                manifest = self.load_manifest()
            except SimNotFoundError:
                pass
            else:
                return {
                    "pilot_mode": manifest.pilot_mode,
                    "autopilot": dict(manifest.autopilot or {}),
                }
        return {"pilot_mode": "manual", "autopilot": {}}

    def save_pilot_config(
        self, branch: str, pilot_mode: str, autopilot: Optional[Dict[str, Any]] = None
    ) -> None:
        """把某条分支自己的推进模式/自动挡配置写盘，只影响这一条分支。"""
        atomic_write_json(
            self.pilot_config_path(branch),
            {"pilot_mode": pilot_mode, "autopilot": dict(autopilot or {})},
        )

    def delete_branch_dir(self, branch: str) -> None:
        """删除某条分支在磁盘上的目录（`branches/<branch>/`）。

        只负责物理删除 `branch_dir(branch)` 这一层目录；不校验
        `branch != "main"`、不校验"是不是当前活跃分支"——这些业务规则
        由调用方（`branch_manager.delete_branch`）负责，这里保持纯粹
        的"删除一个分支目录"语义，方便单测。`main` 分支的 `branch_dir`
        就是 `sim_dir` 本身，调用方必须自行拦截，否则会删掉整个实例。
        """
        path = self.branch_dir(branch)
        if path.exists():
            shutil.rmtree(path)


def list_sim_ids(data_dir: Path) -> List[str]:
    """列出 `data_dir` 下所有已存在的模拟实例 id（有 manifest.json 的目录）。"""
    data_dir = Path(data_dir)
    if not data_dir.exists():
        return []
    ids = []
    for child in sorted(data_dir.iterdir()):
        if child.is_dir() and (child / "manifest.json").exists():
            ids.append(child.name)
    return ids
