# 产出注册表 P4 实施记录：产出路由

> 对应计划：`management_output_project_model_plan.md` 第 8.3 节与第 9 章 P4
> 状态：**已实施（范围与默认决定见第 2、3 节，需确认）**
> 约束：未绑定的目标行为与改造前完全一致；路由只读；路由失败不影响执行。

---

## 1. 目标回顾

P4 的目标是：绑定了产出项目的执行，其正式产出直接写入项目的 `output/` 目录，而不是先写到 `.agent/` 下的默认位置再搬运。

退出条件（计划第 9 章）：绑定执行的产出全部落在项目目录内。

## 2. 范围说明

| 计划条目 | P4 状态 | 说明 |
|---|---|---|
| 绑定执行的交付物写入项目 `output/` | ✅ | 通过 `goal_output_dir` 解析实现，见 3.2 |
| 模块级输出 `<module>/output/` | ⏸ 未实现 | 当前绑定只到项目根，模块路径尚无承载字段，留待后续 |
| 会话目录的交付物改写 | ⏸ 未实现 | P4 只覆盖目标执行路径；会话产出保持现状（计划第 3 节要求"与项目无关的保持现状"） |
| 过程数据（manifest、cycle 记录）保留在 `.agent/` | ✅ | notes / spec / scratch 不受绑定影响，由测试锁定 |
| 退出条件：绑定执行产出全部落在项目目录内 | ✅（目标执行路径） | 以 `goal_output_dir` 为唯一出口验证；模块级与会话级不在本阶段 |

## 3. 设计要点

### 3.1 解析优先级

`goal_output_dir(paths, goal_id, user_output_dir)` 的解析顺序：

1. `user_output_dir`（用户显式设置）——**最高优先级**，行为不变；
2. 绑定的产出项目 `output/`（本阶段新增）；
3. 默认路径 `.agent/daemon_run_outputs/goals/<id>/output/`——未绑定时的行为不变。

这是对现有"用户指定 > 默认"机制的最小扩展：只插入一级，不改变已有分支的返回值。

### 3.2 路由规则（`output_registry/routing.py`）

- 沿目标树从当前节点向上查找，**第一个有 build 或 iterate 关联的管理项目**生效；
- 当前节点本身也参与查找，因此 objective 可以通过父目标找到绑定；
- 同一管理项目上 build 优先于 iterate；
- **reference 关联不参与路由**（reference 只读，不产出）；
- 返回 `<output_projects_root>/<slug>/output`。

### 3.3 只读与失败回退

- 路由只读取 `goals.json` 原始 JSON 与注册表分表，**不调用** `GoalBacklog.load()`（该方法会回写文件）；
- `goals.json` 损坏、注册表缺失、任何异常，都返回 `None`，由调用方退回默认路径；
- 路由绝不能因为注册表问题让执行失败——这是硬约束，由 `test_corrupt_goals_json_falls_back_to_default` 等测试覆盖。

### 3.4 与 P1 / P2 / P3 的衔接

- 路由依赖 P2 的关联（`list_links`）和 P1 的 `parent_id` 结构（通过原始 JSON 读取）；
- 绑定的项目若已被删除，路由仍返回路径（测试 `test_linked_project_dir_removed_still_returns_path_without_error`）。调用方在写入时创建目录或报错，路由本身不负责创建。

### 3.5 新增的写入副作用（需要知道）

绑定后，`ensure_output_skeleton` 会在项目 `output/` 下创建 `README.md`、`_misc/`、`_archive/`、`scripts/`（含 lib、_run_logs、_experiments）。这些是既有的四目录模型骨架，绑定后会出现在项目中。这是绑定行为的直接结果，不是 P4 额外引入的文件。是否需要对受管项目裁剪这些骨架，需要确认。

## 4. 新增文件与改动

| 文件 | 类型 | 说明 |
|---|---|---|
| `src/mini_agent/output_registry/routing.py` | 新增 | 只读路由解析 |
| `src/mini_agent/evolution/output_workspace.py` | 修改 | `goal_output_dir` 插入绑定分支（3.1） |
| `tests/test_output_routing_characterization.py` | 新增（6 项） | 改造前编写，锁定未绑定与用户指定的行为 |
| `tests/test_output_routing.py` | 新增（14 项） | 绑定、祖先、优先级、reference、覆盖、只读、回退 |
| `next_doc/management_output_project_model_impl_p4.md` | 新增 | 本文档 |
| `next_doc/management_output_project_model_plan.md` | 修改 | 进度表 |
| `next_doc/README.md` | 修改 | 索引 |

## 5. 验证

### 5.1 特征测试先行

`test_output_routing_characterization.py` 在改动 `output_workspace.py` 之前编写并通过（6/6），之后再次通过。

### 5.2 测试结果

| 测试集 | 结果 |
|---|---|
| P4 特征测试 + 路由测试 | 20/20 通过 |
| 目标相关既有测试（2523 项，分 6 个窗口） | 21 项既有失败 + 1 项不稳定用例 |
| 产出项目相关测试（P0–P4 全部） | 142/142 通过 |

**目标相关测试的 1 项"新增"失败**：`TestLLMDebugLogger::test_message_content_truncated`。这个类在第 5.3 节（P1 记录）中已确认为不稳定用例：原始代码上同一个类同样会随机失败，失败的用例每次不同。本阶段没有修改日志相关代码，因此判断为既有不稳定性。

### 5.3 回归范围说明

- ✅ 目标相关的全部 2523 项测试已重跑；
- ✅ 产出项目相关的测试已全部重跑；
- ⚠️ **非目标相关的 4139 项测试在 P4 中没有重跑**。`goal_output_dir` 的调用方（14 处）都属于目标执行路径，但我没有逐一核对非目标模块是否间接依赖它。这是本阶段的残余风险，建议在验收前补跑。

## 6. 已知限制

1. **模块级输出未实现**：`<module>/output/` 需要在关联上增加模块路径字段，留待后续。
2. **会话产出未改写**：会话目录的交付物仍在 `.agent/sessions/<id>/output/`，与计划一致（"与项目无关的保持现状"），但这意味着会话里产生的项目相关文件不会自动进入项目。
3. **路由每次调用都读取 `goals.json` 与分表**：没有缓存。目标数量在数百级别时开销可以接受，但未做性能测量。
4. **骨架目录写入**：绑定后项目 `output/` 下会出现四目录模型的骨架（见 3.5）。
5. **非目标测试未重跑**（见 5.3）。

## 7. 需要确认的问题

1. **绑定后的骨架目录**：是否接受 `output/` 下出现 `README.md`、`_misc/`、`_archive/`、`scripts/`？如果不接受，需要为受管项目定义裁剪规则。
2. **会话产出是否需要纳入路由**：计划说"与项目无关的保持现状"，我的理解是会话产出只有在明确绑定到项目时才应该改写，目前没有这种绑定入口。
3. **是否补跑非目标测试**（见 5.3）。
4. P1 的 `executable=false` 规则、P3 的快照与 `.git` 问题，仍按之前记录的默认值执行。

## 8. 下一阶段

P5（全局巡检）是只读的定期分析，负责检查停滞、版本缺口、断链与游离项目，并据此生成报告。它需要先完成第 7 节的确认，并在 P4.5 阶段采集实际数据，之后再校准阈值。
