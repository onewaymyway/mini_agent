# prompts/user/profile_update_request.md
#
# 发送给模型，要求基于历史会话摘要生成/刷新用户画像
#
# [注意] 本项目的 prompt 模板引擎只支持简单的 {{ variable }} 文本替换，
# 不支持条件/循环语法（见 prompts/manager.py::_render_template），因此
# "有没有上一版画像可参考"这个分支逻辑在 profile.py 里用 Python 拼好
# 完整文本再传入，模板本身保持纯替换。
#
# 变量：
#   {{memory_text}}       — 本次新增的长期记忆摘要列表（每行一条，最近的在后）
#   {{previous_profile_block}} — [next_doc/memory_backfill_and_profile_update_plan.md
#                            方向二] 完整的"上一版画像 + 更新指引"文本块，
#                            由 profile.py 按是否增量更新拼好（可能为空串）。
#   {{context_blocks}}    — [next_doc/profile_context_sources_completeness_plan.md
#                            方向 E] 所有"背景信息快照"合并成的一段文本
#                            （可能为空串）。由 profile.py::_collect_profile_
#                            context_blocks() 统一收集，每次生成时重新拉取，
#                            不是"上一版画像"的一部分。目前包含（具体语义
#                            见 prompts/system/profile_summarizer.md 里对
#                            各类背景信息的说明，不需要在这里逐条列出，
#                            避免两处文档各说各话）：
#                              - 活跃 + 最近完成的目标树快照
#                              - 用户在 watchlist.yaml 显式配置的关注话题
#                              - 用户通过 /profile 等入口显式设置的偏好
#                              - growth_advisor 规则扫描检测到的关注主题
#                              - research/growth 两个 wiki 命名空间最近
#                                更新的条目标题
#                            新增信息源时只需要在 profile.py 的
#                            `_PROFILE_CONTEXT_PROVIDERS` 里注册一个函数，
#                            不需要再改这个模板。

{{previous_profile_block}}{{context_blocks}}Session summaries:
{{memory_text}}

Respond with only the JSON object described in the system prompt.
