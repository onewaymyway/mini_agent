# 数据修复引擎（repair_engine）

步骤读取的数据文件（人物档案、伏笔账本…）格式损坏时，worker 在自动重跑之前先交给 LLM 修复一次，再重跑**同一个**步骤；
接下来调度哪个步骤仍由主流程决定。机制见 `novel_writer/repair.py`。

## 为什么要有受限引擎

旧方式（`repair_engine = "agent"`）用完整主 Agent 激活 `data-repair` skill 去修复。它拥有全部工具、可以激活别的 skill，
且继承全局 `max_turns_on_limit=compact_continue`：到轮次上限后自动压缩历史并「继续」，`data-repair` 的约束被压缩丢掉，
Agent 会自行推断「下一步写第 N 章」，在修复调用里直接写正文、做完章核查，主流程拿不回控制权。

## 受限引擎（默认）

`settings.repair_engine = "restricted"`：

| 方面 | 做法 |
|---|---|
| skill | **不挂任何 skill**（`skill_loader=None`）。框架只在有 skill_loader 时才注册 `skill_activate` 等工具，所以模型没有激活别的 skill 的手段 |
| 工具 | 白名单内置工具：`read_file`、`write_file`、`create_file`、`patch_file`、`patch_file_simple`、`list_dir`、`tree_summary`、`glob`、`grep`。不含 `bash`、`delete_file`、`web_search`，也没有笔记本/记忆/自省/MCP/slash 工具（`extra_tools_enabled=False`） |
| 轮次 | `max_turns_on_limit="stop"`、`max_turns_hard_limit=max_turns`（默认 20），不继承全局 `compact_continue` |
| 规则 | `data-repair` 的规则直接写进提示词；待修文件一律给**绝对路径** |
| 进程 | 仍在子进程中执行，步骤时限强杀沿用原机制 |

修复后仍由 `repair_attempt` 核对：问题变多或改动了范围外文件 → 全部还原（含**修复期间新建的文件**，会被删除）。

## 切回旧方式

`_studio/settings.json`：

```json
{ "repair_engine": "agent" }
```

其余开关不变：`auto_repair`（默认 true）、`repair_max_attempts`（默认 3）。

## 测试

`tests/test_repair_restricted.py`：引擎默认值与切换、白名单内容、受限调用不带 skill 且路径为绝对路径、旧引擎调用形态不变、
回滚删除新建文件，以及用真实框架构造受限 Agent，断言工具集恰好等于白名单、无 skill 工具、策略为 stop。

## 接入蓝图（整本蓝图的文件格式错误）

**问题**：`ch31.word_quota` 等步骤在读取蓝图时，若某个章节文件格式坏了（例如 `ends_with` 字符串里混入英文双引号），原先会抛出普通的 `ChapterOpError`。普通错误不会记录修复请求，于是步骤只是按「自动重跑」反复失败，错误信息每次一样，永远不会好转。

**修复**：蓝图读取改为可修复的错误，走与人物档案相同的数据修复流程。

1. `blueprint_store.read_store` 在报错时记下出问题的文件名（`BlueprintStoreError.files`），包括 `_meta` 文件与各章文件。
2. `chapter_ops._blueprint_strict` 有文件出错时抛出 `RepairableDataError`，`validator="blueprint"`，`files` 为出问题的文件（相对工作区），`scope` 为蓝图目录。没有具体文件的错误（例如蓝图目录里一个章节文件都没有）仍是普通错误，不交给 LLM。
3. `repair.py` 注册了 `blueprint` 校验器，修复后用它确认问题是否消失。
4. runner 把修复请求记入步骤状态；worker 在自动重跑之前调用 LLM 修复（受限引擎，只允许修改列出的文件，范围外的改动一律还原），修复后重跑该步骤。修复次数仍按 `repair_max_attempts`（默认 3）计算，用尽后停下来等人处理，不再无限重置。

**效果**：蓝图文件格式错误会先由 LLM 修复，再自动重试，而不是原地重复失败。

测试：`tests/test_blueprint_repair.py`（错误携带文件与范围、校验器前后变化、LLM 修复后重跑成功、没有章节文件时仍为普通错误）。

## 通用修复：步骤多次重试失败后交给 LLM

前面两节是「校验器能指出具体文件」的情况。对于其它任何失败（没有校验器、原因不在数据格式里），现在有一条通用的兜底路径：

**触发条件**：步骤的自动重跑已用尽（`retries >= max_retries`），且仍然失败。在这之前，已有的结构化修复（带校验器的修复请求）仍然优先。

**做什么**
1. 把步骤最近一次的失败原因（`error`）作为「失败原因」交给 LLM。
2. 修复范围是该步骤的**输入文本文件**（`inputs` 中的文本文件，最多 20 个，单次送给 LLM 的文件内容不超过 12 万字）。没有可修的输入文件时不做修复。
3. 使用与结构化修复相同的受限引擎与范围控制：只能修改这些文件，范围外的改动一律还原。
4. 修复后重跑该步骤。修复本身不判定成败，结论由重跑给出。
5. 提示词明确允许 LLM 判断失败与文件无关（如外部服务、网络、权限）：此时它应不修改任何文件，直接说明原因。

**与结构化修复共用的约束**：`auto_repair` 开关、`repair_max_attempts`（默认 3，按步骤累计，通过后清零）。修复次数用尽后不再修复。

**不适用的情况**（直接按普通失败处理）
- 步骤标记了 `no_retry`（确定性错误，重试没有意义）。
- 步骤超时（`timed_out`）：耗时问题，改输入文件解决不了。
- 没有 LLM 调用器、修复已关闭、修复次数已用尽、没有可修的输入文件。

**出错不拖垮 worker**：修复调用本身抛出异常时，记入运行日志并按普通失败处理，不会把步骤状态变成 `error`。

**代价提示**：瞬时性错误（如临时限流）也会在重试用尽后触发一次 LLM 修复调用。LLM 通常会判断为与文件无关并不修改任何文件，但仍会消耗一次调用。

代码：`repair.py`（`generic_request`、`_checker`、`_build_prompt` 的通用分支、`repair_attempt` 的检查函数改为可选的校验器）；`worker.py`（`_generic_repair_and_rerun` 与重试循环末尾的接入）。

测试：`tests/test_generic_repair.py`（输入范围、范围外改动回滚、提示词含失败原因、修复后重跑、各种不适用情况）。全量测试中原有的 3 项既有失败与本次改动无关。
