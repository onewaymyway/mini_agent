# 看板工作流可视化编辑器 + 续跑定义快照修复 改进计划

> 本文档记录设计方案本身，供实施前确认；实施完成后另写
> `next_doc/workflow_visual_editor_implementation_record.md`，不混在本文档里。
>
> **状态**：方案已确认（决策见 §一），实施中——M1（续跑定义快照修复）、M2（后端编辑）、M3（看板侧纯函数模块与 client 方法）、
> M4（看板 UI）已完成，详见 `next_doc/workflow_visual_editor_implementation_record.md`；M5 起未开始。
>
> **相关文档**：`docs/workflow-guide.md`、`docs/kanban-dashboard-guide.md`、
> `next_doc/workflow_directory_mode_design.md`（目录化 workflow）、
> `next_doc/workflow_mechanism_improvement_plan.md`（P7 单步编辑 `patch_workflow_step`）。
>
> **范围约束**：按 `CLAUDE.md`，看板前端改动只改 `apps/mini_agent_kanban`（Streamlit），
> **不**同步到 `apps/mini_agent_kanban_x`（React SPA）；后端 REST 改动两个看板共用。

---

## 一、已确认的决策

| # | 议题 | 结论 |
|---|---|---|
| 1 | 画布组件 | 用可选依赖 `streamlit-flow-component`；未安装时降级为 `st.graphviz_chart` 只读图 + 节点选择器 |
| 2 | 写入开关 | `workflow.visual_editor_enabled` **默认开启**（可在 `agent_config.json` 关闭）；风险由备份 / 乐观锁 / 保存前校验兜底，见 §五 |
| 3 | YAML 注释 | **保留注释**：保存改用 `ruamel.yaml` round-trip，不再走 PyYAML 整体重写 |
| 4 | 范围 | 阶段一 + 阶段二一起做，内部按里程碑 M1~M5 顺序实施（§八），每个里程碑自带测试 |
| 5 | 续跑快照问题 | **一并修复**：`resume` 新增 `use_latest_definition` 参数，默认关（§六） |

---

## 二、现状摸底（读代码得到的事实）

### 2.1 看板现状

- `apps/mini_agent_kanban/app.py::render_workflow_tab` 只有运行面板 + `st.code(yaml_text)` 折叠区，
  没有任何图形化展示。
- 现有唯一的定义写入口是 `patch_workflow_step`（`api_helpers.py`，看板“✏️ 修改此步骤定义”），
  只能改单个 step 的 prompt / timeout。

### 2.2 不能复用 `store.save()` 做编辑器保存

`WorkflowStore.save()` 是 `加载 → WorkflowDef → to_dict() → yaml.dump` 的往返，有四处有损：

1. **`include` 片段被摊平**：`_load_path` 先 `_expand_includes()` 再 `from_dict`，保存后
   `include: xxx` 变成一批带 `<id>__` 前缀的普通 step，片段引用关系永久丢失。
2. **`script_path` 变绝对路径**：`_resolve_script_paths()` 把它改写成绝对路径，`to_dict()` 原样写回，
   项目迁移后失效。
3. **`prompt_file` 正文写不回文件**：`to_dict()` 有 `prompt_file` 时只写路径，面板里改的 prompt 正文丢失。
4. **注释与格式丢失**：PyYAML 重写不保留注释，多行 prompt 变成带 `\n` 的引号字符串。

结论：编辑器必须直接编辑**原始 YAML 文档**（保留 include / 相对路径 / 注释），
只在**校验**时才走 `WorkflowDef` 路径。

### 2.3 续跑用的是“首次运行时的旧快照”（疑似已有问题，实施时先复现）

- `workflow/runner.py:265`：定义快照只在**新建** run 时写一次（`loaded is None` 分支）。
- `workflow/api_helpers.py::resume_workflow_run`（约 243 行）与
  `cli/commands/workflow_cmd.py`（约 511 行）续跑时都是读快照、`parse_yaml`，不重读 `WorkflowStore`。
- 看板“保存修改并从此步骤续跑”（`app.py` 约 7673–7677）是 `patch_workflow_step` →
  `resume_workflow_run(force_rerun_from=...)`；工具 `patch_workflow_step` 的文档也宣称这是标准流程。

