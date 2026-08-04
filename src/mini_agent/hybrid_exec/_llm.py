"""
hybrid_exec/_llm.py — 构造 LLMHelper 的共享辅助函数

与 workflow/py_step_runner.py::_build_llm_helper 走同一条 load_config()
路径，保证 provider/model/api_key 解析规则与主 Agent、python_step 一致，
不额外发明一套配置解析逻辑。仅供 hybrid_exec 包内部使用。
"""

from __future__ import annotations

from pathlib import Path

from .runner import RunnerAppConfig


def build_llm_helper(app_cfg: RunnerAppConfig):
    from mini_agent.config import load_config
    from mini_agent.llm.service import LLMHelper

    cfg = load_config(
        project_root=Path(app_cfg.project_root),
        verbose=False,
        sandbox=app_cfg.sandbox,
        auto_approve=True,
        model=app_cfg.model,
        llm_provider=app_cfg.llm_provider,
        llm_base_url=app_cfg.llm_base_url,
        debug_llm=app_cfg.debug_llm,
        debug_llm_console=app_cfg.debug_llm_console,
    )
    if app_cfg.api_key:
        cfg.api_key = app_cfg.api_key
    return LLMHelper.from_config(cfg)
