"""novel_writer/revision_flow — 完稿后修改机制（独立流程，不接入写作流水线）。

模块：
  common.py    路径、JSON 读写、文件哈希、错误类型
  config.py    可配置的上限（存于 _revision/settings.json，界面后续接入）
  versions.py  P0 完稿版本基线（v001 只读）、派生版本、丢弃
  changes.py   P1 变更模型、受影响章节计算、预览与确认
  executor.py  P2 按章序滚动改写、校验、断点续跑
  feedback.py  P3 章节反馈分诊（章内修改 / 设定变更）
  derived.py   P4 派生物标记、版本差异、按版本导出
本包只读写 _versions/ 与 _revision/ 两个目录，不改写流水线的任何状态文件。
"""
