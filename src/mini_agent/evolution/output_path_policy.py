"""
evolution/output_path_policy.py — 产出路径规范（用户可编辑）

[goal_cron_feedback_and_output_policy_plan.md 第5节]

规范文件路径：<project_root>/.agent/policies/output_path_policy.md

本模块只负责：
  - 首次不存在时幂等写入内置默认模板（仿照 cron_job_workspace.py 的
    "已存在文件不覆盖"模式，用户后续可以直接编辑这个文件，不需要改代码）；
  - 读取当前内容（含用户改动），供各执行路径统一注入 prompt。

本轮不做路径规范的强制拦截（不在 hook 里硬拦截写 src/ 的工具调用），只做
prompt 层面的规则注入，避免误伤"用户确实特殊说明要改 src/ 下代码"的合法场景。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths


DEFAULT_POLICY = """# 产出路径规范

在没有特殊说明的情况下，执行任务时请遵守：

1. 禁止把产出的代码写入主项目 `src/` 目录。
2. 禁止把产出的代码写入 `tests/` 目录。
3. 和 skill 相关的产出，放到对应 skill 的目录下。
4. 任务本身已经说明了工作目录的，产出放到该工作目录下。
5. 如果任务描述里出现了"本轮产出请写入：<目录>"这一行（周期性 Goal/
   CronJob 每轮自动附加，见 outputs/ 目录规范），以该目录为准，优先级
   高于本规范其他各条。

如果任务描述中明确要求修改 `src/`、`tests/` 或指定了其他路径，以任务描述的
明确说明为准，本规范不覆盖显式指令。
"""


def policy_path(paths: "AgentPaths") -> Path:
    return Path(paths.project_root) / ".agent" / "policies" / "output_path_policy.md"


def ensure_policy_file(paths: "AgentPaths") -> Path:
    """幂等创建规范文件：已存在则不覆盖（用户可能已编辑过）。"""
    path = policy_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(DEFAULT_POLICY, encoding="utf-8")
    return path


def load_policy(paths: "AgentPaths") -> str:
    """读取当前规范内容（含用户改动）；文件不存在时先幂等创建再读取。"""
    path = ensure_policy_file(paths)
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_POLICY.strip()
