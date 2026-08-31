# 受保护文件清单与删除防护机制改进计划

> **状态**：阶段 0、阶段 1、阶段 2、阶段 3 已完成实施。本文档记录设计
> 过程中的确认结论，供后续实施时对照，避免中途遗忘约定细节。

---

## 一、问题背景

daemon 模式下的例行维护任务（`sys:session_cleanup`、`sys:consolidation`
及其调用的 skill 剪枝、感知层的临时目录/原始结果清理、wiki 隔离清理等）
会自动删除它们各自业务语义下判定为"过期/无用"的文件或目录。这些判定逻辑
各自独立、只覆盖自己的业务场景，**没有一份跨模块的"绝对不许删"清单**，
用户没有办法显式声明"这个文件/目录，不管哪个维护任务扫到，都不能删"。

此外，daemon/cron 触发的 agent 本身也可能在执行任务过程中通过 bash 工具
（或其他任意方式）直接删除文件——这条路径不经过任何"内部清理函数"，纯粹
的代码插入检测拦不住。

已有的 [`scripts/protected_paths.py`](../docs/protected-paths-guide.md)
解决的是另一个问题（防止自我演化机制**改坏**框架核心源码），语义、服务
对象、判定时机都不同，不适合直接复用来解决"防止例行维护/agent **删除**
用户数据文件"这个问题，需要新建一套独立机制。

---

## 二、设计原则

1. **清单与被保护对象解耦**：清单文件本身不属于 `src/mini_agent/` 包，
   不依赖能被自我演化流程批量改动的代码路径。
2. **不依赖 git**：受保护的文件本来就大多在 `.gitignore` 范围内
   （sessions、缓存、临时产出等），"是否受版本控制"这个信号完全不可用，
   清单机制必须独立于 git 状态生效。
3. **代码插入检测只能是辅助手段，不能是唯一防线**：agent 可以绕过任何
   "调用点检测"（比如直接跑 `rm -rf`），因此必须有一层不依赖"删除前拦截"
   假设的兜底手段——已删除的文件要能找回来。
4. **默认保守，观察后再收紧**（沿用项目一贯的功能上线原则）：新增的
   自动化行为（尤其"自动恢复"这类会覆盖用户当前状态的动作）默认关闭或
   走最保守的分支，避免"防误删机制自己造成了另一种意外覆盖"。

---

## 三、清单文件设计（已确认）

### 3.1 文件名与格式

- **约定文件名**：`protected_files.txt`
- **格式**：纯文本，一行一条路径；`#` 开头的行视为注释；空行忽略。

```text
# 示例
# 每行一条路径，支持绝对路径 / 相对路径（相对本文件所在目录）；
# 目录路径以 / 结尾表示整个目录树都受保护
important_notes/
../shared_configs/team_settings.json
/home/user/shared/cross_project_data.db
```

### 3.2 扫描/发现机制

- 每次需要用到清单的场景（例行维护删除前判定 / 定期备份 / prompt 拼装）
  都重新扫描，不做常驻缓存（清单文件体积小、读取频率不高，没必要缓存，
  避免"清单改了但缓存没刷新"这类一致性问题）。
- 扫描范围：**仅顶层**，不递归子目录，扫描以下两处：
  1. `<project_root>/protected_files.txt`（工作目录根）
  2. `<project_root>/.agent/protected_files.txt`
- 两处若都存在，**取并集**同时生效，互不覆盖。后续如果有多项目/多清单
  场景的需求，可以在这两个固定位置之外再扩展"扫描其他目录"，但第一版
  只做这两个约定位置，足够覆盖"项目自身的重要文件"和"agent 运行时产出
  但用户希望保留的文件"这两类典型场景。

### 3.3 路径解析规则

- 清单里每一条可以是**绝对路径**，或**相对路径**。
- **相对路径以该清单文件自身所在的目录为基准解析**（不是 `project_root`，
  也不是运行时的当前工作目录）——这样清单文件可以被复制到别的项目、或
  清单本身不在 `project_root` 顶层时（理论上用户可以手动放在别处，虽然
  自动扫描只认上面两个固定位置），路径含义依然自洽。
- 目录写法（末尾 `/`）：整个目录树都受保护，判定方式与
  `protected_paths.py::is_protected_path()` 的目录前缀匹配逻辑一致。
