# 看板工作流可视化编辑器 + 续跑定义快照修复 —— 实施记录

> 对应方案：`next_doc/workflow_visual_editor_plan.md`。方案本身不动，
> 每个里程碑完成后在这里追加一节，不混进方案文档。

## M1：续跑定义快照修复（`use_latest_definition`）

### 1. 复现结论

按方案 §6.1 第 1 步写了 `tests/test_workflow_resume_latest_definition.py`：
创建一个必然失败的单 step run（mock 执行抛异常、`retry_on_error=0` 不
重试）→ `patch_workflow_step` 改该 step 的 prompt → `resume_workflow_run`。

**复现过程中发现一个比方案描述更根本的既有 bug**（方案 §六本身没有预料
到，需要额外记录）：`runner.py` 的批次循环里，无论批次内的 step 是成功
还是跑到 `FAILED`/`NEEDS_FIX`/`GATE_FAILED`/`TIMEOUT`，运行完都会无条件
把 `wf_session.current_batch_index` 推进到下一批次。而 resume 时批次
循环的入口判断是 `if batch_index < wf_session.current_batch_index:
continue`（跳过"已经跑过"的批次）。两者叠加的后果：**只要一个 step 失败
过一次，它所在的批次就会被永久标记为"已跑过"，之后任何
`resume_workflow_run`（不管带不带 `force_rerun_from`、带不带
`use_latest_definition`）都不会重新进入这个批次，该 step 永远不会被
重新执行**——不是"用了旧定义"，而是"根本没有重新执行"。这与
`docs/workflow-guide.md`「断点续跑优先于从头重来」的设计初衷直接矛盾，
也让 `force_rerun_from` 清空下游 `step_results` 的整套机制在"failed 后
resume"这个最常见场景下完全打不到点上（`_mark_downstream_for_rerun` 只
清空 `step_results`，从未接触 `current_batch_index`）。

修复（`runner.py`，与 `use_latest_definition` 无关，是它的前置修复）：
只有当一个批次内所有 step 都落在 `pending_in_batch` 判断同一套终态集合
（`DONE`/`SKIPPED`/`CANCELLED`/`REJECTED`）时才推进
`current_batch_index`；一旦出现不在此集合内的 step（`FAILED`/
`NEEDS_FIX`/`GATE_FAILED`/`TIMEOUT`），之后所有批次都不再推进
`current_batch_index`（即便本次调用里后续批次因依赖失败被标记
`SKIPPED`），把"下一次 resume 应该从哪个批次重新进入"精确钉在第一个
未解决的批次上。用一个布尔量 `_batches_settled_so_far` 实现，一旦置
`False` 就不再改写 `current_batch_index`。

这个修复做完之后，最初的复现测试才能真正跑到"resume 重新执行了 step，
但用的是旧 prompt"这一步——确认方案 §六描述的快照问题本身也成立。

### 2. `use_latest_definition` 实现

按方案 §6.1 第 2/3 步实现，全链路：

- `workflow/api_helpers.py`：
  - 新增 `load_definition_for_resume(cfg, wf_session, snap_path,
    use_latest_definition)`，从原来揉在 `resume_workflow_run` 里的一段
    加载逻辑抽出来，供 `resume_workflow_run` 自己和
    `cli/commands/workflow_cmd.py::_handle_resume` 复用（CLI 之前是手工
    重复了一遍"读快照 → `parse_yaml`"，缺了 source_dir/prompt_file 的
    bugfix，也没有 `use_latest_definition` 支持；抽成公共函数后两边永远
    同步，顺带把这个既有的 CLI 独立入口缺陷也修了）。
  - `resume_workflow_run` 新增 `use_latest_definition: bool = False`
    参数，为真时：`WorkflowStore.load()` 重新加载当前定义 → 跑一遍
    `WorkflowDef.validate()` → 校验通过则重写快照文件、
    `wf_session.current_batch_index` 归零、按 id 对比算出孤儿
    step（新定义里已经不存在的 id，仍保留在 `step_results` 里但不参与
    调度）写进返回值 `warnings`；校验失败或工作流已被删除直接抛
    `WorkflowApiError(code="bad_snapshot")`，不回退到旧快照。
    `force_rerun_from` 按新定义校验（`_mark_downstream_for_rerun` 原本
    就会在 step_id 不存在时抛 `code="bad_step"`，复用不变）。
  - `get_workflow_run_detail` 新增 `definition_changed`
    字段，及独立的 `_compute_definition_changed(cfg,
    workflow_session_id, workflow_name)` 辅助函数（`workflow/tools.py`
    的 `get_workflow_run_status` 也直接调用它，两处共享同一个判断）：
    比较快照 `WorkflowDef.to_dict()` 与当前 `WorkflowStore.load()` 出来
    的 `to_dict()` 是否相等；快照缺失/解析失败/当前定义读取或校验失败
    时返回 `None`（"无法判断"），不误报 `True`/`False`。
- `workflow/tools.py`：`resume_workflow_run` 工具、`get_workflow_run_status`
  工具的 docstring/description 和输出文案同步更新；`get_workflow_run_status`
  在 `definition_changed` 为真时追加一行提示。
- `api/routes.py`：`POST /v1/workflow_runs/{id}/resume` 的 Body 支持
  `use_latest_definition`，响应里带 `warnings`；
  `GET /v1/workflow_runs/{id}` 自动带上 `definition_changed`（走
  `get_workflow_run_detail` 原样透传，不需要额外改动）；路由表注释同步。
