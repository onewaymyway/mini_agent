"""
_engine（对外作为 "capability_sdk" 使用）
==========================================
跨领域可复用的 generative-capability 引擎 SDK 打包入口。

对应文档: next_doc/generative-capability-skill-plan.md 阶段五
  ——"抽象出跨领域复用的引擎 SDK，支持第二个 generative-capability skill 落地，
     验证方案泛化性"。

背景
----
`capability_engine.py` / `distiller.py` / `explorer_runtime.py` /
`health_patrol.py` / `llm_resolver.py` / `schema_validator.py` /
`tool_runtime.py` 内部一直用"flat 模块名"互相 import
（例如 `from explorer_runtime import ExploreStep`），这是阶段一到阶段四
迭代过程中留下的过渡写法：只要求 `_engine` 这个目录本身被塞进
`sys.path`，模块之间就能互相 `import`。

这在"单个 skill 内部"没问题——`capability_engine.py` 自己在需要时动态把
`_engine` 目录插入 `sys.path`。但阶段五的目标是验证"同一套引擎能否让第二个
generative-capability skill 落地、且不改动引擎代码"，如果每新增一个 skill
的驱动脚本都要手写同样的 `sys.path.insert(...)` + 认识 `capability_engine`
这个内部模块名，就不是一个真正意义上的"SDK"，只是又多了一处样板代码，
而且调用方被迫了解引擎的内部模块拆分方式（哪个类在哪个文件里）。

本文件把 `_engine` 目录变成一个可以被直接 `import` 的包，解决的是"调用方
要不要自己写 sys.path hack、要不要认识内部模块名"这一层问题，**不**改变
`capability_engine.py` 等模块内部任何调度/状态机逻辑：
  1. 先把自身目录插入 `sys.path`（这样包内各模块原有的 flat import 语句
     不需要改一行，不引入行为变化风险）；
  2. 再从这里统一 re-export 一份精简的公开 API，作为其他 skill 唯一需要
     认识的入口。

使用方式
--------
任何 generative-capability skill 的驱动代码（无论是预置 member 之外的
调用脚本，还是宿主 agent 框架里的接入点）只需要：

    import sys
    from pathlib import Path

    # 假设当前文件位于 .claude/skills/<some-skill>/somewhere.py，
    # `_engine` 与 `<some-skill>` 是同级目录（约定见 SKILL.md 标准目录规范）。
    _SKILLS_DIR = Path(__file__).resolve().parents[1]
    if str(_SKILLS_DIR) not in sys.path:
        sys.path.insert(0, str(_SKILLS_DIR))

    from _engine import CapabilityEngine, build_llm_resolver, build_llm_explorer

    engine = CapabilityEngine(
        skill_dir=_SKILLS_DIR / "some-generative-capability-skill",
        llm_resolver=build_llm_resolver(),
        explore_runner=build_llm_explorer(),
        tool_executor=my_tool_executor,   # 领域自己的底层原语执行器
    )
    result = engine.call({"text": "...", "target": {...}})

调用方只需要认识本文件 re-export 的这些名字，不需要知道
`capability_engine.py`/`distiller.py`/`explorer_runtime.py` 等内部文件划分，
后续引擎内部再拆分/合并文件也不会影响调用方代码——这正是"SDK"这个词在本方案
里的含义："流程与领域分离"（第1节设计原则）落到工程上的最后一步：调用方与
实现细节之间隔着一层稳定的公开接口。
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_ENGINE_DIR = _Path(__file__).resolve().parent
if str(_ENGINE_DIR) not in _sys.path:
    # 见上方模块 docstring："背景"一节 —— 包内模块之间仍是 flat import，
    # 这一行让它们在被当作包 import 时也能找到彼此，且不需要改动那些文件。
    _sys.path.insert(0, str(_ENGINE_DIR))

from capability_engine import (  # noqa: E402
    CapabilityEngine,
    ResolveResult,
    ExecuteResult,
    CapabilityCallResult,
)
from llm_resolver import build_llm_resolver, build_stub_resolver  # noqa: E402
from explorer_runtime import (  # noqa: E402
    ExploreStep,
    ExploreTrace,
    build_llm_explorer,
    build_stub_explorer,
)
from distiller import distill, DistillResult  # noqa: E402
from schema_validator import validate as validate_schema  # noqa: E402
from health_patrol import run_patrol, PatrolReport, PatrolFinding  # noqa: E402
from tool_runtime import set_tool_executor, get_tool_executor  # noqa: E402

__all__ = [
    # 调度引擎主体
    "CapabilityEngine",
    "ResolveResult",
    "ExecuteResult",
    "CapabilityCallResult",
    # 第二级 LLM 检索裁决
    "build_llm_resolver",
    "build_stub_resolver",
    # 探索子agent
    "ExploreStep",
    "ExploreTrace",
    "build_llm_explorer",
    "build_stub_explorer",
    # 蒸馏固化
    "distill",
    "DistillResult",
    # schema 校验
    "validate_schema",
    # 健康巡检
    "run_patrol",
    "PatrolReport",
    "PatrolFinding",
    # 蒸馏脚本运行时工具执行器注入点
    "set_tool_executor",
    "get_tool_executor",
]

__sdk_version__ = "0.5.0"  # 对应方案文档阶段五
