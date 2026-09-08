# 调研/产出项目根目录规范与主项目 Git 隔离方案

> 状态：**设计已确认（待实施）**。三处开放问题已与用户对齐结论，见 §5。

## 0. 背景

用户反馈：daemon 在执行 goal（尤其是周期性 Goal）或 cron 任务时，经常
把产出的调研/代码项目（比如为了完成某个任务临时搭建的爬虫脚本集合、
数据分析产出、生成的示例代码库等）放进了主项目的 `src/` 目录下，这不
合理——这类"任务顺带产出的独立项目"不应该和主项目自身的源码混在一起，
更不应该被主项目的 git 仓库管理起来（污染主仓库的提交历史/体积，也让
产出项目自己的版本管理无从谈起）。

用户明确的诉求可以拆成三条：

1. 所有调研类、产出类项目，都应该放到**用户指定的目录**；用户没指定时，
   要有一个**全局的、可配置的产出根目录**，默认 `./output_projects`，
   可以通过 `agent_config.json` 修改。
2. 这类项目**不能被主项目的 git 管理**——如果产出项目本身需要版本控制，
   应该自己单独 `git init` 管理，不允许 commit 到主项目仓库里。
3. 先想清楚方案、评审确认之后再动代码。

## 1. 现状盘点

这个问题不是从零开始——仓库里已经有两套相关但都没有完全覆盖这次诉求
的机制，改动前先弄清楚"已经有什么、缺什么"，避免和现有机制打架或重复
造轮子。

| 已有机制 | 位置 | 解决的问题 | 没解决的问题（相对本次诉求） |
|---|---|---|---|
| `.agent/daemon_run_outputs/`（`evolution/output_workspace.py`，见 `goal_cron_output_directory_convention_plan.md`） | `.agent/daemon_run_outputs/goals/<goal_id>/`、`cron/<job_id>/` | 只覆盖"绑定了周期性 Goal / CronJob 的每一轮执行"，给每轮分配固定/递增子目录，写 `manifest.json` 传递跨轮进度 | 目录**硬编码**在 `.agent/` 内，不可配置；覆盖范围窄（不管交互式对话、一次性小任务里临时产出的项目）；只解决"文件放哪"，从设计上就没打算覆盖"项目"级别的、需要独立 git 管理的产出物 |
| `output_path_policy.md`（`evolution/output_path_policy.py`） | `.agent/policies/output_path_policy.md`，prompt 注入 | 一份用户可编辑的**负面清单**：禁止写 `src/`、`tests/`；skill 相关放 skill 目录；任务指定了目录的以任务为准；周期性任务的 `{{output_dir}}` 优先级最高 | 只有"不许放哪"，没有"调研/产出类项目该放哪个正面根目录"这条正面指引；完全没有提到 git 隔离要求 |

**结论**：这次要补的是这两层之上的**第三层**——一个用户可配置、独立于
`.agent/` 内部状态目录的产出项目根目录，以及配套的、明确防止被主项目
git 误管理的结构性兜底，目前是完全空白的。

## 2. 为什么要改

1. **主项目仓库的完整性**：调研/产出项目一旦散落进 `src/` 或仓库根目录，
   下一次 `git add -A`/`git commit -a` 就会把它们连带的一堆脚本、数据、
   甚至它们自己可能产生的 `.git` 子目录（embedded repo）一起带进主仓库
   的提交历史，难以事后干净地摘除。
2. **可预测性**：现有的 `.agent/daemon_run_outputs/` 只覆盖"周期性
   Goal/CronJob"这一种场景，用户/下一次执行的 agent 找不到统一的地方
   去看"这次任务顺手建的项目在哪"——尤其是交互式任务、一次性 goal 里
   临时决定要建一个独立小项目的情况，目前完全没有约定，全凭 agent 临场
   发挥，才会出现"放进 src/"这种不合理结果。
