"""大纲先行模式（writing_mode = outline_first）的步骤定义。

O0 只定义步骤结构与接入点：
  * brief_draft   简述生成（O1 实现处理器）
  * brief_review  简述确认检查点（O2 实现处理器；闸门强制为人工审批）
未注册处理器的 python 步骤会被运行器置为 blocked（执行器尚未实现），因此在 O1/O2 完成前，
选择大纲先行的书会在简述生成处停下，不会误写正文。
直写模式下本模块的步骤不进入计划。
"""
from __future__ import annotations

from typing import Tuple

from novel_writer import steps as S

BRIEF_DRAFT_ID = "brief_draft"
BRIEF_REVIEW_ID = "brief_review"
MODE_OUTLINE_FIRST = "outline_first"


def is_outline_first(settings: dict) -> bool:
    return (settings or {}).get("writing_mode") == MODE_OUTLINE_FIRST


def brief_draft_step(deps: Tuple[str, ...]) -> S.StepDef:
    return S.StepDef(
        id=BRIEF_DRAFT_ID, title="生成章节简述（大纲先行，O1 实现中）", stage="p2", category=S.CAT_BUILD,
        executor=S.EXECUTOR_PYTHON, handler="brief_draft",
        inputs=(S.rel("blueprint"), S.rel("characters_dir") + "/*.md", S.rel("plot_tracker")),
        outputs=(), deps=deps,
    )


def brief_review_step(deps: Tuple[str, ...]) -> S.StepDef:
    return S.StepDef(
        id=BRIEF_REVIEW_ID, title="章节简述确认（等待作者确认，O2 实现中）", stage="p2", category=S.CAT_CHECKPOINT,
        executor=S.EXECUTOR_PYTHON, handler="checkpoint",
        inputs=(), outputs=(), deps=deps,
    )
