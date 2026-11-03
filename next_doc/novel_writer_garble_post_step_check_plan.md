# novel_writer：步骤完成后关键文件乱码检测 + 自动定位并重跑

> 状态：G1、G2、G3 已完成；G4 未开始。实现说明见 `external_projects/novel_writer/docs/garble_check.md`。
> 涉及：`external_projects/novel_writer/novel_writer/{garble.py, runner.py, chapter_ops.py, worker.py, state.py, settings.py, tool_api.py}` 与看板驾驶舱。

## 一、背景与缺口

第 13 章的「章节总结」文件出现乱码，写到第 14 章才被发现。原因是现有乱码检测只覆盖**章节正文**：

- `garble.py::is_garbled`：纯规则检测（替换符/私有区/控制字符、汉字旁半角 `?`、生僻字比例），零 LLM 成本；
- `chapter_ops.py::_garble_guard` + 步骤 `chNN.garble_guard`：只守卫正文，命中则重新生成，最多 3 次，仍失败则挂起问作者。

其余由 agent 写出的文件（章节总结、全书章节索引、人物档案、伏笔账本、蓝图、规划文档……）没有任何乱码检测，坏内容会被下游步骤当作输入继续使用，污染后续章节。已有的 `textio` 编码自愈只处理「文件整体按 GBK 存」这一类，不处理「UTF-8 字节被错解码后再存」。

乱码文本无法转码还原（见 `garble.py` 顶部说明），**唯一的修复办法是让产出它的步骤重新生成**。所以机制的核心是：检测 → 找到产出者 → 重跑产出者 → 防止死循环。

## 二、已有可复用的部件（已核对源码）

| 部件 | 位置 | 用途 |
|---|---|---|
| `is_garbled(text)` | `garble.py` | 判定函数，返回命中原因 |
| `_is_garbled_file(path)` | `chapter_ops.py` | 读不出来（编码错误）也按乱码处理 |
| 写入者记录 | `state.py::record_writer_paths`、`file_writers`、`read_write_history(path=…)` | 文件 → 最后写入它的步骤 id（`declared`/`agent`/`author` 三种来源） |
| 步骤内写入追踪 | `runner.py` 约 195 行 | 步骤开始/结束对声明的 inputs/outputs 做指纹比较，得到「本步骤改动的文件」 |
| 步骤内重试环 | `runner.py::_run_skill_step_inner` | 回执/产出校验不通过时，带着问题清单（`hint`）让 agent 重做，最多 `MAX_RECEIPT_ATTEMPTS` 次 |
| 产出内容校验 | `validators.py::content_problems`（`output_check`） | 产出存在但内容不合格时返回问题清单，进入上面的重试环 |
| 重跑与失效传播 | `state.reset_step`、`mark_stale`、worker 的自动重跑循环 | 重置步骤为 pending；上游产物变化后，下游已通过步骤被标为 stale |
| 钉死输出 | `chapter_ops._pin_regen_output` | 重生成时强制覆盖写入同一文件，不换文件名 |
| 挂起问作者 | `GARBLE_OPTIONS` + `_answers_file` | 「再重新生成 / 手动修改后重新核查 / 接受（风险自负）」 |

## 三、设计

### 3.1 检测什么：「关键文件」

不是每个文件都查，查**本步骤刚写过的、对下游有影响的文件**：

1. **声明产出**：`step.outputs` 展开后的文件（章节总结、审核报告、上下文包……）；
2. **关键文件注册表**（新增 `garble_watch`，按 workspace 路径键声明，步骤改动了其中任一文件就检）：章节总结、全书章节索引、记忆摘要、人物档案目录、伏笔账本、蓝图、节奏/主题等规划文档。这类文件是步骤顺手更新的、没写进 `outputs`，原有写入追踪覆盖不到（本次的「全书章节索引」就属于这种）；
3. 排除：`_studio/`、`导出/`、`_drafts/`、`*.bak`、非文本后缀。

判定沿用 `is_garbled`，不改阈值；汉字少于 200 时生僻字比例不判，所以很短的文件只有硬信号（替换符、控制字符、`?`）才会命中。

### 3.2 在哪里检测：两层

**A 层：步骤内（skill 步骤，成本最低）**
把乱码检测作为一类「产出内容问题」并入 `_run_skill_step_inner` 的重试环（紧接 `content_problems` 之后）：

- 命中 → 问题清单写成「`章节总结/第13章_总结.md` 疑似乱码（原因…）。请重写全文并**覆盖写入该文件**，文件名不变」，沿用现有 `hint` 让**同一步骤**的 agent 重做；
- 命中的乱码文件先备份到 `_studio/backup/garbled/<步骤>_<时间>/`（不删除）；
- 不新增步骤、不跨步骤调度，最多 `MAX_RECEIPT_ATTEMPTS` 次，用尽按 B 层处理。

