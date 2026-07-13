"""
mini_agent.agent 包。

对外保持与拆分前完全一致的导入路径：
    from mini_agent.agent import Agent

内部实现已按职责拆分到同目录下的多个文件（core / lifecycle / reflection /
profile / llm_control / turn_loop / role_judge / reminders_correction /
compaction / snapshot / _helpers），详见 core.py 顶部说明。

同时重导出少量原本是模块级私有函数、但被测试直接引用的符号
（如 `_parse_timeline_summary`），避免拆分影响现有测试的导入路径。
"""

from mini_agent.llm import create_client

from mini_agent.agent.core import Agent
from mini_agent.agent._helpers import (
    _term_write_lock_ctx,
    _NullCtx,
    _locked_print_info,
    _locked_print_warning,
    _is_tool_error,
    _clamp_confidence,
    _parse_lesson_candidates,
    _parse_timeline_summary,
)

__all__ = ["Agent"]