3. **产出项目自身的版本管理诉求**：调研/产出项目本身可能需要被单独提交
   历史追踪（比如反复迭代的分析脚本），这和"不能进主仓库"并不矛盾——
   只要给它一个独立的、明确不受主仓库管理的位置，它完全可以有自己的
   `git init`。现状没有任何目录能满足"独立、可选自建 git 仓库"这个要求。

## 3. 方案设计

### 3.1 配置项

`agent_config.json` 新增顶层字段（对齐 `claude_md_file` 这类简单字符串
配置的既有风格，不做成嵌套对象——只有一个值，没必要）：

```json
"output_projects_root": "./output_projects"
```

- 相对路径相对 `project_root` 解析；也支持绝对路径（呼应
  `external_projects_workspace_plan.md` 里"路径独立"的既有诉求，比如
  放到别的磁盘分区）。
- 不设置时默认 `./output_projects`。
- 首次被解析到时幂等 `mkdir(parents=True, exist_ok=True)`，不存在就建，
  已存在不报错、不覆盖任何内容。

`AppConfig`（`config/models.py`）新增同名字段 `output_projects_root:
str = "./output_projects"`；`config/loader.py` 按 `claude_md_file` 的
既有读取模式，从 `file_cfg.get("output_projects_root", ...)` 读取。

### 3.2 目录归属范围（跟已有机制的边界）

明确"调研项目/产出项目"具体指什么、不指什么，避免和上面盘点的两套已有
机制打架：

- **纳入本方案**：agent 在执行任意任务（交互式对话、一次性 goal、
  周期性 goal、cron、workflow）过程中，判断需要新建一个**独立的、有
  自己完整文件结构的**产出物——调研报告合集、爬虫/分析脚本项目、生成
  的示例代码库、临时验证用的小项目等——这类东西一律建到
  `<output_projects_root>/<项目名>/` 下的新子目录（`项目名` 用
  snake_case，agent 根据任务主题自行命名）。
- **不纳入、维持现状**（已有专门归宿，不重复搬家，对应用户确认的开放
  问题 3）：
  - `.agent/daemon_run_outputs/`：周期性 Goal/CronJob 每轮执行的 manifest
    + 产出，本来就已经隔离得不错，只是不可配置——这次不动它，只是给
    "这次改造之前完全没覆盖的场景"（一次性任务里临时决定要建的独立
    项目）补上规则。
  - 已注册的 `external_projects/`：本身就有 `project.yaml` 契约和独立
    `Workspace`，路径独立是其框架原则之一，不受影响。
  - 用户任务里已经明确指定了目录的：`output_path_policy.md` 现有第4条
    规则已覆盖，优先级仍然最高，本方案不改变这条优先级关系。

### 3.3 Prompt 层规则注入（复用现有机制，不新造一套）

`output_path_policy.py` 的 `DEFAULT_POLICY` 追加一条新规则，并支持一个
`{{output_projects_root}}` 占位符（仿照 `daemon_run_outputs` 那条规则里
`{{output_dir}}` 的既有 mustache 用法），`load_policy()` 渲染时替换成
解析后的**绝对路径**：

```
6. 调研类、产出类项目（有独立文件结构，可能需要自己的代码/数据版本
   管理）一律新建到 `{{output_projects_root}}/<项目名>/` 下，禁止放进
   主项目 src/、tests/ 或仓库根目录。这类目录不受主项目 git 管理；如该
   产出项目自身需要版本控制，应在其自己的目录内单独执行 `git init`
   建独立仓库，禁止把它的改动 commit 到主项目仓库。
   （已经有专门产出机制覆盖的场景——周期性 Goal/CronJob 的
   `.agent/daemon_run_outputs/`、已注册的 external_projects——以那些
   机制已有的规则为准，不受本条影响。）
```

### 3.4 老用户已有 policy 文件的追加式迁移

