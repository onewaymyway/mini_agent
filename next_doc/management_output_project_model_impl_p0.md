# 产出注册表 P0 实施记录：只读盘点

> 对应计划：`management_output_project_model_plan.md` 第 9 章 P0
> 状态：**已实施**
> 约束：只读。不修改 `output_projects_root` 下的任何内容，不修改现有 `goals.json`。

---

## 1. 目标回顾

P0 的目的是在动任何写路径之前，先回答三个问题：

1. `output_projects_root` 下现有哪些项目，它们各自的状态如何？
2. 六处产出位置分别是什么、有多少内容、与目标树是否有关联？
3. 现有目标的 `user_output_dir` 有多少能映射到产出项目，有多少是遗留路径？

P0 的产出是一份**候选清单（草稿）**，供后续阶段（P1 目标属性化、P2 绑定、P5 巡检校准）作为输入。

## 2. 交付内容

| 文件 | 类型 | 说明 |
|---|---|---|
| `src/mini_agent/output_registry/__init__.py` | 新增 | 包入口，导出 `build_inventory`、`write_inventory_draft` |
| `src/mini_agent/output_registry/inventory.py` | 新增 | 盘点实现：扫描根目录、解析 goals.json、统计六处产出位置 |
| `scripts/output_registry_inventory.py` | 新增 | 命令行入口，支持 `--stdout` 只打印不落盘 |
| `tests/test_output_registry_inventory.py` | 新增 | 9 项测试，覆盖只读性、兼容性、边界情况 |
| `next_doc/management_output_project_model_impl_p0.md` | 新增 | 本文档 |
| `next_doc/management_output_project_model_plan.md` | 修改 | 增加实施进度表 |

## 3. 实现要点

### 3.1 只读保证的三条约束

1. **根目录不写**：盘点只调用 `iterdir`、`stat`、`os.walk` 与 `read_text`。测试 `test_inventory_does_not_touch_output_projects_root` 对根目录做逐文件 SHA-256 快照，前后比对。
2. **不调用 `GoalBacklog.load()`**：该方法会执行 `_backfill_user_output_dir_suggestions()`，可能回写 `goals.json`。P0 改为直接解析 `.agent/goals.json` 原始 JSON。测试 `test_goals_json_is_not_modified` 比对字节级内容。
3. **唯一写入的草稿位置**：`.agent/output_registry/inventory_draft.json`。写入前校验该路径不在 `output_projects_root` 之下，防止草稿混入产出目录。

### 3.2 草稿位置的选择

计划文档中注册表的位置是 `output_projects_root/_registry/`，但那属于 P2 的写入动作。P0 为了保持"不写现有产出目录"，把草稿放在 `.agent/` 内部。P2 正式建立 `_registry/` 时，草稿可以直接作为其初始内容的参考。

### 3.3 兼容性处理

- 所有项目候选都标记 `legacy=true`、`managed=<是否存在 PROJECT.json>`、`in_registry=false`；
- 下划线开头的一级目录归入 `reserved_dirs`，不作为项目列出；
- 缺少 `PROJECT_INFO.md` 的项目仍被识别，并在 `notes` 中提示，而不是被跳过；
- 文件统计设有上限（5000 个文件），超出则标记 `file_count_truncated`，避免超大目录拖慢盘点；
- `goals.json` 无法解析时，盘点继续进行，错误写入 `anomalies`，而不是抛出异常。

### 3.4 六处产出位置的统计口径

| key | 位置 | 分类 | 说明 |
|---|---|---|---|
| `session_output` | `.agent/sessions/` | ephemeral_scratch | 统计子目录数，要求其含 `output/` |
| `adhoc_output` | `.agent/adhoc/` | ephemeral_scratch | 同上 |
| `workflow_output` | `.agent/workflow_sessions/` | ephemeral_scratch | 同上 |
| `daemon_outputs` | `.agent/daemon_run_outputs/{goals,cron}` | process_records | 过程数据，不纳入产出项目 |
| `goal_user_output` | goals.json 的 `user_output_dir` | legacy_binding | 去重后列出，并判断是否存在 |
| `output_projects_root` | 全局产出根 | managed_candidate | P0 的盘点对象 |

分类字段用于后续阶段决定处理方式：`ephemeral_scratch` 保持现状，`process_records` 不进入项目，`legacy_binding` 只用于路径推导，`managed_candidate` 才是注册表的候选来源。

## 4. 草稿结构（schema: `output_registry.inventory.v1`）

```
schema, generated_at, readonly_scan=true
project_root, output_projects_root
summary        候选数、已绑定/游离数、目标数、保留目录数、异常数
projects[]     每个候选：slug、path、legacy、managed、in_registry、
               suggested_owner_goal_ids、suggested_status、
               has_project_info、has_project_json、has_git、has_releases、
               file_count、size_bytes、latest_file_mtime、top_level_entries、notes
reserved_dirs[]
goal_bindings[]   每个目标：goal_id、level、status、user_output_dir、matched_slug
output_locations[]  六处产出位置的统计
anomalies[]    字符串列表，如"目标 X 的 user_output_dir 不在根目录之下"
```

