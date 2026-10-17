"""novel_writer/steps.py — 步骤定义（方案第 4.1–4.3 节）。

一个步骤 = `StepDef`：执行器（skill / python / checkpoint）、所属阶段与闸门类别、
依赖、输入（用于脏标记）、期望产出（用于校验）。这里只**描述**步骤，不执行。

执行与闸门判定分别在 `runner.py` 与 `gates.py`；展开成完整流水线在 `pipeline.py`。
路径全部经 `workspace.PATHS` 生成，步骤里不写硬编码路径。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from novel_writer.workspace import PATHS

# 执行器类型
EXECUTOR_SKILL = "skill"        # 由 LLM 执行 skill（经 WorkflowRunner.skill_agent）
EXECUTOR_PYTHON = "python"      # 确定性代码（handler 注册在 runner/worker 中）

# 闸门类别（gates.py 的挡位表按此查表）
CAT_INFRA = "infra"                 # 目录/配置等确定性初始化：所有挡位自动
CAT_DESIGN = "design"               # 规划与设计（主题/节奏/人设设计/开篇）
CAT_BUILD = "build"                 # 建档（人设档案/世界圣经/伏笔注册）
CAT_CHECKPOINT = "checkpoint"       # 强制检查点（蓝图确认、前三章确认）
CAT_CHAPTER_PREP = "chapter_prep"   # 写前准备（W1–W4）
CAT_CHAPTER_WRITE = "chapter_write" # 写作（W8）
CAT_CHAPTER_REVIEW = "chapter_review"  # 审校/修改/归档/同步（W9–W14）
CAT_ACT = "act"                     # 幕末步骤
CAT_REVISE = "revise"               # 修订/推演/灵感：始终手动

CATEGORIES = (
    CAT_INFRA, CAT_DESIGN, CAT_BUILD, CAT_CHECKPOINT, CAT_CHAPTER_PREP,
    CAT_CHAPTER_WRITE, CAT_CHAPTER_REVIEW, CAT_ACT, CAT_REVISE,
)

# 前三章的章号（方案 1.2 / 4.2：前三章逐章人工确认）
OPENING_CHAPTERS = 3
# 读者模拟的默认间隔（标准档：每 5 章；方案 6.4）
READER_SIM_INTERVAL = 5


@dataclass(frozen=True)
class StepDef:
    id: str
    title: str
    stage: str                  # p0 / p1 / p2 / p3 / act
    category: str
    executor: str
    skill: str = ""             # executor == skill 时必填
    handler: str = ""           # executor == python 时的处理器名（见 runner.PY_HANDLERS）
    instructions: str = ""      # 本步的任务说明（拼进 prompt）
    inputs: Tuple[str, ...] = ()   # 相对工作目录的路径或 glob，用于计算输入哈希
    outputs: Tuple[str, ...] = ()  # 相对工作目录的路径或 `glob:` 模式，用于产出校验
    chapter: int = 0            # 所属章节号（非章节步骤为 0）
    deps: Tuple[str, ...] = ()  # 依赖的步骤 id（线性流水线下主要用于展示与说明）
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_checkpoint(self) -> bool:
        return self.category == CAT_CHECKPOINT

    @property
    def counts_as_llm_call(self) -> bool:
        return self.executor == EXECUTOR_SKILL


def rel(key: str, **fmt: Any) -> str:
    """按路径键生成相对工作目录的路径（与 `Workspace.path` 同样的格式化规则，但不带根）。"""
    template = PATHS[key]
    if "{nn}" in template and isinstance(fmt.get("nn"), int):
        fmt["nn"] = f"{fmt['nn']:02d}"
    return template.format(**fmt)


def glob_of(key: str, **fmt: Any) -> str:
    """章节类文件标题未知时用 glob 匹配：`第nn章 *.md`。"""
    return "glob:" + rel(key, **{**fmt, "title": "*"})


def chapter_id(n: int, name: str) -> str:
    return f"ch{n:02d}.{name}"


# ── 规划阶段（P1，方案 4.2）────────────────────────────────────────────────────


def init_workspace_step() -> StepDef:
    return StepDef(
        id="init_workspace", title="初始化工作目录", stage="p0", category=CAT_INFRA,
        executor=EXECUTOR_PYTHON, handler="init_workspace",
        outputs=(rel("studio_manifest"), rel("workspace_config")),
    )


def plan_blueprint_step() -> StepDef:
    return StepDef(
        id="plan_blueprint", title="生成全书蓝图", stage="p1", category=CAT_CHECKPOINT,
        executor=EXECUTOR_SKILL, skill="novel-planner",
        instructions="根据作者在向导里给出的意图，产出全书蓝图（结构、章节表、主角弧、伏笔索引）及可读大纲。",
        inputs=(rel("workspace_config"),),
        outputs=(rel("blueprint"), rel("blueprint_readable")),
        deps=("init_workspace",),
    )


def design_theme_step() -> StepDef:
    return StepDef(
        id="design_theme", title="主题设计", stage="p1", category=CAT_DESIGN,
        executor=EXECUTOR_SKILL, skill="theme-architect",
        instructions="基于蓝图设计主题与防说教方案，写入主题设计文档。",
        inputs=(rel("blueprint"),), outputs=(rel("theme_design"),),
        deps=("plan_blueprint",),
    )


def design_pacing_step() -> StepDef:
    return StepDef(
        id="design_pacing", title="节奏设计", stage="p1", category=CAT_DESIGN,
        executor=EXECUTOR_SKILL, skill="pacing-architect",
        instructions="基于蓝图设计跨章节奏、三章单元与幕次张力曲线。",
        inputs=(rel("blueprint"),), outputs=(rel("pacing_design"),),
        deps=("plan_blueprint",),
    )


def design_character_step(name: str) -> StepDef:
    return StepDef(
        id=f"design_character:{name}", title=f"人设设计：{name}", stage="p1", category=CAT_DESIGN,
        executor=EXECUTOR_SKILL, skill="character-designer",
        instructions=f"为主要角色「{name}」设计人设卡（人设卡文件名即角色名）。",
        inputs=(rel("blueprint"),), outputs=(rel("character_card", name=name),),
        deps=("plan_blueprint",), extra={"character": name},
    )


def build_characters_step(card_names: Sequence[str]) -> StepDef:
    return StepDef(
        id="build_characters", title="建立人物档案", stage="p1", category=CAT_BUILD,
        executor=EXECUTOR_SKILL, skill="character-manager",
        instructions="把人设卡转换为结构化人物档案与行为边界索引。",
        inputs=tuple(rel("character_card", name=n) for n in card_names),
        outputs=(rel("characters"), rel("behavior_index")),
        deps=tuple(f"design_character:{n}" for n in card_names),
    )


def build_world_step() -> StepDef:
    return StepDef(
        id="build_world", title="建立世界观设定", stage="p1", category=CAT_BUILD,
        executor=EXECUTOR_SKILL, skill="world-builder",
        instructions="基于蓝图建立世界圣经（世界规则、地点、势力）。",
        inputs=(rel("blueprint"),), outputs=(rel("world_bible"),),
        deps=("plan_blueprint",),
    )


def register_plot_step() -> StepDef:
    return StepDef(
        id="register_plot", title="注册伏笔与张力基准", stage="p1", category=CAT_BUILD,
        executor=EXECUTOR_SKILL, skill="plot-tracker",
        instructions="从蓝图注册全部伏笔与依赖链，建立张力基准。",
        inputs=(rel("blueprint"),), outputs=(rel("plot_tracker"), rel("foreshadowing_list")),
        deps=("build_characters", "build_world"),
    )


def init_word_count_step() -> StepDef:
    return StepDef(
        id="init_word_count", title="初始化字数配额", stage="p1", category=CAT_INFRA,
        executor=EXECUTOR_PYTHON, handler="init_word_count",
        inputs=(rel("blueprint"),), outputs=(rel("word_count"),),
        deps=("plan_blueprint",),
    )


def simulate_blueprint_step() -> StepDef:
    return StepDef(
        id="simulate_blueprint", title="蓝图快进推演（可选）", stage="p1", category=CAT_REVISE,
        executor=EXECUTOR_SKILL, skill="plot-simulator",
        instructions="快进推演蓝图，验证伏笔可兑现、找出断裂点。推演结果写入推演记录目录。",
        inputs=(rel("blueprint"), rel("plot_tracker")), outputs=(rel("simulations_dir") + "/*",),
        deps=("register_plot", "init_word_count"),
    )


# ── 前三章（P2）────────────────────────────────────────────────────────────────


def design_opening_step() -> StepDef:
    return StepDef(
        id="design_opening", title="前三章开篇系统", stage="p2", category=CAT_DESIGN,
        executor=EXECUTOR_SKILL, skill="hook-designer",
        instructions="设计前三章的开篇系统与章末钩子，作为后续写作的额外指导。",
        inputs=(rel("blueprint"),), outputs=(rel("opening_design"),),
        deps=("register_plot",),
    )


def confirm_first3_step() -> StepDef:
    return StepDef(
        id="confirm_first3", title="前三章确认（强制检查点）", stage="p2", category=CAT_CHECKPOINT,
        executor=EXECUTOR_PYTHON, handler="checkpoint",
        deps=("ch03.sync",),
    )


def init_style_b_step() -> StepDef:
    return StepDef(
        id="init_style_b", title="建立风格指纹（模式 B）", stage="p2", category=CAT_BUILD,
        executor=EXECUTOR_SKILL, skill="style-guardian",
        instructions="以前三章（已确认）为样本建立风格指纹。",
        inputs=(rel("blueprint"), glob_of("chapter_text", nn=1)), outputs=(rel("style_profile"),),
        deps=("confirm_first3",),
    )


# ── 章节循环（P2/P3，方案 4.3）──────────────────────────────────────────────────


def chapter_steps(n: int, title: str, *, total: Optional[int], prev_last: str) -> List[StepDef]:
    """第 n 章的单章循环（W1–W14）。

    返回的步骤按**线性顺序**排列，第一个步骤依赖 `prev_last`（上一个步骤 id）。
    W5–W7（推演/场景/对话）属于 M3/M4 范围，本期不展开。
    标题未知（蓝图缺失）时，章节文件用 glob 匹配产出。
    """
    p = lambda name: chapter_id(n, name)  # noqa: E731
    chapter_text = rel("chapter_text", nn=n, title=title) if title else glob_of("chapter_text", nn=n)
    prev_summary = glob_of("chapter_summary", nn=n - 1) if n > 1 else None
    base_inputs = (rel("blueprint"), rel("characters"), rel("plot_tracker"))
    common = dict(chapter=n)

    steps: List[StepDef] = [
        StepDef(id=p("word_quota"), title=f"第{n}章：字数配额", stage=_stage_of(n), category=CAT_CHAPTER_PREP,
                executor=EXECUTOR_PYTHON, handler="word_quota", inputs=(rel("word_count"),),
                deps=(prev_last,), **common),
        StepDef(id=p("plot_tasks"), title=f"第{n}章：章前情节清单", stage=_stage_of(n), category=CAT_CHAPTER_PREP,
                executor=EXECUTOR_PYTHON, handler="plot_tasks", inputs=base_inputs,
                deps=(p("word_quota"),), **common),
        StepDef(id=p("context_pack"), title=f"第{n}章：上下文包", stage=_stage_of(n), category=CAT_CHAPTER_PREP,
                executor=EXECUTOR_PYTHON, handler="context_pack",
                inputs=base_inputs + ((prev_summary,) if prev_summary else ()),
                outputs=(rel("context_pack", n=n),), deps=(p("plot_tasks"),), **common),
        StepDef(id=p("style_hint"), title=f"第{n}章：风格提示", stage=_stage_of(n), category=CAT_CHAPTER_PREP,
                executor=EXECUTOR_PYTHON, handler="style_hint", inputs=(rel("style_profile"),),
                deps=(p("context_pack"),), **common),
        StepDef(id=p("write_chapter"), title=f"第{n}章：写作", stage=_stage_of(n), category=CAT_CHAPTER_WRITE,
                executor=EXECUTOR_SKILL, skill="chapter-writer",
                instructions=f"写第 {n} 章正文。严格遵循上下文包与章前情节清单；产出正文并在回执里给出字数与写作备注。",
                inputs=(rel("context_pack", n=n), rel("style_hint", n=n), rel("chapter_tasks", n=n),
                        rel("quota_file", n=n)),
                outputs=(chapter_text,), deps=(p("style_hint"),), **common),
        StepDef(id=p("machine_check"), title=f"第{n}章：机器校验", stage=_stage_of(n), category=CAT_CHAPTER_REVIEW,
                executor=EXECUTOR_PYTHON, handler="machine_check", inputs=(chapter_text,),
                deps=(p("write_chapter"),), **common),
        StepDef(id=p("review"), title=f"第{n}章：完章核查", stage=_stage_of(n), category=CAT_CHAPTER_REVIEW,
                executor=EXECUTOR_SKILL, skill="chapter-review",
                instructions=(f"对第 {n} 章做一致性核查。问题清单的每一条必须以 🔴（硬错误）或 🟡（建议）开头，"
                              "并给出原文定位；没有问题时写明「无 🔴」。"),
                inputs=(chapter_text, rel("characters"), rel("plot_tracker")),
                outputs=(rel("review_report", nn=n),), deps=(p("machine_check"),), **common),
        StepDef(id=p("revise_loop"), title=f"第{n}章：修改循环", stage=_stage_of(n), category=CAT_CHAPTER_REVIEW,
                executor=EXECUTOR_PYTHON, handler="revise_loop", inputs=(rel("review_report", nn=n),),
                deps=(p("review"),), **common),
        StepDef(id=p("archive"), title=f"第{n}章：概述归档", stage=_stage_of(n), category=CAT_CHAPTER_REVIEW,
                executor=EXECUTOR_SKILL, skill="chapter-review",
                instructions=f"为第 {n} 章写章节概述并更新全书章节索引与记忆摘要（只在核查通过后执行）。",
                inputs=(chapter_text, rel("review_report", nn=n)),
                outputs=(rel("chapter_summary", nn=n),), deps=(p("revise_loop"),), **common),
        StepDef(id=p("sync"), title=f"第{n}章：状态同步", stage=_stage_of(n), category=CAT_CHAPTER_REVIEW,
                executor=EXECUTOR_SKILL, skill="character-manager",
                instructions=f"根据第 {n} 章的概述同步人物当前状态。",
                inputs=(rel("chapter_summary", nn=n), rel("characters")),
                outputs=(rel("characters"),), deps=(p("archive"),), **common),
    ]
    steps.append(StepDef(
        id=p("sync_plot"), title=f"第{n}章：伏笔状态同步", stage=_stage_of(n), category=CAT_CHAPTER_REVIEW,
        executor=EXECUTOR_SKILL, skill="plot-tracker",
        instructions=f"根据第 {n} 章的概述更新伏笔状态（埋设/推进/兑现）与故事线进度。",
        inputs=(rel("chapter_summary", nn=n), rel("plot_tracker")),
        outputs=(rel("plot_tracker"),), deps=(p("sync"),), **common,
    ))
    if n % READER_SIM_INTERVAL == 0:
        steps.append(StepDef(
            id=p("reader_sim"), title=f"第{n}章：读者体验模拟", stage=_stage_of(n), category=CAT_CHAPTER_REVIEW,
            executor=EXECUTOR_SKILL, skill="reader-simulator",
            instructions=f"以读者视角评估第 {n} 章（注意力/理解/情感/可信/节奏）。",
            inputs=(chapter_text,), outputs=(rel("reader_report", nn=n),), deps=(p("sync_plot"),), **common,
        ))
    return steps


# ── 幕末（方案 4.2 幕末表）────────────────────────────────────────────────────


def act_steps(act: int) -> List[StepDef]:
    """第 act 幕结束后的幕末步骤。全部是 skill 步骤，闸门随挡位（幕末类别）。"""
    a = {"act": act}
    return [
        StepDef(id=f"act_review:{act}", title=f"第{act}幕：深度审稿", stage="act", category=CAT_ACT,
                executor=EXECUTOR_SKILL, skill="consistency-checker", extra=a,
                instructions=f"对第 {act} 幕做深度审稿（五维：事实/人物/世界规则/伏笔/主题），输出幕次健康报告。",
                outputs=(rel("act_health_report", act=act),)),
        StepDef(id=f"act_style:{act}", title=f"第{act}幕：风格幕间对比", stage="act", category=CAT_ACT,
                executor=EXECUTOR_SKILL, skill="style-guardian", extra=a,
                instructions=f"对比第 {act} 幕与风格指纹，区分「有意调整」与「有害漂移」。",
                outputs=(rel("act_style_report", act=act),)),
        StepDef(id=f"act_pacing:{act}", title=f"第{act}幕：节奏复盘", stage="act", category=CAT_ACT,
                executor=EXECUTOR_SKILL, skill="pacing-architect", extra=a,
                instructions=f"复盘第 {act} 幕的张力曲线，并重新规划下一幕的三章单元。",
                outputs=(rel("act_pacing_report", act=act),)),
        StepDef(id=f"act_theme:{act}", title=f"第{act}幕：主题一致性", stage="act", category=CAT_ACT,
                executor=EXECUTOR_SKILL, skill="theme-architect", extra=a,
                instructions=f"检查第 {act} 幕的主题表达是否一致、是否说教。",
                outputs=(rel("act_theme_report", act=act),)),
        StepDef(id=f"act_worldcheck:{act}", title=f"第{act}幕：世界设定巡检", stage="act", category=CAT_ACT,
                executor=EXECUTOR_SKILL, skill="world-builder", extra=a,
                instructions=f"巡检第 {act} 幕用到的世界设定，标出冗余与冲突。",
                outputs=(rel("world_usage_report", n=act),)),
        StepDef(id=f"act_ideas:{act}", title=f"第{act}幕：复查暂存灵感", stage="act", category=CAT_ACT,
                executor=EXECUTOR_SKILL, skill="idea-catcher", extra=a,
                instructions="复查灵感箱中暂存的灵感，判断是否现在整合进后续章节。",
                outputs=()),
        StepDef(id=f"act_simulate:{act}", title=f"第{act}幕：中层推演（可选）", stage="act", category=CAT_REVISE,
                executor=EXECUTOR_SKILL, skill="plot-simulator", extra={**a, "optional": True},
                instructions="在下一幕开始前做中层推演，验证伏笔与走向。",
                outputs=(rel("simulations_dir") + "/*",)),
    ]


def snapshot_step(n: int) -> StepDef:
    """每 10 章的全局快照（方案 4.2）。python 步骤，基础设施类别，所有挡位自动。"""
    return StepDef(
        id=f"snapshot:{n}", title=f"第{n}章后全局快照", stage="p3", category=CAT_INFRA,
        executor=EXECUTOR_PYTHON, handler="snapshot", chapter=0,
        inputs=(rel("characters"), rel("plot_tracker")), outputs=(rel("memory_snapshot", n=n),),
        extra={"through_chapter": n},
    )


def _stage_of(n: int) -> str:
    return "p2" if n <= OPENING_CHAPTERS else "p3"
