# agent 自动 commit 撤销感知（agent commit guard）指南

> **这篇文档管什么**：`agent_commit_guard` 子系统"现在是什么样"——
> 默认行为、配置项、`/commit-guard` 命令、数据存放位置、已知限制。
> **不管什么**：为什么这样设计、否决过哪些方案、每个阶段具体怎么实现的——
> 这些"为什么/怎么做出来的"记录在
> [next_doc/agent_commit_undo_guard_plan.md](../next_doc/agent_commit_undo_guard_plan.md)，
> 本文档只链接过去，不复制内容。

## 1. 解决什么问题

daemon/cron 模式下，agent 会在整理工作区、执行例行任务时自行
`git commit`。其中部分文件用户并不希望被提交，事后会用
`git reset`/`git revert`/`git commit --amend` 等方式撤销——这个撤销
动作可能发生在 agent 会话之外（用户直接在自己的终端操作），也可能是
很久之后才发生的，agent 本身完全感知不到。

`agent_commit_guard` 做的事情：感知"agent 自动提交的内容后来被撤销
了"，并把这个事实转成一条前瞻性提醒，供 agent 下次准备 `git commit`
前参考——是否要把历史上被撤销过的文件排除在本次提交之外，由 agent
自主判断，不是写死的黑名单。

## 2. 整体流程（现状）

```
agent 执行 bash 工具 "git commit ..." 成功
        │
        ▼
记账：<project_root>/.agent/agent_commits.jsonl
  写入 commit hash / 涉及文件 / session_id / 时间 / commit subject
        │
        │  （之后的某个时间点——可能是同一个 agent 会话里，
        │   也可能是用户很久之后在别的终端里）
        ▼
用户执行 git reset / git revert / git commit --amend / git rebase
        │
        ├─ 若是 agent 自己在会话里执行的 → bash 工具执行完后立即核对
        │
        └─ 若是用户在 agent 之外执行的 → 靠以下任一时机核对：
             · 下次任意 bash 调用的机会性节流检查（默认 10 分钟一次）
             · agent 下次 SessionStart
             · 已安装的 post-checkout/post-merge/post-rewrite git hook
               写了"待检查"哨兵文件，下次检查会无视节流间隔立即跑
        │
        ▼
核对方式：对账本里每条"未结案"记录（从未核对过的 + 仍在复查窗口内、
尚未确认撤销的已核对记录）做
  `git merge-base --is-ancestor <commit_hash> HEAD`
  不在当前分支祖先链里 = 已被撤销（终态，立即结案）
  仍在祖先链里 = 记为"当前判断仍在"，但只要还在复查窗口内，
  下次核对时机还会再查一次，直到窗口过期才真正结案
        │
        ▼
写入 MemoryEntry(source="revert_record") lesson
  （与 `/evolution revert` 共用同一个函数
   `perception/agent_commit_guard.py::record_undo_lesson()`）
        │
        ▼
`evolution/lesson_to_reminder.py` 扫描到 revert_record（直接激活档位，
不需要凑够统计门槛）→ 生成 trigger_event=pre_tool、tool_name=bash 的
reminder
        │
        ▼
下次 agent 准备执行 bash 工具（尤其是 git commit）前，reminder 作为
context 注入，agent 据此自主决定是否跳过相关文件
```

**已知的采集盲点**：`git reset` 本身不触发任何 git hook，所以"用户在
agent 之外直接 reset"这种情况，无法被实时捕获，只能靠上面列的机会性
节流 / SessionStart / 哨兵文件三种兜底时机之一触发核对，**不是实时
的**——两次检查之间可能有延迟（默认最长 10 分钟，或者到下次 agent
会话启动为止）。

**"核对一次≠永久结案"（2026-08-26 修复）**：早期实现里，一条记录只要
被核对过一次——不管结果是"仍在历史里"还是"已撤销"——就会永久标记
`resolved=True`，之后再也不会被复查。这在实践中几乎总会漏掉真实场景：
机会性节流核对几乎必然在 commit 之后很快就跑一次（因为下一次 bash 调用
往往紧随其后），那时候用户还没来得及做任何撤销操作，于是记录被判定
"仍在历史里"并永久冻结——用户之后（哪怕几分钟后）在终端手动 `reset`，
账本再也不会去重新核对这条已"结案"的记录，`undone` 永远停在 0。这恰恰
是本节最开始说的核心场景。现在的行为：`resolved=True` 只代表"当前
判断"，只要还在 commit 后 3 天（`RECHECK_WINDOW_SEC`）的复查窗口内、
且复查次数未超过上限（`MAX_RECHECK_COUNT=50`），每次核对时机都会把它
重新纳入核对范围；一旦某次复查发现被撤销了，`undone=True` 立即变成
真正的终态，不会再复查。超过复查窗口后，"仍在历史里"的记录才会真正
结案——这是刻意的取舍，用有限的复查窗口换账本不会无限增长/无限复查，
超窗口后才发生的撤销仍然测不到，属于已知限制（见第 8 节）。