- `cli/commands/workflow_cmd.py`：`_handle_resume` 重构为调用
  `api_helpers.load_definition_for_resume`，新增 `--latest` 参数
  （REPL `/workflow resume` 和独立 `mini-agent workflow resume` 两个
  入口共用同一份实现，天然都支持）；`--latest` 传给独立后台进程
  （`_spawn_detached_run`）时原样透传。
- `apps/mini_agent_kanban/client.py`：`resume_workflow_run()` 新增
  `use_latest_definition` 参数。
- `apps/mini_agent_kanban/app.py`：
  - "保存修改并从此步骤续跑"按钮（`patch_workflow_step` 之后紧跟着的
    `resume_workflow_run`）改为默认传 `use_latest_definition=True`——
    这正是方案 §2.3 怀疑的问题场景，不加这个参数的话 patch 的改动不会
    生效。
  - "以此结果继续（重跑下游步骤）"按钮（`override_workflow_step_output`
    之后的 resume）**不改**：这个场景改的是已落盘的 step 输出值本身，不
    是工作流定义，`use_latest_definition` 在这里没有意义。
  - run 详情页新增 `definition_changed` 为真时的
    "⚠️ 该次执行使用的是旧定义快照，当前定义已修改"提示；通用的
    "▶️ 续跑"按钮旁新增一个只在 `definition_changed` 为真时才出现的
    "续跑时改用最新定义"勾选框。
  - 编辑器保存后在最近运行上提供"用最新定义续跑"入口——方案 §六 5 提到
    的这一部分依赖 M4 的可视化编辑器，本里程碑尚未实现，留给 M4/M5。

### 3. 测试

`tests/test_workflow_resume_latest_definition.py`，12 个测试：

- `TestResumeReproducesStaleSnapshot`：修完 batch-index bug 之后，确认
  默认（不传 `use_latest_definition`）续跑确实还在用旧 prompt——锁定
  向后兼容行为。
- `TestResumeUseLatestDefinition`：`use_latest_definition=True` 拿到
  新 prompt；重写快照后下一次不传参数的 resume 也沿用新定义；已完成
  step 结果按 id 保留、不重新执行；`force_rerun_from` 引用新定义里不
  存在的 step 报 `bad_step`；孤儿 step 结果列进 `warnings` 不静默丢弃；
  当前定义已被删除时报错、不回退。
- `TestDefinitionChangedFlag`：`definition_changed` 在未改动/已改动/
  当前定义不可读三种情况下分别是 `False`/`True`/`None`。
- `TestCliResumeLatestFlag`：CLI `_handle_resume` 带 `--latest`/不带
  两种情况下分别拿到新/旧 prompt，验证 CLI 复用
  `load_definition_for_resume` 之后行为与 API/工具一致。

回归：`PYTHONPATH=src python3 -m pytest tests/ -q -k workflow
--ignore=tests/test_session.py
--ignore=tests/test_stock_watch_optimization_loop_e2e.py`
（后两个 ignore 是环境问题——`test_session.py` 导入了一个已被移除的
`_flock` 符号，`test_stock_watch_...` 缺 `websocket` 模块，均与本次改动
无关，collection 阶段就失败，不是本次改动引入）——**205 passed, 4
failed**；这 4 个失败（`test_session_to_workflow.py` 1 个、
`test_workflow_p11.py` 2 个、`test_workflow_p15.py` 1 个）在还原
`runner.py` 到改动前的版本后单独跑同样失败，确认是与本次改动无关的
既有失败（环境缺依赖 / 环境相关，非本次引入的回归）。

### 4. 已知限制 / 留给后续里程碑

- 方案 §六 5 提到的"编辑器保存后在最近运行上提供'用最新定义续跑'"依赖
  M4 的可视化编辑器 UI，本里程碑未实现。
- `force_rerun_from` 清空的是"下游"（不含自身）——若用户想让
  `force_rerun_from` 指定的 step 自己也用新 prompt 重跑，需要该 step
  本身状态不是 `DONE`（比如本来就是 `FAILED`，这也是最常见的触发场景）；
  若该 step 已经 `DONE` 又想强制用新定义重跑它自己，目前仍需要配合
  `override_step_output` 或后续里程碑补充的机制，`use_latest_definition`
  本身不改变 `force_rerun_from` "自身沿用当前落盘结果"这条既有语义。
- REST `resume` 路由目前不支持 `step_overrides`（这是改动前就有的现状，
  不是本次引入的缺口，未在本里程碑范围内一并补上）。

## M2：后端编辑（`editor_helpers.py` + 路由 + 配置）

### 1. 交付内容

- `workflow/store.py`：从 `save()` 抽出 `validate_def(wf, cfg, role_checker)`（同一组开关、同一套校验），
  `save()` 行为不变；新增公开的 `resolve_path(name)`。
- `config/models.py::WorkflowConfig`：`visual_editor_enabled`（默认 `True`）、`editor_backup_keep`（默认 20），
  走 `param_registry` 的 nested block，未动 `loader.py`；`agent_config.json` 同步写入默认值。
- 新增 `workflow/editor_helpers.py`：`load_for_edit` / `validate_draft` / `save_draft` /
  `list_backups` / `restore_backup` / `editor_meta`，以及编辑器专用的 `WorkflowEditorError`
  （`WorkflowApiError` 子类，多带 `payload`）。