`suggested_status` 的取值只有两种：`candidate_bound`（被某个目标的 `user_output_dir` 指向）和 `floating`（无任何引用）。它只是建议，不写入任何地方。

## 5. 使用方法

```bash
# 只打印，不落盘
PYTHONPATH=src python scripts/output_registry_inventory.py --project <项目根> --stdout

# 写入草稿（.agent/output_registry/inventory_draft.json）
PYTHONPATH=src python scripts/output_registry_inventory.py --project <项目根>
```

## 6. 验证

- 新增测试 9 项，全部通过（`tests/test_output_registry_inventory.py`）。
- 基线对比：见第 7 节。
- 在本仓库的真实项目根上运行脚本，可正常完成盘点（当前无产出目录，结果为空）。

## 7. 基线测试与回归

### 7.1 方法

1. **修改后的代码**：在 `src/` 当前状态下跑完整测试集，共收集 **6662** 个用例。由于单次命令有时长上限，改为按 600–1200 个用例分窗口执行（窗口顺序与 `pytest --co` 一致），失败窗口额外用 JUnit XML 精确记录失败用例 ID。
2. **原始代码**：对修改后失败的 **111** 个用例 ID，在未修改的原始压缩包上逐一复跑。
3. 两组失败 ID 集合做逐项比较。

环境：CPU 单核；额外安装的可选依赖为 `streamlit`、`websocket-client`、`pytest-xdist`（未使用）、`fastapi`、`uvicorn`、`sse-starlette`、`python-multipart`。

### 7.2 结果

| 项目 | 结果 |
|---|---|
| 收集用例数 | 6662 |
| 修改后失败 + 错误 | 111（99 失败 + 12 错误） |
| 原始代码上同一批用例 | **同样 111 个全部失败** |
| 两组失败 ID 集合 | **完全一致** |
| 新增的 P0 测试（`test_output_registry_inventory.py`） | **9/9 通过** |
| 修改后新增失败 | **0** |

**结论**：本阶段引入的回归为零。111 个失败是原始代码就存在的既有问题，与 P0 无关。

### 7.3 既有失败的分布（按测试模块，仅列前 15 个）

| 失败数 | 测试模块 |
|---|---|
| 8 | `test_goal_execution_spec_kanban_routes` |
| 6 | `test_skill_cli`（TestSkillOff） |
| 6 | `test_skill_manager`（activate / deactivate / loader exclude） |
| 6 | `test_format_correction_integration` |
| 5 | `test_skill_cli`（TestSkillOn） |
| 5 | `test_goal_mode` |
| 4 | `test_skill_compact` |
| 4 | `test_llm_network_trust_env` |
| 3 | `test_skill_cli`（TestSkillReset） |
| 3 | `test_skill_compact`（TestAgentSkillCompact） |
| 2 | `test_workflow_p11`、`test_python_step_subprocess_e2e`、`test_report_tiers`、`test_explorer_runtime_subagent`、`test_goal_cron_feedback_and_output_policy`、`test_system_tool_call_and_debug`、`test_evolution_cli`、`test_nvidia`、`test_notification_dispatcher` 等 |

这些失败涉及 skill 子系统、格式纠错、goal 执行规格、子代理探索等模块，与产出注册表无关。建议把它们单独列为"既有债务"，不在 P1 中顺手修复，以保持 P1 的改动边界清晰。

### 7.4 环境相关的副作用

既有测试会在当前工作目录下创建空的 `output_projects/` 目录（`ensure_output_projects_root` 使用相对默认路径）。这不是 P0 造成的，但它会让运行测试的人在项目根看到一个多余目录。建议后续给这类测试加上 `tmp_path` 隔离。

## 8. 已知限制

1. `list_projects()`（`evolution/output_projects_root.py`）目前仍会列出下划线开头的目录。P0 不修改该函数，待 P2 出现 `_registry/` 时再处理，以避免改变现有看板行为。
2. 盘点只统计文件，不解析 `PROJECT_INFO.md` 的内容，因此无法判断介绍是否过时。
3. 没有对 `.git` 仓库做状态检查（如是否有未提交变更）。这属于 P5 巡检的范围。
4. `goals_with_user_output_dir` 中的路径若是相对路径，按 `project_root` 解析；若用户手写了绝对路径，则按绝对路径解析。两种情况都已覆盖，但未对符号链接做特殊处理。

## 9. 下一阶段

P1（目标属性化）会修改 `goal_backlog.py` 的节点结构，属于写路径改动。开始前需要先补充 P1 的特征测试，保证现有目标的执行行为在改造前后一致。
