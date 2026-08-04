"""
hybrid_exec/repository.py — ScriptRepository：脚本版本存储 + 成功率统计

对应 next_doc/hybrid_exec_design_plan.md §3.3 / §6。

存储布局（默认位于 <project_root>/.agent/hybrid_exec/scripts/<task_id>/）：
    meta.json     # 当前 active 版本号 + 各版本统计
    v1.py
    v2.py
    ...

设计取舍：
  - 不引入 git/StateRepo 那套风险分级与 worktree 隔离——hybrid_exec 管理的
    脚本不是项目核心代码，只是"可复用的执行手段"，用简单的目录+JSON 记录
    版本历史即可，过度设计反而拖慢 MVP。
  - retire 阈值（连续失败几次后退役）作为构造参数传入，不写死，方便测试和
    后续按 task 类型调参。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ScriptRecord:
    version: int
    created_at: str
    created_by: str  # llm_explorer / agent_explorer / llm_repairer / agent_repairer / manual
    status: str = "active"  # active / superseded / retired
    success_count: int = 0
    fail_count: int = 0
    consecutive_fail: int = 0
    last_error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "status": self.status,
            "success_count": self.success_count,
            "fail_count": self.fail_count,
            "consecutive_fail": self.consecutive_fail,
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ScriptRecord":
        return cls(
            version=d["version"],
            created_at=d.get("created_at", ""),
            created_by=d.get("created_by", "unknown"),
            status=d.get("status", "active"),
            success_count=d.get("success_count", 0),
            fail_count=d.get("fail_count", 0),
            consecutive_fail=d.get("consecutive_fail", 0),
            last_error=d.get("last_error"),
        )


class ScriptRepository:
    """按 task_id 归档脚本版本。一个 task_id 同一时刻只有一个 active 版本
    （§9 已确认，MVP 不做输入结构指纹分支）。"""

    def __init__(self, base_dir: Path, *, retire_after_consecutive_fail: int = 3) -> None:
        self.base_dir = Path(base_dir)
        self.retire_after_consecutive_fail = retire_after_consecutive_fail

    # -- 内部路径/元信息辅助 --------------------------------------------

    def _task_dir(self, task_id: str) -> Path:
        return self.base_dir / task_id

    def _meta_path(self, task_id: str) -> Path:
        return self._task_dir(task_id) / "meta.json"

    def _script_path(self, task_id: str, version: int) -> Path:
        return self._task_dir(task_id) / f"v{version}.py"

    def _load_meta(self, task_id: str) -> dict:
        meta_path = self._meta_path(task_id)
        if not meta_path.exists():
            return {"active_version": None, "versions": {}}
        return json.loads(meta_path.read_text(encoding="utf-8"))

    def _save_meta(self, task_id: str, meta: dict) -> None:
        self._task_dir(task_id).mkdir(parents=True, exist_ok=True)
        self._meta_path(task_id).write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _now_iso() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()

    # -- 对外接口 ---------------------------------------------------------

    def get_active_script(self, task_id: str) -> Optional[ScriptRecord]:
        meta = self._load_meta(task_id)
        active_version = meta.get("active_version")
        if active_version is None:
            return None
        rec = meta.get("versions", {}).get(str(active_version))
        if rec is None:
            return None
        return ScriptRecord.from_dict(rec)

    def get_script_path(self, task_id: str, version: int) -> Path:
        return self._script_path(task_id, version)

    def load_code(self, task_id: str, version: int) -> str:
        return self._script_path(task_id, version).read_text(encoding="utf-8")

    def _next_version(self, meta: dict) -> int:
        versions = meta.get("versions", {})
        if not versions:
            return 1
        return max(int(v) for v in versions.keys()) + 1

    def save_new_version(self, task_id: str, code: str, created_by: str) -> ScriptRecord:
        """写入一个新脚本版本并将其设为 active（用于探索阶段产出首个版本，
        或修复阶段产出的新版本）。旧的 active 版本状态改为 superseded，
        但版本文件与统计历史保留，不删除。"""
        meta = self._load_meta(task_id)
        old_active = meta.get("active_version")
        if old_active is not None:
            old_rec = meta.get("versions", {}).get(str(old_active))
            if old_rec is not None and old_rec.get("status") == "active":
                old_rec["status"] = "superseded"

        version = self._next_version(meta)
        rec = ScriptRecord(version=version, created_at=self._now_iso(), created_by=created_by)
        meta.setdefault("versions", {})[str(version)] = rec.to_dict()
        meta["active_version"] = version
        self._save_meta(task_id, meta)

        self._task_dir(task_id).mkdir(parents=True, exist_ok=True)
        self._script_path(task_id, version).write_text(code, encoding="utf-8")
        return rec

    # 修复产出的新版本，语义上和 save_new_version 一致（都是"新增一个版本
    # 并转正"），保留独立方法名是为了在调用处/日志里语义更清晰。
    def save_repaired_version(self, task_id: str, code: str, created_by: str) -> ScriptRecord:
        return self.save_new_version(task_id, code, created_by)

    def record_success(self, task_id: str, version: int) -> None:
        meta = self._load_meta(task_id)
        rec = meta.get("versions", {}).get(str(version))
        if rec is None:
            return
        rec["success_count"] = rec.get("success_count", 0) + 1
        rec["consecutive_fail"] = 0
        self._save_meta(task_id, meta)

    def record_failure(self, task_id: str, version: int, error: str) -> None:
        meta = self._load_meta(task_id)
        rec = meta.get("versions", {}).get(str(version))
        if rec is None:
            return
        rec["fail_count"] = rec.get("fail_count", 0) + 1
        rec["consecutive_fail"] = rec.get("consecutive_fail", 0) + 1
        rec["last_error"] = error
        if rec["consecutive_fail"] >= self.retire_after_consecutive_fail:
            rec["status"] = "retired"
            if meta.get("active_version") == version:
                meta["active_version"] = None
        self._save_meta(task_id, meta)

    def retire(self, task_id: str, version: int, reason: str) -> None:
        meta = self._load_meta(task_id)
        rec = meta.get("versions", {}).get(str(version))
        if rec is None:
            return
        rec["status"] = "retired"
        rec["last_error"] = reason
        if meta.get("active_version") == version:
            meta["active_version"] = None
        self._save_meta(task_id, meta)

    def list_versions(self, task_id: str) -> "list[ScriptRecord]":
        meta = self._load_meta(task_id)
        return [
            ScriptRecord.from_dict(v)
            for v in sorted(meta.get("versions", {}).values(), key=lambda d: d["version"])
        ]
