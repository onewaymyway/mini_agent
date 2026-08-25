# agent 自动 commit 撤销感知与前瞻提醒方案（agent_commit_guard）

## 背景问题

daemon/cron 模式下，agent 在整理工作区、执行例行任务时会自行 `git commit`，
其中有些文件是用户不希望被提交的。用户发现后会手动 `git reset`/`git revert`
等方式撤销（undo last commit），但这个撤销动作：

1. 可能发生在 agent 会话之外（用户直接在自己的终端操作）；
2. 可能发生在很久之后，不一定紧跟着那次 commit；
3. 目前 agent 完全感知不到——`git reset` 本身不触发任何 git hook，
   现有 `perception/behavior` 的 git 采集器也只装了 `post-commit`/
   `post-checkout` 两个 hook。

目标：让 agent 能够感知"自己自动提交的内容后来被用户撤销了"，并把这个
经验转化成下次提交前的前瞻性提醒（软提示，而不是硬编码黑名单）。

## 设计原则：不新建独立系统，复用现有机制

项目里已经有三块可以直接拼起来复用的机制：

- **hooks 系统**（`hooks/loader.py` + `tool_executor.py` 里的
  PreToolUse/PostToolUse 调用点）：可以在 agent 执行 `bash` 工具时拿到
  命令原文和执行结果。
- **lesson → reminder 闭环**（`perception/memory_store.py` 的
  `MemoryEntry(source="revert_record")` + `evolution/lesson_to_reminder.py`）：
  `cli/commands/evolution.py` 的 `/evolution revert` 命令已经实现了
  "一次具体的撤销 → 一条 revert_record lesson → 自动激活成 pre_tool
  reminder" 的完整链路，只是场景局限在"evolution 自我改进提案 commit"。
  本方案把它泛化成"任意 agent 自动 commit"这个更通用的场景，两处共用
  同一个记录函数，不维护两套几乎一样的代码。
- **行为感知系统的 git hook 生成器**（`perception/behavior/collectors/
  external_hooks.py`）：提供了"往 `.git/hooks/` 里写脚本"的现成模式，
  可以参考它的写法，但 commit guard 本身**默认开启**（跟 behavior 感知
  系统"默认全关"的隐私哲学不同），所以做成自包含模块，不依赖 behavior
  感知总开关。

## 整体流程

```
agent 执行 bash 工具
  │
  ├─ 命令是 "git commit" ────────────► 记账：写入 <project_root>/.agent/agent_commits.jsonl
  │                                      （commit hash / 涉及文件 / session_id / 时间 / subject）
  │
  └─ 命令是 "git reset/revert/commit --amend/rebase" ─► 立即核对（路径 A）
                                                          │
SessionStart / 每隔一段时间的机会性检查 ────────────────►│
（同时消费 git hook 写的"待检查"哨兵文件，见下）          │
                                                          ▼
                                    对账本里未确认的 commit 逐条做
                                    `git merge-base --is-ancestor <hash> HEAD`
                                    不在祖先链里 = 被撤销（路径 B，覆盖
                                    "用户在 agent 之外直接 reset" 的场景）
                                                          │
                                                          ▼
                                    生成 revert_record lesson
                                    （复用 evolution.py 抽出来的共享函数）
                                                          │
                                                          ▼
                                    lesson_to_reminder.py 扫描 → 生成
                                    pre_tool + tool_name=bash 的 reminder
                                                          │
                                                          ▼
                                    下次 agent 准备 commit 前，作为
                                    context 注入，agent 自主决定是否
                                    把相关文件排除在本次提交之外
```

## 需求确认结论（本次已拍板）

| 问题 | 结论 |
|---|---|
| 默认开关 | **默认开启**，`agent_commit_guard_config.json` 里可关闭 |
| 撤销核对时机 | 定时检查（机会性节流 + SessionStart）**+ git hook 触发**（`post-checkout`/`post-merge`/`post-rewrite` 写哨兵文件，下次检查时优先处理，不受节流间隔限制） |
| 账本文件位置 | 项目级：`<project_root>/.agent/agent_commits.jsonl` |
| 与 `/evolution revert` 的关系 | 合并成同一个共享函数 `record_undo_lesson()`，两处调用方都改用它 |

## 实现阶段

- [x] 阶段 0：设计方案确认（本文档）
- [x] 阶段 1：`agent_commit_guard.py` 核心模块（`src/mini_agent/perception/agent_commit_guard.py`）
      - 配置读写（默认开启，`<project_root>/agent_commit_guard_config.json`）
      - 账本读写（`<project_root>/.agent/agent_commits.jsonl`，含
        `resolved`/`undone` 标记，避免重复处理，超量自动裁剪最旧的
        pending 记录）
      - `git commit` 命令识别 + 记账（`record_agent_commit`）
      - `git reset/revert/amend/rebase` 命令识别 + 立即核对（路径 A，
        `is_git_undo_command` + `scan_for_undo(via="agent_session")`）
      - 祖先链核对 `scan_for_undo()`（路径 B，`git merge-base
        --is-ancestor`，`None` 结果视为"无法判断"不误判）
      - 机会性节流核对 `maybe_opportunistic_scan()` + 哨兵文件消费
        `consume_pending_sentinel()`（哨兵存在时无视节流间隔）
      - 共享的 `record_undo_lesson()`（写 `MemoryEntry(source="revert_record")`，
        供本模块和 `/evolution revert` 共用）
      - 自包含的 git hook 安装函数 `install_undo_scan_git_hooks()`
        （`post-checkout`/`post-merge`/`post-rewrite` → 写哨兵文件
        `.agent/.commit_guard_pending_scan`；追加写入，不覆盖用户已有
        hook 内容；`git reset` 本身仍不触发任何 hook，靠机会性节流 +
        SessionStart 兜底）
