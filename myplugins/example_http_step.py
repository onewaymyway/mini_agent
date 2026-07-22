# myplugins/example_http_step.py
#
# 示例插件：演示如何通过 register_step_executor() 新增一种自定义
# workflow step 类型（P7-④1/④2 workflow_mechanism_improvement_plan.md）。
#
# 默认不会做任何事——只有 myplugins/ 目录被扫描到、且 workflow YAML 里
# 真的写了 `type: http` 的 step 才会被用到。如果不需要这个示例类型，
# 删除本文件即可，不影响其它插件或核心功能。
"""
用法示例（workflow YAML）：

    steps:
      - id: fetch
        type: http
        tool_args:
          url: "https://example.com/api/status"
        prompt: "（http 类型不使用 prompt，占位即可）"
"""

from __future__ import annotations

from mini_agent.workflow.executors import StepExecutor, register_step_executor
from mini_agent.workflow.schema import WorkflowStep


class HttpStepExecutor(StepExecutor):
    """type=http：直接发一次 HTTP GET 请求，把响应文本作为该 step 的输出。"""

    def execute(self, runner, step: WorkflowStep, prompt: str) -> str:
        import urllib.request

        url = (step.tool_args or {}).get("url")
        if not url:
            raise ValueError(f"步骤 {step.id!r} 是 http 类型但未在 tool_args.url 指定请求地址")

        timeout = step.timeout or 30.0
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310（示例代码，非生产用）
            return resp.read().decode("utf-8", errors="replace")

    def validate_step(self, step: WorkflowStep) -> list[str]:
        errors = []
        if not (step.tool_args or {}).get("url"):
            errors.append(f"步骤 {step.id!r} 是 http 类型但未指定 tool_args.url")
        return errors


def register(cfg) -> None:
    """myplugins/ 插件统一入口，由 mini_agent.plugins.discover_and_register_plugins() 调用。"""
    register_step_executor("http", HttpStepExecutor())
