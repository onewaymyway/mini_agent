"""core/experience_store.py — Experience 的持久化实现（SQLite）。

Sprint 2（`02-executable-sprint-plan.md`）阶段选的是 JSONL，理由是
"量级低、不需要索引/事务，SQLite 是过度设计"。Phase 3 Sprint 3-1
（`04-phase3-experience-layer-sprint-plan.md`）明确要求升级到 SQLite——
原因是 Sprint 3-2 即将在这个 store 之上加检索（`experience/retrieval.py`）
和聚合统计（`experience/patterns.py`），这两者都需要按字段过滤/排序，
继续用"整份读进内存再 Python 过滤"的 JSONL 方式会随着记录量增长而失效，
不是提前引入复杂度，是止损原则里"若检索量增长到需要索引，再按需升级"
这句话里说的"需要"已经出现。

对外 API（`append`/`all`/`search`）与构造函数签名（`path` 可选）保持不变，
`goal_mode/runner.py`、`cli/commands/experience_cmd.py`、既有测试
（`tests/test_core_experience_store.py`）不需要跟着改一行——它们只通过
这三个方法与 store 交互，不关心底层文件格式。

存储位置：`AgentPaths.workdir_experience_store`
（`<project_root>/.agent/experience_store.db`，Sprint 3-1 起从 `.jsonl`
改名为 `.db`，避免和 Sprint 2 遗留的旧 JSONL 文件混淆；旧文件里的历史
数据用一次性脚本 `scripts/migrate_experience_jsonl_to_sqlite.py` 迁移
进新 store，见该脚本文档）。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from .experience import Experience

# dict 类型字段用 JSON 文本存储（sqlite3 没有原生 dict 列类型）。
_DICT_FIELDS = ("context", "state_before", "state_after", "evidence")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiences (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    goal_text TEXT NOT NULL,
    status TEXT NOT NULL,
    rounds_used INTEGER NOT NULL,
    final_report TEXT NOT NULL,
    created_at REAL NOT NULL,
    context TEXT NOT NULL,
    state_before TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    prediction TEXT NOT NULL,
    state_after TEXT NOT NULL,
    evidence TEXT NOT NULL,
    lesson TEXT NOT NULL,
    causal_hypothesis TEXT NOT NULL,
    confidence REAL
);
"""


class ExperienceStore:
    """Experience 的持久化 + 检索实现（SQLite，单表，append-only 语义）。"""

    def __init__(self, path: Optional[Path] = None) -> None:
        if path is None:
            from mini_agent.storage.paths import AgentPaths

            path = AgentPaths().workdir_experience_store
        self.path = Path(path)
        self._write_lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path))
        conn.execute(_SCHEMA)
        return conn

    def append(self, experience: Experience) -> None:
        """把一条 Experience 写入存储（按 `id` upsert，正常场景下 `id`
        全局唯一因此等价于纯 append；`INSERT OR REPLACE` 只是为迁移脚本
        重复执行时提供幂等性，不改变 Sprint 2 定下的"只在轮次/执行结束
        这种低频时机调用"写入模式）。
        """
        d = experience.to_dict()
        row = (
            d["id"],
            d["source"],
            d["goal_text"],
            d["status"],
            int(d["rounds_used"]),
            d["final_report"],
            float(d["created_at"]),
            json.dumps(d["context"], ensure_ascii=False),
            json.dumps(d["state_before"], ensure_ascii=False),
            d["action"],
            d["reason"],
            d["prediction"],
            json.dumps(d["state_after"], ensure_ascii=False),
            json.dumps(d["evidence"], ensure_ascii=False),
            d["lesson"],
            d["causal_hypothesis"],
            d["confidence"],
        )
        with self._write_lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO experiences (
                        id, source, goal_text, status, rounds_used, final_report,
                        created_at, context, state_before, action, reason,
                        prediction, state_after, evidence, lesson,
                        causal_hypothesis, confidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )
                conn.commit()
            finally:
                conn.close()

    def _row_to_experience(self, row: tuple) -> Experience:
        (
            id_, source, goal_text, status, rounds_used, final_report,
            created_at, context, state_before, action, reason,
            prediction, state_after, evidence, lesson,
            causal_hypothesis, confidence,
        ) = row
        return Experience(
            id=id_,
            source=source,
            goal_text=goal_text,
            status=status,
            rounds_used=int(rounds_used),
            final_report=final_report,
            created_at=float(created_at),
            context=json.loads(context) if context else {},
            state_before=json.loads(state_before) if state_before else {},
            action=action or "",
            reason=reason or "",
            prediction=prediction or "",
            state_after=json.loads(state_after) if state_after else {},
            evidence=json.loads(evidence) if evidence else {},
            lesson=lesson or "",
            causal_hypothesis=causal_hypothesis or "",
            confidence=confidence,
        )

    def all(self) -> list[Experience]:
        """读出全部已持久化的 Experience，按写入顺序（旧→新，即
        `created_at` 升序，与 Sprint 2 JSONL 版本的"文件行序=写入顺序"
        行为一致）。
        """
        if not self.path.exists():
            return []
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT id, source, goal_text, status, rounds_used, final_report,
                       created_at, context, state_before, action, reason,
                       prediction, state_after, evidence, lesson,
                       causal_hypothesis, confidence
                FROM experiences ORDER BY created_at ASC, id ASC
                """
            ).fetchall()
        finally:
            conn.close()
        return [self._row_to_experience(r) for r in rows]

    def search(self, query: str, limit: int = 10) -> list[Experience]:
        """按关键词在 `goal_text` / `final_report` 里做子串匹配检索
        （沿用 Sprint 2 的大小写不敏感子串匹配语义，不引入分词/embedding；
        那属于 Sprint 3-2 `experience/retrieval.py` 的范围）。最近的记录
        优先返回。
        """
        q = query.strip().lower()
        if not q:
            return []
        matched = [
            exp
            for exp in reversed(self.all())
            if q in exp.goal_text.lower() or q in exp.final_report.lower()
        ]
        return matched[:limit]