- `api/routes.py`：`GET /v1/workflows/{name}/editor`、`POST .../editor/validate`、`PUT .../editor`、
  `GET /v1/workflow_editor/meta`；错误 `detail` 为对象 `{message, code, ...}`；文件 IO 走
  `asyncio.to_thread`，不阻塞事件循环。备份列表 / 恢复的**端点**留给 M5（helper 已就绪并有测试）；
  `create_workflow` 同样留给 M5。
- 依赖：`ruamel.yaml>=0.17` 加入 `requirements.txt` 与 `pyproject.toml`。

### 2. 与方案的偏差 / 补充（实施中的判断）

1. **缩进风格自适应**：方案 §5.3 第 6 点给的是固定 `indent(mapping=2, sequence=4, offset=2)`，但既有文件里
   PyYAML 输出（序列不缩进）与手写（序列缩进 2）两种风格并存，固定参数会让整份文件的序列行全部改缩进。
   实现改为：对未改动的文档用若干候选缩进各 dump 一次，选与原文逐行相同数最多的一组；方案里的那组是其中之一。
2. **语义回读保险**（方案未写）：写盘前把生成的 YAML 重新解析，必须与草稿语义完全一致，否则抛
   `sync_mismatch` 拒绝写入。目的是即便 ruamel 同步在极端排版下出错，也不会落一份与用户所见不一致的文件。
3. **`renames` 参数**：`validate` / `save` 接受 `{旧id: 新id}`，让"改 id"的 step 仍匹配到原节点、保住注释，
   而不是被当成"删一个、加一个"（方案 §4.5 的改 id 联动由 M3/M4 的看板侧实现，这里只提供后端接口）。
4. **None 语义**：草稿里值为 `None` 的顶层 / step 级键视为"已清空"——丢弃该键（原文档没有则不写成 `key: null`，
   有则删除）；原文档本来就显式 `key: null` 且未动则保持。嵌套用户数据（`params` / `tool_args`）里的 null 原样保留。
5. **`prompt_hashes`**：`load_for_edit` 额外返回各 `prompt_file` 的字节 hash，保存时若传入则同样做乐观锁，
   避免覆盖别处改过的 prompt 文件（方案 §5.4 只写了 yaml 的 `base_hash`）。
6. **名称不可改**：草稿的 `name` 与磁盘不同视为校验错误（改名会让按名字定位文件的所有入口错位）。
7. **缺失 `prompt_file` 只给 warning**：文件不存在且草稿未提供正文时不阻断保存（否则一个已损坏的旧工作流
   连别处的修复都存不了）；越界则是 error。
8. **`editor_backup_keep` 最小按 1 处理**：不提供"完全不备份"，保留安全网。
9. 角色未注册、`script` / `python_step` 运行期开关关闭均只给 warning，不阻断保存，也不绕过运行期开关。
10. **`validate()` 本身不检测环**：环检测是编辑器校验新增的（DFS 找出具体环路径，环上每个节点都归入
    `errors_by_step`）；`store.save()` 的行为保持不变，未顺手改。

### 3. 已知限制

- 注释保留的限制与方案 §5.3 一致：删除 step 时紧贴其前后的独立注释行可能丢失或错位；重排 step 时独立成行的
  注释可能跟着相邻 step 移动（测试只固定语义结果与行尾注释，不固定这类位置）。
- `include` 节点没有显式 `id` 时，画布节点 id 的约定（本实现内部用 `include:<片段名>` 匹配、错误归到片段名）
  留给 M3 的图转换模块统一。
- Windows：`atomic_write_text` 以文本模式写入，换行由平台决定；本模块读取时统一按 `\n` 处理，不主动写 CRLF。

### 4. 测试

- `tests/test_workflow_editor_helpers.py`（51 个）：往返保真（无修改字节不变且不产生备份、改一行只有该行 diff、
  注释 / 引号 / `|` 块样式保留、PyYAML 风格文件、多行字符串、新增 / 删除 / 重排 / 改名 step、include 与相对
  `script_path` 不被摊平、None 语义）；ruamel 缺失降级（含确认流程）；保存护栏（hash 冲突与强制覆盖、
  校验失败不落盘不备份、开关关闭、备份字节一致与超量清理、原子写入失败不留残留、`sync_mismatch`、改名拒绝）；
  `prompt_file` 写回 / 备份 / 越界 / 冲突；校验（批次、环、依赖 / condition / 占位符 / 类型必填 / autonomous /
  字段类型错误的节点归类、include 归属、`script_path` 越界、运行期开关 warning）；备份恢复（含 prompt 文件、
  非法 id）；`editor_meta`；`store.validate_def` 与 `save()` 一致；配置字段默认值与从 `agent_config.json` 加载。
- `tests/test_workflow_editor_routes.py`（11 个）：四个端点的状态码与 `detail` 结构（200 / 400 / 403 / 404 /
  409 / 422），写入开关关闭时 GET 仍可用，既有 `GET /v1/workflows/{name}` 不受影响。
- 回归：`tests/test_workflow*.py`、`test_session_to_workflow.py`、`test_zhihu_workflow_steps.py` 共 271 个中
  266 通过；5 个失败在未改动的原始代码上同样失败（`test_workflow_p11` 3 个中有 1 个本身不稳定、
  `test_workflow_p15` 1 个、`test_session_to_workflow` 1 个），与本里程碑无关。
  （`streamlit` 缺失导致部分看板测试无法在沙箱收集，未运行。）

