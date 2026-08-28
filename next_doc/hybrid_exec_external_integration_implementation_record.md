# hybrid_exec 外部集成实施记录（A1 / A2 / B1）

> 对应 `next_doc/hybrid_exec_improvement_directions.md` §5 优先级建议的
> 前三项。本文档记录这三项具体怎么落地、改了哪些文件、新增了哪些测试，
> 不重复分析文档已经讲过的动机——动机见原分析文档 §2/§3/§4。

## 落地范围

按分析文档 §5 的顺序，实施了 **A1、A2、B1** 三项（成本从低到高、且有
依赖关系的前三条）；**A3**（外部项目状态命令附带 hybrid_exec 摘要）、
**B2**（TaskSpec 模板库）、**A4**（独立可安装子包）本次未动，留待后续
单独立项——A3 依赖 A1 已经具备，但涉及改 `mini-agent projects status`
命令的输出格式，属于新的一批改动，与 B2/A4 一样先记录在这份文档末尾的
"未落地项"里，不在本次范围内。

## A1：`Workspace` 补齐 hybrid_exec 相关路径属性

**改动文件**：`src/mini_agent/workspace.py`、
`src/mini_agent/hybrid_exec/executor.py`

- `Workspace` 新增三个只读属性：`hybrid_exec_scripts_dir` /
  `hybrid_exec_runs_dir` / `hybrid_exec_playbooks_dir`，分别对应
  `<root>/.agent/hybrid_exec/{scripts,runs,playbooks}/`——与
  `default_executor()` 内部原来硬编码拼接的路径在数值上完全一致，只是
  现在有了一个显式的编程接口。
- `default_executor()` 签名从 `default_executor(project_root, ...)` 改为
  `default_executor(project_root=None, *, workspace=None, ...)`：
  `project_root`/`workspace` 二选一，`workspace` 优先；都不传时抛
  `ValueError`（防呆，避免误用两者都不传导致后面 `NoneType` 报错更难
  排查）。传 `workspace=` 时，脚本仓库/运行记录/playbook 仓库三个目录
  改用 `workspace.hybrid_exec_*_dir` 派生；不传时行为与改动前完全一致
  （`project_root` 裸路径 + 内部拼接），**不影响任何现有调用方**——
  `hybrid_exec/cli.py`、`hybrid_exec/tools.py`、
  `workflow_integration.py` 里所有既有调用点都是位置参数传
  `project_root`，签名变化对它们透明。

## A2：`try_hybrid_exec()` 降级包装

**新增文件**：`src/mini_agent/hybrid_exec/optional.py`

- 提供 `try_hybrid_exec(task, *, project_root=None, workspace=None,
  executor=None, **default_executor_kwargs) -> Optional[ExecutionResult]`。
- 语义：构造/运行 `HybridExecutor` 这件事本身失败（`ImportError` 或其它
  环境性异常，如 provider 配置缺失）时返回 `None`；任务本身跑完但没
  通过校验（`ExecutionResult.ok is False`）**不会**被吞成 `None`，原样
  返回给调用方——这是本次实现里刻意做的区分，避免"环境不可用"和"这次
  任务没做成"两种性质不同的失败被调用方混为一谈。
- 已在 `hybrid_exec/__init__.py` 里导出（`from mini_agent.hybrid_exec
  import try_hybrid_exec`），风格与 `ledger.py::track_run()` 对齐。

## B1：`mini-agent hybrid-exec scaffold` 子命令

**新增文件**：`src/mini_agent/hybrid_exec/scaffold.py`
**改动文件**：`src/mini_agent/hybrid_exec/cli.py`

- `scaffold.py` 提供纯函数 `render_scaffold(carrier, task_id, desc) ->
  str`（`carrier` ∈ `{script, workflow-step, entrypoint}`）和
  `default_output_filename(carrier, task_id) -> str`，不做任何文件系统
  写入/参数解析，方便单测和被其它调用方（比如未来的对话式 skill）复用。
- 三种载体样板：
  - `script`：独立可执行脚本，`TaskSpec` + `default_executor(...).run(...)`
    骨架，输入来源留 `build_input_data()` TODO。
  - `workflow-step`：可直接粘进 workflow yaml `steps` 列表的
    `hybrid_step` 节点片段，字段对齐
    `hybrid_exec/workflow_integration.py` 头部 docstring 的完整参数表。
  - `entrypoint`：贴合 `external_projects` 标准结构的
    `entrypoints/<task_id>.py` 样板，**同时包含 A2 的 `try_hybrid_exec`
    降级包装** 和 `track_run()` 账本样板，风格对齐
    `.claude/skills/external-project-manager/templates/entrypoint.py.tmpl`
    （分析文档 B1 里提到的"外部项目 entrypoint 两件套模板"）。
- `cli.py` 新增 `scaffold` 子命令：`mini-agent hybrid-exec scaffold
  <task_id> --carrier {script,workflow-step,entrypoint} [--desc TEXT]
  [--output PATH] [--force]`。
  - 目标文件已存在且未传 `--force` 时拒绝覆盖，打印提示后直接返回（不
    报错退出码非零，因为这是可预期的正常路径，不是命令用法错误）。
  - **B3（生成后自检）一并实现**：`script`/`entrypoint` 载体生成后用
    `compile()` 做一次语法自检；`workflow-step` 载体尝试用 `pyyaml`
    做一次 YAML 解析自检（未安装 `pyyaml` 时跳过、不报错）。自检只验证
    "样板本身语法正确、能被 import/解析"，不代表 TODO 部分的业务逻辑
    已经补完整；自检通过后会打印一句提示，建议接下来跑
    `mini-agent hybrid-exec run <task_id> --project <path> -v` 验证
    `attempts` 决策轨迹。

## 测试

新增 `tests/test_hybrid_exec_external_integration.py`（16 个用例），覆盖：
- A1：`Workspace` 三个新属性的路径值；`workspace=`/`project_root=`
  等价性；都不传时抛 `ValueError`；两者都传时 `workspace` 优先。
- A2：不传任何定位信息时抛 `ValueError`；构造阶段抛异常时降级为
  `None`；传入现成 `executor` 时正常返回 `ExecutionResult`（不吞业务
  失败）。
- B1：三种 `carrier` 都能生成非空、含 `task_id` 的文本；未知 `carrier`
  抛 `ValueError`；`script`/`entrypoint` 样板能通过 `compile()`；
  `default_output_filename()` 各分支；通过子进程实际调用 CLI 验证
  `scaffold` 命令写文件、自检提示、以及未加 `--force` 时拒绝覆盖已有
  文件。

回归验证：`tests/test_hybrid_exec*.py`（除依赖 `fastapi` 的
`test_hybrid_exec_summary_route.py`，环境未装该可选依赖，与本次改动
无关）与 `tests/test_workspace.py` 全部通过，共 99 个用例（含本次新增
的 16 个）。

## 未落地项（留待后续）

- **A3**（外部项目状态命令附带 hybrid_exec 摘要）：依赖 A1，本次未做，
  需要改 `mini-agent projects status` 命令的输出，属于下一批改动。
- **B2**（常见任务类型的 `TaskSpec` 模板库）：内容型工作，按分析文档
  建议放最后，随时可以补充。
- **A4**（hybrid_exec 独立可安装子包）：分析文档已明确暂缓，等真实
  需求出现再评估，本次不动。
