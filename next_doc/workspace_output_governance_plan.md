# 工作目录输出治理：排查记录与修复方案

## 1. 背景

用户要求全面梳理 daemon 执行期间会创建哪些目录/文件，并排查有哪些产出
"没有被规范地输出到预先定义好的目录下"（理论上不应该在项目根目录 `./`
下创建未被定义的目录）。

## 2. 现状盘点（结论）

代码层面（`.py` 里的 `mkdir`/`os.makedirs`）已经收敛得比较好：几乎所有
落盘路径都经由 `storage/paths.py::AgentPaths` 统一管理，分三层：

- `~/.agent/`（用户级，跨项目）
- `<project_root>/.agent/`（项目级，daemon 主要工作区：sessions/、
  daemon_run_outputs/、cron_jobs/、wiki/、notification/ 等）
- `<project_root>/output_projects/`（可配置 `output_projects_root`，
  刻意独立于 `.agent/` 的调研类产出目录，`resolve_root()` 会锚定
  `project_root`，且已写入 `.gitignore`，属于有意为之、已文档化的例外，
  不算问题）

真正的缺口不在 `.py` 代码里，而在**会被注入给 LLM 的 prompt/reminder
文本**——这些文本让模型自己判断该往哪写、要不要 `mkdir`，代码本身管不
到模型是否照做。排查到三处具体问题：

1. `prompts/reminders/write_large_file.md`：无条件建议"未指定路径时创建
   到 `./temp/`"，并给出 `bash("mkdir -p ./temp")` 的示例——是一段静态
   文本（`reminders/generator.py` 不支持变量替换），和 session 级
   `temp_dir` 机制完全脱节，最可能是项目根目录下出现游离 `./temp` 的
   直接原因。
2. `prompts/system/workspace_hygiene.md`："Atomic File Changes"一节里
   有两处写死的字面量 `./temp/`（其余段落都正确使用 `{{temp_dir}}`
   占位符），同一份文档前后矛盾。
3. `config/prompt_builder.py` → `prompts/manager.py`：`session_id` 为空
   时（`orchestrator/sub_agent.py` 显式记录"`session_id` 可能为
   None"、`agent/turn_loop.py` 的极端兜底分支都会命中），
   `temp_dir`/`output_dir` 留空，`prompts/manager.py` 里
   `temp_dir or "./temp"` 的兜底值会把字面量相对路径渲染进 system
   prompt。

## 3. 修复方案

### 3.1 三处 prompt/reminder 文本修复

- `write_large_file.md`：改为引用"system prompt 里给出的本 session
  Temp dir/Output dir"，不再出现任何具体路径字面量，也去掉
  `mkdir -p ./temp` 的建议（session 的 temp/output 目录已经在初始化时
  建好，正常不需要模型自己 mkdir）。
- `workspace_hygiene.md`：两处 `./temp/` 改成 `{{temp_dir}}` 占位符，
  与文档其余部分保持一致。
- `config/prompt_builder.py`：`session_id` 为空时不再让 `temp_dir_str`/
  `output_dir_str` 留空，而是调用新增的
  `AgentPaths.ensure_adhoc_working_dirs("unbound")`，落到
  `.agent/adhoc/unbound/{temp,output}/`——同样在 `.agent/` 管辖范围内，
  只是不挂在具体某个 session 目录下。`prompts/manager.py` 里对应的
  `or "./temp"`/`or "./output"` 兜底分支也一并去掉，换成一句不含可执行
  路径字面量的警示文字（防止调用方哪天又不小心传回空字符串时，模型
  把警示文字误当路径去 `mkdir`）。

对应改动文件：
- `src/mini_agent/prompts/reminders/write_large_file.md`
- `src/mini_agent/prompts/system/workspace_hygiene.md`
- `src/mini_agent/config/prompt_builder.py`
- `src/mini_agent/prompts/manager.py`
- `src/mini_agent/storage/paths.py`（新增 `adhoc_dir()` /
  `adhoc_temp_dir()` / `adhoc_output_dir()` /
  `ensure_adhoc_working_dirs()` / `created_dirs_registry_path`）

