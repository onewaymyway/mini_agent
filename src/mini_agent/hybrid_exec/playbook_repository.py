"""
hybrid_exec/playbook_repository.py — PlaybookRepository：skill 档 playbook
（步骤说明文档）的版本存储 + 成功率统计。

对应 next_doc/generative_capability_raw_result_and_hybrid_merge_plan.md 第3节，
用户已确认的开放问题决策：playbook 不复用 ScriptRepository 的
`<task_id>/v{n}.py` 目录布局，单独设计一套版本化目录，便于区分"这是一份
可执行代码"还是"这是一份给 Agent 参照执行的步骤说明"两种截然不同的产物。

存储布局（默认位于 <project_root>/.agent/hybrid_exec/playbooks/<task_id>/）：
    meta.json     # 当前 active 版本号 + 各版本统计
    v1.md
    v2.md
    ...

设计上刻意与 ScriptRepository 保持同构（同样的 meta.json 字段名、同样的
save/record_success/record_failure/retire/list_versions 接口），原因：
  - HybridExecutor 未来把 SKILL 档接入主循环时，可以对 SCRIPT/SKILL 两档
    复用同一套"记录尝试结果、连续失败退役"的调用代码，只是传入的 repository
    实例不同、文件后缀不同——不需要写两套不同形状的调用逻辑。
  - 版本记录字段含义完全对应：SCRIPT 档的 version 是一份 `run(input)->dict`
    源码，SKILL 档的 version 是一份 playbook 说明文本，两者都可以"新版本
    产出后转正、旧版本 superseded、连续失败退役"，统计口径不需要区分对待。

与 ScriptRepository 的差异只在于：
  - 落盘目录不同（`playbooks/` vs `scripts/`），互不干扰、互不共享 task_id
    命名空间的 meta.json（同一个 task_id 完全可以既有 script 版本历史，
    也有 playbook 版本历史，两边各自独立记录，由调用方决定当前实际用哪一档）。
  - 文件后缀是 `.md` 而不是 `.py`（playbook 是给 Agent 读的说明文档，不是
    可执行代码，不应该被当作 Python 模块 import）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class PlaybookRecord:
    version: int
    created_at: str
    created_by: str  # agent_explorer / agent_repairer / manual
    status: str = "active"  # active / superseded / retired
    success_count: int = 0
    fail_count: int = 0
    consecutive_fail: int = 0
    last_error: Optional[str] = None
    # [next_doc/generative_capability_three_tier_improvement_plan.md 阶段二新增]
    # 上一次"尝试把该 playbook 升级蒸馏为 script.py"失败的时间（ISO
    # 字符串），成功升级后不再需要读它（member 已经有 script.py，不会再
    # 触发升级检查）。与 success_count/fail_count 等 playbook 自身的执行
    # 成败统计完全独立——升级尝试的成败不影响 playbook 本身是否可靠。
    last_upgrade_attempt_at: Optional[str] = None

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
            "last_upgrade_attempt_at": self.last_upgrade_attempt_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PlaybookRecord":
        return cls(
            version=d["version"],
            created_at=d.get("created_at", ""),
            created_by=d.get("created_by", "unknown"),
            status=d.get("status", "active"),
            success_count=d.get("success_count", 0),
            fail_count=d.get("fail_count", 0),
            consecutive_fail=d.get("consecutive_fail", 0),
            last_error=d.get("last_error"),
            last_upgrade_attempt_at=d.get("last_upgrade_attempt_at"),
        )


class PlaybookRepository:
    """按 task_id 归档 playbook（步骤说明文档）版本。接口形状与
    hybrid_exec.repository.ScriptRepository 同构，详见文件头说明。"""

    def __init__(self, base_dir: Path, *, retire_after_consecutive_fail: int = 3) -> None:
        self.base_dir = Path(base_dir)
        self.retire_after_consecutive_fail = retire_after_consecutive_fail

    # -- 内部路径/元信息辅助 --------------------------------------------

    def _task_dir(self, task_id: str) -> Path:
        return self.base_dir / task_id

    def _meta_path(self, task_id: str) -> Path:
        return self._task_dir(task_id) / "meta.json"

    def _playbook_path(self, task_id: str, version: int) -> Path:
        return self._task_dir(task_id) / f"v{version}.md"

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

    def get_active_playbook(self, task_id: str) -> Optional[PlaybookRecord]:
        meta = self._load_meta(task_id)
        active_version = meta.get("active_version")
        if active_version is None:
            return None
        rec = meta.get("versions", {}).get(str(active_version))
        if rec is None:
            return None
        return PlaybookRecord.from_dict(rec)

    def get_playbook_path(self, task_id: str, version: int) -> Path:
        return self._playbook_path(task_id, version)

    def load_content(self, task_id: str, version: int) -> str:
        return self._playbook_path(task_id, version).read_text(encoding="utf-8")

    def _next_version(self, meta: dict) -> int:
        versions = meta.get("versions", {})
        if not versions:
            return 1
        return max(int(v) for v in versions.keys()) + 1

    def save_new_version(self, task_id: str, content: str, created_by: str) -> PlaybookRecord:
        """写入一份新 playbook 版本并将其设为 active（用于探索阶段整理出的
        步骤说明，或修复阶段产出的修订版）。旧的 active 版本状态改为
        superseded，但文件与统计历史保留，不删除。"""
        meta = self._load_meta(task_id)
        old_active = meta.get("active_version")
        if old_active is not None:
            old_rec = meta.get("versions", {}).get(str(old_active))
            if old_rec is not None and old_rec.get("status") == "active":
                old_rec["status"] = "superseded"

        version = self._next_version(meta)
        rec = PlaybookRecord(version=version, created_at=self._now_iso(), created_by=created_by)
        meta.setdefault("versions", {})[str(version)] = rec.to_dict()
        meta["active_version"] = version
        self._save_meta(task_id, meta)

        self._task_dir(task_id).mkdir(parents=True, exist_ok=True)
        self._playbook_path(task_id, version).write_text(content, encoding="utf-8")
        return rec

    def save_revised_version(self, task_id: str, content: str, created_by: str) -> PlaybookRecord:
        """修复/修订产出的新版本，语义与 save_new_version 一致，保留独立
        方法名是为了在调用处/日志里语义更清晰（对应 ScriptRepository 的
        save_repaired_version）。"""
        return self.save_new_version(task_id, content, created_by)

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

    def record_upgrade_attempt(self, task_id: str, version: int) -> None:
        """[next_doc/generative_capability_three_tier_improvement_plan.md
        阶段二新增] 记录一次"尝试把该 playbook 升级蒸馏为 script.py"失败，
        只写 `last_upgrade_attempt_at`，不触碰 success_count/fail_count/
        consecutive_fail——升级尝试的成败与 playbook 本身作为 SKILL 档
        手段是否可靠，是两件独立的事，不共用同一套统计。调用方（
        `capability_engine._maybe_upgrade_skill_to_script`）据此实现一个
        简单的冷却期节流，避免升级持续失败时每次成功执行都重新触发 LLM
        调用。"""
        meta = self._load_meta(task_id)
        rec = meta.get("versions", {}).get(str(version))
        if rec is None:
            return
        rec["last_upgrade_attempt_at"] = self._now_iso()
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

    def list_versions(self, task_id: str) -> "list[PlaybookRecord]":
        meta = self._load_meta(task_id)
        return [
            PlaybookRecord.from_dict(v)
            for v in sorted(meta.get("versions", {}).values(), key=lambda d: d["version"])
        ]
