# novel_writer：数据修复「越界继续写后续步骤」根治方案

> 状态：**层 2（受限 Agent：无 skill + 白名单内置工具 + 策略 stop）已实施**，默认启用，`settings.repair_engine="agent"` 可切回旧方式；见 `external_projects/novel_writer/docs/repair_engine.md`。白名单含读/写/改文件与目录工具（不含 bash、delete_file）。同步修了越界回滚不删除新建文件的问题。层 0/1/4 及其余项未做（按确认范围只做本机制）。
> 涉及：`external_projects/novel_writer/novel_writer/repair.py`、`runner.py::FrameworkInvoker`、`worker.py::_repair_and_rerun`，以及主框架 `agent_config.json` 的 max-turns 策略对子 Agent 的继承。

## 一、现象

运行 `until_gate`，`ch13.context_pack` 因 `char_013.md` 第 9 行 `behavior_guide` 含英文双引号而失败（`DataFileError`）。
worker 按设计调用 `repair_attempt` 交给 LLM（skill `data-repair`）修复。结果：

- 修复本身其实成功了（07:55:56 校验输出 OK），随后写了 `receipt.json`；
- 但修复 Agent 没有结束，而是继续**自己动手写第 13 章正文**（`create_file 第13章 首都黎明.md`），接着 `skill_activate chapter-review` 做完章核查；
- 主流程（worker 的调度循环）根本没拿到控制权：它还在 `invoker(...)` 里等这个"修复"调用返回。

期望行为：修复调用只做 `ch13.context_pack` 所需的数据修复 → 返回 → worker 重跑 `ch13.context_pack` → 由主流程调度后续步骤。

## 二、根因（日志 + 源码逐条对应）

调度语义本身是对的（`worker.py::_repair_and_rerun` 只修复一次、只重跑同一步骤）。问题出在「修复调用」没有边界。

**R1 修复被当成开放式 Agent 任务。**
`FrameworkInvoker` 起的是完整主 Agent：60 个工具、笔记本、记忆、项目上下文，`cwd=PROJECT_ROOT`。「只改这个文件」只写在 prompt 和 skill 文字里；唯一的硬校验 `repair_attempt` 的快照比对发生在**调用结束之后**——而这个调用一直没结束。

**R2 `max_turns=20` 不是硬上限。**
`runner.py` 只给 step 设了 `max_turns=20`，但子 Agent 经 `load_config()` 继承了全局 `agent_config.json`：`max_turns_on_limit="compact_continue"`、`max_turns_hard_limit=250`。第 20 轮触发的不是停止，而是（日志 07:56:01）`hit 20, policy=compact_continue, compacting then continuing`：先压缩历史，再自动注入一条"继续"。

**R3 压缩把约束压没了，剩下的上下文把模型推向"写章节"。**
压缩后日志显示 `auto-unloaded 1 idle skill(s): ['data-repair']`——约束它的 skill 被当作闲置技能卸载；摘要里只剩"char_013 已修好"。此时 Agent 面对的是：`cwd` 是 novel_writer 项目根、项目文档/笔记本都在讲写小说流水线、用户消息只有一句"继续"。它自行推断"下一步该写第 13 章"。

**R4 做完没有终止信号。**
`receipt.json` 写成功后，Agent 循环不会停——只有模型回复里不再调用工具才结束；引擎只在循环结束后才读回执。所以"完成"无法终止它。

**放大因素（把预算耗在探路上，才撞到 R2 的上限）：**
- prompt 里的文件路径相对工作区，但子进程 `cwd` 是项目根 → 前十几轮在找文件：`ls`/`Get-ChildItem` 在 cmd 下不存在、`patch_file` 路径找不到、先读到另一本小说（`2126默示录`）的 `char_013.md`。
- 免费额度 429 限流反复冷却，把整个过程拖到近 8 分钟、累计输入 68 万 token。

