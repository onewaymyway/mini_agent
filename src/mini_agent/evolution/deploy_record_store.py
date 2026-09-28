"""evolution/deploy_record_store.py — `DeployRecord` 持久化（Phase 9 Sprint 9-4）

见 `next_doc/refactor_plan/10-phase9-self-evolution-sprint-plan.md`
Sprint 9-4。Sprint 9-3 的已知局限 §2：`DeployRecord` 只有 `to_dict()/
from_dict()`，没有落盘，而 Observe 通常发生在部署之后若干次运行之后（可能
跨进程），所以闭环只能被显式调用驱动。本模块补上这一环。

存储格式：append-only JSONL（`AgentPaths.workdir_deploy_records`）。
每次 `save()` 追加一行快照，同一 `proposal_id` 以**最后一行**为准。选择
追加日志而不是“读-改-写整个文件”，理由：
  - 状态迁移（observing → promoted / rolled_back / rollback_failed）本身
    就是审计信息，覆盖写会丢掉“它曾经处于什么状态”；
  - 追加单行在崩溃时最多损坏最后一行，读取时跳过坏行即可，不会让整个
    文件不可读；
  - 与 `events.jsonl` 同一风格。

这是运行时状态，**不经过** `StateRepo.apply()`，也不进 git（见
`.gitignore`）。真正改变 Agent 行为的文件仍只能经 `StateRepo` 落地。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional

from mini_agent.evolution.deployment import STATE_OBSERVING, DeployRecord


class DeployRecordStore:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def save(self, record: DeployRecord) -> None:
        """追加一条快照。父目录不存在时创建。"""
        line = json.dumps(
            {**record.to_dict(), "saved_at": time.time()},
            ensure_ascii=False,
        )
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # 上一次写入若在中途崩溃，末行可能缺少换行符；不先补上的话，新记录
            # 会被接在残缺行后面，一起变成无法解析的一行（测试
            # `test_store_skips_corrupt_and_truncated_lines` 首次运行即复现）。
            prefix = "\n" if self._ends_without_newline() else ""
            with self._path.open("a", encoding="utf-8") as f:
                f.write(prefix + line + "\n")

    def _ends_without_newline(self) -> bool:
        try:
            with self._path.open("rb") as f:
                f.seek(0, 2)
                if f.tell() == 0:
                    return False
                f.seek(-1, 2)
                return f.read(1) != b"\n"
        except FileNotFoundError:
            return False

    def _latest(self) -> dict[str, DeployRecord]:
        """按 proposal_id 取最后一条快照；无法解析的行（含被截断的末行）跳过。"""
        if not self._path.exists():
            return {}
        latest: dict[str, DeployRecord] = {}
        with self._lock:
            text = self._path.read_text(encoding="utf-8", errors="replace")
        for raw in text.splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                d = json.loads(raw)
                if not isinstance(d, dict) or "proposal_id" not in d:
                    continue
                latest[d["proposal_id"]] = DeployRecord.from_dict(d)
            except (ValueError, TypeError):
                continue
        return latest

    def get(self, proposal_id: str) -> Optional[DeployRecord]:
        return self._latest().get(proposal_id)

    def all(self) -> list[DeployRecord]:
        """每个 proposal 的最新记录，按部署时间升序。"""
        return sorted(self._latest().values(), key=lambda r: r.deployed_at)

    def list_observing(self) -> list[DeployRecord]:
        return [r for r in self.all() if r.state == STATE_OBSERVING]


__all__ = ["DeployRecordStore"]
