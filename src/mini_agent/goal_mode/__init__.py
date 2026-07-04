"""
goal_mode — Goal 模式：设定一个目标，Agent 自动多轮尝试直至达成或触发安全阀。

子模块：
  spec.py      GoalSpec 数据结构 + GoalSpecBuilder（自然语言 → 结构化验收标准，
               支持多轮对话式确认/修订）
  executor.py  GoalStepExecutor 接口 + CoarseStepExecutor（粗粒度：每步一次完整
               run_turn），为将来的细粒度版本预留接口
  state.py     GoalState 落盘/恢复（进程异常中断后可续跑）
  runner.py    GoalRunner 外层驱动循环（判定 / 反馈注入 / compact 整合 / 安全阀）

使用方式（典型流程）：
  from mini_agent.goal_mode.spec import GoalSpecBuilder
  from mini_agent.goal_mode.runner import GoalRunner

  builder = GoalSpecBuilder(cfg)
  spec = builder.build_initial("把测试覆盖率提上去")
  # ... 展示给用户，收集反馈，builder.revise(spec, feedback) ...
  spec.confirmed = True

  runner = GoalRunner(agent, cfg, spec)
  result = runner.run()
"""

from .spec import GoalSpec, GoalSpecBuilder
from .state import GoalState, GoalStateStore
from .executor import GoalStepExecutor, CoarseStepExecutor, GoalStepResult
from .runner import GoalRunner, GoalRunResult

__all__ = [
    "GoalSpec",
    "GoalSpecBuilder",
    "GoalState",
    "GoalStateStore",
    "GoalStepExecutor",
    "CoarseStepExecutor",
    "GoalStepResult",
    "GoalRunner",
    "GoalRunResult",
]