### 3.2 新增机制：目录"整体消失"健康检查（本次新增需求）

用户提出的新需求：当 agent 发现自己之前创建的目录（及目录下的文件）
整个都不存在了，应该考虑"很可能是用户手动删除的，原因很可能是这些
文件当初就建在了错误的目录下"，进而在后续任务里重新思考到底该建到
哪个目录，而不是在原地机械地重新创建同一个（可能本来就不规范的）
路径。

实现在新文件 `src/mini_agent/storage/created_dirs_registry.py`：

- `KNOWN_LEGACY_DIR_NAMES = ("temp", "output", "tmp")`：历史上出现过
  问题（被硬编码进 prompt/reminder、导致 agent 在项目根目录误建）的
  目录名清单，出现新的可以随时追加。
- `scan_known_legacy_dirs(paths)`：每次 session 绑定时扫描项目根目录下
  这几个名字，如果**当前存在**就登记/刷新进
  `.agent/created_dirs_registry.json`（不会主动创建它们）。
- `refresh_and_diagnose(paths)`：对比登记表与当前磁盘状态，识别两类
  情况，各生成一条一次性提醒（用 `acknowledged_vanished`/
  `acknowledged_present` 标记防止重复提醒）：
  - **vanished**：登记表里"上次看到时还有内容"，这次整个目录都不存在
    了 → 提醒 agent"大概率是用户手动删除，很可能是因为建错了位置，
    不要在原地重建，先确认这次产出该落到哪个规范目录"。
  - **still_present**：目录当前仍然存在且有内容，还没提醒过 → 提醒
    agent 评估是否需要迁移/清理，不建议自动处理（目录里可能有用户自己
    放的、agent 不知情的文件）。
- `check_workspace_directory_health(project_root)`：一站式入口，
  串联上面两步，任何异常都静默吞掉、返回空列表——这只是一个辅助提醒
  机制，不能因为自身出错影响正常的 session 绑定流程。

只做**记录 + 生成提示文本**，不做任何自动删除/自动迁移——这一点是刻意
的：目录里可能有用户自己放的、agent 完全不知情的文件，贸然处理比放着
不管更危险，最终去留应该留给用户/agent 结合上下文判断。

### 3.3 接线位置

- `AgentPaths` 新增 `created_dirs_registry_path` 属性
  （`.agent/created_dirs_registry.json`）。
- `agent/lifecycle.py::_bind_session_extras()`（在 `_init_session`/
  `load_session`/`new_session` 三个场景都会调用）里，紧跟着
  `ensure_session_working_dirs()`/`ensure_adhoc_working_dirs()` 之后，
  调用 `check_workspace_directory_health()`，把结果存进
  `self._workspace_health_notes`。
- `context_builder.py::ContextBuilder` 新增
  `workspace_health_notes_getter` 懒取参数（`agent/lifecycle.py` 里用
  `lambda: getattr(self, "_workspace_health_notes", [])` 接上），
  `build()` 里读取后传给 `config/prompt_builder.py::build_system_prompt()`
  的新参数 `workspace_health_notes`，最终拼进
  `system/workspace_hygiene.md` 顶部的 `{{workspace_health_notes}}`
  占位符。

### 3.4 验证情况

- 用一段脚本模拟"legacy 目录存在→提醒一次→不重复提醒→目录被删除→
  提醒一次 vanished→不重复提醒"的完整生命周期，行为符合预期。
- 用 `prompts.pm.build_system_prompt()` 直接渲染，确认
  `workspace_health_notes` 会正确出现在输出文本里，且
  `temp_dir`/`output_dir` 为空时不再输出任何可执行的相对路径字面量。

### 3.5 本次未做的事

- 不做历史遗留目录（如果确实存在 `./temp`、`./output`）的自动清理/
  迁移——由新增的健康检查机制提醒 agent/用户自行判断处理。
- 不引入额外第三方依赖，`created_dirs_registry.json` 用标准库 `json` +
  临时文件原子替换实现，与仓库里其它落盘代码风格一致。
