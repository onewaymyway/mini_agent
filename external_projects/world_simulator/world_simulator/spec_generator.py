"""world_simulator/spec_generator.py — 意图 → 模拟提案草稿

设计依据：`world_simulator_external_project_plan.md` 2.3 节。走
`workflows/generate_scenario.yaml`（`skill_agent` 类型），复用既有的
"LLM 输出 → 结构化校验 → 落盘 → 失败自动重试"整套机制，不自己重新写
一套"调 LLM + 解析 JSON + 校验"的轮子。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from world_simulator.state_model import ChoiceOption


class ScenarioGenerationError(RuntimeError):
    pass


DEFAULT_OPTIONS_COUNT = 4
DEFAULT_TIME_GRANULARITY = (
    "自动——由你根据模拟对象的性质自行判断每一步合理的时间跨度"
    "（不必固定假设\u201c一步 = 一年\u201d，比如模拟一场谈判可能一步只是几分钟，"
    "模拟一个文明可能一步是几十年）"
)

_GRANULARITY_CONTINUITY_NOTE_ADVANCE = (
    "\n默认延续上一步用过的粒度（见 `{current_time_granularity}`），除非"
    "情境明显需要改变（比如从平稳时期进入密集博弈/谈判/危机，或者反过来"
    "从紧张情境恢复平稳）——不要没有情节依据就在粒度之间来回跳。请在输出里"
    "给出这一步实际使用的粒度 `next_time_granularity`；如果这个值和上一步"
    "不同，额外给一两句话的 `granularity_reason` 说明为什么现在要切换节奏，"
    "没变化则不需要这个字段。"
)
_GRANULARITY_CONTINUITY_NOTE_CREATE = (
    "\n这是初始状态，你给出的粒度会作为本次模拟的起始基准（输出字段"
    "`time_granularity`），之后每一步默认延续这个基准，只在情境明显需要"
    "时才会切换——请选一个能代表这次模拟\"日常节奏\"的粒度，不用考虑中途"
    "临时的特殊情境。"
)
_VALID_GRANULARITY_MODES = ("fixed", "auto", "guided")


def resolve_hints(settings: "Dict[str, Any] | None" = None, *, stage: str = "advance") -> Dict[str, str]:
    """把 `manifest.settings`（或创建向导里还没落盘成 manifest 时的临时
    设置字典）转成喂给 workflow prompt 的提示字符串。

    单独抽成一个函数，是因为 `generate_scenario()`（创建阶段，这时候
    还没有 manifest）和 `engine.advance()`（推进阶段，从
    `manifest.settings` 读）两处都要用同一套"没设置就用什么默认值"的
    规则——不想两处各写一份，以后要改默认值/校验逻辑就得同步改两处、
    容易漏改一处。

    Args:
        stage: `"create"`（`generate_scenario` 场景，还没有"上一步"）
            或 `"advance"`（默认，`engine.advance()` 场景，有
            `{current_time_granularity}` 可以参考）——两种场景下
            "自动"/"引导"模式的提示文案略有不同（`create` 讲的是"建立
            基准"，`advance` 讲的是"延续上一步、变化需说明理由"），
            避免 `generate_scenario.yaml` 的 prompt 里出现一句引用了
            不存在的 `{current_time_granularity}` 占位符的说明文字。

    `time_granularity_hint` 的内容按 `time_granularity_mode` 分三种：
    - `fixed`：明确要求"每一步都严格按这个值推进"，行为与阶段一/二
      完全一致（一次模拟从头到尾只有一种粒度）。
    - `auto`（未设置时的默认值）：交给 skill 按情境自行判断，同一次
      模拟允许出现多种粒度，附带"延续上一步、变化需给理由"的约束，
      避免毫无依据地来回跳。
    - `guided`：在 `auto` 的自由裁量基础上，多附一句用户给的偏好/基准
      引导语，不是精确值，skill 仍自主判断。
    """
    settings = settings or {}
    raw_count = settings.get("options_count")
    try:
        options_count = int(raw_count)
    except (TypeError, ValueError):
        options_count = DEFAULT_OPTIONS_COUNT
    options_count = max(1, min(options_count, 8))

    mode = str(settings.get("time_granularity_mode") or "").strip()
    if mode not in _VALID_GRANULARITY_MODES:
        mode = "auto"

    continuity_note = (
        _GRANULARITY_CONTINUITY_NOTE_CREATE if stage == "create" else _GRANULARITY_CONTINUITY_NOTE_ADVANCE
    )

    if mode == "fixed":
        fixed_value = str(settings.get("time_granularity") or "").strip() or DEFAULT_TIME_GRANULARITY
        time_granularity_hint = f"固定模式——每一步都必须严格按这个粒度推进：{fixed_value}"
    elif mode == "guided":
        guide = str(settings.get("time_granularity_guide") or "").strip()
        if guide:
            time_granularity_hint = (
                f"引导模式——用户给出的偏好/基准（不是精确值，仍需你自行判断）："
                f"{guide}{continuity_note}"
            )
        else:
            time_granularity_hint = f"{DEFAULT_TIME_GRANULARITY}{continuity_note}"
    else:  # auto
        time_granularity_hint = f"{DEFAULT_TIME_GRANULARITY}{continuity_note}"

    return {
        "option_count_hint": f"{options_count} 个左右",
        "time_granularity_hint": time_granularity_hint,
    }


@dataclass
class ScenarioDraft:
    """一份模拟提案草稿（对应 `generate_scenario.yaml` 的 result_file）。"""

    title: str
    summary: str
    vars: Dict[str, Any]
    options: List[ChoiceOption]
    time_label: str = ""
    time_granularity: str = ""
    """这次模拟的起始基准粒度（`settings.time_granularity_mode` 为
    `auto`/`guided` 时才有意义），落盘为 `state0.time_granularity`，
    之后每一步 `advance()` 默认延续这个基准，见
    `state_model.SimState.time_granularity` 的说明。"""
    resource_fields: List[Any] = field(default_factory=list)
    """skill 在生成初始 `vars` 时给出的"哪些字段是资源类数值字段"建议
    （阶段九，见 `state_model.SimManifest.settings` 里 `resource_fields`
    的格式说明），可选输出，留空表示 skill 认为这次模拟没有需要做下限
    校验的资源字段。创建向导里会把这个建议展示给用户、允许编辑，最终
    编辑结果随 `settings.resource_fields` 一起存进 manifest，这个字段
    本身只是"草稿阶段的建议值"，不直接落盘。"""
    uncertain_fields: List[Dict[str, Any]] = field(default_factory=list)
    """skill 在生成初始 `vars` 时主动标注的"本质是主观估计、置信度不高"
    的字段（阶段十一，见 `state_model.SimState.uncertain_fields` 的格式
    说明），可选输出，留空表示 skill 认为初始状态没有需要标注的估计值。
    这个字段会直接落盘到 `state0.uncertain_fields`（不像
    `resource_fields` 那样需要先经过用户编辑再存进 `settings`——置信度
    标注是"描述性"的，不是需要用户确认的配置项）。"""
    objectives: List[Any] = field(default_factory=list)
    """skill 在生成初始状态时给出的"这次模拟主要关心的指标"建议
    （阶段十二，`next_doc/world_simulator_universal_world_model_upgrade_
    plan.md` 4.4 节 Problem Compiler 雏形），比如
    `["资产净值", "工作满意度", "健康水平"]`。用途与 `resource_fields`
    一致：创建向导展示建议值、允许用户编辑，最终结果存进
    `settings.objectives`（见 `state_model.SimManifest.settings`），这个
    字段本身只是"草稿阶段的建议值"，不直接落盘。存下来之后，「对比
    实验」页面的关注字段默认会带出这里声明的指标。

    阶段十四（4.6 节）起，每一项除了纯字符串（阶段十二行为，只展示不
    参与排序）还可以是结构化字典
    `{"label": "资产净值", "field": "resources.cash", "direction": "max"}`
    ——声明了 `field` 的条目会在「对比实验」页面出现"按关注指标排序"
    的辅助展示区（见 `world_simulator.analysis.rank_by_objectives()`），
    但排序结果始终"仅供参考"，不会替用户自动选出最优解。类型放宽为
    `List[Any]` 就是为了同时兼容这两种写法，不强制转成字符串。"""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScenarioDraft":
        return cls(
            title=str(data.get("title", "")),
            summary=str(data.get("summary", "")),
            vars=dict(data.get("vars") or {}),
            options=[ChoiceOption.from_dict(o) for o in (data.get("options") or [])],
            time_label=str(data.get("time_label", "") or ""),
            time_granularity=str(data.get("time_granularity", "") or ""),
            resource_fields=list(data.get("resource_fields") or []),
            uncertain_fields=list(data.get("uncertain_fields") or []),
            objectives=[
                (dict(o) if isinstance(o, dict) else str(o))
                for o in (data.get("objectives") or [])
            ],
        )


def _skill_name_for_template(template: str) -> str:
    """模板名 → skill 名的约定：`<template 里下划线替换成连字符>-sim-template`。

    对应方案 2.4 节"新增一种模拟类型 = 新增一个 skill，引擎本身不用改"：
    只要按这个命名约定在 `skills/` 下放一个新 skill 目录（如模板名
    `life_sim` 对应 `skills/life-sim-template/`），`generate_scenario` /
    `advance_step` 两个 workflow 定义完全不用改，见 `_load_and_bind_skill()`。
    skill 名按惯例用连字符（`SkillLoader` 目录名规范），模板名按 Python
    惯例用下划线（供 `--template` CLI 参数/内部变量使用），两者在这里
    做唯一一次转换。
    """
    return f"{template.replace('_', '-')}-template"


def _load_and_bind_skill(workspace_root: Path, workflow_name: str, template: str, step_id: str):
    """加载 workflow 定义，并把指定 step 的 `skill_name` 按 `template`
    动态改写。

    `skill_agent` 步骤的 `skill_name` 字段本身不支持 `{var}` 占位符替换
    （替换机制只作用于 prompt 文本），所以在触发前用 Python 直接改写
    已解析出的 `WorkflowStep` 对象——`WorkflowStore.load()` 每次都是
    重新从磁盘解析，不会污染磁盘上的 yaml 定义或影响其它并发调用。
    """
    from mini_agent.workflow.store import WorkflowStore

    store = WorkflowStore(workspace_root)
    wf = store.load(workflow_name)
    if wf is None:
        raise ScenarioGenerationError(
            f"找不到 workflow 定义 {workflow_name!r}"
            f"（预期路径：{workspace_root}/workflows/{workflow_name}.yaml）"
        )
    skill_name = _skill_name_for_template(template)
    found = False
    for step in wf.steps:
        if step.id == step_id:
            step.skill_name = skill_name
            found = True
            break
    if not found:
        raise ScenarioGenerationError(
            f"workflow {workflow_name!r} 中找不到 step id={step_id!r}"
        )
    return wf, skill_name


def generate_scenario(
    cfg,
    workspace_root: Path,
    *,
    template: str,
    intent: str,
    feedback: str = "",
    previous_draft: "ScenarioDraft | None" = None,
    settings: "Dict[str, Any] | None" = None,
) -> ScenarioDraft:
    """触发一次 `generate_scenario` workflow，返回结构化的提案草稿。

    Args:
        cfg: `mini_agent.config.load_config()` 返回的 `AppConfig`。
        workspace_root: world_simulator 项目根（workflow/skill 私有
            目录 `<root>/workflows`、`<root>/skills` 从这里派生）。
        template: 场景模板名（如 `life_sim`），决定挂载哪个 skill。
        intent: 用户的一句话模拟意图。
        feedback: 用户对上一份草稿的补充意见（可选）；非空时表示这是
            一次"根据意见修改"的重新生成，而不是从零生成——
            `previous_draft` 应同时给出，供 skill 在原草稿基础上按
            意见调整，而不是完全无视原草稿重新编一份。
        previous_draft: 上一份草稿（`feedback` 非空时使用），用于把
            "改什么"锚定在"从什么改"上，避免每次修改意见都产出一份
            跟上次毫无关系的新草稿。
        settings: 创建向导里用户设置的 `options_count`/`time_granularity`
            （还没有 sim_id/manifest 时的临时字典，落盘后会原样存进
            `SimManifest.settings`，供之后每一步 `advance()` 沿用同一套
            配置）；为 None 时按 `resolve_hints()` 的默认值处理。
    """
    from mini_agent.workflow.runner import WorkflowRunner

    wf, skill_name = _load_and_bind_skill(
        Path(workspace_root), "generate_scenario", template, "draft"
    )

    previous_draft_json = ""
    if previous_draft is not None:
        previous_draft_json = json.dumps(
            {
                "title": previous_draft.title,
                "summary": previous_draft.summary,
                "vars": previous_draft.vars,
                "options": [o.to_dict() for o in previous_draft.options],
            },
            ensure_ascii=False,
        )

    runner = WorkflowRunner(cfg)
    result = runner.run(
        wf,
        {
            "intent": intent,
            "feedback": feedback or "",
            "previous_draft_json": previous_draft_json,
            **resolve_hints(settings, stage="create"),
        },
    )

    if result.status != "done":
        failed = [
            f"{sr.step_id}({sr.status.value}): {sr.error}"
            for sr in result.step_results
            if sr.status.value != "done"
        ]
        raise ScenarioGenerationError(
            f"generate_scenario workflow 执行未成功（skill={skill_name}）："
            f"status={result.status}；" + "；".join(failed)
        )

    draft_step = next((sr for sr in result.step_results if sr.step_id == "draft"), None)
    if draft_step is None or not draft_step.result_file:
        raise ScenarioGenerationError("draft 步骤未产出 result_file，无法解析提案草稿")

    data = json.loads(Path(draft_step.result_file).read_text(encoding="utf-8"))
    return ScenarioDraft.from_dict(data)
