"""
hybrid_exec/scaffold.py — "执行载体"样板生成

对应 next_doc/hybrid_exec_improvement_directions.md B1。

背景：`TaskSpec` 定义好之后，把它接进某个可运行入口（"执行载体"）目前
完全靠手写——独立脚本、workflow 的 `hybrid_step` 节点、外部项目的
`entrypoints/*.py`，三种形态各自的样板代码此前都没有可以直接抄的模板。
本模块只负责"生成骨架代码文本"，不做任何文件系统写入/CLI 参数解析
（那部分留给 `cli.py::_cmd_scaffold`），方便被测试和被其它调用方
（比如未来的对话式 skill）复用。
"""

from __future__ import annotations

CARRIER_CHOICES = ("script", "workflow-step", "entrypoint")


def _script_template(task_id: str, desc: str) -> str:
    return f'''#!/usr/bin/env python
"""独立可运行脚本：用 hybrid_exec 执行任务 {task_id!r}。

由 `mini-agent hybrid-exec scaffold` 生成，参考
next_doc/hybrid_exec_improvement_directions.md B1。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from mini_agent.hybrid_exec import TaskSpec, default_executor

# TODO：如果这段脚本本身就位于目标项目根目录下，可以直接用
# `Path(__file__).resolve().parent`；如果是独立于目标项目存放的脚本，
# 改成显式指向目标项目根目录的路径。
PROJECT_ROOT = Path(__file__).resolve().parent


def build_input_data() -> dict:
    """TODO：这里决定 input_data 怎么来——命令行参数 / stdin / 读文件都可以，
    参考 `mini-agent hybrid-exec run` 的 `_resolve_input_data()`
    （src/mini_agent/hybrid_exec/cli.py）里的几种做法。当前先给一个最简单
    的占位实现：从 argv[1] 读一段 JSON 字符串，没传则用空 dict。"""
    if len(sys.argv) > 1:
        return json.loads(sys.argv[1])
    return {{}}


def main() -> int:
    executor = default_executor(PROJECT_ROOT)
    result = executor.run(
        TaskSpec(
            task_id={task_id!r},
            description={desc!r},
            input_data=build_input_data(),
            # TODO：按需补充 output_validator / allow_tiers 等其它字段，
            # 见 hybrid_exec/spec.py::TaskSpec 的字段说明。
        )
    )
    print(f"ok={{result.ok}} tier_used={{result.tier_used.value}} "
          f"script_version={{result.script_version}}")
    if isinstance(result.output, (dict, list)):
        print(json.dumps(result.output, ensure_ascii=False, indent=2))
    else:
        print(result.output)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _workflow_step_template(task_id: str, desc: str, step_id: str) -> str:
    return f'''# 由 `mini-agent hybrid-exec scaffold` 生成，可直接粘进某个 workflow
# yaml 的 steps 列表里。参考 next_doc/hybrid_exec_improvement_directions.md
# B1，完整字段说明见 hybrid_exec/workflow_integration.py 头部 docstring。
- id: {step_id}
  type: hybrid_step
  # TODO：如果这一步需要依赖上游 step 的输出作为 input_data 的一部分，
  # 在这里声明 depends_on，上游输出会被自动合并进 input_data["upstream"]。
  # depends_on: [some_upstream_step]
  params:
    task_id: {task_id}
    description: "{desc}"
    # TODO：额外的字面量输入（与上游合并），不需要就删掉这个 key。
    # input:
    #   hint: "..."
    allow_tiers: [script, llm, agent]
    max_script_repair_attempts: 2
    agent_fs_write_enabled: false
    # TODO：如果本任务应该返回 dict 且必须包含某些顶层 key，取消注释并
    # 填写，会被自动转换成一个 output_validator：
    # result_required_keys: []
    force_reexplore: false
'''


def _entrypoint_template(task_id: str, desc: str, entrypoint_key: str) -> str:
    return f'''#!/usr/bin/env python
