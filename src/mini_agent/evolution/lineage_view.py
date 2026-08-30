"""evolution/lineage_view.py — "谱系"视图
（next_doc/self_awareness_identity_evolution_plan.md §2.3）。

背景：`EvolutionWorkspace`（evolution/workspace.py）+ `StateRepo`
（evolution/state_repo.py）的 git worktree 隔离机制，技术上已经是
"复制自己、在隔离环境里变异、评估后选择合并或丢弃"，但项目里一直把它
理解为普通的"代码变更审核流程"。本模块不改变底层机制，只做一层纯只读
的重新表述：把 evolve 分支历史组织成"谱系"——每条分支是一个"变体候选
自己"，`StateRepo` 的风险分级（T0-T3）是"变异幅度"，merge/丢弃是
"选择保留/淘汰"。

**数据来源与已知限制（如实记录，不臆造）**：
  - `active_variants`（尝试中的变体）：当前仍存在的 `evolve/*` 分支，
    直接读 `StateRepo.list_branches(prefix="evolve/")`，可靠。
  - `merged_variants`（被保留的变体）：`StateRepo.merge_branch()` 默认
    生成 `"Merge evolve proposal: <branch>"` 格式的 merge commit，
    扫描当前分支的 commit log 按这个 subject 前缀识别，可靠但依赖
    调用方使用默认 message（自定义 message 的合并不会被识别为谱系
    事件，这是已知边界，不强行用启发式猜测）。
  - `discarded_variants`（被淘汰的变体）：**当前无法可靠还原**。
    `EvolutionWorkspace.destroy()` 只清理 worktree，`StateRepo.
    delete_branch()` 删除分支后 git 不会在正常历史里留下"这个分支曾经
    存在、为什么被放弃"的记录（reflog 有时效性，且不同 git 版本/gc
    策略下不保证保留），项目目前也没有独立的"进化尝试结果"日志把
    "尝试过 X，没有成功，原因是 Y"这类叙事所需的信息持久化下来。
    本模块如实返回空列表 + 一条说明，不用启发式或 LLM 编造"应该存在
    的"淘汰记录——方案原文本身也把"淘汰历史留一笔"列为需要新增记录点
    的能力，而不是从现有数据里能反查出来的。留待后续如果要做实，需要
    在 §4.2/§4.3 之类的评审/丢弃动作发生时新增一条独立的落盘记录。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_MERGE_SUBJECT_RE = re.compile(r"^Merge evolve proposal: (?P<branch>\S+)")


@dataclass
class LineageView:
    active_variants: list[dict] = field(default_factory=list)
    merged_variants: list[dict] = field(default_factory=list)
    discarded_variants: list[dict] = field(default_factory=list)
    discarded_note: str = (
        "淘汰的变体分支当前无法从 git 历史可靠还原（分支删除后不留存"
        "\"尝试过什么、为什么放弃\"这类记录）；如需支持，需要在丢弃动作"
        "发生时新增一条独立的落盘记录，本视图不做启发式猜测。"
    )

    def to_dict(self) -> dict:
        return {
            "active_variants": self.active_variants,
            "merged_variants": self.merged_variants,
            "discarded_variants": self.discarded_variants,
            "discarded_note": self.discarded_note,
        }


def compute_lineage_view(paths, *, merged_scan_limit: int = 200) -> LineageView:
    """只读计算谱系视图。`StateRepo` 不可用/无 commit 历史时返回全空视图
    （不是错误——很多 workdir 从未使用过 evolve 机制）。"""
    try:
        from mini_agent.evolution.state_repo import StateRepo

        repo = StateRepo(paths.project_root)
        if not repo.has_commits():
            return LineageView()
    except Exception:
        return LineageView()

    active_variants = []
    try:
        for branch in repo.list_branches(prefix="evolve/"):
            try:
                commits = repo.commits_on_branch(branch)
            except Exception:
                commits = []
            active_variants.append({
                "branch": branch,
                "commit_count": len(commits),
                "tiers": sorted({
                    c.subject[1:c.subject.index("]")]
                    for c in commits
                    if c.subject.startswith("[T") and "]" in c.subject
                    and c.subject[1:c.subject.index("]")] in ("T0", "T1", "T2", "T3")
                }),
            })
    except Exception:
        pass

    merged_variants = []
    try:
        for c in repo.log(limit=merged_scan_limit):
            m = _MERGE_SUBJECT_RE.match(c.subject or "")
            if m:
                merged_variants.append({
                    "branch": m.group("branch"),
                    "merged_at": c.date,
                    "commit": c.commit[:12],
                })
    except Exception:
        pass

    return LineageView(active_variants=active_variants, merged_variants=merged_variants)