- 第一版只支持"精确文件 / 目录前缀"两种条目形式，不做正则/glob——
  维持清单本身足够简单、用户手写不容易出错；后续如果有强诉求再评估加。

### 3.4 清单文件自身的保护

- 任何被上述扫描机制**发现**的 `protected_files.txt` 文件，无条件加入
  受保护集合——不需要用户在清单内容里显式列出它自己。
- 即使某份清单当前是空文件（0 字节/只有注释），它本身依然受保护。

---

## 四、三层防护机制

单一防线拦不住"agent 直接执行 bash 删除命令"这类情况，因此设计为三层，
各自解决不同的失效面，互相不依赖：

### 第 1 层：Prompt 提醒（预防性，不保证生效）

在 agent 的 system prompt 拼装环节（`context_builder.py::build()`，
覆盖交互式会话和 daemon/cron 触发的 agent——两者共用同一套上下文构建
逻辑，不需要分别接入）注入一段"当前生效的受保护文件清单"提示片段：
列出（或路径过多时做目录级摘要）当前受保护的路径，明确告知 agent
"这些文件/目录任何情况下都不能删除、移动、覆盖或清空内容，包括通过
bash 命令、脚本、或其他工具间接达成"。

- 这一层只是**降低 agent 犯错概率**，不是保证——agent 仍可能因为
  幻觉、路径理解偏差等原因误删，不能作为唯一依赖。
- 交互式会话和 daemon 都注入（不单独区分），因为交互式会话里 agent
  一样可能执行危险的 bash 命令，只是触发频率通常更低，没有理由只在
  daemon 场景提醒。

### 第 2 层：内部清理函数的代码级 guard（拦得住"自己人"）

在项目自身的例行维护删除点（`evolution/session_cleanup.py`、
`perception/raw_result_cleanup.py`、`perception/cycle_tuning.py`、
`perception/exploration_sandbox.py`、`wiki/quarantine.py`、
`skills/generative_capability/health_patrol.py` 等，具体清单见下方
"实施范围"）接入统一判定函数，命中受保护路径则跳过删除、记录一条日志，
不中断整个维护任务对其余候选项的处理。

这一层能拦住的是"框架自身自动化清理逻辑"误删受保护文件的情况，拦不住
agent 主动执行的 bash 命令——这正是需要第 3 层兜底的原因。

### 第 3 层：定期备份 + 可还原（真正的兜底）

新增内置 cron job `sys:protected_files_backup`：

- **触发频率**：`interval:86400`（每天一次，对齐 `sys:self_maintain`
  等同类日常维护 job 的默认节奏）。
- **行为**：扫描当前生效的全部受保护路径（含自动纳入的清单文件本身），
  逐一打包快照到独立存储位置（如
  `<project_root>/.agent/protected_backup/<generation_id>/`），
  `generation_id` 用时间戳命名，便于按时间定位某一份快照。
- **保留策略**：只保留最近 N 份快照，超出的旧快照自动清理（"清理旧备份"
  这个动作本身操作的是备份目录，不在受保护集合里，不会互相冲突）。
  **N 默认 5，可配置**（对应 `cfg.xxx.protected_files_backup_keep_count`，
  具体配置块归属见下方"待实施时确定"）。
- **缺失核对**：同一个 job 里顺带核对"上一份快照存在、但当前受保护路径
  下已经不存在"的情况——发现缺失即记一条告警（写入
  `activity_digest.jsonl`，复用现有晨报机制，用户下次能看到），
  **默认不做任何自动恢复动作**，只告警，遵循项目"新功能默认保守"的
  一贯原则：如果默认自动恢复，用户后续如果确实是故意删除了某个曾经
  受保护的文件（并从清单里移除了它），会被自动"复活"，属于意外的
  覆盖行为，风险比"用户需要多一步手动确认恢复"更高。是否要提供
  一键恢复的入口（比如 `/agent protected restore <path> [--generation
  <id>]` 命令，手动触发、需要用户显式执行），留给实施阶段按这份计划的
  阶段规划来做，不在自动化路径里默认开启。

---

## 五、实施范围（代码级 guard 覆盖的删除点，待实施时逐一核对是否仍然适用）