"""entrypoints/{entrypoint_key}.py — 外部项目 entrypoint，内部用 hybrid_exec
完成任务 {task_id!r}。

由 `mini-agent hybrid-exec scaffold` 生成，参考
next_doc/hybrid_exec_improvement_directions.md B1（entrypoint 载体样板）
与 A2（`try_hybrid_exec` 降级包装）。

约定（对齐 external_projects_workspace_plan.md 原则二/三，与
.claude/skills/external-project-manager/templates/entrypoint.py.tmpl
风格保持一致）：
  - 必须能在完全没有 daemon 进程的情况下，被
    `python entrypoints/{entrypoint_key}.py` 或 OS 原生调度直接正确执行。
  - 必须用 `track_run()` 把这次执行记进 `.agent/run_status.jsonl`。
  - 用 `try_hybrid_exec()` 而不是直接 `default_executor()`：检测不到
    mini_agent 框架（比如换了台没装全依赖的机器）时不应该让整个
    entrypoint 直接崩掉，而是能退回下面 TODO 标注的兜底逻辑。
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

from mini_agent.external_projects.ledger import track_run  # noqa: E402
from mini_agent.hybrid_exec import TaskSpec  # noqa: E402
from mini_agent.hybrid_exec.optional import try_hybrid_exec  # noqa: E402


def run(argv: list) -> None:
    """TODO：真正的业务逻辑写在这里；argv 是 project.yaml 里 `params`
    按声明顺序拼出的位置参数（如果这个 entrypoint 没有声明 params，
    argv 通常为空）。"""
    task = TaskSpec(
        task_id={task_id!r},
        description={desc!r},
        # TODO：按需要把 argv / 环境里的其它数据拼进 input_data。
        input_data={{}},
    )
    result = try_hybrid_exec(task, project_root=_PROJECT_ROOT)
    if result is None:
        # mini_agent 环境不可用（未安装/未正确配置）：退回一段不依赖
        # hybrid_exec 的兜底实现，或者按需要直接报错——取决于这个任务
        # 对 hybrid_exec 的依赖是"核心手段"还是"锦上添花"。
        raise NotImplementedError(
            "TODO: mini_agent 环境不可用时的兜底逻辑（或者去掉这一行，"
            "改成直接依赖 hybrid_exec 并让失败原样抛出）"
        )
    if not result.ok:
        raise RuntimeError(f"hybrid_exec 任务 {{task.task_id}} 未成功：tier_used={{result.tier_used.value}}")
    # TODO: 用 result.output 做后续处理


def main() -> int:
    with track_run(_PROJECT_ROOT, "{entrypoint_key}", trigger="manual") as handle:
        run(sys.argv[1:])
        # 如果想在成功时也留一句摘要（可选），可以设置：
        # handle.detail = "本次处理了 N 条记录"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def render_scaffold(carrier: str, task_id: str, desc: str) -> str:
    """按 `carrier` 生成对应样板代码文本。`carrier` 必须是
    `CARRIER_CHOICES` 之一，调用方（`cli.py`）负责校验，这里不重复校验
    以保持这个函数纯粹（输入合法 → 输出文本，不做参数防御）。"""
    if carrier == "script":
        return _script_template(task_id, desc)
    if carrier == "workflow-step":
        return _workflow_step_template(task_id, desc, step_id=task_id)
    if carrier == "entrypoint":
        return _entrypoint_template(task_id, desc, entrypoint_key=task_id)
    raise ValueError(f"未知的 carrier：{carrier!r}，可选值：{CARRIER_CHOICES}")


def default_output_filename(carrier: str, task_id: str) -> str:
    """`--output` 未指定时的默认文件名（相对当前工作目录）。"""
    if carrier == "script":
        return f"{task_id}_hybrid_exec.py"
    if carrier == "workflow-step":
        return f"{task_id}_hybrid_step.yaml"
    if carrier == "entrypoint":
        return f"{task_id}.py"
    raise ValueError(f"未知的 carrier：{carrier!r}，可选值：{CARRIER_CHOICES}")