若上述阅读成立，则 `patch` 改的是 `.agent/workflows/` 里的定义，而续跑执行的仍是旧快照，
**改动不会生效**。这是读代码的推断，**M1 第一步先写测试复现**，复现不了则只记录、不改行为。

### 2.4 可复用的现成能力

- `WorkflowDef.validate()`：依赖存在性、condition 静态检查、占位符引用、各类型必填、autonomous 模式阻塞点。
- `runner._compute_parallel_batches()` / `api_helpers.preview_workflow_def()`：拓扑分层与环检测，
  **布局不需要落盘坐标**，用自动分层即可。
- `api_helpers.test_workflow_step()`：单步试运行（不落盘 run 历史）。
- `apps/mini_agent_kanban/diff_view.py`：看板已有 diff 渲染，保存前变更预览直接复用。
- `utils.atomic_write.atomic_write_text`：原子写入（CLAUDE.md 规范）。

---

## 三、总体架构

```
┌ 看板(Streamlit) ─────────────────────────────┐        ┌ daemon(FastAPI) ───────────────────────┐
│ workflow_editor.py                           │  HTTP  │ routes.py（薄封装 + 开关校验）            │
│  ├ 纯函数：raw⇄节点/边、边⇄depends_on、       │ ─────▶ │  └ workflow/editor_helpers.py（纯函数）   │
│  │        环检测、错误→节点归类               │        │     ├ ruamel round-trip 读写 + 同步      │
│  ├ 画布：streamlit-flow / graphviz 降级       │        │     ├ 校验（复用 WorkflowDef.validate）  │
│  └ 属性面板、diff、保存                        │        │     ├ 备份 / 乐观锁 / prompt_file 写回    │
│ client.py（新增方法）                          │        │     └ 编辑器元信息（类型/角色/工具/skill）│
└──────────────────────────────────────────────┘        └────────────────────────────────────────┘
```

`app.py` 只在 `render_workflow_tab` 增加入口（几行），编辑器主体放独立文件
（`app.py` 已 14.6k 行，沿用 `diff_view.py` / `async_job_ui.py` 的拆分先例）。

---

## 四、交互设计

### 4.1 入口

工作流 tab 顶部增加切换：「▶️ 运行与记录」（现有内容原样保留）/「✏️ 编辑器」。

### 4.2 编辑器布局

```
┌ 选择工作流 ▾ [新建][复制]   ● 有未保存修改  [查看变更][校验][保存][放弃] ┐
├────────────────────────────────┬─────────────────────────────────────┤
│ 图形画布（自上而下自动分层）     │ 属性面板（选中节点后出现）            │
│  [analyze]─▶[review]─▶[evaluate]│ 基础：id / 名称 / 类型               │
│                    └─▶[report] │ 依赖：depends_on 多选                │
│ 点击节点=选中；拖线=加依赖；      │ 类型专属字段（见 4.4）                │
│ 点边=删依赖                     │ 高级：condition/重试/超时/审批…       │
│ [＋节点][复制][删除][插入到边间] │ [删除此节点]                         │
├────────────────────────────────┴─────────────────────────────────────┤
│ 校验结果：❌ 步骤 'report' 依赖不存在的步骤 'x'（点击定位该节点）        │
└──────────────────────────────────────────────────────────────────────┘
```

### 4.3 节点展示

- 颜色 / 图标按 step 类型区分；徽标：🔒 需审批、🔀 有 condition、⚠️ 有校验错误、🧩 include 片段。
- `merge_sources` 画虚线；`foreach` 的内层 step 在面板里编辑，不在图上展开。
- `sub_workflow` 节点面板提供“跳转到该工作流”。
- **include 节点**（🧩）作为整体节点：只允许改 `id` / `depends_on`，面板提示“片段内容请在
  `.agent/workflow_snippets/` 维护”，避免编辑器摊平片段。

### 4.4 属性面板（按类型出表单，不暴露无关字段）

| 类型 | 专属字段 |
|---|---|
| agent / role_agent | prompt（或 prompt_file 正文）、role 下拉（已注册角色）、model、max_turns |
| tool_call | tool_name 下拉、tool_args（JSON 文本框 + 语法校验） |
| sub_workflow | workflow_name 下拉（排除自身） |
| human_input | input_prompt、input_key |
| script / python_step | script 或 script_path（相对路径）、params（JSON）、result_file 及必填 key |
| skill_agent | skill_name 下拉 |
| foreach / wait / merge | items 与 foreach_step / wait_seconds / merge_sources 与策略、分隔符 |

