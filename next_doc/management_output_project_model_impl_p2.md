# 产出注册表 P2 实施记录：管理项目与关联

> 对应计划：`management_output_project_model_plan.md` 第 5.3–5.5、8.5 节与第 9 章 P2
> 状态：**已实施（范围说明见第 2 节）**
> 约束：注册表只写 `output_projects_root/_registry/`；不修改任何产出项目目录内容。

---

## 1. 目标回顾

P2 要做三件事：

1. 允许目标树中的节点作为**管理项目（子树根）**；
2. 建立**管理项目 ↔ 产出项目**的关联，关联带角色（build / iterate / reference）；
3. 让**游离项目**可以绑定到已有管理项目，或据此派生新的管理项目。

退出条件（计划第 9 章）：可以通过注册表看到目标与产出项目的关联。

## 2. 范围说明

| 计划条目 | P2 状态 | 说明 |
|---|---|---|
| 管理项目作为子树根 | ✅ 已实现 | 通过 `mark_management_root` 标记（存放于总表，见 3.1） |
| 管理-产出关联（build / iterate / reference） | ✅ 已实现 | `link_management_output` / `unlink_management_output` |
| 游离项目绑定到已有管理项目 | ✅ 已实现 | `bind_floating_project` |
| 游离项目派生新的管理项目 | ✅ 已实现 | `derive_management_from_project`，在目标树新增 goal 节点 |
| 在看板上看到关联 | ⏸ 未实现 | 本阶段只提供注册表 API 与命令行入口；看板视图留待后续子阶段 |
| 管理项目重新打开（计划 5.3） | ⏸ 未实现 | 属于管理项目状态流转，放到 P3 与版本一并处理 |

**退出条件的达成方式**：注册表 API 与命令行 `show` 命令可以列出全部关联，验证了"能看到关联"这一数据层要求；看板展示属于 UI 工作，明确延后。

## 3. 设计要点

### 3.1 管理项目标记存放在总表，而不是 GoalNode 字段（偏离计划 6.3）

计划 6.3 建议在 `GoalNode` 上增加 `management` 与 `output_links` 字段。本阶段改为存放在注册表总表 `_registry/index.json` 的 `management_roots` 中。理由：

- P1 刚刚改动过目标节点的序列化，再次修改会引发新一轮全量回归；
- 管理项目标记本质上是"这个节点被哪些产出项目引用"的索引信息，属于注册表的职责；
- 计划 9 章 P5 之前不需要在目标树中直接查询管理标记。

后续如需在目标树中显示管理标记，可以由注册表读取，不必改动目标节点。

### 3.2 存储结构

```
output_projects_root/_registry/
  index.json           总表：projects（摘要）、management_roots（标题、创建时间）
  index.json.lock      文件锁（写操作期间持有）
  projects/<id>.json   分表：权威记录，含 owner_goal_id、goal_refs、links、versions、status_history
```

- 分表是权威来源；总表是索引。
- 所有写操作在 `index.json` 的独占锁内完成，并使用原子写入。
- 项目 id 取目录名（旧项目兼容：不改名、不移动）。

### 3.3 关联规则

| 规则 | 说明 |
|---|---|
| 角色合法 | 只接受 build / iterate / reference，其他取值直接拒绝 |
| 管理项目已标记 | 关联前必须先 `mark_management_root` |
| 产出项目存在 | 必须是 `output_projects_root` 下的已有目录 |
| owner 决定 | 项目尚无 owner 时，第一个 build 关联成为 owner；其余进入 `goal_refs` |
| 幂等 | 同一 (管理项目, 项目, 角色) 三元组重复调用不产生重复记录 |
| 状态 | 有任何关联时状态为 `active`；解除最后一个关联后回到 `floating` |

### 3.4 游离项目的两条路径（计划 8.5）

1. **绑定到已有管理项目**：`bind_floating_project`，项目未登记时先自动登记；
2. **派生新的管理项目**：`derive_management_from_project`，在父节点下新增 goal 节点，标记为管理项目，并建立关联。父节点不合法（如 goal 直接挂在 ultimate 下）时返回 `RegistryError`，并且不会留下半成品关联。

### 3.5 重建

删除 `index.json` 后，`rebuild_index` 扫描产出目录并读取分表，重建项目摘要与管理项目 id；关联从分表恢复。**唯一无法从注册表恢复的是管理项目标题**，它只能由目标树补齐（`fill_management_titles`）。这一限制已在测试中明确断言。

