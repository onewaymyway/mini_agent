# 产出注册表 P3 实施记录：受管新建、版本与重新打开

> 对应计划：`management_output_project_model_plan.md` 第 5.3、5.6、8.4 节与第 9 章 P3
> 状态：**已实施（范围与默认决定见第 2、3 节，需确认）**
> 约束：新建只创建不覆盖；版本只追加；旧项目不被写入 PROJECT.json。

---

## 1. 目标回顾

P3 要做三件事：

1. 新建的产出项目带 `PROJECT.json` 和骨架目录（受管项目）；
2. 提供手动发布版本的动作：语义化版本、快照复制全部内容、只读快照、可选 git tag；
3. 管理项目可以重新打开，并记录原因与历史。

## 2. 范围说明

| 计划条目 | P3 状态 | 说明 |
|---|---|---|
| 新建受管产出项目（PROJECT.json + 骨架） | ✅ | `create_output_project` |
| 语义化版本、严格递增 | ✅ | `parse_semver`、`publish_version` |
| 快照复制全部内容，只读 | ✅ | 文件级只读，见 3.4 |
| git 仓库打 tag | ✅（代码路径已实现，未在测试中覆盖） | 见 5.3 |
| 管理项目重新打开，记录原因 | ✅ | `reopen_management_root` |
| PROJECT.json 与注册表同步（计划 13 章问题 A） | ✅ | 见 3.2 |
| 看板展示版本与重新打开 | ⏸ 未实现 | 与 P2 的看板一并延后 |

## 3. 设计要点

### 3.1 新建只创建、不覆盖

- 目标目录已存在（无论是否为空）时直接拒绝，并保留其中全部内容。
- 新建的内容：`PROJECT_INFO.md`（标题与可选介绍）、`output/`、`PROJECT.json`（镜像）。`releases/` 在首次发布时创建。
- 项目名必须符合 `^[A-Za-z0-9][A-Za-z0-9_.-]*$`，防止路径穿越。

### 3.2 PROJECT.json 同步（计划 13 章问题 A 的解决方案）

- **只对受管项目**（`managed=true`）写入 PROJECT.json。旧项目保持无此文件。
- 每次注册表写入记录时，受管项目的 PROJECT.json 同步更新为完整记录（版本历史、状态历史、owner、引用等）。
- `rebuild_index` 在没有分表时，优先从 PROJECT.json 恢复记录，因此删除整个 `_registry/` 后版本历史仍可恢复。已有测试 `test_rebuild_restores_versions_from_project_json` 覆盖这一点。
- 旧项目的历史只存在于分表中；删除注册表后这部分会丢失。这一限制已在第 6 节说明。

### 3.3 版本规则

| 规则 | 实现 |
|---|---|
| 格式 | 严格 `MAJOR.MINOR.PATCH`；拒绝 `1.0`、`v1.0.0`、`01.0.0`、预发布后缀 |
| 递增 | 必须严格大于已发布的最大版本；比较按数字而非字符串 |
| 不重复 | 同名快照已存在则拒绝 |
| 只追加 | 版本记录只 append；已发布条目不修改 |
| 确认 | 发布是用户确认的动作；CLI 必须带 `--confirm`，模块本身不做交互 |

### 3.4 快照

- 复制项目目录的**全部内容**到 `releases/<version>/`，唯一排除项是项目根下的 `releases/` 本身（否则会递归复制）。
- **只读的范围是文件级**：快照中的文件权限设为 0444。目录保持可写，原因是只读目录会导致后续清理失败。快照目录本身仍可被删除，这属于有意的取舍。
- 快照之后对项目的修改不影响快照内容（测试 `test_snapshot_is_unaffected_by_later_edits`）。

### 3.5 git tag

- 若项目目录内存在 `.git`，则执行 `git -C <项目> tag v<version>`。
- tag 失败（git 不可用、tag 已存在等）**不影响发布本身**：错误写入版本记录的 `git_error` 字段，`git_tag` 为空。
- 注意：该路径在测试中未覆盖（测试环境下没有 git 仓库）。这是已知的测试缺口，见第 6 节。

