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
  evolve      — /evolve review|list|consolidate|timeline（Stage 3.1：lesson → skill 提案闭环 + 巩固循环知识整备，旧名 phase-g）
  goals       — /agent goals|/goals（Stage 9：Goal Backlog）
  debug_cmd   — /debug system|history|all|save（打印/导出 system prompt 与 history，便于分析调试）
  platform    — /platform status|filtered|reload（可加载对象的平台/tag 过滤策略查看与重载）
  quarantine  — /quarantine status|list|remove|clear|reload|enable|disable
                （运行时自动屏蔽：skill/tool/agent 因反复环境不兼容失败被自动拉黑，默认关闭）
  roles       — /role list|use|show|exit|status|reload（角色扮演 Persona 系统）
  proxy       — /proxy status|refresh|sources [add-mibei77|add-discovered]|integration [set <key> <value>]
                （代理订阅池：抓取/验证/查看可用节点、可扩展订阅源类型、接入其它模块的开关，懒加载于 repl.py）
  behavior    — /behavior status|on|off|enable|disable|token|recent|clear
                （用户行为感知系统：前台窗口/空闲/浏览器插件上报，默认全部关闭）
  wiki        — /wiki <page-id>|list|search|rebuild（wiki式知识库重构计划阶段四：
                人工浏览页面/backlinks、三段式检索 A/B 对比、手动索引重建）
  recall      — /recall <query>（compact_mechanism_improvement_plan.md P2-B：
                手动检索被 compact 掉的原始 raw history 片段）
"""

from mini_agent.cli.commands.skills import handle_skills_list, handle_skill_cmd
from mini_agent.cli.commands.sessions import handle_session_cmd
from mini_agent.cli.commands.tasks import handle_tasks_cmd
from mini_agent.cli.commands.plans import handle_plan_cmd
from mini_agent.cli.commands.notepad import handle_notepad_cmd
from mini_agent.cli.commands.concurrency import handle_concurrency_cmd
from mini_agent.cli.commands.providers import handle_provider_cmd
from mini_agent.cli.commands.agents import handle_agents_cmd
from mini_agent.cli.commands.hooks import handle_hooks_cmd
from mini_agent.cli.commands.platform import handle_platform_cmd
from mini_agent.cli.commands.quarantine import handle_quarantine_cmd
from mini_agent.cli.commands.evolution import handle_evolution_cmd
from mini_agent.cli.commands.evolve import handle_evolve_cmd
from mini_agent.cli.commands.goals import handle_goals_cmd
from mini_agent.cli.commands.goal_mode_cmd import handle_goal_cmd
from mini_agent.cli.commands.debug_cmd import handle_debug_cmd
from mini_agent.cli.commands.roles import handle_role_cmd
from mini_agent.cli.commands.behavior import handle_behavior_cmd
from mini_agent.cli.commands.wiki import handle_wiki_cmd
from mini_agent.cli.commands.recall import handle_recall_cmd
from mini_agent.cli.commands.digest_cmd import handle_digest_cmd
from mini_agent.cli.commands.next_action_cmd import handle_next_action_cmd
from mini_agent.cli.commands.profile_cmd import handle_profile_cmd

__all__ = [
    "handle_behavior_cmd",
    "handle_wiki_cmd",
    "handle_skills_list",
    "handle_skill_cmd",
    "handle_session_cmd",
    "handle_tasks_cmd",
    "handle_plan_cmd",
    "handle_notepad_cmd",
    "handle_concurrency_cmd",
    "handle_provider_cmd",
    "handle_agents_cmd",
    "handle_hooks_cmd",
    "handle_platform_cmd",
    "handle_quarantine_cmd",
    "handle_evolution_cmd",
    "handle_evolve_cmd",
    "handle_goals_cmd",
    "handle_goal_cmd",
    "handle_debug_cmd",
    "handle_role_cmd",
    "handle_recall_cmd",
    "handle_digest_cmd",
    "handle_next_action_cmd",
    "handle_profile_cmd",
]