## M3：看板侧纯函数模块与 client 方法

### 1. 交付内容

- 新增 `apps/mini_agent_kanban/workflow_editor.py`：**纯函数**（不 import streamlit、无网络），M4 的 UI
  只负责渲染与事件转发。所有"改草稿"的函数不原地修改入参，返回新草稿（深拷贝）。
  - 图转换：`draft_to_graph`（节点含图标 / 配色 / 徽标 🔒🔀🧩⚠️ / 错误·警告边框 / 批次；`depends_on` 实线、
    不重复的 `merge_sources` 虚线只读；指向不存在节点的引用进 `dangling` 而不画边）、`compute_layers`（本地 Kahn 分层，
    有环也不丢节点）。
  - 依赖编辑：`find_cycle` / `would_create_cycle`（返回具体环路径）/ `add_dependency`（拒绝自依赖、重复、成环）/
    `remove_dependency` / `dependency_diff` + `apply_dependency_diff`（画布边差集 → depends_on，逐条环检测，
    失败的收集报错、其余照常应用）。
  - 节点操作：`add_step`（各类型空骨架）/ `duplicate_step` / `delete_step` + `analyze_delete`（影响面：谁依赖它、
    谁的 condition / 占位符还引用它）/ `insert_between`（方案 §4.2「插入到边间」的纯函数，UI 在 M5）/
    `validate_new_id` / `unique_step_id`。
  - 改 id 联动 `rename_step`：改写其它 step 的 `depends_on` / `merge_sources` / `condition` / 各文本字段里的
    `{old.xxx}` 占位符（含 `tool_args` 嵌套、`prompt_file` 正文），返回 `renames`（传给后端保住注释）与
    `changes` 清单（进"查看变更"）。
  - 校验归类：`group_messages`（与后端同口径的本地兜底）、`normalize_validation`、`parse_editor_error`
    （把 client 失败返回值归成 conflict / validation / disabled / needs_confirm / not_found / network / other）。
  - 变更预览：`semantic_equal` / `is_dirty` / `summarize_changes` / `build_change_diff`（git 风格 unified diff，
    可直接喂给 `diff_view.parse_unified_diff`，方案 §2.4 的复用点）。
  - 降级渲染 `to_dot`（graphviz DOT，选中高亮、错误红框、merge 虚线）。
  - 属性面板字段清单 `STEP_FIELD_SPECS` / `ADVANCED_FIELD_SPECS` / `WORKFLOW_LEVEL_FIELD_SPECS` / `fields_for` /
    `runtime_switch_note`（§4.4 的表单清单数据化，include 节点只开放 `id` / `depends_on`）。
- `apps/mini_agent_kanban/client.py`：`workflow_editor_meta` / `workflow_editor_doc` / `validate_workflow_draft` /
  `save_workflow_draft`，省略的可选参数不出现在请求体里。

### 2. 与方案的偏差 / 补充

1. **client 用独立的 `_editor_request`，不复用 `_get/_post/_put`**：既有三者遇到非 200 只返回被截成 200 字符的
   `_error` 文本，而编辑器必须拿到后端 detail 的**完整结构**（422 的 `errors_by_step` 用来给节点标红、
   409 的 `current_hash` 用来做"重新加载 / 强制覆盖"）。新方法仍以 `_error` 表示失败（与既有 UI 判断方式一致），
   额外带回 `_status` 与 `_detail`。
2. **condition 改写基于 AST 而非正则**：只改 `ast.Name` 节点，因此 `inputs.old` 的属性名、字符串字面量
   `'old'`、同前缀标识符 `old_x` 都不会被误伤；AST 列偏移是 UTF-8 字节偏移，已按字节处理（中文 id 有测试）；
   语法错误的表达式原样保留不猜。占位符只改带点的 `{old.xxx}`（后端规则：无点的 `{param}` 是运行时 inputs）。
3. **`prompt` 出现在 tool_call / sub_workflow / script 的字段清单里**：后端 `validate()` 要求这三类（以及 skill_agent）
   prompt 非空（除非用 `prompt_file`），面板若不暴露就永远存不了。清单里标注"校验要求非空"。
4. **新节点骨架只放必填键、值留空**，不塞占位内容——校验会如实报"还没填"（测试断言各类型空骨架都不能悄悄通过后端校验，
   仅 `wait` 骨架本身合法）。
5. 复制带 `prompt_file` 的节点时，副本**不共享文件**：有正文就内联成 `prompt`，没有就只去掉 `prompt_file`。
6. 新增 id 的字符约束（无空白 / `.` / `{}` / 引号 / `:` / `,` / `[]` / `#`）来自占位符与 condition 的解析规则；
   非合法 Python 标识符的 id 允许但提示"之后无法在 condition 里引用"。
7. `build_change_diff` 是**语义预览**（规范化后的 YAML 文本对比），与磁盘最终的逐行 diff 可能因格式细节略有出入，
   但不会漏报内容变化；真正的逐行保真由后端保存保证（M2）。

### 3. 测试

