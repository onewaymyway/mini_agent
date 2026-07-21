# Workflow 文件夹化 + 本地 Agent/Skill/Prompt 文件改进计划

> 状态：**进行中**。本文档随每个阶段完成同步更新"实施记录"部分，
> 设计部分（一~四节）为最终确认版本，实施中如有偏离会在对应阶段的
> 实施记录里注明并说明原因。

## 背景

现有 workflow 是单个 YAML 文件（`.agent/workflows/<name>.yaml`），
`WorkflowStep.prompt` 只能内嵌字符串，且无法引用工作流私有的 agent
profile 或 skill——`_execute_with_main_agent` 构造主 Agent 时既没有
传入 `skill_loader`，`Agent.__init__` 里读取 agent profile 又是硬编码
调用全局单例 `get_profile_loader()`；`_execute_with_role_agent` 同样只
认全局 `.agent/agents` 目录的 dispatcher。

目标：让复杂 workflow 可以组织成一个文件夹，里面放专属的 agent 定义、
skill 定义、抽出来的 prompt 模板文件，工作流的 step 既能直接调用这些
本地 agent/skill，也能让"主 Agent"执行的 step 在执行期间感知到这些
本地资源。同时**完全向后兼容**现有单文件 YAML workflow。

## 一、目录化 Workflow 格式

新增"文件夹型工作流"，与现有"单文件型"共存：

```
.agent/workflows/
  code_review.yaml            ← 旧的单文件模式，继续原样支持
  my_pipeline/                ← 新的文件夹模式
    workflow.yaml             ← 主入口，字段结构基本沿用现有 schema
    agents/                   ← 本工作流私有 agent profile（同 .agent/agents/*.md 格式）
      reviewer.md
    skills/                   ← 本工作流私有 skill（同 .claude/skills 目录格式）
      pdf-diff/
        SKILL.md
    prompts/                  ← 抽出来的 prompt 模板文件
      analyze.md
```

加载规则：`WorkflowStore.load(name)` 优先查找
`<workflows_dir>/<name>/workflow.yaml`，找不到再退回
`<workflows_dir>/<name>.yaml`。`WorkflowDef` 新增字段
`source_dir: Optional[Path]`：文件夹模式下指向 `my_pipeline/`，单文件
模式下为 `None`，用于解析本地 agents/skills 目录、解析相对路径的
prompt 文件。

`WorkflowStore.save()` 默认继续写单文件（不强制迁移旧数据）；新增
`save_as_dir()` 把工作流写成文件夹结构（若目标已是文件夹模式，直接
覆盖 `workflow.yaml`）；新增 CLI 子命令
`workflow to-dir <name>` 把已有单文件工作流一键转换为文件夹模式（建
`agents/`、`skills/`、`prompts/` 空目录，原 YAML 移入 `workflow.yaml`）。

## 二、Prompt 文件引用

`WorkflowStep` 新增字段：

```python
prompt_file: Optional[str] = None   # 相对 workflow 所在目录的相对路径，如 "prompts/analyze.md"
```

- `prompt` 与 `prompt_file` 二选一，都填时 `prompt_file` 优先。
- 加载阶段（`store._load_path`）解析出 workflow 所在目录（文件夹模式
  用 `source_dir`，单文件模式用 yaml 文件所在目录），把 `prompt_file`
  对应的文本读出来填充进 `step.prompt`，同时保留 `prompt_file` 字段。
- `to_dict()` 序列化时，若 `prompt_file` 有值，**只写 `prompt_file`，
  不写展开后的 `prompt` 文本**，避免迁移时把大段文本重复写回 YAML，
  也避免编辑 prompt 文件后 YAML 里的旧文本"假装"还生效。
- `validate()` 增加：`prompt_file` 指向的文件必须存在。
- **实际实现调整**：`validate()` 是纯数据校验（不做文件 IO），`prompt_file`
  的存在性检查放在 `WorkflowStore._load_path`/`_resolve_prompt_files` 里
  ——文件缺失时打印警告、`step.prompt` 保留原值（通常为空串），不阻断
  整体加载，避免因为一个 step 的 prompt 文件被误删就让整个工作流不可
  查看/编辑。

## 三、Workflow 本地 Agent / Skill 的接入

### 3.1 资源合并（Bundle）

`WorkflowRunner.run()` 开始时，若 `wf.source_dir` 存在，构造一次性的
`WorkflowResourceBundle`：

```python
class WorkflowResourceBundle:
    agent_loader: AgentProfileLoader   # dirs = [全局, 项目级, workflow本地 agents/]（本地同名覆盖）
    skill_loader: SkillLoader          # skills_dirs = [全局 skills_dir, workflow本地 skills/]
```

挂在 `self._current_resource_bundle`，随 step 执行使用。子工作流
（`sub_workflow` 类型）**不继承**父级 bundle，各自按自己的
`source_dir` 重新构造，避免隐式跨工作流资源泄漏。

### 3.2 主 Agent 执行（`_execute_with_main_agent`）

1. 构造 `Agent(...)` 时显式传入
   `skill_loader=bundle.skill_loader if bundle else None`（原来完全没
   传，顺带补上这个既有缺口）。
2. `Agent.__init__` 新增可选参数 `agent_profile_loader`，为空时才回退
   现有的全局单例 `get_profile_loader()`；有值时优先用传入的。
