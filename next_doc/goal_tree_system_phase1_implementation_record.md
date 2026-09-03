# 目标树系统改进 — 阶段一（数据模型）实施记录

> 对应 `next_doc/goal_tree_system_plan.md` §五 分阶段实施规划 第 1 项。
> 设计方案本身不在本文档重复，只记录本阶段实际改了什么、跟原方案的
> 差异，以及验证方式。

## 一、改动范围

只改了 `src/mini_agent/perception/goal_backlog.py` 一个文件 + 新增一份
测试文件 `tests/test_goal_tree_phase1.py`。没有触碰任何执行链路
（`GoalRunner`/`ObjectiveExecutor`/`goal_cron_bridge`/看板），符合方案
§三"这一阶段是地基，不影响现有 Goal/Objective 数据和依赖它们的全部现有
功能"的要求。

## 二、具体改动

### 2.1 `GoalNode.level` 开放 + 新增字段

- `level` 字段本身类型早已是 `str`（此前只是文档注释写"两值枚举"，
  没有代码层面的强校验），本阶段没有改字段类型，改的是**在哪里、以什么
  规则校验它**（见 2.2）。
- 新增三个字段（均有默认值，`to_dict`/`from_dict` 双向打通，旧
  `goals.json`（没有这几个键）加载时按方案约定的默认值兜底，不需要迁移
  脚本）：
  - `current_focus_ids: list[str]`
  - `focus_pinned_ids: list[str]`
  - `decompose_candidates: list`
- 新增 `GoalNode.is_structural` 只读属性：`level in {"ultimate", "domain",
  "stage"}`，对应方案§三"执行语义只在树的下半段生效"，后续阶段二/三/四
  判断"这个节点要不要走 GoalJudge/cron"时统一用这个属性，不重复写
  `level in (...)` 字面量。

### 2.2 层级校验：`LEVEL_ORDER` + `validate_node_hierarchy()`

新增模块级常量 `LEVEL_ORDER = ("ultimate", "domain", "stage", "goal",
"objective")` 和纯函数 `validate_node_hierarchy(level, parent_level) ->
Optional[str]`，实现方案 §4.1.1 的规则：

- `ultimate`：只能是根（`parent_id`/`parent_level` 必须为空）；
- `domain`：父节点必须是 `ultimate`；
- `stage`：父节点可以是 `ultimate` 或 `domain`（允许跳过 domain）；
- `goal`：父节点可以是 `domain`/`stage`/`goal`；
- `objective`：父节点只能是 `goal`（与改动前行为完全一致）。

写法上对齐既有的 `validate_status_write_for_recurring_goal()`：纯函数、
不接触 `GoalBacklog` 状态、返回 `None` 表示合法、返回字符串是可以直接
展示给用户的错误说明，方便阶段四看板复用同一个函数做前端校验提示。

全局唯一性（"当前是否已存在另一个 ultimate 节点"）不属于纯规则判断，
需要看已有状态，放在 `GoalBacklog.add_node()` 里做，不在这个纯函数里。

### 2.3 `GoalBacklog.add_node()` 通用创建入口

新增方法，支持 `LEVEL_ORDER` 里任意一层。做的事：

1. 校验 `parent_id` 存在（存在才能取到 `parent_level`）；
2. 调用 `validate_node_hierarchy()`；
3. `level="ultimate"` 时额外校验全局唯一；
4. 合法则创建节点、挂到父节点 `children_ids`、落盘；不合法抛
   `ValueError`（不创建任何节点、不落盘）。

**与原方案的差异**：原方案 §4.5 写"现有 `add_goal()`/`add_objective()`
改造为对它的薄封装"。本阶段**没有**做这个改造——`add_goal()` 有一段
"创建时跑产出目录提示检测"的专属副作用逻辑，`add_objectives_for_goal()`
批量创建路径也有专属的产出目录分配逻辑，两者都被现有相当数量的测试
（`test_goal_backlog.py`、`test_goal_output_directory_*.py` 等）依赖了
具体行为细节。在没有把这些副作用一起梳理进 `add_node()` 的通用扩展点之前
强行改造，回归风险大于收益，所以本阶段选择：**新增 `add_node()` 作为
新的通用入口（三层结构节点、以及未来任何需要走通用校验的创建路径都用
它），保留 `add_goal()`/`add_objective()` 原样不动**。是否需要把两者
收敛到 `add_node()` 之上，留到后续阶段视实际需要再做，不阻塞阶段二/三/
四（它们创建的都是新节点类型或 `goal`/`objective` 的分解候选，走
`add_node()` 或对应的批量创建方法即可，不依赖 `add_goal()` 内部实现）。

