# 记忆回填（Memory Backfill）与用户画像更新机制指南

> 对应方案：`next_doc/memory_backfill_and_profile_update_plan.md`
> （方向一：记忆回填 M1；方向二：画像增量更新 M2，均已实现）。
> 代码入口：`src/mini_agent/evolution/memory_backfill.py`（记忆回填）、
> `src/mini_agent/profile.py`（画像生成/更新）。

## 1. 这解决什么问题

长期记忆(`MemoryEntry`，成长顾问的信号扫描、用户画像都靠它)只有一条
写入路径：session 结束时的摘要生成。这条路径要求 session **正常走完
`save_session()`、且轮次达标**，因此以下几类 session **从来不会产生
任何记忆**：

- 进程被中断/崩溃，没有正常走到摘要生成那一步；
- 摘要生成本身因为 LLM 调用失败而静默失败，且没有重试；
- 轮次不够摘要生成阈值（`session.summary_min_turns`，默认 4）。

同时，用户画像（`profile.derived.summary/tech_stack/habits`，成长顾问
诊断面板里的"Agent 对你的了解"就是这个）此前每次刷新都是"只看最近
20 条记忆，从零重新总结、整体覆盖旧画像"，导致画像持续向"只反映最近
一小段任务"漂移——哪怕某个特征长期成立，只要相关记忆掉出了最近 20 条
窗口，下次刷新就会把它悄悄丢掉。

本机制分两部分修复：

- **记忆回填**：定期扫描 `summary` 为空但轮次达标的存量 session，离线
  补生成一次摘要并写入长期记忆。
- **画像增量更新**：画像刷新从"整体重写"改为"在上一版基础上更新"，
  并给 `tech_stack`/`habits` 的每一项挂一个"最后被印证时间"，供长期
  没有新证据支持的旧特征在下一轮生成时被显式标注、交给 LLM 重新评估
  是否还成立。

> **cron/daemon 任务的记忆覆盖**：cron 任务本身触发的运行不会产生
> `Session`，因此不在上述"离线扫描存量 session"的范围内，但这类运行
> 现在会在收尾时**直接**产出记忆（不经过 `Session`/摘要回填这条链路）
> ——见 2.5 节"cron 任务记忆回填（v4 N2）"。方案文档 2.4 节方案 B
> （cron 全面持久化 session）仍未实施，见 2.5 节末尾说明。

## 2. 记忆回填（Memory Backfill）

### 2.1 手动触发

```
/memory backfill [--dry-run] [--limit N]
```

- 不加参数：真正执行，扫描候选并补生成摘要 + 写入长期记忆，同时把
  `Session.summary` 回写到磁盘（只改 `meta.json`，不动 `history.json`）。
- `--dry-run`：只报告会处理哪些 session，不实际调用 LLM、不写入任何
  东西。**建议第一次先跑 dry-run 看看会处理哪些候选。**
- `--limit N`：覆盖单轮最多处理的候选数（默认见下方配置
  `max_sessions_per_run`）。

判定候选的条件：`Session.summary` 为空 **且** 轮次 ≥
`memory_backfill.min_turns_for_backfill`（默认 4，和正常摘要生成的
阈值一致）。**不限制候选的新旧程度**——多陈旧的 session 都会被扫描到，
只靠 `max_sessions_per_run` 控制单轮开销，多轮扫描下最终会覆盖全部
存量（候选按 `updated_at` 从旧到新排序，保证公平推进，不会被新
产生的候选一直插队）。

当前 session 不会被当作候选（正在进行中，还没到该生成摘要的时候）。

### 2.2 自动运行

内置 cron job `sys:memory_backfill_scan`，默认每 6 小时跑一次，
`/cron list` 能看到。跟成长顾问的信号扫描 cron job 一样，是"零成本
用起来"的默认开启策略，出问题可以单独关掉（见下方配置）。

### 2.3 配置项

`agent_config.json` 里的 `memory_backfill` 块：