- `tests/test_workflow_editor_graph.py`（40 个）：图转换（类型推断、徽标、include 无 id、merge 虚线去重、悬空引用、
  错误 / 批次进节点）；环检测与依赖编辑（含环路径、自依赖 / 缺失 / 重复、画布边差集与成环边被拒但其余生效）；
  增删复制插入（id 唯一、prompt_file 内联、删除联动与影响面、merge_sources 清理、边上插入）；改 id 联动
  （deps / condition / 占位符 / 嵌套 tool_args / prompt_file 正文；不误伤前缀同名、`inputs.a`、字符串字面量、
  `{a}` 与 `{data.a}`；中文 id；语法错误表达式；纯函数性）；错误归类与解析；`is_dirty` / 摘要 / diff 可被
  `diff_view` 解析；DOT；字段清单与后端 `WorkflowStep` 字段一致、样式覆盖全部内置类型；新骨架与后端校验联动；
  client 请求方法 / 路径 / 请求体与失败时保留完整 detail。
- 回归：`test_kanban_client_goal_tree_extras.py`、`test_kanban_diff_view.py` 与 M2 的 62 个测试一并通过。

### 4. 留给后续里程碑

- M4：Streamlit UI（入口切换、画布 / 降级图、属性面板、diff / 保存流程、冲突处理），`_flow_available()` 探测。
- M5：拖线 / 点边删依赖、新建 / 复制工作流、边上插入、并行批次高亮、单步试运行、备份恢复端点与 UI、
  "编辑器保存后在最近运行上用最新定义续跑"入口（M1 遗留）。

---

## M4：看板 UI（Streamlit 渲染层）

### 1. 交付内容

全部落在 `apps/mini_agent_kanban/app.py`（新增，无独立文件——M3 已经把纯函数拆到 `workflow_editor.py`，
这里只做渲染与 session_state 管理，沿用 `diff_view.py` / `async_job_ui.py` 的拆分先例）：

- **入口切换**（§4.1）：`render_workflow_tab` 顶部加 `st.radio`「▶️ 运行与记录」/「✏️ 编辑器」，原有运行面板
  原样保留在前者（`_render_workflow_run_panel`，未改动一行）；后者转发到新增的 `_render_workflow_editor_tab`。
- **编辑器主流程** `_render_workflow_editor_tab`：选工作流 → "打开 / 重新加载"（`_wfed_reload`，把
  `workflow_editor_doc()` 的返回值摊进 `st.session_state[f"wfed_state_{name}"]`：`draft` / `original` /
  `prompt_files` / `original_prompt_files` / `prompt_hashes` / `base_hash` / `renames` / `selected_node` /
  `validation` 等）→ 顶部状态行（🟠/🟢 脏标记、`editor_enabled=false` 时的 🔒 提示、无 ruamel 时的 ⚠️ 提示）
  → 「✅ 校验」/「💾 保存」/「↩️ 放弃修改」→ 冲突（409 conflict：重新加载 / 强制覆盖）与需要确认
  （409 needs_confirm：会丢注释，二次确认）两条专门的解决流程 → 「🔍 查看变更」（`build_change_diff` 接
  `diff_view.parse_unified_diff` + `summarize_files`，复用 M3 §2.4 的复用点）→ 画布 + 节点操作 + 属性面板
  两栏布局 →「⚙️ 工作流属性」折叠区（`WORKFLOW_LEVEL_FIELD_SPECS`）。
- **画布**：`_flow_available()` 探测到装了 `streamlit-flow-component` 时，`_wfed_render_flow_canvas` 用
  `StreamlitFlowNode/Edge` + `LayeredLayout(direction="down")` 自动分层、`get_node_on_click=True` 点选
  （`allow_new_edges=False`——拖线加依赖是 M5）；节点样式（填色 / 边框 / 徽标）直接吃 M3 `draft_to_graph`
  算好的值。未安装时降级为 `st.graphviz_chart(we.to_dot(...))` 只读图 + `st.selectbox` 节点选择器
  （方案 §七的降级路径，功能完整只是没有点选和拖线）。
- **节点操作**：画布下方「新增 / 复制 / 删除」三个按钮，分别接 `we.add_step` / `we.duplicate_step` /
  `we.delete_step`；删除前用 `we.analyze_delete` 的影响面（谁依赖它、谁的 merge_sources 引用它、谁的
  condition / 占位符还引用它）做二次确认卡片，而不是删了才提示。
- **属性面板** `_wfed_render_property_panel`：`st.form` 一次性提交（改 id / 名称 / depends_on 多选 / 类型
  专属字段 / 高级字段折叠区），"应用到草稿"时：先按需 `rename_step`（`sync_refs` 复选框控制是否联动改其它
  节点的引用），再对 `depends_on` 算差集分别 `add_dependency` / `remove_dependency`（加依赖失败——比如会成
  环——只中断加依赖这一步，已经应用的改名和删依赖不回滚，报错里点名是哪条边），最后把表单字段写回 step。
  include 节点只展开 `id` / `depends_on` 两个字段（M3 `is_include` 的口径）。字段按 `kind` 分发到具体
  widget：`str/text/int/float/bool` 直给对应控件；`tribool` 用「继承默认/是/否」三选一映射
  `None/True/False`；`json` 用 `st.text_area` + `json.loads`，解析失败当场报错且**不**写回草稿（避免非法
  值污染 draft）；`list` 按行分割；`select/multiselect` 从 `workflow_editor_meta()` 的 `roles/tools/skills/
  workflows/merge_strategies/modes` 或当前草稿的 step id 列表（`merge_sources` 用）取选项。
