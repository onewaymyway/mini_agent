"""
tests/conftest.py — 测试基础设施：隔离 Path.home()，避免测试套件污染真实
执行机器的 ~/.agent/ 目录。

背景：Agent 的 global 数据（global_memory.jsonl、self_profile.json、
projects_index.json、activity_log.jsonl 等）全部落在 Path.home() / ".agent"
下，没有依赖注入入口（这是设计上的合理选择——生产代码里"agent 的 home 在
哪"本身就该是真实的 Path.home()，不应该为了可测试性而引入配置项）。

这意味着任何在测试里真实构造 Agent（而不是 Agent.__new__ 手动拼字段）的
测试，都会不经意间在运行测试的机器上写入真实 ~/.agent/ 文件——这个问题
在 Stage 1-4（global_memory.jsonl）就已经存在，Stage 5（self_profile.json /
projects_index.json / activity_log.jsonl）让它更容易被注意到。

用一个 autouse fixture 在每个测试函数级别把 Path.home() 重定向到一个
每次测试独立的临时目录，一次性修复全部测试文件，不需要逐个文件添加
isolation fixture（那样会有 9+ 个文件需要改动，且容易遗漏新增测试）。

不影响任何测试断言本身——所有现有测试都只关心"功能是否正确"，没有任何
测试依赖"必须是同一个固定的 home 路径"，重定向到任意临时目录都是安全的。
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_agent_home(tmp_path_factory, monkeypatch):
    """
    每个测试函数自动获得一个独立的虚拟 home 目录，Path.home() 在测试期间
    始终返回它，杜绝任何真实 Agent 构造意外写入运行测试机器的真实
    ~/.agent/ 目录。

    用 tmp_path_factory（而不是 tmp_path fixture）是因为部分测试自己也
    使用 tmp_path 作为 project_root，与 home 目录分开存放更接近真实场景
    （project_root 和 home 通常是两个不同路径），避免两者意外重叠导致
    误判。
    """
    fake_home = tmp_path_factory.mktemp("agent_home")
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    yield fake_home


@pytest.fixture(autouse=True)
def _reset_debug_logger_state():
    """
    自动重置 debug_logger 模块级全局状态，确保测试间隔离。
    重置：
    - 全局序列号计数器 (_seq)
    - 默认单例 logger (_default_logger)
    """
    from mini_agent.llm import debug_logger
    debug_logger.reset_global_state()
    yield
    debug_logger.reset_global_state()


@pytest.fixture(autouse=True)
def _close_all_resources():
    """
    测试结束后显式关闭测试期间创建的文件句柄资源，防止 Windows 下
    TemporaryDirectory 清理时出现 PermissionError (WinError 32)。
    
    只关闭测试代码创建的 handlers（如 llm_debug_* loggers），
    不触及 root logger 或 pytest 自身的 handlers。
    """
    import logging
    
    # 记录测试前已存在的 logger handlers（不关闭这些）
    pre_existing_handlers = {}
    for name in list(logging.Logger.manager.loggerDict.keys()):
        logger = logging.getLogger(name)
        pre_existing_handlers[name] = list(logger.handlers)
    
    # 记录 root logger 原有 handlers
    root_logger = logging.getLogger()
    pre_existing_root_handlers = list(root_logger.handlers)
    
    yield
    
    # 1. 只关闭测试期间新增的 handlers（llm_debug_* 等测试专用 loggers）
    for name in list(logging.Logger.manager.loggerDict.keys()):
        if not name.startswith('llm_debug_'):
            continue
        logger = logging.getLogger(name)
        for handler in logger.handlers[:]:
            # 只关闭不在 pre_existing 中的 handlers
            if name in pre_existing_handlers and handler in pre_existing_handlers[name]:
                continue
            try:
                handler.close()
            except Exception:
                pass
            logger.removeHandler(handler)
    
    # 2. 关闭已知的全局资源管理器
    try:
        from mini_agent.tools.orchestration import _task_manager
        if _task_manager is not None:
            _task_manager.shutdown()
    except Exception:
        pass
    
    try:
        from mini_agent.llm.debug_logger import _default_logger
        if _default_logger is not None:
            if hasattr(_default_logger, '_py_logger') and _default_logger._py_logger:
                for h in _default_logger._py_logger.handlers[:]:
                    try:
                        h.close()
                    except Exception:
                        pass
                    _default_logger._py_logger.removeHandler(h)
    except Exception:
        pass
    
    # 3. 仅做轻量 GC，不主动关闭文件对象（避免关闭已在使用的文件）
    import gc
    gc.collect()