### 3.6 管理项目重新打开

- 原因必填，空白原因被拒绝。
- 只允许重新打开**非 active** 的管理项目。
- 状态通过目标树的 `set_status` 修改（不直接写 goals.json），并返回其结果。
- 历史事件（from_status、reason、时间）追加到总表 `management_roots[goal_id].history`。
- 重新打开后仍可建立关联（测试 `test_link_still_works_after_reopen`）。

### 3.7 与 P2 的衔接

- P3 复用 P2 的注册表读写与锁；`save_project_record` 与 `append_management_history` 是为 P3 新增的公共入口。
- `publish_version` 对尚未登记的旧项目会先自动登记，再发布版本（不写 PROJECT.json）。

## 4. 新增文件与改动

| 文件 | 类型 | 说明 |
|---|---|---|
| `src/mini_agent/output_registry/lifecycle.py` | 新增 | 新建、发布、重新打开 |
| `src/mini_agent/output_registry/registry.py` | 修改 | PROJECT.json 镜像、`save_project_record`、`append_management_history`、从镜像重建、总表 `latest_version` |
| `scripts/output_project_lifecycle.py` | 新增 | 命令行入口：create / publish / reopen，后两者需 `--confirm` |
| `tests/test_output_registry_lifecycle.py` | 新增（24 项） | 新建、版本规则、快照、只读、旧项目兼容、镜像恢复、重新打开 |
| `next_doc/management_output_project_model_impl_p3.md` | 新增 | 本文档 |
| `next_doc/management_output_project_model_plan.md` | 修改 | 进度表：P3 已实施 |
| `next_doc/README.md` | 修改 | 索引补充 P3 |

## 5. 验证

### 5.1 测试结果

| 测试集 | 结果 |
|---|---|
| `test_output_registry_lifecycle.py`（P3 新增） | 24/24 通过 |
| `test_output_registry_links.py`（P2） | 21/21 通过 |
| `test_output_registry_inventory.py`（P0） | 9/9 通过 |
| `test_goal_attributes*.py`（P1） | 45/45 通过 |
| 产出项目相关既有测试（5 个文件） | 全部通过 |
| 合计 | **122 项通过，0 失败** |

### 5.2 冒烟测试

命令行完成了 create → publish 1.0.0 → 尝试发布 0.5.0（被拒绝）→ 未加 `--confirm` 的发布（被拒绝）的完整流程，结果符合预期。

### 5.3 未覆盖的路径

- **git tag**：测试环境没有 git 仓库，tag 代码路径未被执行。
- **并发发布**：同一项目并发发布版本未测试。
- **快照清理**：只读快照的删除未测试（依赖文件系统行为）。

## 6. 已知限制

1. 旧项目的版本历史只存在于分表中，删除 `_registry/` 后会丢失（受管项目不受影响）。
2. `git tag` 路径未经测试。
3. 快照的只读是文件级守卫，不防止有权限的用户删除或替换快照目录。
4. 快照包含 `.git` 目录（计划要求"全部内容"），会使快照体积增大。是否排除需要确认。
5. 没有看板视图。
6. P2 的全量回归尚未补跑（见 P2 记录第 5.3 节）。

## 7. 需要确认的问题

1. **快照是否包含 `.git`**：当前按计划"全部内容"包含；如果排除，git 仓库的快照只含工作区文件，版本可追溯性依赖 git tag。
2. **快照只读的强度**：当前为文件级 0444。若需要更强保证，可以考虑把快照目录移出可写区域，但这会增加清理难度。
3. P1 的 `executable=false` 规则与 P2 记录第 3.7 节的默认值。

## 8. 下一阶段

P4（产出路由）将让绑定了产出项目的执行，把交付文件直接写入项目的 `output/` 目录，而不是先写到会话目录再搬运。这一阶段会改动执行路径，需要先补充特征测试，并确认 P1 与 P2 的规则已经定下。