- **改 id 的连续改名合并** `_wfed_merge_renames`：同一次编辑会话里"a→b 再 b→c"，累积成 `{a: c}` 传给后端，
  否则后端按 `renames` 找旧注释锚点时会因为中间态 `b` 已经不存在而找不到。
- **保存** `_wfed_save`：正常路径更新 `original`/`base_hash`、清空 `renames`、`st.toast` 提示；三种失败分支
  （`conflict` / `needs_confirm` / `validation`）各自落一个 session_state 标记，交给上面提到的两条专门 UI
  处理；`validation` 分支直接把后端返回的 `errors_by_step` 灌回 `state["validation"]`，不用户再点一次「校验」
  就能在节点上看到红框。
- `apps/mini_agent_kanban/workflow_editor.py` 新增公开别名 `deps_of()`（= 原来的模块私有 `_deps()`），
  UI 层读某节点当前依赖走这个，不直接碰下划线私有函数。
- `apps/mini_agent_kanban/requirements.txt` 补充可选依赖 `streamlit-flow-component>=1.6.1` 的说明与降级指引。

### 2. 与方案的偏差 / 补充（实施中的判断）

1. **字段编辑用「表单 + 应用」按钮，不做逐字段实时联动**：方案 §4.4 没有强制要求实时保存；Streamlit 的
   rerun 模型下，逐字段改一下就整体 rerun 一次会导致文本框输入卡顿、光标跳动，改用 `st.form` 一次性提交
   （"改 id / 名称 / 依赖 / 各字段"打包成一次 `st.rerun()`）体验更稳，且和现有「🛠️ 修改此步骤定义」面板
   的既有交互风格一致。代价是画布上的选中节点切换后，面板里没提交的未保存输入会丢——可接受，字段改动量通常
   不大。
2. **画布组件的"点击选中"与"Python 侧强制切换选中节点"会打架，已在实现里修正**：新增 / 复制节点后 UI 会把
   `state["selected_node"]` 设成新节点 id 并 `st.rerun()`，但 `streamlit-flow-component` 的组件状态是
   跨 rerun 由前端缓存的——如果节点集合没变（只是选中变了），组件不会自己用新的 `selected_id` 重新渲染，
   会用它自己缓存的上一次选中态覆盖回来，导致"新增节点后画布却还选中着旧节点、属性面板对不上"。修复方式：
   `_wfed_render_flow_canvas` 复用缓存状态时，无条件用调用方传入的 `selected`（= `state["selected_node"]`，
   我们自己维护的单一事实来源）覆盖缓存对象的 `selected_id`，只有真正的用户点击（组件返回值）才会反过来
   更新它。这个问题是在写完整套烟雾测试（见下）之后才复现出来的，不是纯靠读代码能看出来的时序 bug，值得
   在这里记一笔，免得以后"优化"画布缓存逻辑时把这行覆盖删掉。
3. **`_flow_available()` 捕获 `Exception` 而非只捕获 `ImportError`**（`_sortable_available` 只捕获
   `ImportError`）：`streamlit-flow-component` 的 `__init__.py` 在 import 阶段就会调用
   `st.components.v1.declare_component()` 注册前端资源，脱离真正的 Streamlit runtime（比如前端构建产物
   缺失、或被非 `streamlit run` 的方式加载——本阶段的烟雾测试就踩了这个）时抛出的不一定是 `ImportError`，
   不兜住会直接带崩整个编辑器 tab，而不是优雅降级成只读图。
4. **`json` 字段解析失败时用哨兵值 `_WFED_JSON_ERROR` 跳过写回，而不是拿旧值兜底或直接报错中断**：这样
   "一个字段 JSON 写挂了"不会连累同一次提交里其它已经改对的字段（id / 依赖 / 其它字段照常应用），也不会
   悄悄把用户的错误输入丢弃换成看不出改动的旧值。
5. **依赖更新中途失败（加某条依赖会成环）不回滚已经生效的其它修改**：改 id、删依赖、其它已经处理完的加依赖
   仍然应用，只中断"加依赖"这一步剩余的边，并在错误信息里点名是哪一条——用户少点一次"回退重来"，只需要
   针对性地再调整那一条依赖。
6. **`workflow_editor_meta()` 按当前选中的工作流懒加载并缓存在 `session_state`**（方案没有明确要求缓存
   粒度）：因为 `meta` 里的 `roles/tools/skills` 是"当前工作流目录下能看到的"本地资源，换工作流要重新拉；
   同一个工作流反复渲染不用每次都请求。
7. **`is_include` 的节点没有"类型专属字段"和"高级字段"折叠区**：`fields_for()` 对 include 节点本来就只在
   `id`/`depends_on` 两个通用字段生效，UI 侧对应地跳过整段类型字段渲染，而不是渲染出一堆空字段再让用户
   发现填了也没用。

### 3. 测试

- 复用并重跑了 M3 的 `tests/test_workflow_editor_graph.py`（`PYTHONPATH=src python3 -m pytest
  tests/test_workflow_editor_graph.py -q`）：38/40 通过；另外 2 个
  （`test_field_specs_are_consistent_with_backend_schema` /
  `test_new_step_skeletons_get_reported_by_backend_validation`）在本次实施用的沙箱里因为缺 `fastapi`
  （`src/mini_agent` 主包的间接依赖，装不上，与本阶段改动无关）没能收集成功，M4 没有改动 `workflow_editor.py`
  的任何既有函数（只加了一个 `deps_of` 别名），不影响这两个用例本身的正确性。
