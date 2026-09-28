"""scripts/import_lessons_to_experience.py — 把历史 lesson 记忆导入 Experience store。

见 `next_doc/refactor_plan/04-phase3-experience-layer-sprint-plan.md` “完成标志”第 2 条。
手动、幂等、可重复执行；默认 `--dry-run` 只统计不写入，加 `--apply` 才真正写入。

用法：
    python scripts/import_lessons_to_experience.py [project_root]            # 只统计
    python scripts/import_lessons_to_experience.py [project_root] --apply    # 写入

导入的记录 `source="memory_lesson"`、`status="lesson"`。若同时开启了
`goal_mode.experience_retrieval_enabled`，这些 lesson 会作为“相似历史”参与
Goal 规划阶段的检索注入——这是导入后才会出现的行为变化，不导入则完全不变。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mini_agent.core.experience_store import ExperienceStore  # noqa: E402
from mini_agent.core.lesson_import import import_lessons  # noqa: E402
from mini_agent.storage.paths import AgentPaths  # noqa: E402


def run(project_root: Path, apply: bool) -> int:
    from mini_agent.config.loader import load_config
    from mini_agent.perception.memory_factory import create_memory_backend

    cfg = load_config(project_root=project_root)
    backend = create_memory_backend(cfg)
    entries = backend.all_entries()

    if not apply:
        lessons = [e for e in entries if getattr(e, "entry_type", "") == "lesson"]
        print(f"[dry-run] 共 {len(entries)} 条记忆，其中 lesson {len(lessons)} 条，将被导入。加 --apply 才写入。")
        return 0

    store = ExperienceStore(path=AgentPaths(project_root=project_root).workdir_experience_store)
    r = import_lessons(entries, store)
    print(f"导入完成：扫描 {r.scanned}，导入 {r.imported}，跳过（非 lesson）{r.skipped}，失败 {r.failed}。")
    print(f"目标 store：{store.path}")
    return 1 if r.failed else 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    root = Path(args[0]) if args else Path.cwd()
    raise SystemExit(run(root, apply="--apply" in sys.argv))
