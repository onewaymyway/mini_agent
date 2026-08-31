# 受保护文件清单与删除防护机制

> 说明 `protected_files.txt` 清单机制、四层防护（判定模块 → prompt 提醒 →
> 代码级 guard → 定期备份 + 手动恢复）与 Streamlit 看板集成的设计动机、
> 使用方式与扩展规则。对应
> [`next_doc/protected_files_manifest_and_delete_guard_plan.md`](../next_doc/protected_files_manifest_and_delete_guard_plan.md)
> 阶段 0-5，已全部落地。

---

## 1. 这是什么，解决什么问题

daemon 模式下的例行维护任务（`sys:session_cleanup`、`sys:consolidation`
及其调用的 skill 剪枝、感知层的临时目录/原始结果清理、wiki 隔离清理等）
会自动删除它们各自业务语义下判定为"过期/无用"的文件或目录。这些判定逻辑
各自独立、只覆盖自己的业务场景，此前没有一份跨模块的"绝对不许删"清单，
用户也没有办法显式声明"这个文件/目录，不管哪个维护任务扫到，都不能删"。

此外，daemon/cron 触发的 agent 本身也可能在执行任务过程中通过 bash 工具
（或其他任意方式）直接删除文件——这条路径不经过任何"内部清理函数"，纯粹
的代码插入检测拦不住。

本机制与 [受保护路径清单（T3 治理红线）](protected-paths-guide.md)
（`scripts/protected_paths.py`）解决的是不同的问题：后者防止自我演化
机制**改坏**框架核心源码；本机制防止例行维护任务/agent **删除**用户
数据文件。两者语义、服务对象、判定时机都不同，是两套独立机制，互不复用。

**核心可靠性来源**：不是"拦得住"，而是"能找回来"——agent 执行环境里
理论上可以通过任意方式绕过任何拦截，因此本机制的最终兜底是可还原的
定期备份，而不是试图做到"自动检测所有删除操作并强制拦截"。

---

## 2. 清单文件：`protected_files.txt`

### 2.1 文件名与格式

纯文本，一行一条路径；`#` 开头的行视为注释；空行忽略。

```text
# 示例
# 每行一条路径，支持绝对路径 / 相对路径（相对本文件所在目录）；
# 目录路径以 / 结尾表示整个目录树都受保护
important_notes/
../shared_configs/team_settings.json
/home/user/shared/cross_project_data.db
```

### 2.2 扫描位置与解析规则

- 扫描范围**仅顶层**，不递归子目录，固定扫描两处，若都存在**取并集**、
  互不覆盖：
  1. `<project_root>/protected_files.txt`
  2. `<project_root>/.agent/protected_files.txt`
- 每次用到清单都重新扫描，不做常驻缓存（清单体积小、读取频率不高，
  避免"清单改了但缓存没刷新"的一致性问题）。
- 相对路径**以该清单文件自身所在的目录为基准解析**（不是
  `project_root`，也不是运行时 cwd）。
- 目录写法（末尾 `/`）：整个目录树都受保护，前缀匹配语义与
  `protected_paths.py::is_protected_path()` 一致。
- 第一版只支持"精确文件 / 目录前缀"两种条目形式，不支持正则/glob。
- 任何被扫描机制发现的 `protected_files.txt` 文件**自身**无条件加入
  受保护集合（不需要在清单内容里显式列出它自己），即使是空文件也一样。

### 2.3 判定模块：`scripts/protected_files.py`

与 `scripts/protected_paths.py` 保持同样的独立性约束：不放在
`src/mini_agent/` 包内、不 import `mini_agent.*`，避免清单判定逻辑本身
被自我演化流程当作可改动的普通源码。

- `ProtectedFilesGuard`：单次扫描 + 判定的封装，构造时扫描两处固定
  位置并取并集。
- `ProtectedEntry`：解析后的单条受保护条目（规范化绝对路径 + 是否目录
  + 来源清单文件路径）。
- `is_protected()` / `list_entries()`：判定与枚举接口。
- 模块级便捷函数 `is_protected_file()` / `list_protected_files()`：
  供低频调用场景一次性使用。
- `add_entry()` / `remove_entry()`（阶段 5 新增）：清单文件的增删声明
  程序化入口，供 REST API/看板使用。`add_entry()` 幂等（重复添加不
  产生第二行）、自动创建清单文件（含 `.agent/` 目录）；`remove_entry()`
  按规范化后的绝对路径匹配，拒绝删除清单文件自身这一条（该保护是自动
  规则，不是可撤销的用户声明）。

---

## 3. 三层防护 + 一层兜底

### 第 1 层：Prompt 提醒（预防性，不保证生效）

