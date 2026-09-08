# 调研/产出类项目根目录与主项目 Git 隔离

> 设计背景与实施记录：
> `next_doc/output_projects_root_and_git_isolation_plan.md`
>
> 相关但不同的机制：
> - [Goal 产出目录规范](goal-output-directory-guide.md) —— 覆盖的是**已绑定
>   周期性 Goal/CronJob** 的每轮产出（`.agent/daemon_run_outputs/`），
>   本文档不覆盖那个场景，两者互不影响。
> - `.agent/policies/output_path_policy.md` —— 本文档描述的第6条规则就
>   注入在这份 policy 文件里。

## 1. 解决什么问题

agent 在执行任务（交互式对话、一次性 goal、周期性 goal、cron、workflow）
过程中，有时需要新建一个**独立的、有自己完整文件结构**的产出物——调研
报告合集、爬虫/分析脚本项目、生成的示例代码库、临时验证用的小项目等。
这类东西不应该：

- 被放进主项目的 `src/`、`tests/` 或仓库根目录；
- 被主项目的 git 仓库连带管理（污染主仓库提交历史/体积）。

本方案给这类产出物一个统一的、用户可配置的落脚点，并在它落在主项目
工作树内时自动加一层 `.gitignore` 兜底。

## 2. 配置项

`agent_config.json` 顶层新增字段：

```json
"output_projects_root": "./output_projects"
```

- 不设置时默认 `./output_projects`。
- 相对路径相对 `project_root` 解析；也支持绝对路径（比如放到别的磁盘
  分区，此时不受本文档 §4 的 `.gitignore` 自动追加影响）。
- 对应 `AppConfig.output_projects_root`（`config/models.py`）。

## 3. 目录归属范围

**纳入本方案**：判断需要新建一个独立产出项目时，一律建到
`<output_projects_root>/<项目名>/` 下（`项目名` 用 snake_case，agent 根据
任务主题自行命名）。

**不纳入、维持现状**（已有专门归宿，不重复搬家）：

- `.agent/daemon_run_outputs/`：周期性 Goal/CronJob 每轮执行的产出，
  见 [Goal 产出目录规范](goal-output-directory-guide.md)，不受本方案影响。
- 已注册的 `external_projects/`：本身有 `project.yaml` 契约和独立
  `Workspace`，不受影响。
- 用户任务里已经明确指定了目录的：以任务描述为准，优先级最高
  （`output_path_policy.md` 第4/5条规则）。

## 4. Git 隔离怎么做到的

两层兜底，均不做工具调用级别的硬拦截：

1. **Prompt 规则**：`.agent/policies/output_path_policy.md` 第6条规则
   （见 §5）明确要求产出类项目建到 `output_projects_root` 下、禁止
   commit 进主仓库；产出项目自身需要版本控制的，应在其自己目录内单独
   `git init`。
2. **`.gitignore` 自动追加**：首次解析 `output_projects_root` 时，如果
   它落在主项目 `project_root` 内，自动、幂等地把它相对 `project_root`
   的路径（如 `output_projects/`）追加进主项目根目录 `.gitignore`
   （仅在该行不存在时追加，不改动文件其余内容）。即使不小心在主项目
   根目录 `git add -A`，该目录默认也不会被暂存。

若 `output_projects_root` 配置成了主项目目录之外的绝对路径，本来就不在
主仓库工作树内，跳过 `.gitignore` 追加这一步。

**不做的事**：不拦截 `git add`/`git commit` 命令本身，也不做旧数据迁移
——已经散落在 `src/` 或仓库根目录下的历史产出物不受影响，只对改造上线
之后新产生的调研/产出项目生效。

## 5. Prompt 规则原文（第6条）

```
6. 调研类、产出类项目（有独立文件结构，可能需要自己的代码/数据版本
   管理）一律新建到 `<绝对路径>/<项目名>/` 下，禁止放进
   主项目 src/、tests/ 或仓库根目录。这类目录不受主项目 git 管理；如该
   产出项目自身需要版本控制，应在其自己的目录内单独执行 `git init`
   建独立仓库，禁止把它的改动 commit 到主项目仓库。
   （已经有专门产出机制覆盖的场景——周期性 Goal/CronJob 的
   `.agent/daemon_run_outputs/`、已注册的 external_projects——以那些
   机制已有的规则为准，不受本条影响。）
```

老用户已有的 `output_path_policy.md` 文件不会被覆盖：`ensure_policy_file()`
会检测这条规则是否已存在（特征字符串），缺失则在文件末尾追加（带一行
"以下为自动追加的新规则（日期）"的注释），已编辑过的 1~5 条规则内容
不受影响。

## 6. 涉及的代码

| 模块 | 作用 |
|---|---|
| `src/mini_agent/config/models.py` | `AppConfig.output_projects_root` 字段 |
| `src/mini_agent/config/loader.py` | 从 `agent_config.json` 读取该字段 |
| `src/mini_agent/evolution/output_projects_root.py` | `resolve_root`/`ensure_root`/`maybe_append_gitignore`/`ensure_output_projects_root` |
| `src/mini_agent/evolution/output_path_policy.py` | 第6条规则 + `{{output_projects_root}}` 占位符渲染 + 老文件追加式迁移；`load_policy()` 内部顺带调用 `ensure_output_projects_root()` |

`load_policy(paths, cfg=None)` 新增可选 `cfg` 参数：传入 `AppConfig` 时
用其中的 `output_projects_root` 字段渲染占位符；不传时退回直接读取
`<project_root>/agent_config.json` 原始字段，兼容尚未接入 cfg 传参的
调用点（`cron_job_workspace.py`、`cron_scheduler.py`）。
