"""
mini_agent.config — 配置管理包

拆分自原 src/mini_agent/config.py 单文件（v3，对应
self_evolution_implementation_plan.md Stage 0.4）：
  - config/models.py         — 14 个配置 dataclass + AppConfig + 常量
  - config/loader.py         — load_config 及其加载辅助函数
  - config/prompt_builder.py — build_system_prompt 及其辅助函数

本文件统一重导出，保持外部代码现有的
`from mini_agent.config import AppConfig` / `from mini_agent.config import load_config`
等 import 路径完全不变 —— 调用方不需要关心内部是按文件拆分还是单文件。

注：`os` 也一并重导出。这不是配置模块本身的 API，而是历史遗留的隐藏耦合
（orchestrator/sub_agent.py 中有 `from mini_agent.config import os`，
直接借用了原 config.py 顶部 `import os` 留下的模块属性）。拆分后保留此重导出
纯粹是为了不破坏现有调用点；新代码不应再依赖这种写法，应直接 `import os`。
"""

from __future__ import annotations

import os  # noqa: F401  (历史隐藏依赖，见上方说明)

from .models import (
    # 常量
    DEFAULT_MODEL,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MAX_TURNS,
    DEFAULT_AGENT_NAME,
    # 运行时统计
    SessionStats,
    # 功能子配置块
    MemoryConfig,
    CompressConfig,
    ToolTrimConfig,
    SkillConfig,
    PerceptionConfig,
    ProfileConfig,
    SessionConfig,
    DebugConfig,
    HttpConfig,
    WebSearchConfig,
    RetryConfig,
    RoleAgentConfig,
    GoalModeConfig,
    EnvInfoConfig,
    ReminderConfig,
    ProprioceptionConfig,
    WorkflowConfig,
    FormatCorrectionConfig,
    WorkdirKnowledgeConfig,
    GlobalKnowledgeConfig,
    ObservabilityConfig,
    # 主配置
    AppConfig,
)
from .loader import (
    load_config,
    _parse_name_list,
    _load_config_file,
    _load_providers_config,
    _merge_providers_into_chain,
)
from .prompt_builder import (
    build_system_prompt,
    _read_claude_md,
    _resolve_prompts_dir,
    _resolve_skills_dir,
)

__all__ = [
    "os",
    "DEFAULT_MODEL",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_MAX_TURNS",
    "DEFAULT_AGENT_NAME",
    "SessionStats",
    "MemoryConfig",
    "CompressConfig",
    "ToolTrimConfig",
    "SkillConfig",
    "PerceptionConfig",
    "ProfileConfig",
    "SessionConfig",
    "DebugConfig",
    "HttpConfig",
    "WebSearchConfig",
    "RetryConfig",
    "RoleAgentConfig",
    "GoalModeConfig",
    "EnvInfoConfig",
    "ReminderConfig",
    "ProprioceptionConfig",
    "WorkflowConfig",
    "FormatCorrectionConfig",
    "WorkdirKnowledgeConfig",
    "GlobalKnowledgeConfig",
    "ObservabilityConfig",
    "AppConfig",
    "load_config",
    "_parse_name_list",
    "_load_config_file",
    "_load_providers_config",
    "_merge_providers_into_chain",
    "build_system_prompt",
    "_read_claude_md",
    "_resolve_prompts_dir",
    "_resolve_skills_dir",
]