共有高级字段：condition、timeout、retry_on_error、retry_on_gate_fail、allow_parallel、
require_approval、escalate_after_n_same_failures、output_file。
工作流级字段（description / version / mode / defaults / max_total_*）放“工作流属性”折叠区。

`script` / `python_step` 类型在对应开关（`script_step_enabled` / `python_step_enabled`）关闭时，
面板显示“当前配置下该类型不会执行”提示；**编辑器不绕过这两个运行期开关**。

### 4.5 关键编辑行为

- **改 id**：默认勾选“同步更新引用”，一并改写其它 step 的 `depends_on` / `merge_sources` /
  `condition` 中的名字与 prompt / tool_args 里的 `{id.xxx}` 占位符；改写只匹配完整标识符边界，
  并在“查看变更”里可见。
- **删节点**：提示有哪些节点依赖它，默认同步从这些节点的 `depends_on` / `merge_sources` 中移除。
- **加依赖**：本地即时环检测，成环直接拒绝并提示路径。
- **草稿**只存 `st.session_state`；有未保存修改时切换工作流 / 切换 tab 需确认。

---

## 五、后端设计

### 5.1 配置（`config/models.py::WorkflowConfig`，走 `param_registry` 统一注册）

| 字段 | 默认 | 说明 |
|---|---|---|
| `visual_editor_enabled` | `True` | 关闭后写入类端点返回 403；只读 `GET` 始终可用 |
| `editor_backup_keep` | `20` | 每个工作流保留的备份份数 |

### 5.2 新增模块 `workflow/editor_helpers.py`（纯函数，同 `api_helpers.py` 分层）

- `load_for_edit(cfg, name)`：返回 `{draft, base_hash, mode(file|dir), comment_lines, has_ruamel, prompt_files}`。
  `draft` 是原始文档转成的纯 JSON（含 include 条目原样），`prompt_files` 是各 `prompt_file` 的正文。
- `validate_draft(cfg, name, draft, prompt_files)`：按加载同一路径构造 `WorkflowDef`（展开 include、
  解析 script_path，用草稿里的 prompt_file 正文覆盖）→ `validate()`（与 `store.save` 同一组开关，
  抽出 `WorkflowStore.validate_def()` 复用，`save()` 行为不变）。返回
  `{errors, warnings, errors_by_step, batches}`，`errors_by_step` 由错误文案里的 `步骤 '<id>'` 解析得到。
- `save_draft(cfg, name, draft, prompt_files, base_hash)`：见 5.4。
- `create_workflow(cfg, name, copy_from=None)`：新建空白或复制（目录模式整目录复制，命名规则与 `_path` 一致）。
- `editor_meta(cfg)`：step 类型清单（含内置与插件注册类型）、角色 profile、已注册工具、skill、
  已有工作流名、片段名。
- 备份：`list_backups` / `restore_backup`。

### 5.3 保留注释：ruamel round-trip + 按 step id 同步

不直接把草稿 dump 成新文件，而是把草稿**同步进**用 `ruamel.yaml` round-trip 加载的原文档：

1. 读原文件得 `CommentedMap`，与草稿（纯 JSON）逐层比较。
2. 顶层字段：值相同不动；变化则原位更新；新增字段插到 `steps` 之前；草稿里没有的已知字段删除。
3. `steps`：按 `id` 匹配。已存在的 step **原位递归同步**（只改有差异的键，未变的键连同其
   注释原样保留）；新 step 追加；被删 step 移除；顺序按草稿重排（保持 step 对象本身不重建，
   其附带注释跟随）。
4. 多行字符串变更时写成 `|` 块样式；值未变则不碰其原有样式。
5. **无变化不写盘**：草稿与当前文档等价时返回 `unchanged`，文件字节级不变。
6. 输出参数：`indent(mapping=2, sequence=4, offset=2)`、`width` 放大避免长行被折断、`preserve_quotes=True`。

**原型验证结论（已在沙箱试过）**：上述方式能保留行首 / 行尾 / 块间注释，新增 step、改多行
字符串、删 step 均正常。已知限制（写进用户文档，并用测试固定行为）：

