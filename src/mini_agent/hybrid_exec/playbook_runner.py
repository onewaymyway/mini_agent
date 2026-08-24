"""
hybrid_exec/playbook_runner.py — PlaybookRunner：SKILL 档"参照 playbook 执行"

对应 next_doc/generative_capability_raw_result_and_hybrid_merge_plan.md 第3节。

定位：与 explorer.py 里的 `LLMExplorer`/`AgentExplorer` 是同一层级的执行
原语，但产出物不同——Explorer 产出的是一份脚本源码（之后按 SCRIPT 档反复
执行），PlaybookRunner 每次调用都直接拉起一次轻量 Agent 执行任务本身，
参照的是一份已经验证过大致可行的 playbook（步骤说明文本），不产出可复用
代码。这是"比脚本鲁棒（不会因为运行时细节变化就直接报废）、比全新探索
便宜（已知大致步骤，不是从零摸索）"这一档手段的具体实现。

复用 `_agent.run_agent_prompt()`——同一段"临时起一个最小 Agent 跑一次
prompt"的共享逻辑，与 `AgentExplorer`/`AgentRepairer`/
`FallbackExecutor.agent_direct` 保持一致，不重新实现一套 Agent 拉起流程。

[用户已确认的开放问题] 工具集范围、回合预算暂不在本模块预设默认值——
`max_turns` 是必须显式传入的构造参数，没有默认值。调用方（
`capability_engine` 接入 SKILL 档时）需要结合真实场景决定具体数值，本模块
不代为决定，避免"先拍一个数字，后面很难考证为什么是这个数"。

返回值语义与 `FallbackExecutor.agent_direct()` 一致：返回 Agent 最后一轮
的原始文本回复，不在本模块内做 JSON 解析——是否需要解析、如何解析，交给
`TaskSpec.output_validator`（与 `HybridExecutor._fallback()` 里
`fallback.agent_direct()` 的输出被直接丢给 `task.run_validator()` 是同一
套约定，不新增一套解析规则）。
"""

from __future__ import annotations

import json
from pathlib import Path

from .runner import RunnerAppConfig
from .spec import TaskSpec

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# playbook 判定为"根本走不通"时，agent 在最后一轮回复里约定使用的前缀
# （见 prompts/run_playbook.md 的"输出要求"一节）。PlaybookInvalidError 让
# 调用方能区分"这次执行失败"和"这份 playbook 本身该退役了"两种情况，
# 后者应该调用 PlaybookRepository.record_failure/retire，而不只是当作一次
# 普通的执行失败重试。
_INVALID_PREFIX = "PLAYBOOK_INVALID:"


class PlaybookInvalidError(RuntimeError):
    """Agent 判定这份 playbook 在当前环境下根本性地走不通（不是细节出入）。"""


class PlaybookRunner:
    def __init__(self, app_cfg: RunnerAppConfig, *, max_turns: int) -> None:
        self.app_cfg = app_cfg
        self.max_turns = max_turns

    def run(self, task: TaskSpec, playbook_content: str) -> str:
        """参照 playbook_content 执行一次任务，返回 Agent 最后一轮的原始文本
        回复。若 Agent 判定 playbook 根本走不通，抛出 PlaybookInvalidError
        （携带原因），不是普通执行失败——调用方应据此考虑让该 playbook 版本
        退役，而不是简单重试。
        """
        from ._agent import run_agent_prompt

        template = (_PROMPTS_DIR / "run_playbook.md").read_text(encoding="utf-8")
        prompt = template.format(
            description=task.description,
            playbook=playbook_content,
            input_sample=json.dumps(task.input_data, ensure_ascii=False, indent=2),
        )
        text = run_agent_prompt(
            self.app_cfg, task, prompt, max_turns=self.max_turns, session_label="skill_playbook"
        )
        stripped = text.strip()
        if stripped.startswith(_INVALID_PREFIX):
            reason = stripped[len(_INVALID_PREFIX):].strip()
            raise PlaybookInvalidError(reason or "Agent 未说明具体原因")
        return text
