---
name: git-context
description: 分析当前工作目录的 Git 仓库状态，包括最近 commit 历史、变更文件、分支信息和 diff。当用户询问"最近改了什么"、"有哪些提交"、"代码变更"、"git 历史"等问题时使用。
triggers: git, commit, diff, branch, log, 提交, 变更, 改了什么, git历史, 代码变更, 最近改动, 改了哪些, 哪些文件, 修改记录, changelog, 历史记录, 版本差异
---

# Git Context Skill

帮助 agent 系统性地读取并理解当前工作目录的 Git 仓库状态，从而准确回答用户关于代码变更、提交历史、文件改动的问题。

---

## 使用时机

当用户提出以下类型的问题时，激活本 skill：

- "最近有哪些 commit？"
- "改了哪些文件？"
- "这个版本和上个版本有什么区别？"
- "帮我看看 git 历史"
- "最近的代码变更是什么"
- 需要根据 git 信息做代码审查、生成 changelog、分析影响范围时

---

## 信息采集步骤

**按顺序执行以下命令，用 `bash` 工具采集 git 信息：**

### Step 1：确认 git 仓库

```bash
git -C <工作目录> rev-parse --show-toplevel 2>&1
```

若返回错误（not a git repository），直接告知用户当前目录不是 git 仓库，终止流程。

### Step 2：获取当前分支和状态概览

```bash
# 当前分支
git branch --show-current

# 简洁状态（暂存区 + 工作区未提交变更）
git status --short
```

### Step 3：查看最近 commit 列表

```bash
# 最近 10 条 commit，单行格式，含相对时间
git log --oneline --graph --decorate -10

# 若用户指定了条数或时间范围，调整 -10 或加 --since 参数
# 例：--since="7 days ago" / --since="2024-01-01"
```

### Step 4：获取每个 commit 的变更文件

```bash
# 每个 commit 改动了哪些文件（统计模式，不含具体内容）
git log --stat -5 --format="%H %s (%ar)"

# 或者用 --name-status 获取操作类型（A=新增, M=修改, D=删除, R=重命名）
git log --name-status -5 --format="%ncommit %H%n%s%n%ar"
```

### Step 5：查看具体 diff（按需）

```bash
# 最近一次 commit 的完整 diff
git show HEAD --stat

# 两个 commit 之间的 diff（若用户指定范围）
git diff <commit-a>..<commit-b> --stat

# 特定文件的历史变更
git log --follow -p -- <文件路径>

# 当前未提交的变更 diff
git diff HEAD
```

### Step 6：获取远端同步状态（可选）

```bash
# 本地领先/落后远端多少 commit
git rev-list --left-right --count origin/HEAD...HEAD 2>/dev/null
```

---

## 结果呈现规范

收集完信息后，按以下结构组织回答：

### 1. 仓库概况
- 当前分支名
- 最新 commit 的 hash（前 7 位）、标题、时间
- 是否有未提交的变更（working tree dirty）

### 2. 最近 N 条 commit 摘要

以表格或列表形式展示，字段包括：
| commit | 时间 | 提交信息 |
|--------|------|----------|
| `a3f2c1d` | 2 hours ago | fix: 修复登录验证逻辑 |

### 3. 文件变更汇总

按文件路径分组，标注操作类型：
- `M` **src/auth/login.py** — 修改
- `A` **tests/test_login.py** — 新增
- `D` **legacy/old_auth.py** — 删除

若文件数量较多（>20），优先展示变更行数最多的文件。

### 4. 关键 diff（仅用户明确要求时）

展示具体代码变更，用 markdown 代码块包裹，注明文件名。

---

## 常用参数组合速查

| 用户意图 | 推荐命令 |
|----------|----------|
| 最近 N 条提交 | `git log --oneline -N` |
| 某天之后的提交 | `git log --oneline --since="YYYY-MM-DD"` |
| 某个文件的历史 | `git log --oneline -- <path>` |
| 两个版本间的差异 | `git diff v1.0..v2.0 --stat` |
| 某次 commit 的完整变更 | `git show <hash>` |
| 当前未暂存的改动 | `git diff` |
| 已暂存但未提交的改动 | `git diff --cached` |
| 查找包含关键词的提交 | `git log --oneline --grep="<keyword>"` |
| 查找某行代码何时引入 | `git log -S "<code-snippet>" --oneline` |

---

## 注意事项

- **大仓库性能**：`git log -p`（含 diff 内容）在大仓库可能很慢，优先用 `--stat` 代替，只在用户明确要查看 diff 时才展开。
- **二进制文件**：diff 中出现 `Binary files differ` 时，仅报告文件名和操作类型，不展示内容。
- **敏感信息**：若 diff 中出现疑似密钥、密码（如 `sk-`、`password=`、`token=`），在展示时打码或提示用户注意安全。
- **Detached HEAD**：若当前处于 detached HEAD 状态，在概况中明确说明，并显示当前 HEAD 指向的 commit。
- **子模块**：若仓库包含 git submodule，需要分别对每个子模块运行上述命令（`git submodule foreach`）才能获得完整信息。

---

## 与其他 skill 的协作

- 配合 **python-expert** skill：在分析 Python 文件变更后，可进一步解释代码逻辑含义
- 配合 **agent-generator** skill：基于 git diff 分析影响面，决定是否需要拆分子任务并行处理
