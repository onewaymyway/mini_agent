"""
hybrid_exec/optional.py — 面向外部项目的"检测不到就降级"标准包装

对应 next_doc/hybrid_exec_improvement_directions.md A2。

设计依据：与 `external_projects/ledger.py::track_run()` 同一套原则
（原则二："引擎能力是锦上添花，缺了不影响核心可独立运行"）——外部项目
的 entrypoint 如果想机会主义地用 hybrid_exec 处理"某类任务天然适合脚本
/LLM/Agent 混合兜底"的场景，不应该因为部署环境里恰好没装全 mini_agent
的可选依赖（比如没有 providers.json、缺 LLM 相关的可选包）就让整个
entrypoint 直接崩掉——那样退化成了"要么全量依赖 mini_agent、要么完全
不能用"，与 `ledger`/`backlog` 已经确立的"能力增强、缺了也无所谓"的
容错姿势不一致。

用法：

    from mini_agent.hybrid_exec.optional import try_hybrid_exec
    from mini_agent.hybrid_exec import TaskSpec

    result = try_hybrid_exec(
        TaskSpec(task_id="fix_scraper_v1", description="...", input_data={...}),
        project_root=".",
    )
    if result is None:
        # mini_agent 环境不可用（未安装/未正确配置），退回自己手写的兜底逻辑
        do_plain_business_logic()
    elif result.ok:
        use(result.output)
    else:
        # 环境可用、但这次任务本身没跑成功（脚本/LLM/Agent 都没给出通过
        # 校验的结果）——这属于业务失败，不属于"环境不可用"，调用方应该
        # 按正常的业务失败处理，而不是静默吞掉。
        handle_task_failure(result)

`try_hybrid_exec()` 只在"构造/运行 HybridExecutor 这件事本身失败"时
返回 `None`（`ImportError`——mini_agent 包没装；或者构造/运行阶段抛出
的其它异常——比如 provider 配置缺失、项目根不可写等环境性问题）。任务
本身跑完但没成功（`ExecutionResult.ok is False`）不算这种情况，会照常
把 `ExecutionResult` 返回给调用方，因为那是"环境正常、这次任务没做成"，
调用方需要能区分这两种失败，不能一律吞成 `None`。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型标注，不引入运行时依赖
    from .executor import HybridExecutor
    from .spec import ExecutionResult, TaskSpec


def try_hybrid_exec(
    task: "TaskSpec",
    *,
    project_root=None,
    workspace=None,
    executor: "Optional[HybridExecutor]" = None,
    **default_executor_kwargs,
) -> "Optional[ExecutionResult]":
    """尝试用 hybrid_exec 执行一个任务；检测不到可用的 mini_agent 框架时
    返回 `None`，由调用方自行决定兜底逻辑（比如退化成一段写死的业务
    代码），而不是让异常直接从这里往外抛、炸穿整个 entrypoint。

    三种调用形态：
      1. 最简单：只传 `task` + `project_root`（或 `workspace`），内部会
         调用 `default_executor()` 现起一个（沿用其 `project_root`/
         `workspace` 二选一、`workspace` 优先的约定，见
         `executor.default_executor()` 的说明）。
      2. 已经有现成的 `HybridExecutor`（比如调用方想在多次任务之间复用
         同一个 executor，避免每次都重新构造 LLM 连接池）：直接传
         `executor=`，此时 `project_root`/`workspace`/
         `default_executor_kwargs` 都不会被用到。
      3. 需要自定义 `default_executor()` 的其它参数（如
         `enable_skill_tier`）：通过 `**default_executor_kwargs` 透传。

    `project_root`/`workspace`/`executor` 三者至少要传一个，否则视为
    调用方用法错误、直接抛 `ValueError`（这不属于"环境不可用"，是调用
    方传参本身就没给够信息，提前报错比静默返回 `None` 更容易定位问题）。
    """
    if executor is None and project_root is None and workspace is None:
        raise ValueError(
            "try_hybrid_exec() 需要传入 executor，或 project_root/workspace 二者之一"
        )

    try:
        if executor is None:
            from .executor import default_executor

            executor = default_executor(
                project_root, workspace=workspace, **default_executor_kwargs
            )
        return executor.run(task)
    except ImportError:
        # mini_agent（或其某个可选依赖）没有安装，环境不具备运行条件。
        return None
    except Exception:  # noqa: BLE001 — 构造/运行阶段的环境性问题统一降级为 None，
        # 不区分具体异常类型：provider 配置缺失、项目根不可写、LLM 相关
        # 可选依赖未装齐等，对调用方而言处理方式是一样的——退回自己的
        # 兜底逻辑。这与 `default_executor()` LLM 构造失败时 catch-all
        # 降级为 `shared_llm = None` 的姿势一致。
        return None