- 删除某 step 时，紧贴在它**之前**的独立注释行会随之丢失（ruamel 把它挂在上一项尾部）。
- 对**带行尾注释的标量**改成多行字符串时，行尾注释会挪到块末尾，仍是合法注释，但位置有偏差。
- 重排 step 顺序时，独立成行的注释可能跟着相邻 step 移动。

**降级**：未安装 `ruamel.yaml` 时回退 PyYAML 保存，并在校验结果里明确警告“本次保存会丢失注释”
（且要求用户勾选确认）。`ruamel.yaml` 加入 `requirements.txt` 与 `pyproject.toml` 依赖。

### 5.4 保存流程（`PUT`）

1. 开关校验：`visual_editor_enabled` 为假 → 403，提示如何开启。
2. **乐观锁**：`base_hash`（原文件字节 sha256）与当前不一致 → 409，提示“主 Agent 或其它入口已修改该文件”，
   看板给出“重新加载 / 强制覆盖”选择，避免与 `patch_workflow_step` 等入口互相覆盖。
3. 校验：`validate_draft`，有 error 拒绝保存并返回 `errors_by_step`。
4. 备份旧文件到 `.agent/workflow_backups/<name>/<时间戳>.yaml`，超出 `editor_backup_keep` 删除最旧。
5. ruamel 同步（5.3）→ `atomic_write_text` 原子写入。
6. `prompt_file` 类型 step 的正文写回各自文件，**目标路径解析后必须落在 workflow 目录内**，
   否则拒绝（与 `_resolve_script_paths` 的越界拦截同一标准）。写入同样先备份、原子写。
7. 返回新 `base_hash`、`changed_steps`、`warnings`，并附 `git_integration.save_hint()` 的提示（沿用现有，
   只提示不自动 commit）。

### 5.5 REST 端点（`api/routes.py`，仅薄封装，错误映射沿用 `_workflow_api_error_to_http`）

| 端点 | 阶段 | 说明 |
|---|---|---|
| `GET /v1/workflows/{name}/editor` | 一 | 编辑用文档 + hash + 元信息 |
| `POST /v1/workflows/{name}/editor/validate` | 一 | 草稿校验（不落盘） |
| `PUT /v1/workflows/{name}/editor` | 一 | 保存（受开关控制） |
| `GET /v1/workflow_editor/meta` | 一 | 类型 / 角色 / 工具 / skill / 工作流名 / 片段名 |
| `POST /v1/workflows` | 二 | 新建 / 复制（`{name, copy_from?}`） |
| `POST /v1/workflows/{name}/steps/{step_id}/test` | 二 | 包装 `test_workflow_step`（用**已保存**定义） |
| `GET /v1/workflows/{name}/backups`、`POST .../backups/{id}/restore` | 二 | 备份列表与恢复 |
| `POST /v1/workflow_runs/{id}/resume` | 一 | Body 新增 `use_latest_definition`（§六） |
| `GET /v1/workflow_runs/{id}` | 一 | 响应新增 `definition_changed`（§六） |

不提供“删除工作流”的编辑器入口（本次不做，保持写入面收敛）。

---

## 六、续跑定义快照修复

### 6.1 步骤

1. **先复现**：写测试——创建 run 并令其某 step 失败 → `patch_workflow_step` 改该 step → `resume(force_rerun_from)`，
   断言实际执行的是新定义。若测试在现状下失败，则确认问题成立。
2. 新增参数 `use_latest_definition`（默认 `False`，行为完全向后兼容）：
   - `api_helpers.resume_workflow_run(..., use_latest_definition=False)`
   - `routes.py` 的 resume Body、`client.resume_workflow_run(..., use_latest_definition=...)`
   - 工具 `resume_workflow_run` 增加同名参数，并更新其 description / docstring
   - CLI `workflow resume` 增加 `--latest`
3. 语义（为真时）：
   - 从 `WorkflowStore` 重新加载当前定义（走与 `start_workflow_run` 相同的加载 + 校验），
     **重写快照**，此后该 run 的再次续跑默认沿用新快照；
   - 已有 `step_results` 按 step id 保留；新定义里已不存在的 id 的结果保留在 session 中但不参与调度，
     并在返回值 `warnings` 里列出；新定义里新增的 step 视为 pending；
   - `force_rerun_from` 必须存在于新定义中，否则 `WorkflowApiError`；
   - 当前定义加载或校验失败 → 报错，**不回退**到旧快照（避免用户以为用了新定义）。
