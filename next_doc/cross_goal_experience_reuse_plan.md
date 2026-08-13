# 跨 Goal 经验复用（相似历史 Goal 执行规范推荐）

延续此前"任务执行改进方向"讨论第 5 类识别出的缺口：dead_ends/lessons/wiki
知识沉淀基本停留在**单个 Goal 内**或**全局知识库**两个粒度，中间缺一层
"同类型 Goal 之间互相借鉴"。两个内容形态相似的 recurring Goal，各自独立
探索、独立踩坑，彼此已经趟出来的 `GoalExecutionSpec`（执行规范：产出物
命名/校验规则/交接字段等）完全不互通，新 Goal 又要从头探索一遍。

## 设计

### 1. 相似度匹配（不引入新的向量检索基础设施）

新增 `perception/cross_goal_reference.py::find_similar_confirmed_goals(
goal_backlog, title, description, *, top_k=3, min_similarity=0.35)`：

- 候选池：`goal_backlog.all_nodes()` 中 `is_goal` 且
  `execution_spec_confirmed=True` 的节点（只推荐"已经趟出来、被用户确认过"
  的规范，避免推荐一个自己都还没稳定的规范）；
- 相似度：新 Goal 的 `title + description` 与候选 Goal 的
  `title + description` 用 `difflib.SequenceMatcher` 比较（跟 Stage D 的
  `compute_progress_trend_signal` 同一套轻量文本相似度思路，不新增依赖，
  后续如果证明有用再考虑升级成 embedding 检索）；
- 低于 `min_similarity` 的候选直接丢弃（"看起来完全不相关"不该被推荐），
  按相似度降序取前 `top_k` 条；
- 每条候选附上对应的 `GoalExecutionSpec.render_summary_for_user()`（如果
  spec 已经被归档/删除则该条跳过，不展示一个读不到内容的推荐）。

### 2. 暴露方式

新增只读端点 `GET /v1/goals/similar_confirmed_specs?title=&description=`
——不是在创建 Goal 时自动触发（避免每次创建都强制等一次全量扫描拖慢交互），
而是看板"➕ 新建 Goal"表单里加一个"🔍 查找相似的历史执行规范"按钮，用户
填完标题/描述后自愿点击查看，看到推荐后自己决定要不要把摘要复制进
description 里作为参考起点，人工把关，不做任何自动应用。

### 3. 明确不做的部分

- 不自动把匹配到的 spec 应用到新 Goal（人工决定权保留在用户手里，新 Goal
  的实际情况完全可能跟历史 Goal 有细微但重要的差异）；
- 不引入 embedding/向量检索（difflib 版本先验证"这个功能有没有人用"，
  值得再考虑升级）；
- 不做"自动检测两个已存在的 recurring Goal 高度相似并提示合并"（另一个
  更复杂的问题，超出本轮范围）。

## 实施记录

已实现：`perception/cross_goal_reference.py::find_similar_confirmed_goals()`
+ `GET /v1/goals/similar_confirmed_specs` + `AgentClient.
similar_confirmed_goal_specs()` + 看板"📌 目标看板"tab"➕ 新建 Goal"表单
新增"🔍 查找相似的历史执行规范"按钮（点击后展示推荐列表，不自动应用）。
