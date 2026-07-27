# Workflow 机制改进计划 P15

> 承接 P12→P13→P14 三轮迭代。P14 总结里列出的走查原始候选项
> （`foreach`/`map`、`merge`/`aggregate`、`wait`、`tool_call` 占位符、
> `result_file` 字段访问、condition 异常分类、workflow 级熔断、内置/
> 插件校验路径统一）已逐项落地或做出取舍。走查纪要里还有一条当时被
> 归到"`script` 类型可以补一个 structured 模式"的意见，在 P12-P14 三份
> 文档里都没有被显式列入候选池、也没有被实施——本文档（P15）单独把这
> 一项补上。

## 背景 / 走查结论

原始走查意见原文：

> **`script` 类型可以补一个"structured 模式"**，类似
> `skill_agent`/`python_step` 已经有的 `result_file` 契约——现在
> `script` 的返回值是 stdout 全文本，下游 `python_step` 想要结构化数据
> 还得自己在脚本里 `json.loads`，跟你们已经在 `skill_agent` 上踩过的
> "下游拿到的是原始文本不是结构化结果"是同一类问题，值得统一成一套
> 约定。

核对现状（`src/mini_agent/workflow/`）：
- `result_file`/`result_file_required_keys` 两个字段本身在 `schema.py`
  里已经是 `WorkflowStep` 的**通用**字段，不是 `skill_agent` 专属——
  `WorkflowRunner.resolve_result_file_path()`/`_validate_result_file()`
  两个方法也都只依赖 `step.result_file`，跟 `step.type` 无关。
- 但目前只有 `SkillAgentStepExecutor` 实际调用了这两个方法；
  `ScriptStepExecutor.execute()` 完全没有引用 `step.result_file`，
  声明了 `result_file` 的 `script` step 会被静默忽略——`result_file`
  校验不会触发，`{step_id.result_file}` 占位符也解析不到值，只是
  恰好不报错，容易让人误以为"所有类型都支持 result_file"而踩坑。
  这正是走查意见指出的缺口，值得补齐。
- `merge_use_result_file`（P14）、`foreach` 的 `items` 占位符（P13）都是
  "读别的 step 已经产出的 result_file"，而不是"让 script 自己产出
  result_file"，两者不冲突，是这条契约的两个不同使用方向（生产端 vs
  消费端），补上生产端后消费端能直接复用、不用再改。

## Phase 1 — `script` 支持 `result_file` structured 模式

**改动范围**：`executors.py`（`ScriptStepExecutor`）、`schema.py`
（`result_file` 字段注释更新，不改字段定义/不新增字段）。

**设计**：
- 不新增任何 `WorkflowStep` 字段——`result_file`/
  `result_file_required_keys` 已经是通用字段，`script` 类型直接复用，
  跟 `skill_agent` 共用同一套校验方法
  （`runner.resolve_result_file_path()`/`runner._validate_result_file()`），
  不重新发明契约。
- `ScriptStepExecutor.execute()`：
  - 未声明 `step.result_file` 时，行为完全不变（stdout 即结果，向后
    兼容所有现有 `script` step）。
  - 声明了 `step.result_file` 时：在 `subprocess.run()` 之前先调用
    `runner.resolve_result_file_path(step)` 算出绝对路径，通过环境变量
    `WORKFLOW_RESULT_FILE_PATH` 注入子进程 env（`script` 是裸 shell
    命令，没有 `python_step` 那样的 stdin JSON 协议，环境变量是唯一
    轻量、不用改调用约定的通道）——脚本内用
    `echo "$WORKFLOW_RESULT_FILE_PATH"` 或语言内 `os.environ` 拿到
    路径后自行写文件。
  - 子进程 `returncode == 0`（脚本自身判定的"成功"）之后，若声明了
    `result_file`，追加调用 `runner._validate_result_file(step)`——
    校验不通过（文件不存在/不是合法 JSON/缺必填 key）时，即使
    `returncode == 0` 也把整个 step 判定为失败，抛 `RuntimeError`
    交给外层既有 `retry_on_error`/`NEEDS_FIX` 机制处理（不做
    `skill_agent` 那种"resume 同一个 agent 补写文件"的重试——`script`
    是一次性子进程、没有可 resume 的会话上下文，重跑整个 step 是唯一
    合理的重试形态，这点与 `skill_agent` 的差异在文档里写清楚，不是
    遗漏）。
  - 校验通过时返回值不变，仍是 `proc.stdout`（人类可读的执行日志/
    调试信息保留在 `output` 里；结构化数据走 `result_file`，与
    `skill_agent` 的"`output` 是对话原文、`result_file` 是结构化产物"
    的既有分工完全一致，下游 `python_step`/`{step_id.result_file:...}`
    占位符不需要区分"这个 step 是 script 还是 skill_agent 产出的
    result_file"，用法统一）。
- `schema.py`：把 `result_file` 字段注释里"skill_agent 专用"改成
  "`skill_agent`/`script` 通用"，说明两种类型分别通过什么方式让
  runner 知道目标路径（`skill_agent` 是在 prompt 里注入路径文字给
  Agent 看；`script` 是通过环境变量注入给子进程），不改变字段本身。