4. **漂移提示**：`get_workflow_run_detail` 增加 `definition_changed`（快照的 `to_dict()` 与当前已保存
   定义的 `to_dict()` 不等价时为真，当前定义读取失败时为 `None`）。
5. **看板**：
   - 现有“保存修改并从此步骤续跑”和编辑器保存后在最近运行上提供的“用最新定义续跑”，均传
     `use_latest_definition=True`；
   - run 详情在 `definition_changed` 为真时显示“⚠️ 该次执行使用的是旧定义快照，当前定义已修改”。
6. 文档同步：`docs/workflow-guide.md` 中 `patch_workflow_step` / `resume_workflow_run` 段落写明
   “patch 只改定义；要让续跑生效需 `use_latest_definition`”，并更新
   `docs/workflow-directions-history.md` 的“出错定位、编辑与重跑”一节。

### 6.2 为什么默认关

`resume` 默认沿用快照本身是有意设计（`runner.py` 注释：防止运行中途原 YAML 被改动）。
默认打开会改变所有既有调用方语义，因此只在“用户明确要用最新定义”的入口（看板 patch 续跑、编辑器保存后）
显式传入。

---

## 七、画布与降级细节

- **主路径**：`streamlit_flow(..., layout=LayeredLayout(direction="down"), get_node_on_click=True,
  allow_new_edges=<阶段二>)`；节点用 `selectable=True`，选中 id 取自返回 state 的 `selected_id`。
  布局交给 ELK 自动分层，坐标不落盘、YAML 不新增字段。
- **状态同步**：草稿变化后重建节点列表并更新 `timestamp` 强制刷新；画布回传的边与 `depends_on`
  做差集，得到“新增 / 删除的依赖”再应用到草稿（阶段二）；`merge_sources` 虚线边只读。
- **降级**：`streamlit_flow` 导入失败时改用 `st.graphviz_chart` 画只读 DAG（选中节点高亮），
  节点选择走 `st.selectbox`；加 / 删依赖走面板里的 `depends_on` 多选，功能完整只是没有点选和拖线。
  沿用 `_sortable_available()` 的探测写法（新增 `_flow_available()`）。
- 依赖写入 `apps/mini_agent_kanban/requirements.txt`（可选，带注释说明未安装时的降级行为）。
- **主要技术风险**：第三方组件与 Streamlit rerun 的状态往返偶有抖动，因此保证“任何编辑动作都能
  仅通过面板完成”，画布只是选择与连线的快捷方式。

---

## 八、实施里程碑（阶段一 + 阶段二一起做）

| 里程碑 | 内容 | 阶段 |
|---|---|---|
| M1 | 快照问题：复现测试 → `use_latest_definition` 全链路（api_helpers / routes / tools / CLI / client）→ `definition_changed` ✅已完成 | — |
| M2 | 后端编辑：`WorkflowStore.validate_def()` 抽取、`editor_helpers.py`（ruamel 同步 / 校验 / 保存 / 备份 / 元信息）、配置项、`GET/validate/PUT/meta` 路由 ✅已完成 | 一 |
| M3 | 看板侧纯函数模块与 client 方法（图转换、环检测、改 id 联动、错误归类）及测试 ✅已完成 | 一 |
| M4 | 看板 UI：只读图 + 选中 + 属性面板 + 增删复制节点 + 依赖多选 + 校验 + diff + 保存 + 降级 ✅已完成 | 一 |
| M5 | 画布拖线 / 点边删依赖、新建 / 复制工作流、边上插入节点、并行批次高亮预览、单步试运行、备份恢复；文档、实施记录、打包 | 二 |

单步试运行会真实调用 LLM / 工具并消耗 token：UI 二次确认、仅使用**已保存**定义（有未保存修改时按钮置灰），
`script` / `python_step` 仍受各自运行期开关约束。

---

## 九、文件清单

**新增**
- `src/mini_agent/workflow/editor_helpers.py`
- `apps/mini_agent_kanban/workflow_editor.py`
- `tests/test_workflow_resume_latest_definition.py`
- `tests/test_workflow_editor_helpers.py`
- `tests/test_workflow_editor_routes.py`
- `tests/test_workflow_editor_graph.py`
- `next_doc/workflow_visual_editor_plan.md`（本文档）
- `next_doc/workflow_visual_editor_implementation_record.md`（实施完成后）