| 模块 | 文件 | 当前删除的对象 |
|---|---|---|
| Session 清理 | `evolution/session_cleanup.py` | `.agent/sessions/<id>/` 整个目录 |
| 巩固循环/生成式能力 | `skills/generative_capability/health_patrol.py` | `member_dir`（generative_capability 缓存成员目录） |
| 感知层 | `perception/raw_result_cleanup.py` | 过期的原始工具输出目录 |
| 感知层 | `perception/cycle_tuning.py` | 探索/收敛周期的临时目录 |
| 沙箱 | `perception/exploration_sandbox.py` | git worktree 沙箱目录 |
| wiki 治理 | `wiki/quarantine.py` | 被隔离的 wiki 页面文件 |

实施阶段会先核对以上每个删除点当前是否仍然存在（部分模块自上次盘点后
可能已有变化），逐一接入统一判定函数。

---

## 六、待实施时确定的技术细节（非阻塞性，实施阶段直接定即可）

- 新模块命名：`scripts/protected_files.py`（判定逻辑）+ 与
  `protected_paths.py` 一致的独立性约束（不放在 `src/mini_agent/` 包内、
  不 import `mini_agent.*`）。
- 备份保留份数配置项挂在哪个 config 块（`GoalModeConfig` 不合适，可能
  新建一个轻量的 `MaintenanceConfig` 或直接挂在顶层 `AppConfig`，实施时
  参考项目现有配置分块惯例决定）。
- prompt 提醒片段的具体文案、以及路径数量较多时"摘要展示"的截断规则。
- `activity_digest.jsonl` 里"受保护文件缺失告警"事件的具体 `type` 命名
  （沿用 `self_maintenance.py` 的 `"health_report"` 风格）。

---

## 七、阶段规划

原则：与项目一贯的"默认关闭或纯增量、每阶段独立可交付"风格保持一致。

### 阶段 0：清单发现 + 判定模块 ✅ 已完成
- 已落地 `scripts/protected_files.py`：
  - `ProtectedFilesGuard`：单次扫描 + 判定的封装，构造时扫描
    `<project_root>/protected_files.txt` 与
    `<project_root>/.agent/protected_files.txt` 并取并集（不做常驻缓存，
    每次新建实例都重新扫描，符合 3.2 约定）。
  - `ProtectedEntry`：解析后的单条受保护条目（规范化绝对路径 + 是否目录
    + 来源清单文件路径）。
  - `is_protected()` / `list_entries()`：判定与枚举接口。
  - 模块级便捷函数 `is_protected_file()` / `list_protected_files()`：
    供低频调用场景一次性使用，内部各自新建 Guard。
  - 与 `scripts/protected_paths.py` 保持同样的独立性约束：不放在
    `src/mini_agent/` 包内、不 import `mini_agent.*`。
- 已落地 `tests/test_protected_files.py`（11 个用例，覆盖：无清单时不
  误伤、顶层清单、`.agent/` 清单、两处取并集、目录前缀匹配不误伤同名前缀
  目录、相对路径以清单文件自身目录为基准解析、绝对路径条目、注释/空行
  忽略、清单文件自身即使为空也受保护、模块级便捷函数、模块自包含性），
  全部通过。
- 尚未接入任何删除点或 prompt（阶段 1、2 的范围），当前无任何运行时
  行为改变。

### 阶段 1：Prompt 提醒接入 ✅ 已完成
- 新增配置开关 `AppConfig.protected_files_reminder_enabled`（默认
  `True`，挂在顶层 `AppConfig`，紧邻 `notepad_enabled` 等同类总开关）。
- 新增 prompt 模板 `src/mini_agent/prompts/system/protected_files.md`，
  与 `notepad.md` 同样的 `{{variable}}` 占位符渲染方式，明确告知 agent
  "这些文件/目录任何情况下都不能删除、移动、覆盖或清空内容，包括通过
  bash 命令、脚本、或其他工具间接达成"，且要求"确实需要动这些路径时先
  停下来问用户"。
- `ContextBuilder` 新增 `_build_protected_files_reminder()`：
  - 通过 `scripts.protected_files.ProtectedFilesGuard` 扫描当前生效的
    受保护清单（复用阶段 0 的判定模块，逻辑不重复实现）。
  - 没有任何清单文件时直接跳过，不注入空提醒。
  - 路径数量摘要规则（对应"风险 2"）：超过 20 条时只展示前 20 条，末尾
    追加一行"还有 N 处未列出，见 protected_files.txt"，避免无限增长地把
    完整清单塞进每次的 system prompt。
  - 扫描/渲染失败时静默降级为空字符串（记录到 `errors.log_exception`），
    不影响 system prompt 其余部分的组装。
  - 已接入 `build()` 主流程，位置在 Workdir/Global 知识层注入之前、
    AgentSelfModel 注入之前；交互式会话与 daemon/cron 触发的 agent
    共用同一个 `build()` 入口，不需要分别接入。