### 2.4 根节点：`GoalBacklog.get_root_node()`

幂等方法：已存在 `level="ultimate"` 节点则直接返回；不存在则创建一个
占位根节点（标题"我的人生目标"，等用户在看板里编辑）后返回。多次调用
只会有一个根节点。

### 2.5 `Direction → domain` 迁移：`migrate_directions_to_domain_nodes()`

按方案 §4.1"`Direction` 数据结构标记废弃，提供一次性迁移函数"实现：

- `dry_run=True`（默认）：只返回预览报告 `{"directions_migrated": [...],
  "goals_reparented": [...]}`，不修改任何状态；
- `dry_run=False`：真正执行——每条 `Direction` 转成 `level="domain"` 的
  `GoalNode`（**复用原 `id`**，挂在全局根节点下，根节点不存在时顺带
  创建），原来通过 `direction_id` 关联的 `goal` 节点，`parent_id` 改指向
  对应的 domain 节点；
- **不清空 `direction_id`**——保留读取兼容，过渡期结束后再清理，符合
  方案原话；
- 幂等：已经迁移过的 Direction（`self._nodes` 里已存在同 id 的节点）和
  已经重新挂好父节点的 Goal 都会被跳过，重复调用不会产生重复数据或报错。

`Direction` 类本身、`add_direction()`/`list_directions()`/
`delete_direction()`/`assign_direction()` 等既有方法**没有删除**——按
方案原文只是"标记废弃"，不是立即移除，废弃字段的真正清理留到过渡期
结束后。

## 三、验证

新增 `tests/test_goal_tree_phase1.py`（30 个用例），覆盖：

- `validate_node_hierarchy()` 五层的全部合法/非法组合（含允许跳级、
  goal 挂 goal）；
- `GoalNode.is_structural` 属性；
- 新增三个字段的 `to_dict`/`from_dict` 往返，以及旧数据缺字段时的默认值
  兜底；
- `add_node()`：完整五层链路创建、跳级创建、非法层级/parent 不存在/
  重复 ultimate 均正确拒绝且不留副作用（校验失败不创建任何节点）；
- `get_root_node()`：缺失时创建、幂等；
- `migrate_directions_to_domain_nodes()`：dry_run 不改状态、真实执行后
  domain 节点/根节点挂载/Goal 重新挂载 parent_id 都符合预期、
  `direction_id` 保留、二次调用幂等空报告。

执行结果：

```
python -m pytest tests/test_goal_tree_phase1.py tests/test_goal_backlog.py -q
30 passed
```

另外跑了同目录下能正常 import 的既有 Goal 相关测试
（`test_goal_backlog.py`/`test_goal_provenance.py`/
`test_goal_relevance_candidate.py`/`test_goal_relevance_judge.py`/
`test_goal_cron_bridge.py`，共 93 个用例）确认全部通过，没有因为本次
改动回归；`test_goal_stuck_stats.py` 里 4 个用例失败，但失败点在
`evolution/goal_stuck_stats.py`（本阶段完全未改动的模块），排查后确认
是沙盒环境本身的问题（该模块未在本次改动范围内），与本次改动无关。
其余大量 Goal 相关测试文件在本沙盒环境里因为缺少 `fastapi`/`starlette`
等第三方依赖或其它未安装模块，本身就 import 失败，属于环境问题，不在
本次验证范围内。

## 四、下一阶段

阶段二（自动分解 `GoalTreeDecomposer`）依赖本阶段的 `add_node()`（用来
落地 accept 后的真实子节点）、`GoalNode.decompose_candidates`/
`is_structural` 字段，可以直接开始，不需要再等待 `add_goal()`/
`add_objective()` 的封装收敛。
