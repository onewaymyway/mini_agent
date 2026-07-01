"""
cli/commands — slash 命令处理模块包

每个子模块负责一组语义相关的 slash 命令：
  skills      — /skills  /skill on|off|info|stats|reset
  sessions    — /session
  tasks       — /tasks
  plans       — /plan
  concurrency — /concurrency (/cc)
  providers   — /provider
  evolution   — /evolution log|show|diff|revert（Stage 2：自我演化安全网）
  evolve      — /evolve review|list（Stage 3.1：lesson → skill 提案闭环）
  goals       — /agent goals|/goals（Stage 9：Goal Backlog）
  debug_cmd   — /debug system|history|all|save（打印/导出 system prompt 与 history，便于分析调试）
"""

from mini_agent.cli.commands.skills import handle_skills_list, handle_skill_cmd
from mini_agent.cli.commands.sessions import handle_session_cmd
from mini_agent.cli.commands.tasks import handle_tasks_cmd
from mini_agent.cli.commands.plans import handle_plan_cmd
from mini_agent.cli.commands.concurrency import handle_concurrency_cmd
from mini_agent.cli.commands.providers import handle_provider_cmd
from mini_agent.cli.commands.agents import handle_agents_cmd
from mini_agent.cli.commands.hooks import handle_hooks_cmd
from mini_agent.cli.commands.evolution import handle_evolution_cmd
from mini_agent.cli.commands.evolve import handle_evolve_cmd
from mini_agent.cli.commands.goals import handle_goals_cmd
from mini_agent.cli.commands.debug_cmd import handle_debug_cmd

__all__ = [
    "handle_skills_list",
    "handle_skill_cmd",
    "handle_session_cmd",
    "handle_tasks_cmd",
    "handle_plan_cmd",
    "handle_concurrency_cmd",
    "handle_provider_cmd",
    "handle_agents_cmd",
    "handle_hooks_cmd",
    "handle_evolution_cmd",
    "handle_evolve_cmd",
    "handle_goals_cmd",
    "handle_debug_cmd",
]