## 3. 默认行为

- **默认开启**，不需要任何配置就会记账、核对、生成 lesson。这跟
  `perception/behavior`（用户行为感知系统）"默认全关"的哲学不同——
  commit guard 不采集任何额外隐私信息，只处理仓库自己的 git 历史，
  且默认开就有用，所以默认开。
- git hook（`post-checkout`/`post-merge`/`post-rewrite` 哨兵）**不会
  自动安装**，需要用户显式执行 `/commit-guard install-hooks`——写
  `.git/hooks/` 属于对用户仓库的修改，不应该静默发生。不装这三个
  hook 也不影响核心功能，只是"用户在 agent 之外做了 checkout/merge/
  amend 之后"这几种场景的核对时机会退化成只依赖机会性节流 +
  SessionStart。
- 任何一步失败（不是 git 仓库、git 命令执行失败等）都只静默/警告，
  不会影响 bash 工具本身的执行结果或 agent 主流程。

## 4. 配置

配置文件：`<project_root>/agent_commit_guard_config.json`（不存在时用
默认值，全部默认开启）。

| 字段 | 默认值 | 说明 |
|---|---|---|
| `enabled` | `true` | 总开关 |
| `immediate_undo_check` | `true` | agent 会话内执行 `git reset`/`revert`/`amend`/`rebase` 后是否立即核对 |
| `opportunistic_scan_enabled` | `true` | 是否在每次 bash 调用时做节流版机会性核对 |
| `opportunistic_scan_interval_sec` | `600` | 机会性核对的最短间隔（秒） |
| `scan_on_session_start` | `true` | SessionStart 时是否做一次完整核对 |
| `ledger_max_pending` | `500` | 账本里"未结案"记录（含仍在复查窗口内的）的上限，超出后按时间淘汰最旧的 |

以下两个是模块内的常量（`perception/agent_commit_guard.py`），当前不
支持通过配置文件调整，如需修改需要改代码：

| 常量 | 默认值 | 说明 |
|---|---|---|
| `RECHECK_WINDOW_SEC` | `3 天` | 一条"仍在历史里"的记录，commit 之后多久内会持续被复查；超过之后才真正结案 |
| `MAX_RECHECK_COUNT` | `50` | 单条记录最多被复查多少次的兜底上限（正常情况下远用不到） |

可以直接编辑这个 JSON 文件，也可以用 `/commit-guard on`/`off` 只切换
`enabled` 字段。

## 5. `/commit-guard` 命令

| 命令 | 说明 |
|---|---|
| `/commit-guard` 或 `/commit-guard status` | 显示开关状态、配置摘要、账本统计（已记账 / 待首次核对 pending / 复查窗口内 rechecking / 已确认撤销 undone 数量） |
| `/commit-guard on` / `/commit-guard off` | 打开/关闭总开关 |
| `/commit-guard scan` | 立即核对一次（忽略节流间隔），命中即写入 `revert_record` lesson（需要在有可用 memory 后端的 agent 会话内执行才会真正写 lesson，纯 CLI 脚本调用只做核对不写 lesson） |
| `/commit-guard install-hooks [repo]` | 给指定仓库（默认当前项目）安装 `post-checkout`/`post-merge`/`post-rewrite` 哨兵 hook |
| `/commit-guard ledger [n]` | 查看账本最近 n 条（默认 20），标注 `pending`/`rechecking`/`resolved`/`undone` 状态（`rechecking` = 已核对过、判定仍在历史里，但还在复查窗口内，之后可能被翻成 `undone`；`resolved` = 复查窗口已过期，真正结案） |
| `/commit-guard clear` | 清空账本文件（不影响已经生成的 lesson/reminder，那些已经进了 `MemoryStore`，不会跟着账本一起清） |

## 6. 数据存放位置

- 账本：`<project_root>/.agent/agent_commits.jsonl`（jsonl，每行一条
  `commit_hash` / `files` / `subject` / `session_id` / `created_at` /
  `resolved` / `undone` / `resolved_at` / `last_checked_at` /
  `checked_count`。后两个字段是复查机制新增的：`last_checked_at` 记录
  最近一次核对时间，`checked_count` 记录已核对次数，用于配合
  `RECHECK_WINDOW_SEC`/`MAX_RECHECK_COUNT` 判断是否已经真正结案）。
