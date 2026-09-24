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