`ensure_policy_file()` 目前是"文件不存在则整份写入默认模板，存在则完全
不碰"。这次改成：文件存在时，额外检查是否已包含新规则的**特征字符串**
（比如新规则的标题行），缺失则在文件末尾追加这一条（不改动、不覆盖用户
已有的其余内容，包括用户自己编辑过的 1~5 条规则原文），并在追加内容前
留一行简短注释说明"以下为自动追加的新规则（<日期>）"，方便用户事后
识别哪些是自己写的、哪些是系统追加的。这个"检查特征字符串→缺失则追加"
的迁移逻辑做成可复用的小函数，为将来还会有的第 7、第 8 条规则追加打个
样，不用每次都手写一遍分支判断。

### 3.5 结构性兜底：自动 `.gitignore` 追加

首次解析 `output_projects_root` 时，如果该目录落在主项目 `project_root`
内（绝对路径下是 `project_root` 的子路径），自动、幂等地把它相对
`project_root` 的路径（末尾带 `/`，如 `output_projects/`）追加进主项目
根目录的 `.gitignore`（仅在该行不存在时追加一行，不做去重整理、不改动
文件其余内容——和 `output_path_policy.py` 里"已存在文件不覆盖"的一贯
处理方式保持一致的保守风格）。

这样即使 agent 或用户手滑在主项目根目录执行了 `git add -A`/
`git commit -a`，这个目录默认也不会被暂存，是比"只靠 prompt 提醒"更硬
一层的兜底，但仍然不涉及拦截任何工具调用（跟用户确认的结论一致：不做
`git add`/`git commit` 命令级别的硬拦截，只做 `.gitignore` + prompt 两层）。

若 `output_projects_root` 解析出来是主项目目录之外的路径（比如配置成了
绝对路径、放到别的盘符），则跳过这一步——本来就不在主仓库工作树内，
不存在被误 `git add` 的可能，不需要往主项目 `.gitignore` 里加无意义的
一行。

## 4. 落地涉及的模块

| 模块 | 改动内容 |
|---|---|
| `src/mini_agent/config/models.py` | `AppConfig` 新增字段 `output_projects_root: str = "./output_projects"` |
| `src/mini_agent/config/loader.py` | 从 `agent_config.json` 读取该字段，写入 `AppConfig` 实例（模式对齐 `claude_md_file`） |
| 新增 `src/mini_agent/evolution/output_projects_root.py` | `resolve_root(cfg) -> Path`：解析相对/绝对路径为绝对路径；`ensure_root(cfg) -> Path`：幂等建目录；`maybe_append_gitignore(cfg)`：判断是否在 project_root 内，是则幂等追加 `.gitignore` 一行；三者可组合成一个 `ensure_output_projects_root(cfg)` 供调用方一次性调用 |
| `src/mini_agent/evolution/output_path_policy.py` | `DEFAULT_POLICY` 追加第6条规则（含 `{{output_projects_root}}` 占位符）；`load_policy()` 渲染时替换占位符为绝对路径；`ensure_policy_file()` 增加"存在则检查特征字符串、缺失则追加"的迁移逻辑（§3.4） |
| 调用点（首次执行时机） | 在 agent 启动/加载配置完成后的既有初始化路径上，调用一次 `ensure_output_projects_root(cfg)`（具体挂载点在实施阶段确认，参照 `ensure_policy_file()` 现有调用方式——大概率是同一个"session/objective 开始前的规范文件确保"环节，两者可以合并成一次调用，避免散落两处） |
| `docs/`（补文档，具体文件在实施阶段确定，可能是 `docs/goal-cron-binding-guide.md` 同一份或新开一节） | 说明 `output_projects_root` 配置项、默认值、目录归属边界（§3.2）、git 隔离约定 |

## 5. 已确认的开放问题结论

评审阶段提出的三个开放问题，均已与用户对齐：

