"""world_simulator/spec_generator.py — 意图 → 模拟提案草稿

设计依据：`world_simulator_external_project_plan.md` 2.3 节。走
`workflows/generate_scenario.yaml`（`skill_agent` 类型），复用既有的
"LLM 输出 → 结构化校验 → 落盘 → 失败自动重试"整套机制，不自己重新写
一套"调 LLM + 解析 JSON + 校验"的轮子。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from world_simulator.state_model import ChoiceOption


class ScenarioGenerationError(RuntimeError):
    pass


@dataclass
class ScenarioDraft:
    """一份模拟提案草稿（对应 `generate_scenario.yaml` 的 result_file）。"""

    title: str
    summary: str
    vars: Dict[str, Any]
    options: List[ChoiceOption]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScenarioDraft":
        return cls(
            title=str(data.get("title", "")),
            summary=str(data.get("summary", "")),
            vars=dict(data.get("vars") or {}),
            options=[ChoiceOption.from_dict(o) for o in (data.get("options") or [])],
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


def generate_scenario(cfg, workspace_root: Path, *, template: str, intent: str) -> ScenarioDraft:
    """触发一次 `generate_scenario` workflow，返回结构化的提案草稿。

    Args:
        cfg: `mini_agent.config.load_config()` 返回的 `AppConfig`。
        workspace_root: world_simulator 项目根（workflow/skill 私有
            目录 `<root>/workflows`、`<root>/skills` 从这里派生）。
        template: 场景模板名（如 `life_sim`），决定挂载哪个 skill。
        intent: 用户的一句话模拟意图。
    """
    from mini_agent.workflow.runner import WorkflowRunner

    wf, skill_name = _load_and_bind_skill(
        Path(workspace_root), "generate_scenario", template, "draft"
    )

    runner = WorkflowRunner(cfg)
    result = runner.run(wf, {"intent": intent})

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