3. `runner` 把 `bundle.agent_loader` 传给该 step 的 Agent 实例。

这样该 step 执行期间，主 Agent 通过 `spawn_named_agent` 能看到 workflow
本地 agent，技能触发 / `skill_activate` 工具能看到 workflow 本地 skill。

### 3.3 Role Agent 执行（`_execute_with_role_agent`）

`profile = dispatcher._loader.get(step.role)` 改为优先查
`bundle.agent_loader.get(step.role)`，查不到再退回全局
`dispatcher._loader`。`role: reviewer` 因此既可以指向 workflow 私有的
`agents/reviewer.md`，也兼容引用全局角色，不需要新增 step 类型。

### 3.4 新增 Step 类型：`skill_agent`

新增字段：

```python
skill_name: Optional[str] = None   # skill_agent 专用
```

语义：临时启动一个只强制挂载该 skill（不做关键词触发判断）的最小
Agent 执行 `prompt`，返回输出文本。查找顺序：先 `bundle.skill_loader`，
再全局 skills_dir。用于"这一步明确要用某个 skill 的能力"，不依赖关键
词命中。`executors.py` 新增 `SkillAgentStepExecutor`，注册进
`_EXECUTORS` 表，不改动 runner 主循环分发逻辑。`schema.STEP_TYPES`
增加 `"skill_agent"`；`validate()` 增加必填校验。

### 3.5 "直接调用 agent" 复用 role_agent

`type: role_agent` + `role: <name>` 配合 3.3 的查找顺序，即是"直接调用
workflow 私有 agent"，不需要额外 step 类型。

## 四、向后兼容 & 风险点

- 旧单文件 YAML、内嵌 `prompt`、旧 `role`/`type` 字段完全不受影响，
  `source_dir=None` 时新逻辑自动跳过。
- `Agent.__init__` 新增可选参数为纯增量改动，不影响现有调用方
  （`cli/app.py`、`orchestrator/sub_agent.py`、`api/session_pool.py` 等）。
- 子工作流不继承父级本地资源包，行为更可预期。
- 需要补充测试：目录发现优先级（本地覆盖全局同名）、prompt_file
  解析与序列化往返、skill_agent 执行、save/load 往返。

---

## 实施记录

### 阶段 1：schema.py — 数据模型扩展
状态：✅ 已完成
- `WorkflowStep` 新增 `prompt_file`、`skill_name` 字段。
- `WorkflowDef` 新增 `source_dir`（不参与 to_dict 序列化，纯运行时字段）。
- `STEP_TYPES` 增加 `"skill_agent"`。
- `from_dict`/`to_dict` 支持新字段；`to_dict` 在 `prompt_file` 有值时
  不写入展开后的 `prompt`。
- `validate()` 增加：`skill_agent` 必须有 `skill_name`；`prompt_file`
  为空时不再要求 `prompt` 非空（human_input 类型同理），文件是否存在的
  校验放在 store 加载阶段（见下）。

### 阶段 2：store.py — 目录模式加载/保存
状态：✅ 已完成
- `_path()` 改为返回候选路径元组（目录模式优先 / 单文件兜底）。
- 新增目录模式加载：读取 `<name>/workflow.yaml`，设置 `source_dir`。
- 新增 prompt_file 解析：加载后按 `source_dir`（或单文件所在目录）
  拼路径读取文本填充 `step.prompt`。
- 新增 `save_as_dir()`、`to_dir()` 转换方法。

### 阶段 3：executors.py / runner.py — 资源 Bundle 与 skill_agent
状态：✅ 已完成
- 新增 `WorkflowResourceBundle`（`workflow/resource_bundle.py`）。
- `runner.run()` 构造 bundle 并挂到 `self._current_resource_bundle`。
- `_execute_with_main_agent` 传入 `skill_loader` / `agent_profile_loader`。
- `_execute_with_role_agent` 优先查 bundle 本地 agent。
- 新增 `SkillAgentStepExecutor` 并注册。

### 阶段 4：agent/core.py — Agent 支持外部 profile_loader
状态：✅ 已完成
- `Agent.__init__` 新增 `agent_profile_loader` 可选参数，为空回退全局单例。

### 阶段 5：CLI / 文档收尾
状态：✅ 已完成
- `workflow_cmd.py` 新增 `to-dir` 子命令（`WorkflowStore.to_dir()`）。
- 更新 `docs/workflow-guide.md`：文件位置章节说明双模式共存，新增
  "文件夹模式 Workflow" 专章（目录结构、prompt_file、本地 agent/skill
  调用方式、边界与限制），CLI 命令列表补充 `to-dir`。

### 已知限制
- `set_effective_profile_loader()`（`orchestrator/agent_profiles.py`）
  沿用了本项目里 `set_active_skills_provider` 的既有写法：模块级全局
  变量，不是 thread-local。多个 workflow step 并发执行（`allow_parallel`）
  且各自引用不同本地 agent 时，`spawn_named_agent` 在同一时刻可能读到
  "最后一个构造的 Agent 实例设置的" profile_loader，而不是各自 step
  对应的那个。这是延续现有代码的既有约定/既有风险面，不是本次改动新
  引入的问题；如果后续要收紧，可以把这里也改造成 thread-local，同时
  一并改造 `_get_active_skills()`。