- `_render_workflow_editor_tab` 及其子函数是 Streamlit 渲染代码，不适合写成 `pytest` 单测（方案 §十的
  测试计划里 M4 本来就没列专门的自动化测试文件，和 `diff_view.py` / `async_job_ui.py` 的既有先例一致）。
  实施过程中搭了一个一次性的本地烟雾测试脚本（极简 `streamlit` 桩 + 假 `AgentClient` + 假
  `streamlit_flow` 包），跑通了以下路径并在交付前删除（不进正式仓库）：
  - 未打开 / 已打开 / 切换选中节点 / 选中 include 节点，四种状态下完整渲染一遍不报错；
  - `streamlit-flow-component` 已装（走画布主路径）与未装（走 `st.graphviz_chart` 降级路径）两种环境；
  - 保存的三种失败分支（`conflict` / `needs_confirm` / `validation_failed`）各自触发对应的解决 UI；
  - 属性面板改 id：验证 `depends_on` 下游节点联动改名（`report` 依赖 `review`，把 `review` 改名成
    `review_renamed` 后 `report.depends_on` 同步更新），过程中定位并修复了上面第 2 条的画布选中态 bug。

### 4. 已知限制 / 留给后续里程碑

- 字段编辑要点「应用到草稿」才生效，不是逐字段实时保存（见偏差 1）；画布本身的拖拽布局不落盘，每次渲染都
  重新自动分层（方案 §七本就没要求持久化坐标）。
- M5：拖线加依赖 / 点边删依赖（当前 `allow_new_edges=False`）、新建 / 复制工作流、边上插入节点的 UI
  （纯函数 `insert_between` 已在 M3 做好）、并行批次高亮预览、单步试运行、备份恢复端点与 UI、
  "编辑器保存后在最近运行上用最新定义续跑"入口（M1 遗留）。

---

## M5：画布拖线 / 点边删依赖、新建 / 复制、边上插入节点、并行批次高亮、单步试运行、备份恢复

### 1. 实施内容

**后端（`src/mini_agent/workflow/editor_helpers.py` + `src/mini_agent/api/routes.py`）**

- `create_workflow(cfg, name, copy_from=None)`：新建空白工作流（单文件模板，带一个能通过校验的起始
  step）或复制已有工作流（单文件复制只改顶层 `name:` 行，保留注释 / include / 相对路径；目录模式整目录
  复制，`agents/` `skills/` `prompts/` 一并带走）。名称只允许字母 / 数字（含中文）/ 下划线 / 中划线（与
  `WorkflowStore._path` 的文件名规范化同口径，避免"输入 a b、落成 a_b"这类找不到文件的静默改名）。写完后
  回读校验，失败会清理刚建的文件，不留半成品。单文件复制若源工作流有 `prompt_file` 步骤，副本与源共享这些
  文件（相对路径指向同一位置），在 `warnings` 里提示。重名 → `already_exists`(409)。
- `precheck_step_test(cfg, name, step_id)`：单步试运行提交前的同步廉价检查（工作流 / step 是否存在），
  404/422 立即失败，通过后再进异步任务。
- 四个新端点：`POST /v1/workflows`（新建/复制）、`POST /v1/workflows/{name}/steps/{step_id}/test`
  （单步试运行，包装既有 `api_helpers.test_workflow_step`，走 `async_jobs` 异步——会真实调用 LLM/工具，
  耗时不可控）、`GET /v1/workflows/{name}/backups`、`POST /v1/workflows/{name}/backups/{id}/restore`
  （均已在 M2 的 `editor_helpers.list_backups`/`restore_backup` 实现，M5 只是补上路由）。

**看板纯函数（`apps/mini_agent_kanban/workflow_editor.py` §10~§13）**

- §10 画布边同步：`canvas_depends_pairs`/`push_sent_history`/`detect_canvas_edits`/`sync_canvas_edges`/
  `resolve_canvas_selection`/`edge_choices`/`edge_label`。核心问题：`streamlit-flow-component` 的回传值
  是异步到达的，可能是上一轮的旧边集——如果直接拿它和当前草稿求差，刚在属性面板里加的依赖会被当成"用户在
  画布上删了"而误删。解法：不直接对比草稿，而是维护"最近几次发给前端的边集历史"，把回传边集**相对最近一次
  发送集合**求差，得到的才是真正的用户编辑增量，再把这个增量应用到当前草稿。
- §11 并行批次：`batch_summary`（可选传入后端算好的 batches，否则本地 Kahn 分层）、
  `apply_batch_highlight`（返回新图，不改原图）；`to_dot` 扩展 `selected_edge`/`show_batches` 参数。
- §12 单步试运行：`suggest_test_mocks`（扫描 step 里的 `{id.output}`/`{id.score}`/`{变量}` 占位符，生成
  mock 骨架；`{id.output_file}` 这类依赖真实落盘文件的引用进 `unmockable`，无法伪造）、
  `parse_json_object`（文本框 → dict，容错）、`summarize_test_result`（结果摘要，区分
  ok/failed/skipped/error 四种终态）。
- §13 `validate_workflow_name`（前端即时校验，与后端 `editor_helpers.validate_new_name` 同口径）、
  `pick_resumable_run`（M1 遗留的"续跑"入口用，挑最近一次可续跑的执行）。