- 已落地 `tests/test_context_builder_protected_files.py`（6 个用例，覆盖：
  无清单时为空、`enabled=False` 时即使有清单也为空、条目正确注入且清单
  文件自身出现在提醒里、超过阈值时摘要截断、`build()` 完整流程注入验证、
  无清单时完整 prompt 里不出现提醒片段），全部通过；连同阶段 0 与既有
  `test_context_builder_workdir_knowledge.py` /
  `test_context_builder_global_knowledge.py` 等相关测试一并跑过，共 55
  项，无回归。
- 纯信息展示，不改变任何执行逻辑（阶段 2 的代码级 guard 仍待接入），
  默认开启符合"风险最低"的判断。

### 阶段 2：代码级 guard 接入 ✅ 已完成
- 新增统一封装 `src/mini_agent/utils/protected_files_guard.py`：
  - 收敛"把仓库根目录加入 sys.path 再 import scripts.protected_files"这段
    样板代码，各删除点只需要
    `from mini_agent.utils.protected_files_guard import is_protected`。
  - `is_protected(path, project_root)`：判定模块加载失败或扫描异常时
    返回 `True`（视为受保护、跳过删除），取舍与 `evolution/state_repo.py`
    对 `scripts/protected_paths.py` 加载失败时的"宁可错杀不可放过"一致——
    判定不了时放行会让这层防护失去意义。
- 已逐一接入"实施范围"表格列出的 6 个删除点（均已核对当前代码仍然存在，
  无需调整实施范围）：
  1. `evolution/session_cleanup.py::cleanup_orphan_session_dirs` ——
     命中受保护路径的候选目录计入 `failed`（reason 追加"命中受保护文件
     清单，跳过删除"），不计入 `deleted`，不中断对其余候选的处理。
  2. `skills/generative_capability/health_patrol.py::_cleanup_member`
     （经 `run_patrol` 调用）—— `run_patrol` 新增可选参数
     `project_root`（未传时退化为 `Path.cwd()`，与项目里
     `project_root or Path.cwd()` 的既有惯例一致）；命中时跳过
     `shutil.rmtree`，registry/index 状态保持不变（不摘除），只在
     `fixed_inconsistencies` 里记一条说明。
  3. `perception/raw_result_cleanup.py::run_cleanup` —— 命中的 session
     目录新增 `CleanupFinding(kind="protected_skipped")`，不计入
     `cleaned_sessions`。
  4. `perception/cycle_tuning.py::delete_proposals` —— 命中时直接返回
     `False`（沿用函数原有的"删除是否成功"语义，调用方据此可以提示用户
     手动处理），不做任何删除。
  5. `perception/exploration_sandbox.py::_cleanup_worktree` —— 命中时
     直接返回，不尝试 `EvolutionWorkspace.cleanup_worktree()` 也不
     fallback 到 `shutil.rmtree`。
  6. `wiki/quarantine.py::purge_quarantined` —— `PurgeReport` 新增字段
     `protected_skipped`；命中的页面既不删除文件，也不摘除隔离区记录
     （下次巡检仍会看到，保持可追溯）。
- 已落地 `tests/test_protected_files_guard_integration.py`（7 个用例：
  `is_protected` 封装本身的正/负例 + 上述 6 个删除点各一条集成测试，
  均验证"命中受保护清单时文件/目录确实原地保留、且不中断/不报错"），
  全部通过；连同阶段 0、阶段 1 与既有
  `test_generative_capability_engine.py` /
  `test_cycle_tuning.py` / `test_wiki_quarantine.py` /
  `test_affordance_risk_gating.py` / `test_exploration_outcome_recording.py`
  / `test_raw_result_and_smart_summary.py` 等相关测试一并跑过，共 175
  项，无回归。
- 尚未新增日志落盘（如 `activity_digest.jsonl` 里的专门事件类型）——
  当前每个删除点各自的报告结构（`OrphanItem.reason` /
  `CleanupFinding` / `PurgeReport.protected_skipped` 等）已经能让调用方
  感知到"发生了跳过"，是否需要额外汇总进 `activity_digest.jsonl` 留给
  阶段 3（届时"缺失核对"告警本来就要写这个文件，可以一并评估是否要把
  "本轮维护跳过了哪些受保护路径"也带上，避免定义重复的日志入口）。