- `tests/test_workflow_p15.py` 新增用例：
  - 未声明 `result_file` 时 `script` 行为不变（回归）。
  - 声明 `result_file` 且脚本正确把 JSON 写入
    `$WORKFLOW_RESULT_FILE_PATH` 时，`_validate_result_file` 通过，
    执行成功返回 stdout，且 `runner._step_result_file_paths` 里记录了
    该 step 的文件路径（供 `{step_id.result_file}` 占位符/下游
    `python_step` 使用）。
  - 声明了 `result_file` 但脚本未写文件（或写的不是合法 JSON）时，
    即使 `returncode == 0` 也应该抛异常（体现"structured 模式下，
    stdout 成功不等于整体成功"）。
  - 声明了 `result_file_required_keys` 且脚本写的 JSON 缺少必需字段时
    同样报错。
  - 端到端集成用例（走 `WorkflowRunner.run()` 真实 session 目录，不是
    手工 mock 路径）：一个 `script` step 声明 `result_file`，写入
    `{"total": 3}`，下游 `python_step`（或直接断言
    `StepResult.result_file`）能读到该文件。

**验收标准**：新增用例通过，且不影响任何现有未使用 `result_file` 的
`script` step 用例（`tests/test_workflow_step_types.py` 里
`TestScriptStepExecutor` 三条既有用例保持不变原样通过）。

> **已实施**（本次改动）：
> - `executors.py::ScriptStepExecutor.execute()`：新增
>   `step.result_file` 分支——声明时，先用
>   `runner.resolve_result_file_path(step)` 算出绝对路径，通过
>   `env=dict(os.environ, WORKFLOW_RESULT_FILE_PATH=str(result_path))`
>   传给 `subprocess.run()`（未声明 `result_file` 时不传自定义 `env`，
>   完全沿用旧的 `_popen_kwargs`，不影响任何现有 script step）；
>   `returncode == 0` 后，若声明了 `result_file`，额外调用
>   `runner._validate_result_file(step)`，不通过则拼接原因抛
>   `RuntimeError`（文案里带上 `stdout`/`stderr` 便于排查，跟原有
>   "returncode!=0" 分支的报错格式风格一致）；通过则照常返回
>   `proc.stdout`。
> - `schema.py`：`result_file`/`result_file_required_keys` 字段注释
>   更新为"`skill_agent`/`script` 通用"，补充两种类型各自如何让被执行
>   方知道目标路径的一句说明；未改动字段定义、未新增字段、未改动
>   `validate()`（`result_file` 本来就没有类型限定的校验规则，不需要
>   新增）。
> - 新增测试（`tests/test_workflow_p15.py`）：`TestScriptResultFile`
>   （4 条：未声明 result_file 时行为不变、脚本正确写文件时校验通过且
>   `_step_result_file_paths` 被填充、脚本未写文件时即使 returncode=0
>   也报错、`result_file_required_keys` 缺失字段时报错）、
>   `TestScriptResultFileIntegration`（1 条：走真实
>   `WorkflowRunner.run()`，含真实 session 目录，`script` step 写
>   `result_file` 后下游 `python_step` 能通过
>   `inputs["producer"]["result_file"]` 读到路径并解析出内容）。
> - 测试结果：新增 5 条用例全部通过；`tests/test_workflow_step_types.py`
>   里 `TestScriptStepExecutor` 既有 3 条用例保持不变、全部通过；
>   连同 P10-P14 既有 workflow 测试文件（12 个）+ 本次新增，全量重跑
>   3 轮稳定，唯一失败仍是此前 P13/P14 阶段已确认、与本轮改动无关的
>   `test_workflow_p11.py` 里那条依赖真实线程调度时序的 flaky 用例。
> - 涉及文件：`src/mini_agent/workflow/executors.py`、
>   `src/mini_agent/workflow/schema.py`（注释）、
>   `tests/test_workflow_p15.py`（新增）。

---

## 总结（P15 完成）

| Phase | 改动 | 状态 |
|---|---|---|
| 1 | `script` 类型支持 `result_file` structured 模式（复用既有通用字段与校验方法，仅扩展 `ScriptStepExecutor`） | 已完成 |

本轮只改动了 `executors.py` 一个执行文件（`schema.py` 仅注释更新，
未改字段/未改 `validate()`），新增 `tests/test_workflow_p15.py`
（5 条用例）。至此，最初走查纪要里提出的全部候选项——`foreach`/`map`
（P13）、`merge`/`aggregate`（P14）、`wait`（P13）、`tool_call` 占位符
（P12）、`result_file` 字段访问（P12）、condition 异常分类（P12）、
workflow 级熔断（P14）、内置/插件校验路径统一（P14，scoped-down）、
`script` structured 模式（P15）——均已逐项落地或做出有记录的取舍，
P12-P15 四轮迭代告一段落。

**后续候选**：`skill_agent` 的"resume/restart 补写文件"重试策略目前
是该类型专属逻辑；如果未来 `script`/`python_step` 也出现"result_file
校验失败后值得原地重试而不是整体重跑"的真实场景，可以再评估是否要
把这部分重试策略也抽成通用能力——目前没有实际案例支撑，不在本轮
展开设计。

## 实施与交付方式

单 Phase 直接实施（改动范围小、且是走查纪要里唯一遗漏项，不需要像
P13/P14 那样拆多个 Phase）。完成后：跑相关测试确认改动本身及既有
回归不受影响 → 在本文档补"已实施"记录 → 打包本次改动涉及的文件供
下载。
