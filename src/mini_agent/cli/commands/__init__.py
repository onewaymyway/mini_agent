"""
cli/commands — slash 命令处理模块包

每个子模块负责一组语义相关的 slash 命令：
  skills      — /skills  /skill on|off|info|stats|reset
  sessions    — /session
  tasks       — /tasks
  plans       — /plan
  concurrency — /concurrency (/cc)
  providers   — /provider
"""

from mini_agent.cli.commands.skills import handle_skills_list, handle_skill_cmd
from mini_agent.cli.commands.sessions import handle_session_cmd
from mini_agent.cli.commands.tasks import handle_tasks_cmd
from mini_agent.cli.commands.plans import handle_plan_cmd
from mini_agent.cli.commands.concurrency import handle_concurrency_cmd
from mini_agent.cli.commands.providers import handle_provider_cmd

__all__ = [
    "handle_skills_list",
    "handle_skill_cmd",
    "handle_session_cmd",
    "handle_tasks_cmd",
    "handle_plan_cmd",
    "handle_concurrency_cmd",
    "handle_provider_cmd",
]