**B 层：步骤完成后的统一守卫（所有步骤，含 python 步骤）**
在 `run_step` 里步骤将要置为 `passed` / `waiting_review` 之前执行一次检测：

1. 没有命中 → 不改变任何行为；
2. 命中 → **找产出者**（见 3.3）→ 按 3.4 处理。

A 层让绝大多数乱码在本步骤内就被修好；B 层兜底（A 层用尽、python 步骤、以及乱码出现在非本步骤声明范围）并处理「下游发现上游文件乱码」的跨步骤情况。

### 3.3 找产出者（归因）

对每个命中的文件 `rel`：

1. `file_writers[rel]` 取最后写入者；没有则查 `read_write_history(path=rel)` 最新的 `declared`/`agent` 记录；
2. 写入者是**当前步骤** → 产出者 = 当前步骤；
3. 写入者是**上游其他步骤** X（例如 python 步骤 `chNN.context_pack` 读到了上游乱码的总结）→ 产出者 = X；
4. 写入者是 `author`（作者手改）或查不到 → **不自动重跑**，只记录并提示作者（不能替作者决定重生成）；
5. 同一个文件被多个步骤写过（例如章节索引被每章归档追加）→ 取**最后一个**写入者；它通常是当前步骤。

### 3.4 修复动作

对产出者步骤 `P`（`mode = rerun` 时）：

- `P` 是当前步骤：把当前步骤标为 failed 并带上 `garble` 信息，交给 worker 现有的自动重跑循环（带 `retry_hint`＝乱码文件和原因，重生成时钉死输出文件）；
- `P` 是上游步骤：`reset_step(P)` 置为 pending，并把当前步骤也重置为 pending（它的产出建立在乱码输入之上）。调度器下一轮按计划顺序取第一个未通过步骤，自然先跑 `P`；`P` 的产出变化后，其下游已通过的步骤由 `mark_stale` 标为需重跑；
- 每个文件备份后再重跑，**不删除原文件**。

### 3.5 防死循环与成本控制

- 计数：`_studio` 状态里按「步骤 + 文件」记录 `garble_reruns`，默认上限 2 次（可配置）；文件检测通过时清零；
- 用尽后挂起 `waiting_user`，选项沿用 `GARBLE_OPTIONS`：再重新生成 / 手动修改后重新检测 / 接受；
- 「接受」把该文件当前指纹记入 `garble_accepted`，指纹不变则不再检测，改动后重新检测；
- 步骤时限、暂停/停止指令、预算检查沿用现有逻辑；检测本身只读文件，不调用 LLM；
- 与现有 `garble_guard`：保留不动。A 层会让 `write_chapter` 在步骤内就修好，`garble_guard` 变成第二道保险。

### 3.6 配置（保守、opt-in）

`settings.json`：

```json
"garble_check": {
  "mode": "off",           // off | report | rerun
  "max_reruns": 2,
  "watch_extra": []        // 额外要守卫的路径键或 glob
}
```

- `off`：不检测（现状）；
- `report`：只检测并写运行日志 + 收件箱，不改任何状态、不重跑；
- `rerun`：A+B 全开，自动定位并重跑。

默认值 `off`（见待确认 1）。

### 3.7 存量文件体检（解决「现在已经有乱码」）

新增 `tool_api.scan_garble(workspace)`：扫描全部关键文件，返回「文件 / 命中原因 / 归因步骤 / 是否可自动重跑」；驾驶舱加「乱码体检」按钮，列出结果并提供「重跑相关步骤」。与上一轮给的 `scan_garble.py` 同一判定，但带归因。这是处理第 13 章总结乱码的正式入口。

## 四、分阶段实施

| 阶段 | 内容 | 风险 |
|---|---|---|
| G1 | `garble_check` 配置 + 关键文件注册表 + `scan_garble()`（只读体检）+ 驾驶舱「乱码体检」；`report` 模式在 `run_step` 后只记录日志/收件箱 | 低：只读 |
| G2 | A 层：skill 步骤内重试环并入乱码检测（`rerun` 模式）+ 备份 + 钉死输出 | 中：改动 `_run_skill_step_inner` |
| G3 | B 层：`run_step` 统一守卫 + 归因 + 当前/上游步骤重置 + 计数 + 挂起问作者 | 中：涉及状态与失效传播 |
| G4 | 测试与文档（`docs/garble_check.md`） | 低 |

每阶段完成后更新文档并打包 diff zip（保持目录结构）。

## 五、测试要点

- 构造乱码的章节总结：A 层命中并让同一步骤重做，第二次产出干净则通过；
- python 步骤读到上游乱码：归因到上游 skill 步骤，上游被重置，当前步骤回到 pending，顺序正确；
- 作者手改的乱码文件：不自动重跑，只提示；
- 计数用尽：挂起 `waiting_user`；选「接受」后同指纹不再检测；
- `mode = off` 时行为与现状逐字节一致；
- 误报样本：含大量人名生僻字的正常段落不命中；
- 下游 `mark_stale` 在上游重跑后正确标记。

