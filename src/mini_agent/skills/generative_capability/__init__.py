"""
mini_agent.skills.generative_capability
=========================================
Generative-Capability Skill 的通用调度引擎（平台内置，跨 skill 复用）。

对应文档: next_doc/generative-capability-skill-plan.md

阶段七迁移说明
--------------
本包在阶段一至阶段六里一直位于 `.claude/skills/_engine/`（skill 内容目录），
包内模块用"flat 模块名"互相 import（如 `from explorer_runtime import
ExploreStep`），依赖调用方手动把该目录塞进 `sys.path` 才能工作。这带来三个
实际问题（详见阶段七实施记录）：

  1. 引擎代码完全没有接入 `src/mini_agent` 的运行时——没有任何工具能让 agent
     真正调用到 `capability_call()`，此前所有验证都只能靠手动跑 CLI 自测入口。
  2. 没有进 `tests/` 目录的正常 pytest 覆盖，每次验证都是临时构造沙盒场景。
  3. `sys.path.insert(...)` 散落在多个函数入口里，是"这坨代码本不该待在
     skill 空间里"的直接症状。

阶段七把这五个 —— `capability_engine.py` / `distiller.py` /
`explorer_runtime.py` / `health_patrol.py` / `llm_resolver.py` /
`schema_validator.py` / `tool_runtime.py` —— 迁移到这里，作为
`src/mini_agent` 下的正常子包，包内模块间引用改为相对 import。
`.claude/skills/<capability-name>/` 目录下只保留声明式配置与运行时数据
（`SKILL.md` / `capability.yaml` / `explorer/prompt.md` /
`explorer/tool_allowlist.json` / `_index.json` / `registry.json` /
`members/`），这正是方案文档第 1 条设计原则"流程与领域分离"、第 9 节
"与静态 skill 系统的关系"表格里"调度引擎、状态机、检索逻辑全平台复用"
这句话在工程上的落地方式。

唯一保留的 `sys.path` 用法：`CapabilityEngine.execute()` 与
`distiller._sandbox_run()` 加载/自测的是 member 脚本文件（运行时按路径
`importlib` 动态加载的独立文件，不是本包的一部分），脚本内部引用
`tool_runtime` 用的仍是 flat import，因此这两处需要把本包目录塞进
`sys.path`，让 `tool_runtime` 能作为一个可被 flat import 的模块名被
脚本文件找到；这是运行时环境的必要机制，不是"图省事"的遗留写法。

调用方（`mini_agent.tools.capability_call` 是首个真实调用方）只需要：

    from mini_agent.skills.generative_capability import (
        CapabilityEngine, build_llm_resolver, build_llm_explorer,
    )
    from mini_agent.tools.orchestration import get_current_llm_helper

    llm_helper = get_current_llm_helper()  # 或直接传 agent.llm_helper
    engine = CapabilityEngine(
        skill_dir=some_skill_dir,       # 如 .claude/skills/browser-site-scraper
        llm_resolver=build_llm_resolver(llm_helper),
        explore_runner=build_llm_explorer(my_tool_executor, llm_helper),
        tool_executor=my_tool_executor,  # 领域自己的底层原语执行器
    )
    result = engine.call({"text": "...", "target": {...}})

不需要知道引擎内部具体拆成了哪几个文件，后续引擎内部再拆分/合并文件
也不会影响调用方代码。

阶段九改造说明
--------------
`llm_resolver.py`（第二级检索裁决）与 `explorer_runtime.py`（探索子agent
决策循环）此前各自用 urllib 直连 Anthropic Messages API，是整个引擎里仅有
的两处没有走框架统一 LLM 调用基础设施（`llm/service.py::LLMHelper`）的地方。
阶段九把两者改为接收调用方传入的 `llm_helper`（通常是 `Agent.llm_helper`，
可通过 `tools/orchestration.py::get_current_llm_helper()` 拿到当前线程正在
跑的 agent 实例），从而自动获得：跟随 `/model` 切换、`LLMClientPool` 的多
key/多配置 fallback、统一的 `RetryPolicy`、`call_stats` 调用计数——不再固定
写死 provider=anthropic。`build_llm_resolver()`/`build_llm_explorer()` 仍
支持不传 `llm_helper`、改传 `cfg` 的兜底用法（退化为
`LLMHelper.from_config(cfg)`，与 `ensemble/judge.py::judge_llm` 的既有约定
一致）。详见 next_doc/generative-capability-skill-plan.md 实施记录阶段九。
"""

from __future__ import annotations

from .capability_engine import (
    CapabilityEngine,
    ResolveResult,
    ExecuteResult,
    CapabilityCallResult,
)
from .llm_resolver import build_llm_resolver, build_stub_resolver
from .explorer_runtime import (
    ExploreStep,
    ExploreTrace,
    build_llm_explorer,
    build_stub_explorer,
)
from .distiller import distill, DistillResult
from .schema_validator import validate as validate_schema
from .health_patrol import run_patrol, PatrolReport, PatrolFinding
from .tool_runtime import set_tool_executor, get_tool_executor
from .real_tools import build_default_tool_executor, REAL_TOOL_IMPLEMENTATIONS

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
    # 真实底层操作原语注册表（阶段十二新增，目前只有 text-core 是真实实现）
    "build_default_tool_executor",
    "REAL_TOOL_IMPLEMENTATIONS",
]

__sdk_version__ = "0.7.0"  # 对应方案文档阶段七（迁入 src/mini_agent 正式子包）
