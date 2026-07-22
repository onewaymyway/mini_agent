"""
plugins.py — myplugins/ 目录插件发现机制
（[P7-④2 workflow_mechanism_improvement_plan.md]）

背景：`myplugins/` 目录此前只是一个"约定俗成"的存放位置——比如
`myplugins/git_info.py` 里的 `GitInfoProvider` 需要在 agent_config.json
的 `env_info.providers` 里手写完整类路径（`"myplugins.git_info.GitInfoProvider"`）
才会被 `EnvInfoRegistry.from_config()` 加载，没有"放进 myplugins/ 就自动
生效"的机制。P7-④1 新增的 `register_step_executor()` 同理——一个自定义
Step Executor 插件如果每次都要求用户去改 core 包源码调用注册函数，插件化
就没有意义。

本模块提供的 `discover_and_register_plugins(cfg)`：
  1. 扫描 `<project_root>/myplugins/*.py`（不含以 `_` 开头的文件，
     便于放置 `_helpers.py` 这类不希望被当作插件入口的辅助模块）
  2. 逐个 import
  3. 若模块定义了顶层 `register(cfg)` 函数，调用它——这是插件的统一入口，
     与 `workflow/tools.py::register_workflow_tools(cfg)` 的调用时机/签名
     保持一致，插件内部可以在这里调用
     `mini_agent.workflow.executors.register_step_executor(...)`、
     注册自定义工具、或做任何其它启动期初始化。

单个插件加载或 register() 调用失败只记录警告并跳过，不影响其余插件与
主程序启动——`myplugins/` 里的文件质量不受核心包控制，一个插件写挂了
不应该导致整个 Agent 起不来。
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def discover_and_register_plugins(cfg: Any) -> list[str]:
    """
    扫描 `<project_root>/myplugins/*.py` 并调用各模块的 `register(cfg)`。

    返回成功加载（存在且成功调用 register()，或存在但没有 register()
    函数——纯提供 EnvInfoProvider 之类"被动引用"型插件，无需调用）的
    模块名列表，供调用方（cli/app.py）打印启动日志。
    """
    project_root = Path(getattr(cfg, "project_root", None) or ".")
    plugins_dir = project_root / "myplugins"
    loaded: list[str] = []

    if not plugins_dir.is_dir():
        return loaded

    for py_file in sorted(plugins_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        module_name = f"myplugins.{py_file.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where="mini_agent.plugins.discover_and_register_plugins")
            logger.warning("[plugins] 加载 myplugins/%s 失败：%s", py_file.name, e)
            continue

        register_fn = getattr(module, "register", None)
        if callable(register_fn):
            try:
                register_fn(cfg)
            except Exception as e:
                from mini_agent.errors import log_exception
                log_exception(e, where="mini_agent.plugins.discover_and_register_plugins")
                logger.warning("[plugins] myplugins/%s 的 register(cfg) 调用失败：%s", py_file.name, e)
                continue
        loaded.append(py_file.stem)

    return loaded
