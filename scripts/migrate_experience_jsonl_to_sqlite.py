"""scripts/migrate_experience_jsonl_to_sqlite.py — 一次性迁移脚本。

见 `next_doc/refactor_plan/04-phase3-experience-layer-sprint-plan.md`
Sprint 3-1：“把 Sprint 2 阶段已经产生的 Experience 数据迁移进新
store（一次性脚本，跑完即弃）”。

背景：`core/experience_store.py` 从 Sprint 2 的 JSONL 升级为 Sprint 3-1
的 SQLite 后，`AgentPaths.workdir_experience_store` 的文件名也从
`experience_store.jsonl` 改为 `experience_store.db`（见 `storage/paths.py`
对应改动说明）。本脚本负责把旧文件里的历史记录读出来，用
`Experience.from_dict()` 宽容解析（旧记录只有六个字段，新增字段回退到
默认值），逐条写进新的 SQLite store。

用法：
    python scripts/migrate_experience_jsonl_to_sqlite.py [project_root]

`project_root` 缺省为当前目录。脚本是幂等的（`ExperienceStore.append()`
按 `id` upsert），重复运行不会产生重复记录，但因为旧 JSONL 记录本身没有
`id` 字段，`Experience.from_dict()` 会给每条记录生成一个新的随机 `id`——
这意味着重复运行本脚本仍然会插入重复行。跑完确认新 store 数据正确后，
建议手动删除旧的 `.jsonl` 文件，避免误再次运行本脚本。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 让脚本可以在不 `pip install -e .` 的情况下直接跑（仓库其它一次性脚本，
# 例如 `scripts/dep_graph.py`，也是这个模式）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mini_agent.core.experience import Experience  # noqa: E402
from mini_agent.core.experience_store import ExperienceStore  # noqa: E402
from mini_agent.storage.paths import AgentPaths  # noqa: E402


def _old_jsonl_path(project_root: Path) -> Path:
    """Sprint 2 阶段的旧路径：`.agent/experience_store.jsonl`。

    不直接复用 `AgentPaths.workdir_experience_store`——那个属性现在
    指向新的 `.db` 路径，旧路径只在这个迁移脚本里硬编码一次，避免污染
    正式代码路径的语义。
    """
    return AgentPaths(project_root=project_root).workdir_dir / "experience_store.jsonl"


def migrate(project_root: Path) -> int:
    old_path = _old_jsonl_path(project_root)
    if not old_path.exists():
        print(f"未找到旧文件 {old_path}，无需迁移。")
        return 0

    new_store = ExperienceStore(
        path=AgentPaths(project_root=project_root).workdir_experience_store
    )

    migrated = 0
    skipped = 0
    with old_path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                print(f"第 {line_no} 行 JSON 解析失败，跳过：{line[:80]!r}")
                skipped += 1
                continue
            experience = Experience.from_dict(d)
            new_store.append(experience)
            migrated += 1

    print(f"迁移完成：{migrated} 条记录写入 {new_store.path}，跳过 {skipped} 条损坏记录。")
    print(f"确认数据无误后，可手动删除旧文件：{old_path}")
    return 0


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    raise SystemExit(migrate(root))