**看板 client（`apps/mini_agent_kanban/client.py`）**：新增 `create_workflow`/`test_workflow_step`/
`workflow_backups`/`restore_workflow_backup` 四个方法。

**看板 UI（`apps/mini_agent_kanban/app.py`）**

- `_wfed_render_flow_canvas` 新增 `sync_state` 参数：传入时开放 `allow_new_edges`/`get_edge_on_click`/
  `enable_edge_menu`，返回 `(选中节点, 选中边, 本轮画布边集或None)`；边集非 None 表示相对发送历史检测到
  了真实编辑，调用方据此调用 `sync_canvas_edges` 并把结果应用到草稿、刷新发送历史。merge 虚线边设
  `deletable=False`，画布上保持只读。
- 主编辑 tab 新增：并行批次高亮下拉（选中后画布节点变灰/加粗）；"🔗 依赖边操作"折叠区（下拉选边 + 删除
  依赖 + 边上插入节点，降级模式下是唯一的编辑边入口）；"🧪 单步试运行"折叠区（mock JSON 文本框、超时输入、
  有未保存修改时禁用、走 `start_async_job`/`run_async_job` 异步轮询）；"🗄️ 备份 / 恢复"折叠区（列表 +
  恢复二次确认）；"➕ 新建 / 复制工作流"折叠区（默认在没有任何工作流时展开）。

### 2. 与方案的偏差 / 补充

1. **"编辑器保存后用最新定义续跑"（M1 遗留项）本轮未实现 UI 入口**：`pick_resumable_run` 纯函数已写好并
   测试覆盖，但接入"📜 历史执行记录"面板需要改动运行面板的既有交互（选中哪次执行、`resume_workflow_run`
   的 `force_rerun_from` 参数如何暴露），评估后判断与本轮 M5 的核心范围（画布编辑 + 新建复制 + 单步试运行
   + 备份恢复）耦合不深，留到有明确需求时再做，避免为了"顺手做完"而扩大这一轮的改动面。
2. **单步试运行结果展示为纯文本 + 折叠预览，不做语法高亮 / diff**：`summarize_test_result` 返回的
   `prompt_preview`/`output` 直接用 `st.code`/`st.text_area` 展示，与"🔍 查看变更"面板复用同一套 diff
   组件（`parse_unified_diff`）不是同一类数据，没有比对的意义。
3. **`mock_step_results`/`mock_inputs` 用文本框而非逐字段表单**：候选 mock 字段数量、结构因 step 而异
   （取决于 prompt 里到底引用了哪些占位符），逐字段生成表单的复杂度和"改字段用 JSON 文本框"这个既有模式
   （`_wfed_field_widget` 对 `json` 类型字段的处理）不一致，改用同一套模式更省心，`suggest_test_mocks`
   生成的骨架已经把"要填什么"降到了"改几个值"的程度。
4. **画布边同步的"重建组件"兜底**：新增依赖如果成环被拒绝，画布上会残留一条草稿里不存在的边（组件自己
   乐观渲染了这条边）；此时清掉 `session_state` 里缓存的组件对象并 `st.rerun()`，让画布下一轮以草稿的
   真实边集重新初始化，而不是尝试"撤销"画布上那一条边（该组件没有暴露这样的 API）。

### 3. 测试

- 新增 `tests/test_workflow_editor_m5_graph.py`（39 用例）：画布边同步（含"陈旧回传不能把刚加的依赖误删"
  这个核心回归用例）、选中解析、并行批次摘要与高亮、`to_dot` 扩展参数、单步试运行的 mock 骨架/JSON 解析/
  结果摘要、新建校验、续跑挑选、client 四个新方法的请求形状。
- 新增 `tests/test_workflow_editor_m5_routes.py`（16 用例）：`POST /v1/workflows` 新建/复制（含重名 409、
  非法名称 400、源不存在 404、共享 prompt 警告、写入开关关闭 403）、备份列表 + 恢复（含恢复后内容回退、
  恢复本身再生成一份备份、未知 id 404、`base_hash` 冲突 409）、单步试运行路由（precheck 404/422、
  mock 参数类型校验、能提交 async_job 并轮询到终态）。
- 与 M2~M4 既有的 `tests/test_workflow_editor_graph.py`/`tests/test_workflow_editor_helpers.py`/
  `tests/test_workflow_editor_routes.py`（合计 102 用例）一起跑：`PYTHONPATH=src:apps/mini_agent_kanban
  python3 -m pytest tests/test_workflow_editor_m5_routes.py tests/test_workflow_editor_m5_graph.py
  tests/test_workflow_editor_graph.py tests/test_workflow_editor_helpers.py
  tests/test_workflow_editor_routes.py -q` → **156 passed**，无回归。
- **已知限制**：本轮没有对新增的 `app.py` UI 代码（画布边同步接线、三个新面板）做 Streamlit 运行时/浏览器
  级验证——`ast.parse`/`py_compile` 通过，且逐一确认了 `workflow_editor` 模块暴露了 UI 侧调用的全部函数，
  但真实的拖线成边 / 点边删除 / `streamlit-flow-component` 的 `deletable`/`enable_edge_menu` 行为只能在
  浏览器里验证，本次未能补上像 M4 那样的本地烟雾测试脚本。这是留给后续验证或用户实际使用中反馈的已知缺口，
  如实记录于此，而非隐瞒。

