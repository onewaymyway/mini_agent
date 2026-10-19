# 产出注册表 P1 实施记录：目标节点属性化

> 对应计划：`management_output_project_model_plan.md` 第 5.1 节、第 8.1 节与第 9 章 P1
> 状态：**已实施**
> 约束：行为保持。现有目标的执行行为与改造前一致；旧数据无需迁移即可加载。

---

## 1. 目标回顾

P1 要把"执行语义由层级决定"改为"执行语义由节点属性决定"，同时保证：

1. 旧 `goals.json` 无需任何迁移即可加载，行为不变；
2. 层级标签（ultimate / domain / stage / goal / objective）保留，只用于展示与筛选；
3. 退出条件：现有目标的执行行为与改造前一致，由特征测试证明。

## 2. 交付内容

| 文件 | 类型 | 说明 |
|---|---|---|
| `src/mini_agent/perception/goal_backlog.py` | 修改 | 新增三个属性字段、推导规则、有效值属性；`is_structural` 改为读取属性 |
| `tests/test_goal_attributes_characterization.py` | 新增（21 项） | 特征测试：改造前编写，锁定现有执行语义；改造前后都通过 |
| `tests/test_goal_attributes.py` | 新增（24 项） | 属性测试：默认推导、显式覆盖、序列化、旧文件兼容 |
| `next_doc/management_output_project_model_impl_p1.md` | 新增 | 本文档 |
| `next_doc/management_output_project_model_plan.md` | 修改 | 进度表：P1 标记为已实施 |
| `next_doc/README.md` | 修改 | `management_output` 主线索引补充 P1 |

## 3. 设计要点

### 3.1 四个属性与推导规则

| 属性 | 字段 | 默认推导（level → 值） | P1 的消费者 |
|---|---|---|---|
| `verifiable` 可验收 | `verifiable: Optional[bool]` | goal / objective → True；结构层 → False | 无（仅存储与暴露，留给后续阶段） |
| `executable` 可执行 | `executable: Optional[bool]` | goal / objective → True；结构层 → False | `is_structural`（见 3.2） |
| `recurring` 持续性 | 已有字段，未改动 | — | 现有逻辑，未改动 |
| `deliverable` 有交付 | `deliverable: Optional[bool]` | 全部 False | 无（留给 P2 / P4） |

- 字段值为 `None` 表示"由 level 推导"；显式设为 True/False 则覆盖推导值。
- `deliverable` 默认全部 False，是有意的设计：交付判定需要用户确认（计划第 5.2 节），系统不能仅凭层级认定某个目标有交付物。
- 未知的 level 回退为全 False，即按结构节点处理。

### 3.2 执行语义切换点

`is_structural` 原本是 `level in {ultimate, domain, stage}`，现在是 `not effective_executable`。

这个谓词在以下位置被调用，它们因此改为读取属性：

| 调用位置 | 作用 |
|---|---|
| `perception/goal_backlog.py` `focus_research_nodes()` | 结构焦点节点的收集 |
| `evolution/next_action_advisor.py` `_collect_current_focus_node_ids()` | 下一步建议的焦点收集 |
| `evolution/next_action_advisor.py` `_find_focus_next_step_candidates()` | 结构节点的分解建议分支 |
| `evolution/focus_research_trigger.py` `_min_interval_for()` | 结构节点与叶子节点的调研最小间隔 |

对旧数据，`effective_executable` 与原来的层级判断逐一等价，因此上述行为不变。

**未切换的部分**：`is_goal`、`is_objective` 以及 `validate_node_hierarchy()` 仍然基于层级。它们描述的是树的结构约束和分类标签，不属于"是否执行"的语义，计划中也未要求在 P1 改动。

### 3.3 序列化