- 哨兵文件：`<project_root>/.agent/.commit_guard_pending_scan`（安装
  hook 后由 `post-checkout`/`post-merge`/`post-rewrite` 写入，下次任意
  核对时机会消费并删除）。
- 配置：`<project_root>/agent_commit_guard_config.json`。
- 生成的 lesson：跟其他来源的 lesson 一样，进 `MemoryStore`（默认
  `memory.jsonl`，见 [记忆管理指南](memory-management-guide.md)），
  `source="revert_record"`，半衰期 14 天（最快，详见
  [记忆与自我演化完整参考 §5.4](memory-and-self-evolution-complete-reference.md#54-revert_record-evolution-revert-与-commit-guard-联动)）。

## 7. 与 `/evolution revert` 的关系

`/evolution revert <commit>` 撤销一个 evolution 自我改进提案 commit
时，也会生成同样结构的 `revert_record` lesson——两处调用的是同一个
函数 `perception/agent_commit_guard.py::record_undo_lesson()`，
`cli/commands/evolution.py::_record_revert_lesson()` 只负责拼接场景
专属的 trigger/outcome 文案再转调，不是两套独立实现。区别只在于：
`/evolution revert` 针对的是 evolution 自我改进提案 commit（人工显式
执行该命令触发），本指南描述的 commit guard 针对的是任意 agent 自动
commit（既可能是人工 `/commit-guard scan` 触发，也可能是系统自动核对
触发）。详见 [Stage 2 安全网指南 §6.1](self-evolution-stage2-guide.md#61-revert-自动反哺-lesson)。

## 8. 已知限制

- **`git reset` 撤销不是实时感知的**：见第 2 节"已知的采集盲点"，最长
  可能有一个机会性核对周期（默认 10 分钟）或一次 agent 会话重启的
  延迟，不适合对"撤销后立刻响应"有强实时性要求的场景。
- **复查窗口过期后发生的撤销仍然测不到**：一条记录如果在 commit 后
  3 天（`RECHECK_WINDOW_SEC`）内始终"仍在历史里"，超过窗口就会真正
  结案，不再复查——如果用户是在 3 天之后才 reset 掉这次提交，会检测
  不到。这是有意的取舍（否则账本要么无限增长，要么要无限期反复扫描
  每一条历史记录），3 天覆盖了绝大多数"事后反悔"场景；如果实际需要
  更长的窗口，需要改代码里的常量（暂不支持通过配置文件调整）。
- **reminder 是软提示，不是硬过滤**：`reminders/matcher.py` 现有的
  `pre_tool` 匹配只按 `tool_name` 生效，不支持按命令参数内容匹配，
  所以生成的 reminder 是"以文字形式提到哪些路径历史上被撤销过"，
  agent 看到后自主决定是否排除，不会被强制拦截。如果需要更强的确定性
  （比如某些路径无论如何都不能被 agent 提交），应该用别的机制（如
  `.gitignore`、pre-commit hook 拒绝提交特定路径），commit guard 本身
  设计目标就是"辅助记忆"而不是"访问控制"。
- **机会性节流状态不持久化**：`_last_scan_at` 是进程内内存字典，agent
  进程重启后节流计时清零（首次调用一定会跑一次核对），实际影响很小
  （只是多跑一次 `git merge-base --is-ancestor`）。
- **`install-hooks` 需要手动执行**：检测到是 git 仓库不会自动安装
  哨兵 hook，需要用户显式跑一次 `/commit-guard install-hooks`。

## 9. 相关文档

- [next_doc/agent_commit_undo_guard_plan.md](../next_doc/agent_commit_undo_guard_plan.md) — 设计方案、阶段实施记录、变更记录（"为什么这样、怎么做出来的"）
- [记忆与自我演化完整参考 §5.4](memory-and-self-evolution-complete-reference.md#54-revert_record-evolution-revert-与-commit-guard-联动) — `revert_record` 来源在整个记忆体系里的位置
- [Stage 2 自我演化安全网指南 §6.1](self-evolution-stage2-guide.md#61-revert-自动反哺-lesson) — `/evolution revert` 联动细节
- [Reminder 系统指南](reminder-system-guide.md) — reminder 生成/匹配/注入的通用机制
- [命令与工具速查](commands-and-tools-reference.md) — `/commit-guard` 命令简表（同步副本，改动需一并更新）