- [x] 阶段 2：接入
      - `tool_executor.py`：PostToolUse 之后、bash 工具专属处理里调用
        `on_bash_post_tool()`（记账或核对，失败静默，不影响 bash 结果）
      - `agent/lifecycle.py::_init_session`：SessionStart 时调用
        `on_session_start()`（消费哨兵文件 + 做一次完整核对）
- [x] 阶段 3：重构 `cli/commands/evolution.py`
      - `agent_commit_guard.record_undo_lesson()` 改为返回写入的
        `MemoryEntry`（或 `None`），方便调用方复用同一个 entry 对象
        （原来 `_append_memory_delta` 需要跟 `add()` 是同一条 entry）
      - `_record_revert_lesson()` 改为调用共享函数
      - **顺手清理了一处既有 bug**：`_handle_outcomes()` 函数体末尾
        原本混入了一份复制粘贴的死代码（引用了未定义的 `reverted` 变
        量，一旦被执行到会 `NameError`），已删除
- [x] 阶段 4：CLI 速查命令
      - `src/mini_agent/cli/commands/commit_guard.py`：`/commit-guard
        status|on|off|scan|install-hooks [repo]|ledger [n]|clear`
      - 注册进 `cli/commands/__init__.py` 与 `cli/repl.py`（`/commit-guard`
        分支），模式参照 `/quarantine`
- [x] 阶段 5：测试（`tests/test_agent_commit_guard.py`）+ 文档更新

> 约定：每完成一个阶段，回来把上面对应的复选框打勾，并在本文件末尾的
> "变更记录"里补一行。阶段之间可能因为看到具体代码而做小调整，调整会
> 记在这里，不会静默改判断。

## 涉及文件清单（本次改动）

- 新增 `src/mini_agent/perception/agent_commit_guard.py` — 核心模块
- 新增 `src/mini_agent/cli/commands/commit_guard.py` — `/commit-guard` CLI 命令
- 新增 `tests/test_agent_commit_guard.py` — 单元测试（14 条：核心模块
  10 条 + CLI 命令 4 条，用真实临时 git 仓库覆盖记账/核对/lesson 生成/
  hook 安装/节流+哨兵/CLI 各子命令）
- 修改 `src/mini_agent/tool_executor.py` — PostToolUse 之后接入
  `on_bash_post_tool()`
- 修改 `src/mini_agent/agent/lifecycle.py` — `_init_session` 里接入
  `on_session_start()`
- 修改 `src/mini_agent/cli/commands/evolution.py` — `_record_revert_lesson`
  改调用共享函数；顺手清理 `_handle_outcomes` 末尾的死代码/潜在 NameError
- 修改 `src/mini_agent/cli/commands/__init__.py` — 注册 `handle_commit_guard_cmd`
  + 模块包说明里补一条命令列表
- 修改 `src/mini_agent/cli/repl.py` — import + `/commit-guard` 分支
- 新增 `next_doc/agent_commit_undo_guard_plan.md` — 本文档

## 使用方式速查

```
/commit-guard status              查看开关状态 + 账本摘要
/commit-guard on / off            开关（默认已开启）
/commit-guard install-hooks       给当前仓库装 post-checkout/merge/rewrite 哨兵 hook
/commit-guard scan                立即核对一次（忽略节流），命中即写 revert_record lesson
/commit-guard ledger [n]          查看账本最近 n 条（默认 20）
/commit-guard clear               清空账本（不影响已生成的 lesson/reminder）
```

## 已知限制 / 后续可做

- 机会性节流扫描的 `_last_scan_at` 是进程内内存字典，多进程/重启后会
  丢失节流状态（首次调用一定会跑一次），影响很小（只是多跑一次
  `merge-base --is-ancestor`），未做持久化。
- reminder 的 `pre_tool` 匹配目前只按 `tool_name` 生效（`condition`
  不支持按参数内容匹配，这是 `reminders/matcher.py` 现有的设计限制，
  不是本次新引入的），所以 agent 看到的提醒是"以文字形式提到哪些路径
  历史上被撤销过"，不是结构化的强制过滤规则——这是刻意的（软提示，
  agent 自主判断），但如果以后想要更强的确定性，可以考虑扩展
  `ReminderCondition` 支持文件路径正则，再由 `tool_executor.py` 在
  `PreToolUse` 阶段把 `tool_input.command` 传进去匹配。
- `/commit-guard install-hooks` 需要用户手动执行一次（不会在检测到
  git 仓库时自动安装），因为写 `.git/hooks/` 属于对用户仓库的修改，
  应该显式触发而不是静默发生。

## 变更记录

- 2026-08-25：文档创建，方案确认，进入阶段 1。
- 2026-08-25：完成阶段 1～3、5（核心模块 / hooks 接入 / evolution.py
  重构复用 / 单元测试全绿）。阶段 4（CLI 命令）本次未做，已记入"已知
  限制"。
- 2026-08-25：补完阶段 4（`/commit-guard` CLI 命令），全部 5 个阶段
  完成。测试从 10 条增至 14 条，全部通过。
