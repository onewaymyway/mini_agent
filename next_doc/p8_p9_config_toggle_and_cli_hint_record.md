# P8/P9 收尾：配置开关化 + CLI 输入提示补全 · 实施记录

> 背景：`session_to_workflow_design.md`（P8）和
> `workflow_system_next_directions.md`（P9 第一批：1a/1b/2/3）功能本身已经
> 实施完成（详见 `workflow_system_p9_implementation_record.md`），但存在两处
> 遗漏：① 部分功能没有对应的 `agent_config.json` 开关，无法在不改代码的
> 前提下关闭；② 新增的 CLI 子命令没有同步进 Tab 补全列表。本文档记录这一轮
> 收尾改动。

## 1. 新增配置开关（`agent_config.json` → `workflow` 段）

| 配置项 | 默认值 | 控制的功能 |
|---|---|---|
| `session_to_workflow_enabled` | `true` | P8：`list_recent_sessions`/`summarize_session_for_workflow`/`build_workflow_from_summary` 三个 Agent 工具 + CLI 的 `/workflow sessions`/`/workflow from-session` |
| `condition_static_check_enabled` | `true` | P9-3：`WorkflowDef.validate()` 里 condition 表达式的静态一致性检查（引用是否存在/是否在 depends_on 范围内）；关闭后仍做 ast 语法检查 |
| `dry_run_preview_on_generate` | `true` | P9-1b：`generate_workflow`/`build_workflow_from_summary` 生成 YAML 后自动追加的一次 dry-run 预览 |
| `git_hint_enabled` | `true` | P9-2：`save_workflow` 保存成功后的 git commit 提示文案 |

设计原则（与已有开关如 `validate_placeholders_on_save`/`script_step_enabled`
保持一致）：
- 全部默认开启，保持与"功能刚实施完成时"的行为一致，不需要用户手动开启；
- 关闭后不是报错，而是给出明确的"功能已在配置中关闭"提示（工具/CLI 两处
  都是），或者静默跳过纯提示性质的增强（dry-run、git 提示）；
- `cfg` 为 `None`/字段缺失时一律通过 `getattr(..., True)` 兜底为默认开启，
  兼容尚未升级 `agent_config.json` 的旧项目。

## 2. 涉及的代码改动

| 文件 | 改动 |
|---|---|
| `src/mini_agent/config/models.py` | `WorkflowConfig` 新增上述 4 个字段及注释 |
| `agent_config.json` | `workflow` 段新增对应 4 个键，默认值均为 `true` |
| `src/mini_agent/workflow/schema.py` | `WorkflowDef.validate()` 新增 `check_condition` 参数；condition 静态一致性检查（引用存在性/depends_on 范围）包进 `if check_condition:`，ast 语法检查本身不受此开关影响（笔误应始终被拦截） |
| `src/mini_agent/workflow/store.py` | `WorkflowStore.save()` 读取 `cfg.workflow.condition_static_check_enabled` 并传给 `validate()` |
| `src/mini_agent/workflow/generator.py` | `WorkflowGenerator.parse_yaml()`（生成阶段的预览期校验）同样读取该开关，与最终 `save()` 行为保持一致 |
| `src/mini_agent/workflow/tools.py` | `generate_workflow`/`build_workflow_from_summary` 的 dry-run 段落包进 `if getattr(cfg.workflow, "dry_run_preview_on_generate", True):`；`save_workflow` 的 git 提示包进 `if getattr(cfg.workflow, "git_hint_enabled", True):`；`list_recent_sessions`/`summarize_session_for_workflow`/`build_workflow_from_summary` 三个工具函数体开头新增 `session_to_workflow_enabled` 开关检查，关闭时直接返回提示文本 |
| `src/mini_agent/cli/commands/workflow_cmd.py` | `_handle_sessions`/`_handle_from_session` 同步新增开关检查 |
| `src/mini_agent/ui/terminal.py` | **Tab 补全修复**：`_COMMANDS` 里 `/workflow` 的子命令列表补上 P9 新增却一直遗漏的 `stats`/`history`/`diff`（`sessions`/`from-session` 此前已补，这三个此前没有同步） |

## 3. 为什么开关粒度选在"工具/命令级别"而不是"更细的行为级别"

`session_to_workflow_enabled` 是三个工具+两个 CLI 命令共用一个开关（而不是
每个工具单独一个开关）：这三者是 P8 设计里明确要求"按顺序调用、不可拆开"
的一个完整两段式流程（见 `session_to_workflow_design.md` §5.1 末尾），单独
开关化没有实际使用场景（不存在"只想要总结阶段，不想要构建阶段"这种需求），
拆细了反而增加配置项数量却没有对应价值。

`dry_run_preview_on_generate`/`git_hint_enabled` 同理：都是"生成/保存流程
里追加的一段锦上添花的展示内容"，本身有明确的独立开关意义（用户可能觉得
输出太长想精简，或者项目不用 git 想关掉那句提示），且互相独立，符合"选取
真正需要单独控制的功能点"这个标准。

## 4. 验证记录

- 所有改动文件通过 `python3 -m py_compile`。
- `pytest tests/test_session_to_workflow.py tests/test_workflow_directory_mode.py
  tests/test_workflow_parallel.py tests/test_workflow_step_session_dir.py
  tests/test_workflow_step_types.py` 共 98 个用例全部通过，未因新增
  `check_condition`/开关参数产生回归（新增参数均带默认值 `True`，与开关
  开启前的行为完全一致）。
- 手工确认：`WorkflowConfig()` 默认构造后 4 个新字段均为 `True`，
  `agent_config.json` 里新增键与之对应。
- 手工检查 `_COMMANDS` 列表，确认 `/workflow` 全部 21 个子命令
  （`list/show/run/runs/status/resume/pause/cancel/approve/reject/input/
  templates/from-template/delete/to-dir/sessions/from-session/stats/
  history/diff`）均已出现在 Tab 补全列表中。

## 5. 未涉及的部分

- 未新增任何新功能，本轮改动全部是"给已实施功能补开关 + 补 CLI 提示"，
  不引入新的设计决策，因此不需要单独的 `xxx_design.md`。
- `workflow_system_next_directions.md` §4/§5（主动感知建议、权限信任模型）
  仍未启动，不在本轮范围内。
