"""core/experience_store.py — Experience 的最小持久化实现。

见 `next_doc/refactor_plan/02-executable-sprint-plan.md` Sprint 2：
"`experience/store.py` 最小实现：用 JSON 或 SQLite 持久化 Sprint 1
产生的 `Experience` 对象"。

选择 JSONL（append-only，每行一条 JSON），不是 SQLite：
- 与仓库里 `workdir_memory`（`.agent/memory.jsonl`）同样的落盘方式保持
  一致，不引入新的存储技术栈；
- Experience 记录量级（每次 Goal 执行结束才写一条）远低于需要索引/
  事务的场景，SQLite 在这里是过度设计。
- 若未来检索量增长到需要索引，再按需升级，不提前引入复杂度
  （呼应止损原则"不做 Big Bang Rewrite"）。

存储位置：`AgentPaths.workdir_experience_store`
（`<project_root>/.agent/experience_store.jsonl`）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .experience import Experience


class ExperienceStore:
    """Experience 的最小持久化 + 检索实现（JSONL，append-only）。"""

    def __init__(self, path: Optional[Path] = None) -> None:
        if path is None:
            from mini_agent.storage.paths import AgentPaths

            path = AgentPaths().workdir_experience_store
        self.path = Path(path)

    def append(self, experience: Experience) -> None:
        """把一条 Experience 追加写入存储文件。

        只在轮次/执行结束这种低频时机调用（对齐 `goal_mode/state.py`
        "只在轮次边界写，不在轮次内部频繁写"的落盘原则），不需要加锁/
        批量优化。
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(experience.to_dict(), ensure_ascii=False) + "\n")

    def all(self) -> list[Experience]:
        """读出全部已持久化的 Experience，按写入顺序（旧→新）。"""
        if not self.path.exists():
            return []
        results: list[Experience] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    # 与 memory.jsonl 的既有容错策略一致：单行损坏不影响
                    # 其它记录的读取，跳过即可，不整体失败。
                    continue
                results.append(
                    Experience(
                        source=d.get("source", "goal_mode"),
                        goal_text=d.get("goal_text", ""),
                        status=d.get("status", ""),
                        rounds_used=int(d.get("rounds_used", 0)),
                        final_report=d.get("final_report", ""),
                        created_at=float(d.get("created_at", 0.0)),
                    )
                )
        return results

    def search(self, query: str, limit: int = 10) -> list[Experience]:
        """按关键词在 `goal_text` / `final_report` 里做子串匹配检索。

        Sprint 2 范围内只做最简单的大小写不敏感子串匹配，不引入分词/
        embedding 检索（那属于未来 Memory 迁移链或检索能力增强的范围，
        不是"Goal 第二次遇到类似目标时能看到上次做过什么"这个 quick win
        本身需要的复杂度）。最近的记录优先返回。
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
