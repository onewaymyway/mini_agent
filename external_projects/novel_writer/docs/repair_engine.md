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