**附带发现的守卫 bug（`repair.py:170-177`）：** 越界判定后的"还原"只把**被修改**的文件写回快照，**不会删除新建的文件**，并且会把范围内的合法修复一起回滚。也就是说，即使这个调用最终结束、守卫触发，结果也是「第 13 章正文残留 + char_013 的修复丢失」——两头都不对。

## 三、方案：修复 = 纯函数，副作用由代码落盘

原则：**边界靠结构保证，不靠提示词和事后检查。** 一个没有工具、没有循环、没有 cwd 的调用，不可能去写第 13 章。

### 层 0：确定性修复器（零 LLM，优先）
本次这类错误（字符串内含英文双引号）规则非常明确，且校验报错文字本身就在说"改用「」"。新增 `repair_rules.py`：

- `fix_inner_quotes(line)`：扫描 JSON 行，按"结构位置"区分——字符串收尾引号后面必须紧跟 `, ] } :`，其余视为内嵌引号，成对替换为「」。
- 修后必须同时满足：`json.loads` 通过；去掉所有引号类字符后与原文逐字相同（保证不删改内容）。任一不满足则放弃，交给下一层。
- 已用日志里的实际坏行（`说"我没事"然后…`、`说"我很安全"以外的话`）做过原型验证：原行 `Expecting ',' delimiter`，修后解析通过，文字逐字不变。
- 校验器注册表按 `validator` 名挂规则，其他校验器（伏笔账本等）可按需增加各自的规则，没规则则跳过本层。

### 层 1：单次补全式 LLM 修复（替换 Agent 式修复）
规则修不了时才调 LLM，但**不再起 Agent**：

- 代码根据校验报错定位到「文件 + 行号 + 字段」，只把该行和问题描述发给 LLM（复用框架已有的单次调用能力 `LLMHelper`，实施前先核对接口），要求只返回修好的那一行文本。
- 代码侧校验返回内容：能解析、字段名不变、与原行"去引号后文本一致"（或差异在允许范围内）；通过后由代码 `atomic_write_text` 替换该行，再跑 validator。
- 没有工具、没有历史、没有压缩、没有 cwd；token 从数十万降到几百，也不受 429 轮换里 Agent 长对话的拖累。

### 层 2：修复 Agent「不带 skill、只带白名单内置工具」（结构性限制，推荐作为 Agent 路径的做法）
框架已有现成机制，已核对源码，**不需要改主框架**：

- `ToolRegistry.filtered(names=[...])`（`tools/__init__.py`）：按工具名白名单生成子 registry。
- `Agent(..., skill_loader=None)`：`agent/core.py` 只有在 `skill_loader` 非空时才注册 `skill_list/skill_activate/skill_resource_*/capability_call` 等工具。传 `None` 后模型**根本没有 `skill_activate`**，不可能再去激活 `chapter-review` 之类的 skill。
- `Agent(..., extra_tools_enabled=False)`：跳过 `run_slash_command`、`proxy_*`、MCP、introspection（`agent_status`、笔记本等）的无条件注册。`judge_factory` 的"零工具判官"就是这样做的，注释里记录了这条机制就是为了防止工具泄漏。
- 组合起来：`Agent(cfg, guard, registry=get_default_registry().filtered(names=["read_file","patch_file"]), skill_loader=None, extra_tools_enabled=False)`，模型能看到的工具恰好是白名单。
- `step_cfg.max_turns_on_limit="stop"`、`max_turns_hard_limit=max_turns`：直接在构造出的 cfg 上覆盖，不继承全局 `compact_continue`（这是 R2 的根）。
- `data-repair` 本身也是 skill，所以不能再靠 `skill_name` 激活：把它的规则全部**内联进 prompt**（`_build_prompt` 已有大半，补齐 SKILL.md 第 1–6 条即可），并且 prompt 里文件路径一律给绝对路径。
- `read_file/patch_file` 的写入范围用 `PermissionGuard` 再收紧到 `request["files"]`（白名单工具名是粗粒度，路径范围是细粒度，两层都要）。

