# 产出注册表 P6 实施记录：接入辅助

> 对应计划：`management_output_project_model_plan.md` 第 8.6 节与第 9 章 P6
> 状态：**已实施（范围与默认决定见第 2、3 节，需确认）**
> 约束：接入必须用户确认；接入只新增文件，不修改、不移动、不删除目录中已有内容。

---

## 1. 目标回顾

P6 的退出条件是"接入后不影响原目录内容"。它要做两件事：

1. 巡检能发现"目录存在但未登记"的产出项目（计划 8.6 节的"未登记目录"检查项，P5 当时未实现）；
2. 对发现的未登记项目提供一键接入，且只有用户确认后才写入 `PROJECT.json`。

## 2. 范围说明

| 计划条目 | 状态 | 说明 |
|---|---|---|
| 巡检发现未登记目录 | ✅ | `patrol.py` 新增 `unregistered` finding；行数据新增 `registered` 字段 |
| 只读列出候选 | ✅ | `list_unregistered`，命令 `output_patrol.py candidates` |
| 一键接入，需用户确认 | ✅ | `adopt_output_project(confirm=True)`，命令 `output_project_lifecycle.py adopt <slug> --confirm` |
| 仅在确认后写入 PROJECT.json | ✅ | 接入即写入镜像（`managed=True`），未接入的目录不写入任何文件 |
| 接入后不影响原目录内容 | ✅ | 测试逐文件哈希比对，原有文件字节不变 |
| 看板上的"一键接入"按钮 | ⏸ 未实现 | 与 P2、P3 一样延后到看板阶段；当前为函数与命令行入口 |

## 3. 设计要点

### 3.1 接入的语义

- 接入 = 登记 + 受管。计划 3 节规定 `PROJECT.json` 只在"受管新建或被用户明确接入时"写入，因此接入把 `managed` 设为 `True`，并写入镜像。
- 接入不改变状态（保持 `floating`），不关联任何管理项目。后续归属仍走 P2 的绑定路径。
- 接入后该项目受 P5 的 `never_released` 规则约束：若已有变更但从未发布，巡检会给出建议。这是有意的，因为"受管"就意味着要参与版本管理。

### 3.2 只新增、不覆盖

| 文件 | 何时写入 |
|---|---|
| `PROJECT.json` | 接入时总是写入（新文件） |
| `PROJECT_INFO.md` | 仅当目录中不存在该文件，且用户给出了介绍文本 |
| 其它任何文件 | 从不写入、修改、移动或删除 |

### 3.3 确认门槛

- `adopt_output_project` 的 `confirm` 为必填关键字，传 `False` 直接抛错，且在任何读写之前返回；
- 命令行缺少 `--confirm` 时返回退出码 3，不产生写入。

### 3.4 幂等与并发

- 已登记的项目再次接入，直接返回 `already_registered=True`，不做任何写入；
- 写入在 `_registry/index.json` 的文件锁内完成，锁内再次检查登记状态，防止两次接入同时进行。

### 3.5 发现规则

- 候选 = 产出根下的一级目录，排除 `_` 与 `.` 开头者，且分表 `_registry/projects/<slug>.json` 不存在；
- 只读，不读取目录内容，只统计文件数（上限 5000）并尝试读取已有 `PROJECT_INFO.md` 的一级标题作为建议标题。

## 4. 新增文件与改动

| 文件 | 类型 | 说明 |
|---|---|---|
| `src/mini_agent/output_registry/adoption.py` | 新增 | `list_unregistered`、`adopt_output_project` |
| `src/mini_agent/output_registry/patrol.py` | 修改 | 行数据新增 `registered`；评估新增 `unregistered` finding |
| `src/mini_agent/output_registry/__init__.py` | 修改 | 导出两个新函数 |
| `scripts/output_project_lifecycle.py` | 修改 | 新增 `adopt` 子命令 |
| `scripts/output_patrol.py` | 修改 | 新增 `candidates` 子命令 |
| `tests/test_output_registry_adoption.py` | 新增（12 项） | 发现、确认门槛、非破坏性、幂等、恢复、巡检 |
| `next_doc/management_output_project_model_impl_p6.md` | 新增 | 本文档 |
| `next_doc/management_output_project_model_plan.md` | 修改 | 进度表：P6 已实施 |
| `next_doc/README.md` | 修改 | 索引补充 P6 |

## 5. 验证

### 5.1 测试结果

| 测试集 | 结果 |
|---|---|
| `test_output_registry_adoption.py`（P6 新增） | 12/12 通过 |
| `test_output_patrol.py`（P5） | 通过（新增 finding 未破坏既有断言） |
| `test_output_registry_lifecycle.py`（P3） | 通过 |
| `test_output_registry_links.py`（P2） | 通过 |
| `test_output_registry_inventory.py`（P0） | 通过 |
| 合计 | **82 项通过，0 失败** |

### 5.2 冒烟测试

在临时项目上依次运行：`candidates`（列出 demo）→ 未加 `--confirm` 的 `adopt`（退出码 3，无写入）→ 加 `--confirm --intro` 的 `adopt`（写入 PROJECT.json 与 PROJECT_INFO.md）→ `candidates`（列表为空）。结果符合预期。

## 6. 已知限制

1. 没有看板按钮，接入只能通过函数或命令行完成。
2. 接入后 PROJECT.json 会出现在目录中，若用户手动拷贝整个目录，该文件会随之带走，这是设计预期。
3. 接入的项目若之前从未发布过版本，且已有文件变动，会立即收到 `never_released` 建议。如果用户并不希望现在进入版本管理，应先不接入。
4. 候选统计的文件数上限为 5000，超出时 `file_count_truncated=true`。

## 7. 需要确认的问题

1. 接入是否应当直接把 `managed` 设为 `True`，还是应提供"只登记、不受管"的第二档？当前选择的是前者，因为计划 3 节把 PROJECT.json 与"被明确接入"绑定在一起。
2. 是否需要在看板"产出项目"页面增加候选列表与"一键接入"按钮？
3. 巡检报告是否需要接入通知渠道（P5 的未决项，本阶段未触及）。

## 8. 整体收尾

P0 至 P6 的计划条目已全部有对应实现。剩余事项属于确认与数据积累：阈值校准依赖 P4.5 的真实样本，看板入口、报告通知与定期调度需要用户确认呈现与频率。