**修改**
- `src/mini_agent/workflow/store.py`（抽出 `validate_def()`，`save()` 行为不变）
- `src/mini_agent/workflow/api_helpers.py`（`resume` 新参数、`definition_changed`）
- `src/mini_agent/workflow/tools.py`（`resume_workflow_run` 工具参数与文档）
- `src/mini_agent/cli/commands/workflow_cmd.py`（`resume --latest`）
- `src/mini_agent/api/routes.py`（新增端点、resume Body）
- `src/mini_agent/config/models.py`（`WorkflowConfig` 两个字段）
- `apps/mini_agent_kanban/client.py`、`app.py`（入口、patch 续跑传参、run 详情漂移提示）
- `apps/mini_agent_kanban/requirements.txt`、`requirements.txt`、`pyproject.toml`
- `docs/workflow-guide.md`、`docs/workflow-directions-history.md`、`docs/kanban-dashboard-guide.md`、
  `docs/http-api-guide.md`、`next_doc/kanban_feature_inventory.md`

---

## 十、测试计划

- **快照修复**：复现测试（patch 后 resume 生效）；`use_latest_definition=False` 行为不变；已完成 step 结果保留；
  新增 / 删除 step 的处理；定义加载失败不回退；`definition_changed` 真 / 假 / None。
- **往返保真**（最关键）：含 include、相对 `script_path`、`prompt_file`、注释、块样式 prompt 的样例，
  **无修改保存 = 文件字节不变**；改一个字段只有该行 diff；新增 / 删除 / 重排 step；ruamel 缺失降级路径。
- **保存护栏**：hash 冲突 409；校验失败不落盘；备份生成与超量清理；`prompt_file` 越界拦截；开关关闭 403；
  原子写入失败不留半截文件。
- **校验**：`errors_by_step` 归类；include 节点；autonomous 模式阻塞点；condition / 占位符引用错误。
- **图与联动（纯函数）**：raw → 节点 / 边；边差集 → `depends_on`；环检测；改 id 联动（含占位符与 condition、
  不误伤同前缀标识符）；删节点联动。
- 回归：既有 `tests/test_workflow_*.py` 全量通过；`test_kanban_*` 不受影响。

---

## 十一、验收标准

1. 在看板打开任一已有工作流，能看到 DAG 图，点击节点后右侧出现该节点的表单。
2. 修改一个字段并保存，磁盘上的 YAML **只有该处 diff**，注释、`include`、相对路径、块样式均保持。
3. 不做任何修改点保存，文件字节不变。
4. 依赖成环、引用不存在的 step、必填字段缺失时，保存被拒绝，且错误定位到对应节点。
5. 主 Agent 在编辑器打开期间改过该文件，保存时收到冲突提示而不是静默覆盖。
6. 未安装 `streamlit-flow-component` 时，编辑器仍可用（graphviz + 选择器）。
7. `patch` / 编辑器保存后，用 `use_latest_definition` 续跑，实际执行的是新定义；默认续跑行为与改动前一致。

---

## 十二、风险与缓解

| 风险 | 缓解 |
|---|---|
| 写入默认开启，误改工作流 | 保存前 diff、校验拦截、自动备份与恢复、乐观锁；可通过配置一键关闭 |
| ruamel 同步在极端排版下注释位置有偏差 | 已知限制入文档；无变化不写盘；备份兜底；测试固定行为 |
| 第三方画布组件状态抖动 | 所有编辑可仅靠面板完成；画布只做选择与连线快捷方式；降级路径完整 |
| 改 id 联动误改 prompt 中的普通文本 | 仅匹配 `{id.xxx}` 占位符与完整标识符；变更进入 diff 预览；提供关闭联动的勾选 |
| `use_latest_definition` 与已完成结果不一致 | 按 id 保留结果并列出孤立项；仅显式传参才启用；加载失败不回退 |
| 编辑器成为任意命令 / 代码执行入口 | 不绕过 `script_step_enabled` / `python_step_enabled`；`script_path` 与 `prompt_file` 越界拦截；写入端点走既有看板鉴权 |

---

## 十三、交付与打包约定

- 实施完成后只打包**修改与新增**的文件（含文档），路径以**仓库根**为基准，解压到仓库根即可覆盖。
- 代码注释与文档一律中文。
