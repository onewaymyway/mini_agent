"""
workflow/resource_bundle.py — 工作流本地 Agent/Skill 资源包
（workflow_directory_mode_design.md 阶段3）

文件夹模式的 workflow（source_dir 不为 None）可以在
  <source_dir>/agents/*.md    — 私有 agent profile（同 .agent/agents 格式）
  <source_dir>/skills/*/      — 私有 skill（同 .claude/skills 格式）
下放置只属于该 workflow 的资源。WorkflowResourceBundle 在一次
WorkflowRunner.run() 开始时构造一次，合并全局/项目级资源与本地资源
（本地同名覆盖），供各 step 执行时使用：

  - AgentStepExecutor（主 Agent）：把 bundle 传给 Agent()，使
    spawn_named_agent / skill 触发能看到本地资源。
  - RoleAgentStepExecutor：解析 step.role 时优先查 bundle 的 agent_loader。
  - SkillAgentStepExecutor：解析 step.skill_name 时优先查 bundle 的
    skill_loader。

子工作流（sub_workflow 类型）不继承父级 bundle：每个 WorkflowRunner
实例按自己执行的 wf.source_dir 独立构造，避免跨工作流的隐式资源泄漏。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


class WorkflowResourceBundle:
    """某次 workflow 运行期间使用的本地 agent/skill 资源合集。"""

    def __init__(self, cfg, source_dir: Path) -> None:
        self.source_dir = source_dir
        self.agent_loader = self._build_agent_loader(cfg, source_dir)
        self.skill_loader = self._build_skill_loader(cfg, source_dir)

    @staticmethod
    def _build_agent_loader(cfg, source_dir: Path):
        from mini_agent.orchestrator.agent_profiles import AgentProfileLoader
        from mini_agent.storage.paths import AgentPaths

        paths = AgentPaths(Path(getattr(cfg, "project_root", None) or Path.cwd()))
        # 顺序即优先级：后面的目录同名覆盖前面的，本地目录放最后优先级最高。
        dirs = [paths.global_agents_dir, paths.project_agents_dir, source_dir / "agents"]
        return AgentProfileLoader(dirs)

    @staticmethod
    def _build_skill_loader(cfg, source_dir: Path):
        from mini_agent.skills import SkillLoader

        dirs = []
        global_skills_dir = getattr(cfg, "skills_dir", None)
        if global_skills_dir:
            dirs.append(Path(global_skills_dir))
        local_skills_dir = source_dir / "skills"
        dirs.append(local_skills_dir)
        # SkillLoader 内部会跳过不存在的目录，这里不需要预先过滤。
        return SkillLoader(dirs)

    def get_agent_profile(self, name: str):
        """按名称查本地/合并后的 agent profile，找不到返回 None。"""
        return self.agent_loader.get(name)

    def get_skill(self, name: str):
        """按名称查本地/合并后的 skill，找不到返回 None。"""
        return self.skill_loader._all.get(name)


def build_resource_bundle(cfg, wf) -> Optional[WorkflowResourceBundle]:
    """wf.source_dir 为 None（单文件模式）时返回 None，调用方各处需判空。"""
    source_dir = getattr(wf, "source_dir", None)
    if source_dir is None:
        return None
    try:
        return WorkflowResourceBundle(cfg, Path(source_dir))
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where="mini_agent.workflow.resource_bundle.build_resource_bundle")
        return None
