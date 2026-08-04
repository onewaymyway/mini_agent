# myplugins/hybrid_step.py
#
# 薄插件文件：把 hybrid_step 类型注册进 workflow（P7-④1/④2 机制，见
# executors.py::register_step_executor 文档字符串）。真正的逻辑在
# mini_agent.hybrid_exec.workflow_integration 里（可独立测试，不依赖插件
# 发现机制），本文件只做"存在即启用"的开关。
#
# 删除本文件即等效于禁用 hybrid_step 类型（不需要额外改 agent_config.json）。
#
# 用法示例见 mini_agent.hybrid_exec.workflow_integration 模块顶部文档字符串。

from __future__ import annotations

from mini_agent.hybrid_exec.workflow_integration import register  # noqa: F401