```json
{
  "memory_backfill": {
    "enabled": true,
    "min_turns_for_backfill": 4,
    "max_sessions_per_run": 20,
    "cron_run_backfill_enabled": true
  }
}
```

- `enabled`：总开关，关闭后 `/memory backfill` 会提示未开启，cron job
  也会跳过。
- `min_turns_for_backfill`：轮次门槛，太短的 session 内容太少，回填
  意义不大。
- `max_sessions_per_run`：单轮最多处理的候选数，控制一次触发的 LLM
  调用量。
- `cron_run_backfill_enabled`：控制"cron 任务自身直接产出记忆"（2.5
  节）是否生效，默认 `true`。

### 2.4 幂等性

回填只处理 `summary` 为空的 session，写入摘要后 `summary` 非空，天然
幂等——不会重复处理已经回填过的 session。单个候选处理失败不影响其它
候选，失败的候选下次扫描会自然重新出现在候选列表里，不需要手动重试。

### 2.5 cron 任务记忆回填（v4 N2，方案文档 2.4 节方案 A）

对应 `next_doc/growth_advisor_improvement_plan_v4.md` 方向一、
`next_doc/growth_advisor_implementation_record.md` "N2" 章节。

**接入点**：`CronJobExecutor.run_job()` 收尾的 `finally` 块，跟产出物
清单写入并列。**触发条件**：仅当本次运行正常收尾（`final_status ==
idle`，不含 `timed_out`/`needs_human_review`）且有实质产出文本时才
生成记忆——异常/卡死/超时的运行不产出记忆，避免污染成长顾问的信号
扫描。

**跟 2.1~2.4 节的存量回填是两条独立链路**：cron 任务不经过
`Session`/`save_session()`，因此不会被 `sys:memory_backfill_scan`
扫到；本节的记忆是 cron 收尾时**直接**调用
`memory_backfill.py::generate_summary_from_text()`（复用离线回填同一套
摘要 prompt，额外把 job 的任务描述拼进输入）生成并写入的，`session_id`
格式为 `cron:<job_id>:<run_id>`，跟真实 `Session.id`（纯十六进制无
分隔符）取值空间不相交，成长顾问/记忆库的下游代码只做字符串相等比较，
不需要额外适配。

**去重**：同一 job 连续触发如果产出摘要跟该 job 最近一条已生成的记忆
高度雷同（比如"每小时检查一次待办"这类几乎不变的任务），会跳过本次
记忆写入，避免持续产生同质化内容稀释成长顾问信号扫描的信噪比；去重
只跟同一个 job 的历史比较，不跨 job 互相影响，且只影响记忆写入这一步，
不影响该次 cron 运行的其它收尾逻辑（产出物清单照常写）。

**配置**：`memory_backfill.cron_run_backfill_enabled`（默认 `true`，
2.3 节）。`CronJobExecutor` 未升级构造参数（比如测试里直接构造）时，
`memory_backfill_cfg`/`memory_backend`/`llm_client` 均为默认值
`None`，记忆生成静默跳过，不影响主流程，向后兼容。

**验收**：可以直接看 `docs/growth-advisor-guide.md` 5.5 节 N1 的
"📈 健康度趋势"图——上线后 `total_entries` 应能观察到回升。

**未做的部分（维持方案文档判断）**：
- 方案文档 2.4 节方案 B（cron 全面持久化 session，改变
  `cron_agent_bridge.py`"不跨触发保留历史"的核心设计前提）仍不建议
  现在做，建议先观察方案 A 至少一个迭代周期；
- 诊断面板/健康度快照尚未新增"cron 产出记忆 vs 真实交互 session 占比"
  这个维度的独立字段，留给后续需要时再补。

## 3. 用户画像增量更新

### 3.1 行为变化

- `/profile`：立即刷新画像，**走增量更新**——把上一版
  `summary/tech_stack/habits` 也提供给 LLM，只喂"自上次生成以来新增"
  的那部分记忆，要求在此基础上更新而不是重写。