## 六、风险

- **误报**：生僻字比例阈值来自少量样本，规划文档里集中出现人名/古字可能误判。缓解：`report` 先行观察，`rerun` 前可在设置里把误报文件加入忽略；
- **文件被多个步骤写**（章节索引）：归因取最后写入者，重跑它只会重新追加本章内容，需要确认该步骤对索引的写法是幂等的，G3 前核对；
- **成本**：每次重跑是一次真实 LLM 调用，计数上限与 `rerun` 模式默认关闭共同控制；
- 本机制不解决「乱码为什么会产生」。建议并行排查 agent 的写入编码（Windows 下 cmd/PowerShell 写文件、`.原编码.bak` 说明已有历史编码问题）。

## 七、待确认

1. **默认模式**：建议默认 `off`（保守 opt-in），先开 `report` 观察误报，再切 `rerun`。是否同意？还是直接默认 `report`？
2. **关键文件范围**：3.1 的注册表是否合适？是否要加/减（例如规划文档类是否纳入）？
3. **上游乱码的处理**：3.4 中「上游步骤被重置并重跑」是否接受自动执行，还是只在 `rerun` 下对「本步骤产出的乱码」自动重跑、上游情况只提示？
4. **实施顺序**：G1→G2→G3；若想先解决第 13 章现状，只做 G1 即可（体检 + 手动重跑）。

## 八、实施记录

### G1（已完成）
- 新增 `novel_writer/garble_watch.py`：`WATCH_GLOBS` 注册表、`watched_files()`、只读 `scan_garble()`（归因：`attribute()`）、report 模式 `snapshot()` / `changed_since()` / `report_step()`；
- `settings.py` 新增 `garble_check`（`mode` 默认 `off`，`max_reruns` 默认 2，`watch_extra`），含校验；
- `runner.run_step` 接入 report 模式（仅 `mode != off` 时执行，不改变步骤状态）；`rerun` 暂按 report 处理并在日志注明；
- `tool_api.scan_garble()`；驾驶舱审校页新增「🧪 乱码体检」（体检、模式切换、重跑产出者按钮）；
- 测试 `tests/test_garble_check_g1.py`（13 例）通过；novel_writer 全量 736 passed，另有 3 个失败（`test_character_repair`、`test_step_selfcheck`、`test_write_history`）在改动前的 runner.py 上同样失败，与本次改动无关。

### 对「七、待确认」的处理（按方案默认值执行，如需调整请告知）
1. 默认模式：`off`，先开 `report` 观察；
2. 关键文件范围：采用 3.1 的注册表，已包含规划文档；`watch_extra` 可追加；
3. 上游乱码：G3 再实现，G1 只记录；
4. 实施顺序：G1 → G2 → G3，本轮完成 G1。

### G2（已完成）
- `garble_watch.inline_problems()`：rerun 模式下 skill 步骤通过其它校验后，检查本步骤改动的关键文件；命中则备份到 `_studio/backup/garbled/`，问题清单进入同一步骤的重试 hint；
- `runner._run_skill_step_inner` 接入（基线在进入重试循环前取，off/report 模式为空，行为不变）；
- 测试 `tests/test_garble_check_g1.py` 新增 4 例（重做后通过 / 用尽失败 / report 不重做 / off 不变），共 17 例通过；novel_writer 全量 740 passed，仍是 3 个既有失败。
- 文档 `docs/garble_check.md` 更新 rerun 模式章节。

### G3（已完成）
- `garble_watch.guard_after_step()`（B 层，所有步骤含 python，rerun 模式，在写入者记录之前执行）：检测范围 = 改动 ∪ 声明产出 ∪ 读取的关键文件；归因到本步骤 → 本步骤失败重试；归因到上游 → 重置上游与本步骤；作者/未知 → 仅收件箱；`garble_reruns` 按「产出者|文件」计数，用尽挂起 `waiting_user`；
- `garble_watch.handle_pending()`：挂起后的作者答复（再重新生成 / 手动修改后重新核查 / 接受）在步骤下次开始时处理；`garble_accepted` 记录指纹；
- `runner.run_step` 接入：开始前处理答复、结束时调用守卫；report 模式仍走 G1 的 `report_step`；
- 测试 `tests/test_garble_check_g1.py` 共 23 例通过（G3 新增 6 例：自检重试后挂起、接受风险、选择重新生成、下游读取上游乱码重置上游、作者手改仅提示、干净运行清零计数，另加 report 模式不改状态）；novel_writer 全量 746 passed，仍是 3 个既有失败；
- 文档 `docs/garble_check.md` 新增统一守卫章节。

### 与原方案的偏差
- 挂起决定的答复沿用步骤级 `answers` 文件，不新增交互入口；
- 每次收尾只处理第一条自动命中，其余下一轮处理（原方案未规定，取保守值）。