接入方式：`FrameworkInvoker` 目前只会走 `WorkflowRunner(skill_step.yaml)`，该路径在 `_spawn_minimal_agent` 里按 `skill_name` 取 loader，**无法表达"无 skill"**。所以修复要新增一个受限调用入口（`invoke_child` 的 payload 增加 `mode: "restricted"` 与 `allowed_tools`），在子进程里直接用上面的方式构造 Agent 并 `run_turn(prompt)`，不经过 workflow。子进程隔离与步骤时限强杀沿用现有机制。

效果对照：R1（工具/技能无限）→ 工具只剩白名单；R2（预算被放大）→ 策略 stop；R3（约束被压缩丢失、转去写章节）→ 即使压缩也没有任何能写章节或激活 skill 的手段；R4（无终止信号）→ 工具耗尽即停，模型不再调用工具就自然结束，最坏情况受 `max_turns` 硬限制。


### 层 3：修事后守卫的两个 bug
- 回滚时**删除新建文件**，不只是写回被改文件。
- 越界回滚后在 `run.log` 明确写"哪些文件被还原/删除"，并把 `repair_attempts` 计入，避免静默。
- 守卫范围从仅文本后缀扩展到整个工作区文件集合的增删对比（继续排除运行日志与导出目录）。

### 层 4：契约测试，防止回归
- 用日志里的坏行做用例：层 0 直接修好且不调用 invoker。
- 用桩 invoker 模拟"越界新建 `正文/第13章.md`"：断言文件被删除、`char_013` 状态与调用前一致、日志有记录。
- 断言修复路径下 `pipeline_state.json` 里除目标步骤外其他步骤状态不变。
- 断言修复子进程配置 `max_turns_on_limit == "stop"`（层 2）。

## 四、分阶段实施

| 阶段 | 内容 | 风险 |
|---|---|---|
| S1 | 层 0 规则修复器 + 接入 `repair_attempt` 最前面 + 用例 | 低：只在规则修好且校验通过时生效，否则原路径照旧 |
| S2 | 层 3 守卫 bug（删除新建文件、日志） | 低：只会让还原更彻底 |
| S3 | 层 2 受限 Agent（无 skill、白名单工具、策略 stop、绝对路径、prompt 内联规则；新增 `invoke_child` 受限入口） | 中：只改 novel_writer 侧，不动主框架；需验证白名单下 `read_file/patch_file` 足以完成修复，以及 `PermissionGuard` 路径收紧生效 |
| S4 | 层 1 单次补全式修复 | 中：新增调用路径，需核对 `LLMHelper` 接口与限流轮换行为 |

每阶段完成后更新 `external_projects/novel_writer/docs`，并打包 diff zip（保持目录结构）。

## 五、待确认

1. **默认值**：层 0/2/3 只会收紧边界，建议直接生效；层 1（把修复从 Agent 换成单次补全）是行为变化，是否沿用保守的 opt-in（新增 `settings.repair_engine`，默认保持原 Agent 路径，层 2 加固后的版本），还是直接作为默认？
2. **实施顺序**：是否按 S1→S2→S3→S4？若想先止血，也可只做 S1+S2。
3. 层 2 已改为只改 novel_writer 侧（不动主框架）。白名单建议只含 `read_file`、`patch_file`；是否还需要 `write_file`（整文件重写）？建议不给。

## 六、与本次日志无关、但顺手记录的问题

- `time_server` MCP 路径写成 `novel_writer/mcp_servers/time_server.py`，实际不存在，每次子进程启动都报连接失败（不影响结果，建议清理配置）。
- 日志里大量 `ls`/`pwd`/`Get-ChildItem` 失败，说明 `bash` 工具在该环境实际是 cmd，而 Agent 不知道；可在层 2 的白名单里直接去掉 bash。
- `system_tool_call.py` 的 JSON 修复在 `{"name":"bash","command":...}}` 这种形态下把 `command` 丢了（`bash() missing 1 required positional argument`），属于框架工具调用解析的独立小缺陷。