1. **是否做 `git add`/`git commit` 命令级硬拦截**：**否**。只做
   "自动 `.gitignore` 追加 + prompt 规则提醒"这种更保守、也更贴合仓库
   一贯"policy 只做 prompt 注入不做 hook 硬拦截"取舍的方案（§3.5）。
2. **老用户已有 `output_path_policy.md` 要不要自动追加新规则**：
   **要**，追加式迁移，不覆盖用户已有内容（§3.4）。
3. **`.agent/daemon_run_outputs/` 的根目录要不要一并纳入
   `output_projects_root` 统一管理**：**不动**，维持现状（本来就在
   `.agent/` 内、已被 gitignore，问题不大）；本方案只解决"周期性
   Goal/CronJob 之外、之前完全没覆盖的调研/产出项目"这个场景（§3.2）。

## 6. 非目标

- 不做旧数据迁移——已经散落在 `src/` 或仓库根目录下的历史产出物，本
  方案不负责挪动/清理，只对改造上线之后**新产生**的调研/产出项目生效。
- 不做 `output_projects_root` 目录内部的结构规范（比如要不要强制每个
  子项目有 `README.md`）——保持和 `external_projects/` 不同的定位：
  后者是"正式注册、有 daemon 调度契约"的重量级机制，本方案的产出项目
  是更轻量、"建了就是建了、不需要注册"的自由目录，不强加内部结构。
- 不做磁盘占用治理/自动清理策略——跟 `goal_cron_output_directory_
  convention_plan.md` 的既有取舍一致，先解决"放对地方、不进主仓库"，
  清理策略作为独立的后续 Track。

## 7. 阶段计划

> 约定：每完成一个阶段回来把复选框打勾，实施记录直接续写在本文档
> 第 8 节（对齐仓库内其他 next_doc 计划文档的既有习惯）。

- [ ] **阶段 1：配置层**
  `config/models.py` 加字段 → `config/loader.py` 加读取逻辑 → 手动验证
  `agent_config.json` 里设置/不设置该字段两种情况下 `AppConfig` 取值
  符合预期。
- [ ] **阶段 2：目录与 `.gitignore` 兜底**
  新增 `evolution/output_projects_root.py`（`resolve_root`/`ensure_root`/
  `maybe_append_gitignore`/`ensure_output_projects_root`）→ 补单元测试：
  相对路径解析、绝对路径解析、目录内/目录外两种情况下 `.gitignore`
  是否正确追加（含"已存在该行则不重复追加"的幂等性）、`.gitignore`
  不存在时的新建行为。
- [ ] **阶段 3：Prompt 规则注入 + 老文件迁移**
  `output_path_policy.py` 加第6条规则 + 占位符渲染 → 加"特征字符串检测
  →缺失则追加"的迁移逻辑 → 补单元测试：全新项目（无 policy 文件）生成
  内容正确、已有 policy 文件（新/旧两种，"旧"指已含新规则/不含新规则）
  分别验证追加行为符合预期、用户已编辑过 1~5 条规则的内容不受影响。
- [ ] **阶段 4：接入初始化调用点 + 文档**
  找到 `ensure_policy_file()` 现有调用位置，接入 `ensure_output_
  projects_root()`（评估是否可以合并成一次调用）→ 补文档说明配置项与
  使用方式 → 跑一次端到端手动验证：新建一次会话，给一个"帮我做个调研
  小项目"类型的任务，确认 agent 把产出建到了 `output_projects_root`
  下而不是 `src/`，且主项目 `.gitignore` 正确包含该目录。
- [ ] **阶段 5：回归**
  跑现有测试套件（尤其是 `test_*output_path_policy*`、`test_*output_
  workspace*` 相关用例，确认本次改动没有影响 `.agent/daemon_run_
  outputs/` 既有行为）→ 全部通过后视为本方案实施完成。

## 8. 实施记录

（待阶段 1~5 实施完成后在此续写，格式对齐仓库内其他 next_doc 计划文档
的"实施记录"小节。）
