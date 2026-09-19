"""world_simulator/agent_step_result.py — `type: agent` workflow step 的结构化
结果解析工具。

背景（bug 根因，见 `next_doc/` 里 agent_preview / retrospective 相关计划
文档）：`mini_agent.workflow` 的 `result_file` / `result_file_required_keys`
"结果文件契约"（写文件到 `WORKFLOW_RESULT_FILE_PATH`、校验、resume/重开
重试）只在 `type: script` 和 `type: skill_agent` 两种 step 类型里实现
（见 `mini_agent/workflow/executors.py` 的 `ScriptStepExecutor` /
`SkillAgentStepExecutor`）。`type: agent`（`AgentStepExecutor` →
`WorkflowRunner._execute_with_main_agent`）**完全没有**这套机制：不会把
目标路径通过环境变量告诉 Agent，也不会校验/重试，`StepResult.result_file`
对这种 step 永远是 `None`。

`agent_preview.yaml` / `retrospective.yaml` 都特意选了 `type: agent`（不
挂载具体模板 skill——预览/复盘是通用能力，不该绑定某个场景模板私有的
skill），但同时又声明了 `result_file` / `result_file_required_keys`，这个
声明对 `type: agent` 来说是完全不生效的死配置，调用方
（`agent_preview.py` / `retrospective.py`）却在按"文件契约生效"的假设去
读 `step_result.result_file`，导致每次都命中
`if step_result is None or not step_result.result_file` 这条分支，报
"步骤未产出 result_file"——跟 LLM 这次答得好不好完全无关，是 100% 必现
的配置/调用方式不匹配，不是偶发失败。

在不改动 `mini_agent` 框架本身（`external_projects/world_simulator` 之外
的代码）的前提下，这里改用另一条本来就一直有效的路径：`type: agent` 的
`StepResult.output` 就是这次独立 Agent 会话的最终回复文本（`agent.
run_turn(prompt)` 的返回值），只要 prompt 里明确要求"只回复一个 JSON
对象，不要任何其它文字"，这段文本本身就是我们要的结构化结果，不需要
真的落盘再读回来。用 `json_repair`（已经是本项目依赖，`mini_agent.
workflow.runner._validate_result_file` 本身也用它）做一点容错解析，
兼容 LLM 偶尔多套一层 ```json 代码块围栏这种小瑕疵。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class AgentStepOutputError(RuntimeError):
    """`type: agent` step 的 `StepResult.output` 无法解析成期望的结构化 JSON。"""


def extract_agent_json_output(
    output: str, required_keys: Optional[List[str]] = None
) -> Dict[str, Any]:
    """把一个 `type: agent` step 的 `StepResult.output`（Agent 最终回复的
    原始文本）解析成 dict。

    Args:
        output: `StepResult.output`。
        required_keys: 顶层必须包含的 key，缺失时报错（同
            `result_file_required_keys` 语义，供调用方在 prompt 已要求
            输出这些字段的前提下做最后一道兜底校验）。

    Raises:
        AgentStepOutputError: 内容为空、解析不出合法 JSON object、或缺少
            `required_keys` 里声明的字段。
    """
    import json_repair

    text = (output or "").strip()
    if not text:
        raise AgentStepOutputError("Agent 回复为空，无法解析结构化结果")

    # 容忍常见的 ```json ... ``` / ``` ... ``` 围栏包裹（同
    # `WorkflowRunner._validate_result_file` 的处理方式），以及围栏前后
    # 偶尔多出来的寒暄文字——取第一个 `{` 到最后一个 `}` 之间的片段再解析，
    # 比直接整段丢给 json_repair 更稳。
    cleaned = text
    if "```" in cleaned:
        fenced = cleaned.split("```")
        # 取围栏内最长的一段（大概率就是 JSON 本体）
        candidates = [seg for seg in fenced if seg.strip()]
        if candidates:
            cleaned = max(candidates, key=len)
        cleaned = cleaned.strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start : end + 1]

    try:
        data = json_repair.loads(cleaned)
    except Exception as exc:  # noqa: BLE001
        raise AgentStepOutputError(f"Agent 回复不是合法 JSON：{exc}") from exc

    if not isinstance(data, dict):
        raise AgentStepOutputError(
            f"Agent 回复解析出的顶层不是 JSON object，而是 {type(data).__name__}"
        )

    missing = [k for k in (required_keys or []) if k not in data]
    if missing:
        raise AgentStepOutputError(f"Agent 回复缺少必需字段 {missing}")

    return data
