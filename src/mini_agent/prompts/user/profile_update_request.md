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
#   {{goal_tree_block}}   — [next_doc/profile_staleness_and_goal_tree_gap_plan.md
#                            方向二] 当前活跃目标树的轻量快照（可能为空串），
#                            由 profile.py::generate() 每次生成时重新拉取，
#                            不是"上一版画像"的一部分。
#   {{watchlist_block}}   — [响应用户反馈：画像信息来源不够全] 用户在
#                            watchlist.yaml 里显式配置要关注的话题/关键词
#                            （可能为空串），跟 goal_tree_block 同一个定位：
#                            当前状态快照，每次重新拉取。
#   {{preferences_block}} — [响应用户反馈：画像信息来源不够全] 用户通过
#                            /profile 等入口显式设置的偏好（可能为空串），
#                            是既定事实，不需要模型从会话证据里再验证。

{{previous_profile_block}}{{goal_tree_block}}{{watchlist_block}}{{preferences_block}}Session summaries:
{{memory_text}}

Respond with only the JSON object described in the system prompt.