- 配置开关：`AppConfig.protected_files_reminder_enabled`（默认
  `True`），见 [配置系统指南 §`protected_files_reminder_enabled`](config-guide.md#protected_files_reminder_enabledappconfig-直接字段)。
- Prompt 模板：`src/mini_agent/prompts/system/protected_files.md`，与
  `notepad.md` 同样的 `{{variable}}` 占位符渲染方式，明确告知 agent
  "这些文件/目录任何情况下都不能删除、移动、覆盖或清空内容，包括通过
  bash 命令、脚本、或其他工具间接达成"，且要求"确实需要动这些路径时
  先停下来问用户"。
- 由 `ContextBuilder::_build_protected_files_reminder()` 注入，复用
  `ProtectedFilesGuard` 判定；没有任何清单文件时直接跳过、不注入空
  提醒；超过 20 条时只展示前 20 条并追加"还有 N 处未列出"提示，避免
  无限增长地把完整清单塞进每次 system prompt；扫描/渲染失败时静默
  降级为空字符串（记录到 `errors.log_exception`）。
- 交互式会话和 daemon/cron 触发的 agent **共用同一个 `build()` 入口**，
  不需要分别接入。
- 这一层只降低 agent 犯错概率，不保证生效——agent 仍可能因为幻觉、
  路径理解偏差等原因误删，不能作为唯一依赖。

### 第 2 层：内部清理函数的代码级 guard（拦得住"自己人"）

统一封装 `src/mini_agent/utils/protected_files_guard.py::is_protected()`
收敛了"把仓库根目录加入 `sys.path` 再 import `scripts.protected_files`"
的样板代码；判定模块加载失败或扫描异常时返回 `True`（视为受保护、跳过
删除），取舍与 `evolution/state_repo.py` 对 `protected_paths.py` 加载
失败时"宁可错杀不可放过"一致。

已接入以下 6 个删除点（命中即跳过删除，不中断对其余候选项的处理）：

| 模块 | 文件 | 命中时的行为 |
|---|---|---|
| Session 清理 | `evolution/session_cleanup.py::cleanup_orphan_session_dirs` | 计入 `failed`（reason 追加"命中受保护文件清单，跳过删除"），不计入 `deleted` |
| 巩固循环/生成式能力 | `skills/generative_capability/health_patrol.py::_cleanup_member`（经 `run_patrol` 调用） | 跳过 `shutil.rmtree`，registry/index 状态保持不变，`fixed_inconsistencies` 里记一条说明 |
| 感知层 | `perception/raw_result_cleanup.py::run_cleanup` | 记一条 `CleanupFinding(kind="protected_skipped")`，不计入 `cleaned_sessions` |
| 感知层 | `perception/cycle_tuning.py::delete_proposals` | 直接返回 `False`，不做任何删除 |
| 沙箱 | `perception/exploration_sandbox.py::_cleanup_worktree` | 直接返回，不尝试清理也不 fallback 到 `shutil.rmtree` |
| wiki 治理 | `wiki/quarantine.py::purge_quarantined` | `PurgeReport.protected_skipped` 记一条，既不删文件也不摘除隔离区记录（下次巡检仍可见，保持可追溯） |

这一层拦得住"框架自身自动化清理逻辑"误删，拦不住 agent 主动执行的
bash 命令——这正是需要第 3 层兜底的原因。

### 第 3 层：定期备份 + 缺失告警（`evolution/protected_files_backup.py`）

内置 cron job `sys:protected_files_backup`（`interval:86400`，即每天
一次），本地回调 handler、零 LLM 成本，daemon 启动时"缺失才补注册"，
接入位置与 `ensure_wiki_quarantine_repair_job` 等既有 job 相邻。详见
[Cron Job 参考 §2](cron-jobs-reference.md#2-固定内置-jobcron_schedulerpy_builtin_jobs)。

- **`run_backup_once(project_root, keep_count=5, now=None)`**：每次运行
  重新通过 `ProtectedFilesGuard` 扫描当前生效的受保护路径（含清单文件
  自身），逐一打包快照到
  `<project_root>/.agent/protected_backup/<generation_id>/`
  （`generation_id` 用 `%Y%m%d_%H%M%S` 时间戳命名；文件用
  `shutil.copy2`，目录用 `shutil.copytree`），另落一份 `manifest.txt`
  记录本次快照包含的原始路径列表，供下次运行做缺失核对。
- **保留策略**：`keep_count`（默认 `5`，配置项
  `AppConfig.protected_files_backup_keep_count`）份以外的旧快照按
  `generation_id` 排序自动清理；备份目录本身不在受保护集合里，不会
  跟自身清理动作冲突。
- **`manifest.txt` 存储格式**：`<index>\t<original_path>` 每行一条，
  `index` 与打包时使用的 `_safe_snapshot_name(path, index)` 显式记录
  在同一行，不依赖任何形式的重新枚举/排序去推导（阶段 3 落地时是纯
  路径列表 + 重新枚举取 index，实测发现"若某条目在打包时被跳过，重新
  枚举得到的下标会跟快照内实际文件名错位"，阶段 4 已改正）。
- **缺失核对**：对比"上一份快照 manifest 里有、当前 guard 扫描结果里
  已没有"的路径，命中即写入 `activity_digest.jsonl`
  （`type="protected_files_missing"`，复用
  `evolution/resource_arbiter.py::append_activity_digest`）。**默认不做
  任何自动恢复动作**，只告警——遵循项目"新功能默认保守"的一贯原则：
  如果默认自动恢复，用户后续如果确实是故意删除了某个曾经受保护的文件
  （并从清单里移除了它），会被自动"复活"，属于意外的覆盖行为，风险比
  "用户需要多一步手动确认恢复"更高。
- 没有任何受保护路径时（清单为空/不存在）直接跳过，不产生空快照目录；
  缺失核对逻辑在此之前就已执行完，不受影响。

---

## 4. 手动恢复

### 4.1 恢复函数

`RestoreSummary` / `restore_from_snapshot(project_root, generation_id,
paths=None)`：从指定快照恢复；`paths=None` 时恢复该快照 manifest 里的
全部路径，否则只恢复给定路径（未出现在该快照 manifest 里的路径记为
`not_in_snapshot` 错误，不中断其余路径的恢复）。文件用 `shutil.copy2`
覆盖，目录用 `shutil.copytree(dirs_exist_ok=True)` 合并覆盖（不先删除
目标目录，只覆盖快照里存在的文件，比"先删后拷"更保守）。快照本身不
存在、或快照内容缺失时返回明确错误，不抛异常。

### 4.2 CLI：`/agent protected`

挂在既有的 `/agent <subcmd>` 路由下（`cli/repl.py::_handle_agent_subcmd`
新增 `protected` 分支），实现见
`src/mini_agent/cli/commands/protected_cmd.py::handle_protected_cmd`。

| 命令 | 说明 |
|------|------|
| `/agent protected status` | 当前生效的受保护清单 + 最近一次快照概况 |
| `/agent protected list [generation_id]` | 列出全部快照，或某一份快照的具体内容 |
| `/agent protected restore <generation_id> [path] [--force]` | 从指定快照恢复；不加 `--force` 只打印"将要覆盖哪些路径"、不执行，确认无误后带 `--force` 重新执行才真正写盘（与 `/evolution merge --force` 的既有惯例一致，而不是引入一次性的交互式 `confirm()`） |

**不做任何自动闭环**——是否恢复、恢复到哪个版本，全程由用户显式决定，
符合方案"默认保守"的既定取舍。

### 4.3 REST API

全部端点 owner only，见
[HTTP API 指南 §受保护文件管理](http-api-guide.md#受保护文件管理-v1protected-files)。

### 4.4 Streamlit 看板

`apps/mini_agent_kanban/` 新增"🛡️ 受保护文件"Tab，见
[看板指南 §🛡️ 受保护文件 Tab](kanban-dashboard-guide.md#️-受保护文件-tab)。

---

## 5. 使用示例

```text
# 项目根目录 protected_files.txt
important_notes/
../shared_configs/team_settings.json
```

```text
# 判定
from scripts.protected_files import is_protected_file
is_protected_file("important_notes/roadmap.md", project_root=".")  # True（目录前缀匹配）
```

```text
# CLI
/agent protected status
/agent protected list
/agent protected restore 20260830_020000
/agent protected restore 20260830_020000 important_notes/roadmap.md --force
```

---

## 6. 风险与非目标

- **非目标**：不做"自动检测所有删除操作并强制拦截"，本机制的可靠性
  来自"能找回来"而不是"拦得住"。
- **风险 1**：备份快照占用磁盘空间——保留份数可配置、旧快照自动清理，
  缓解无限增长问题；受保护文件本身很大（比如整个目录）时备份成本会
  更高。
- **风险 2**：prompt 提醒占用一定的 token 预算——受保护路径数量多时
  做摘要展示，不无限增长地把完整清单塞进每次 system prompt。

---

## 7. 相关文档

- [受保护路径清单（T3 治理红线）](protected-paths-guide.md) — 另一套
  独立机制：防止自我演化改坏框架核心源码，与本机制不要混淆
- [Cron Job 参考](cron-jobs-reference.md) — `sys:protected_files_backup`
  的注册方式与调度参数
- [配置系统指南](config-guide.md) — `protected_files_reminder_enabled` /
  `protected_files_backup_keep_count` 两个配置项
- [命令与工具参考](commands-and-tools-reference.md) — `/agent protected`
  完整命令列表
- [HTTP API 指南](http-api-guide.md) — `/v1/protected-files/*` REST 端点
- [Streamlit 看板指南](kanban-dashboard-guide.md) — "🛡️ 受保护文件" Tab
- [`next_doc/protected_files_manifest_and_delete_guard_plan.md`](../next_doc/protected_files_manifest_and_delete_guard_plan.md)
  — 完整设计过程与阶段实施记录（阶段 0-5）

---

*最后更新：2026-08（阶段 0-5 全部落地，含 Streamlit 看板集成）*