- `/profile rebuild`：**全量重建**，不参考上一版画像，从最近
  `profile.max_entries_for_profile`（默认 20）条记忆重新生成——用于
  觉得画像跑偏了、想从头再来的场景。
- 自动刷新（记忆每新增 `profile.refresh_interval_entries` 条触发一次）
  同样走增量更新。首次生成（画像此前不存在）总是走全量分支，没有
  "上一版"可参考。

### 3.2 `tech_stack`/`habits` 的"最后被印证时间"

这两个字段现在是结构化列表，每一项形如
`{"text": "...", "last_confirmed_at": <时间戳>}`：

- 某一项在新一轮生成里被 LLM 继续保留，`last_confirmed_at` 会更新为
  本次生成时间；
- 新出现的条目，`last_confirmed_at` = 首次出现时间；
- 超过 `profile.stale_after_days`（默认 90 天）没有被再次印证的条目，
  会在下一次增量更新的 prompt 里被单独标注"很久没有新证据支持，请
  重新评估是否仍然成立"——**是否保留仍然由 LLM 判断**，这只是把"新鲜
  度"这个信号显式暴露给它，不是代码层面的硬删除。
- 旧版本产生的纯字符串列表格式在下次加载时会自动迁移成新结构，
  `last_confirmed_at` 无法回溯，统一取迁移发生的时刻。

### 3.3 诊断面板 / 看板展示

成长顾问诊断面板的"Agent 对你的了解"区块只展示 `text` 部分（跟改动前
的展示效果一致，是纯字符串列表）。`last_confirmed_at` 本身不直接展示，
但看板"🌱 成长顾问"tab 会基于它派生出两块只读展示：

- **🕰️ 待复核特征**：超过 `profile.stale_after_days`（默认 90 天）
  没有被再次印证的 `tech_stack`/`habits` 条目，以可折叠区块展示（默认
  折叠，不打扰日常查看），提醒用户"这些可能已经过时，下次画像刷新会
  交给 LLM 重新评估"——纯提示，不提供手动删除入口。
- **🗄️ 记忆回填状态**：还有多少存量 session 符合回填条件但尚未处理
  （`GET /v1/growth/summary` 的 `diagnostics.memory.
  backfill_candidates_count`——底层是增量维护的候选索引，O(1) 查询，
  不是每次都全量扫描，见 `docs/growth-advisor-guide.md` 5.9 节），以及
  系统内置回填任务 `sys:memory_backfill_scan` 的上次/下次运行时间（非
  daemon 模式下无法获取任务运行状态，会提示改用 `/memory backfill`
  手动执行）。

### 3.4 `profile.enabled` 默认值变化

`profile.enabled` 默认值从 `false` 改为 `true`——画像功能默认开启
（前提是记忆功能 `memory.enabled` 也是开启的；两者都关的话画像不会
生成任何内容，不会报错）。如果不想要这个功能，在 `agent_config.json`
里显式设置 `"profile": {"enabled": false}` 即可关闭。

## 4. 常见问题

**Q: 回填会不会把很久以前、已经不重要的 session 也翻出来？**

会。方案评审时明确决定不加"最多回溯多少天"的时间窗口——`summary`
为空就是候选，靠 `max_sessions_per_run` 控制节奏，不做"太旧就不管了"
的过滤。如果这些陈旧的记忆生成的画像/信号确实没有参考价值，可以
用 `/profile rebuild` 重新生成一版画像，或者直接删除对应的记忆条目。

**Q: 增量更新会不会导致画像里的旧内容永远删不掉？**

理论上有这个风险（去留完全由 LLM 判断），"最后被印证时间"就是为这个
问题准备的缓解手段——超期未印证的条目会被显式提醒重新评估。如果观察
到画像里堆积了明显过时的内容，可以用 `/profile rebuild` 手动重置。

**Q: 回填生成的记忆和正常 session 结束时生成的记忆有区别吗？**

没有本质区别，走的是同一套摘要生成 prompt
（`user/session_summary_request` + `system/summarizer`），产出的
`MemoryEntry` 结构完全一样，成长顾问的信号扫描不会区分两者。