- `to_dict()` 总是写出三个新键，缺省时为 `null`。这是新增键，不影响已有字段。
- `from_dict()` 缺失键时读为 `None`，因此旧文件加载后推导行为与改造前一致。
- 测试 `test_legacy_file_load_and_save_keeps_old_fields` 验证：旧文件加载并保存后，原有字段（标题、优先级、描述、标签）全部保留。

### 3.4 与 P0 的关系

P0 的盘点脚本仍然只读 `goals.json` 的原始 JSON，不调用 `GoalBacklog.load()`，因此不受本次改动影响。

## 4. 特征测试的设计

特征测试在改造前编写并通过（21/21），作为改造前后行为的参照。覆盖以下五类可观察行为：

1. 层级谓词表：五个层级各自的 `is_structural` / `is_goal` / `is_objective`；
2. 焦点收集：结构节点的焦点子节点出现在 `focus_research_nodes()` 中，叶子 Goal 仍然包含在结果中，并排在结构节点之前；
3. 调研间隔：结构节点 7 天，叶子节点 2 天；
4. 层级约束：`validate_node_hierarchy()` 的合法与非法组合表；
5. 序列化与旧文件：往返一致，旧文件可加载。

特征测试中有两处断言在编写时出错，已修正为锁定真实行为（而非假设的行为）。这一过程本身说明特征测试的价值：它记录的是代码实际做的事，而不是我们以为它做的事。

## 5. 回归验证

### 5.1 方法

按与目标相关的文件将全部 6662 个用例分成两组：

- **目标相关组**：2523 个用例，来自名称或内容中包含 "goal" 的测试文件；
- **其余组**：4139 个用例。

两组都在修改后的代码上运行，失败用例逐一与已知既有失败集合比对。已知既有失败集合来自 P0 阶段的全量运行，并且已在原始代码上逐一确认（共 111 项）。

### 5.2 结果

| 组别 | 用例数 | 失败数 | 失败归类 |
|---|---|---|---|
| 目标相关组 | 2523 | 24 | 21 项在原始代码上已知失败；3 项属于不稳定用例（见 5.3） |
| 其余组 | 4139 | 90 | 全部属于既有失败，新增为 0 |
| **合计** | **6662** | **114** | **新增失败为 0** |

### 5.3 不稳定用例

`tests/test_system_tool_call_and_debug.py::TestLLMDebugLogger` 中有 3 个用例在修改后的代码上失败。我在原始代码上单独运行同一个类，结果也是 3 个失败，但失败的用例与修改后不同。失败集合随运行状态变化，说明这是既有的不稳定用例，与 P1 无关。

在 P0 的全量运行中，这个类曾经通过，因此它的不稳定性很可能与执行环境或运行顺序有关。建议后续单独排查（例如检查 logger 的全局句柄、日志文件的日期戳），本阶段不处理。

### 5.4 与既有失败的关系

既有的 111 个失败集中在 skill 子系统、格式纠错、goal_mode 等模块，与 P1 无关。它们在本阶段的回归中保持原样，没有因为 P1 而新增或消失。

## 6. 已知限制

1. `verifiable` 与 `deliverable` 目前只存储，没有消费者。计划第 9 章要求"新增的数据有明确的消费者"，这两个字段将在 P2（绑定）和 P4（产出路由）中使用；在此之前它们属于预留字段。
2. API 与看板还不能写入这三个字段，只能直接编辑 `goals.json`。写入接口留给 P2。
3. `is_goal` / `is_objective` 仍然基于层级。如果将来某个 goal 需要被标记为不可执行（`executable=false`），它会被视为结构节点，但仍被计为 goal 层级的节点；这种组合目前没有对应的界面或校验，需要在 P2 中明确规则。
4. 不稳定用例 `TestLLMDebugLogger` 未修复。

## 7. 下一阶段

P2（管理项目与关联）将允许管理项目作为子树根，并建立管理-产出关联。它会使用本阶段的 `deliverable` 属性作为提示依据，并开放写入接口。开始前需要先确认第 6 节第 3 点的规则。
