"""stock_watch — A 股监控分析系统的项目私有库代码。

本包与 mini_agent 主项目没有 import 依赖关系（除了可选地复用
`external_projects/ledger.py::track_run` 写执行账本），可以整体移动到
任意路径独立运行，符合 external_projects_workspace_plan.md 原则二。
"""