### 阶段 3：定期备份 + 缺失告警 ✅ 已完成
- 新增配置项 `AppConfig.protected_files_backup_keep_count`（默认 `5`，
  紧邻阶段 1 的 `protected_files_reminder_enabled`）。
- 新增 `src/mini_agent/evolution/protected_files_backup.py`（本地回调
  handler，零 LLM 成本，写法与 `evolution/candidate_queue_triage.py::
  ensure_candidate_queue_triage_job` 同构）：
  - `run_backup_once(project_root, keep_count=5, now=None)`：核心执行
    函数，每次运行重新通过 `ProtectedFilesGuard` 扫描当前生效的受保护
    路径（含清单文件自身），逐一打包快照到
    `<project_root>/.agent/protected_backup/<generation_id>/`
    （`generation_id` 用 `%Y%m%d_%H%M%S` 时间戳命名，文件用
    `shutil.copy2`、目录用 `shutil.copytree` 打包，另落盘一份
    `manifest.txt` 记录本次快照包含的原始路径列表，供下次运行做缺失
    核对）。
  - 保留策略：`keep_count` 份以外的旧快照按 `generation_id` 排序清理
    （备份目录本身不在受保护集合里，不会跟自身清理动作冲突）。
  - 缺失核对：对比"上一份快照 manifest 里有、当前 guard 扫描结果里已
    没有"的路径，计入 `BackupSummary.missing`；命中时由
    `_write_missing_alert()` 写入 `activity_digest.jsonl`
    （`type="protected_files_missing"`，复用
    `evolution/resource_arbiter.py::append_activity_digest`），**不做
    任何自动恢复**，符合方案"默认保守"的既定取舍。
  - 没有任何受保护路径时（清单为空/不存在）直接跳过，不产生空快照
    目录；缺失核对逻辑在"没有受保护路径"分支之前就已执行完，不受影响。
  - `ensure_protected_files_backup_job(paths, cron_scheduler, keep_count)`：
    daemon 启动时"缺失才补注册" `sys:protected_files_backup`
    （`interval:86400`），已接入 `src/mini_agent/api/server.py` 的 daemon
    启动流程（与 `ensure_wiki_quarantine_repair_job` 等既有 job 相邻，
    同样的 try/except + `log_exception` 包裹写法），`keep_count` 从
    `cfg.protected_files_backup_keep_count` 读取。
- 已落地 `tests/test_protected_files_backup.py`（6 个用例：无受保护路径
  时不产生快照、文件与目录两种条目都能正确打包、超出 `keep_count` 时
  清理最旧快照、跨快照缺失核对能正确识别"消失的路径"且不误伤仍存在的
  路径、缺失告警正确写入 `activity_digest.jsonl` 且确认没有发生任何
  自动恢复、`ensure_protected_files_backup_job` 正确注册 job 与本地回调
  且二次调用不重复新建），全部通过；连同阶段 0-2 及既有
  `test_candidate_queue_triage.py` 等相关测试一并跑过，共 187 项，
  无回归。
- **本阶段未覆盖**（按方案原文，留给阶段 4）：任何形式的自动恢复、
  手动恢复命令（`/agent protected restore` 一类）。

### 阶段 4（可选，视阶段 3 观察结果决定是否推进）：手动恢复入口
- 提供 `/agent protected restore` 一类命令，让用户在告警后可以显式选择
  从某一份快照恢复，不做全自动闭环。

---

## 八、风险与非目标

- **非目标**：不做"自动检测所有删除操作并强制拦截"（不现实，agent 执行
  环境里可以通过任意方式绕过），本方案的可靠性来自"能找回来"而不是
  "拦得住"。
- **风险 1**：备份快照本身会占用磁盘空间——保留份数可配置、旧快照自动
  清理，缓解无限增长问题；如果受保护文件本身很大（比如整个目录），
  备份成本会更高，实施阶段需要评估是否要对超大文件/目录给出告警或
  跳过整份打包只记录清单。
- **风险 2**：prompt 提醒占用一定的 token 预算——受保护路径数量多时
  需要摘要展示，不能无限增长地把完整清单塞进每次的 system prompt。