### 3.6 目录列表兼容（计划 7.4）

`list_projects` 现在跳过下划线开头的目录，因此 `_registry/` 不会出现在产出项目列表中。这是 P2 对既有函数的唯一改动，改动之前不存在任何下划线目录，因此不影响现有数据。

### 3.7 执行语义的待定规则（P1 遗留，P2 采用默认值）

P1 记录中提出的问题是：goal 被设为 `executable=false` 时如何处理。P2 采用以下默认规则，需要用户确认：

- `executable=false` 的 goal 视为结构节点（不参与执行），但仍保留 goal 层级标签；
- objective **不允许**设为不可执行（objective 是执行的最小单位，不可执行则无意义）。

注意：P2 没有提供写入 `executable` 的接口，该规则目前只在 P1 的属性推导层生效。objective 的限制尚未在代码中强制，需要在写入接口出现时补充校验。

## 4. 新增文件与改动

| 文件 | 类型 | 说明 |
|---|---|---|
| `src/mini_agent/output_registry/registry.py` | 新增 | 注册表：标记、关联、解除、绑定、派生、重建 |
| `src/mini_agent/output_registry/__init__.py` | 修改 | 导出注册表 API |
| `src/mini_agent/evolution/output_projects_root.py` | 修改 | `list_projects` 跳过下划线目录（3.6） |
| `scripts/output_registry_link.py` | 新增 | 命令行入口：register / mark-management / link / unlink / bind / derive / show / rebuild |
| `tests/test_output_registry_links.py` | 新增（21 项） | 只写注册表、关联规则、幂等、解除、绑定、派生、重建、列表兼容 |
| `next_doc/management_output_project_model_impl_p2.md` | 新增 | 本文档 |
| `next_doc/management_output_project_model_plan.md` | 修改 | 进度表：P2 已实施 |
| `next_doc/README.md` | 修改 | 索引补充 P2 |

## 5. 验证

### 5.1 测试结果

| 测试集 | 结果 |
|---|---|
| `test_output_registry_links.py`（P2 新增） | 21/21 通过 |
| `test_output_registry_inventory.py`（P0） | 9/9 通过 |
| `test_goal_attributes*.py`（P1） | 45/45 通过 |
| 产出项目相关既有测试（`test_output_projects_root.py` 等，共 5 个文件） | 全部通过 |
| 合计 | **98 项通过，0 失败** |

### 5.2 冒烟测试

在临时项目上通过命令行完成了 register → derive（在 domain 节点下派生）→ show 的完整流程，关联与管理项目标记均正确写入。

同时发现并修复一个问题：父节点是 ultimate 根时，目标树的层级校验抛出裸 `ValueError`，命令行直接以 traceback 退出。现已转换为 `RegistryError`，并有对应的单元测试。

### 5.3 回归范围说明

P2 **没有修改** `goal_backlog.py`，因此不需要重跑目标相关的 2523 项测试。P2 对既有代码的唯一改动是 `list_projects` 跳过下划线目录，影响范围限于产出项目列表相关的测试与接口，已全部覆盖。

**未做的事**：没有在 P2 中重跑全部 6662 个用例。如果需要与前几阶段同等强度的回归，可以在验收前补跑。

## 6. 已知限制

1. **没有看板视图**：关联只能通过 API 与命令行查看，尚未接入 Kanban 界面。
2. **没有管理项目重新打开**（计划 5.3）：留待 P3。
3. **`executable=false` 的 objective 没有被拒绝**：规则已记录，但写入接口尚未出现，校验尚未实现。
4. **管理项目标题无法从注册表重建**：需要目标树补齐。
5. **`versions` 与 `status_history` 字段已预留但仅 link/unlink 写入历史**：版本发布属于 P3。
6. **并发**：所有写操作持有文件锁，但测试没有覆盖多进程并发场景。

## 7. 下一阶段

P3（受管新建与版本）将：

- 新建的产出项目带 `PROJECT.json` 与骨架目录；
- 提供手动发布版本的动作，版本号强制语义化，快照复制全部内容；
- 管理项目重新打开，并记录原因与状态历史。

P3 开始前需要确认：`PROJECT.json` 是否由注册表在每次写入时同步（计划 13 章问题 A 的建议方案），以及快照中是否需要排除 `_registry` 与 `.git`。
