"""
config/prompt_builder.py — system prompt 构建逻辑

拆分自原 config.py（v3，对应 self_evolution_implementation_plan.md Stage 0.4）。
本文件只放"把 AppConfig + 运行时上下文渲染成最终 system prompt 文本"的逻辑：
  - build_system_prompt()    — 主入口，委托给 mini_agent.prompts.pm 做实际拼装
  - _read_claude_md()        — 读取项目 CLAUDE.md 上下文文档
  - _resolve_prompts_dir()   — 解析自定义 prompts 目录
  - _resolve_skills_dir()    — 解析自定义 skills 目录

数据结构定义在 config/models.py；加载逻辑在 config/loader.py。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .models import AppConfig




def _read_claude_md(root: Path, filename: str = "CLAUDE.md") -> str:
    """读取项目上下文文档。

    Args:
        root: 项目根目录。
        filename: 要加载的文档名（默认 CLAUDE.md）。
                  文件不存在时返回空字符串，不抛出异常。
    """
    for d in [root] + list(root.parents)[:3]:
        p = d / filename
        if p.exists():
            try:
                return p.read_text(encoding="utf-8")
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.config.prompt_builder')
                pass
    return ""


def _resolve_prompts_dir(root: Path) -> Optional[Path]:
    """
    解析用户自定义 prompts 目录。

    查找顺序（优先级从高到低）：
    1. <project_root>/.agent/prompts/   — 项目级自定义 prompt
    2. ~/.agent/prompts/                — 全局自定义 prompt

    若均不存在，返回 None，PromptManager 将仅使用项目内置默认 prompts 目录
    （src/mini_agent/prompts/）。
    """
    from mini_agent.storage.paths import AgentPaths
    paths = AgentPaths(root)
    for c in (paths.workdir_prompts_dir, paths.global_prompts_dir):
        if c.is_dir():
            return c
    return None


def _resolve_skills_dir(root: Path) -> Optional[Path]:
    from mini_agent.storage.paths import AgentPaths
    paths = AgentPaths(root)
    candidates = []
    # [external_projects_workspace_plan.md 5.1] 外部项目（`<root>/project.yaml`
    # 存在即视为外部项目根，与 `WorkflowStore`/`Workspace.project_yaml_path`
    # 的判定标准一致）的项目私有 skill 放在 `<root>/skills/`，不是
    # `<root>/.claude/skills/`——外部项目是"引擎的调用方"，不应该借用
    # mini_agent 交互式会话自己那套 `.claude/` 目录；普通交互式项目目录
    # （没有 project.yaml）行为完全不变，仍然优先 `.claude/skills/`。
    if (root / "project.yaml").exists():
        candidates.append(root / "skills")
    candidates.extend([
        root / ".claude" / "skills",           # 旧路径，兼容保留
        paths.global_skills_dir,               # ~/.agent/skills（新路径）
        Path.home() / ".claude" / "skills",    # 旧全局路径，兼容保留
    ])
    for c in candidates:
        if c.is_dir():
            return c
    return None


def build_system_prompt(
    cfg: AppConfig,
    active_skills: list[str],
    skill_context: str = "",
    user_profile: str = "",
    session_id: Optional[str] = None,
) -> str:
    from datetime import datetime
    from mini_agent.prompts import pm
    if cfg.prompts_dir and pm.custom_dir != cfg.prompts_dir:
        pm.set_custom_dir(cfg.prompts_dir)

    # 采集环境信息
    env_info_block = ""
    if cfg.env_info.enabled:
        try:
            from mini_agent.env_info.registry import EnvInfoRegistry
            registry = EnvInfoRegistry.from_config(
                providers=cfg.env_info.providers,
                provider_kwargs=cfg.env_info.provider_kwargs,
            )
            env_info_block = registry.build_block()
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.config.prompt_builder')
            pass

    # Session 级 temp/output 目录（用户未指定目标目录时的默认落地位置）。
    # 目录本身已在 session 初始化时（agent/lifecycle.py::_bind_session_extras）
    # 创建好，这里只负责推导绝对路径字符串注入 system prompt，不重复建目录。
    temp_dir_str = ""
    output_dir_str = ""
    if session_id:
        try:
            from mini_agent.storage.paths import AgentPaths
            paths = AgentPaths(cfg.project_root)
            temp_dir_str = str(paths.session_temp_dir(session_id))
            output_dir_str = str(paths.session_output_dir(session_id))
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.config.prompt_builder')
            pass

    return pm.build_system_prompt(
        claude_md_content=cfg.claude_md_content,
        active_skills=active_skills,
        skill_context=skill_context,
        system_extra=cfg.system_extra,
        sandbox=cfg.sandbox,
        current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S %A"),
        agent_name=cfg.agent_name,
        user_profile=user_profile,
        env_info=env_info_block,
        temp_dir=temp_dir_str,
        output_dir=output_dir_str,
    )
