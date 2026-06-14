# Reminder 系统测试案例

## 功能概述

测试 mini-agent 的动态 Reminder 注入机制，验证各类触发场景、优先级排序、
双目录加载、自定义覆盖、禁用开关等核心能力。

## 前置条件

1. **正常启动 agent**，无需额外配置，reminder 系统默认启用
2. **调试模式**（可选，推荐测试时开启）：
   ```bash
   python -m mini_agent --reminder-verbose
   ```
   开启后，每次 reminder 匹配和注入都会在终端打印 `[reminder] 注入: '<name>'` 日志

3. **确认系统默认 reminder 已加载**：启动时若开启 verbose，应看到类似输出：
   ```
   [ReminderLoader] 加载完成：system=8 custom=0 merged(enabled)=8
   [ReminderManager] 初始化完成，共 8 条 reminder。
   ```

---

## 单元测试（代码层）

无需启动 agent，直接运行脚本验证核心逻辑：

```bash
cd <项目根目录>
PYTHONPATH=src python3 -c "
from mini_agent.reminders.loader import ReminderLoader
from mini_agent.reminders.matcher import ReminderMatcher
from pathlib import Path

loader = ReminderLoader(system_dir=Path('src/mini_agent/prompts/reminders'), verbose=True)
reminders = loader.load()
print(f'加载 {len(reminders)} 条 reminder')

matcher = ReminderMatcher(reminders)

# 测试 Permission denied
hits = matcher.match_tool_error('bash', 'chmod: Permission denied')
print(f'Permission denied 命中: {[r.name for r in hits]}')

# 测试 ModuleNotFoundError
hits = matcher.match_tool_error('bash', \"ModuleNotFoundError: No module named 'numpy'\")
print(f'ImportError 命中: {[r.name for r in hits]}')

# 测试用户意图
hits = matcher.match_user_intent('帮我创建一个新文件')
print(f'用户意图命中: {[r.name for r in hits]}')
"
```

**期望输出：**
```
加载 8 条 reminder
Permission denied 命中: ['bash_permission_error']
ImportError 命中: ['python_import_error']
用户意图命中: ['write_large_file']
```

---

## 场景测试（对话层）

### 场景一：工具错误触发 — bash 权限错误

**目的**：验证 bash 执行出现 Permission denied 时，自动注入解决思路

**测试步骤**：

1. 启动 agent（建议带 `--reminder-verbose`）
2. 输入以下指令触发权限错误：
   ```
   请执行：chmod 777 /etc/passwd
   ```
3. 观察 agent 的响应

**期望行为**：
- 终端出现 `[reminder] 注入: 'bash_permission_error'`
- agent 回复中体现权限错误处理建议（检查权限、避免操作系统文件等）
- 对话历史中可看到 `[Reminder: bash_permission_error]` 消息（role=user）

---

### 场景二：工具错误触发 — Python 模块缺失

**目的**：验证 Python ImportError 时注入安装建议

**测试步骤**：

1. 输入：
   ```
   写一段 Python 代码，import pandas 并打印版本号，然后执行它
   ```
2. 若环境中没有 pandas，会出现 ModuleNotFoundError

**期望行为**：
- 命中 `python_import_error` reminder
- agent 被提示使用 `pip install pandas --break-system-packages` 进行安装

---

### 场景三：工具错误触发 — 命令未找到

**目的**：验证 command not found 时注入安装建议

**测试步骤**：

1. 输入：
   ```
   用 jq 命令格式化输出以下 JSON：{"name":"test","value":42}
   ```
2. 若 jq 未安装，bash 会返回 `jq: command not found`

**期望行为**：
- 命中 `command_not_found` reminder
- agent 被提示 `apt-get install -y jq` 或使用 Python 替代方案

---

### 场景四：用户意图触发 — 写入文件

**目的**：验证用户表达写文件意图时提前注入注意事项

**测试步骤**：

1. 输入：
   ```
   帮我创建一个 Python 脚本文件，用来统计当前目录下各类型文件的数量
   ```

**期望行为**：
- 在用户消息入队后，命中 `write_large_file` reminder（keyword 匹配"创建"）
- reminder 内容提示使用 `str_replace` 优先于全量覆写等注意事项

---

### 场景五：磁盘空间不足（高优先级验证）

**目的**：验证高优先级 reminder（priority=90）在多条命中时排在最前

**测试步骤**：

```bash
PYTHONPATH=src python3 -c "
from mini_agent.reminders.loader import ReminderLoader
from mini_agent.reminders.matcher import ReminderMatcher
from pathlib import Path

loader = ReminderLoader(system_dir=Path('src/mini_agent/prompts/reminders'))
matcher = ReminderMatcher(loader.load())

# 构造同时触发多条 reminder 的错误信息
error = 'No such file or directory: No space left on device, Permission denied'
hits = matcher.match_tool_error('bash', error)
print('命中顺序（应按 priority 降序）：')
for r in hits:
    print(f'  {r.name:30s} priority={r.priority}')
print(f'第一条（最高优先级）: {hits[0].name}')
"
```

**期望输出**（priority 降序，disk_space_full=90 排首位）：
```
命中顺序（应按 priority 降序）：
  disk_space_full                priority=90
  bash_permission_error          priority=85
  file_not_found                 priority=75
第一条（最高优先级）: disk_space_full
```

---

### 场景六：max_per_turn 截断验证

**目的**：验证同一 turn 内最多注入 3 条 reminder（默认值）

**测试步骤**：

```bash
PYTHONPATH=src python3 -c "
from mini_agent.reminders import ReminderManager
from pathlib import Path

class Cfg:
    class reminder:
        enabled = True; custom_dir = None
        tool_error_enabled = True; post_tool_enabled = True
        user_intent_enabled = True; pattern_enabled = True
        max_per_turn = 3; verbose = False
    prompts_dir = Path('src/mini_agent/prompts')

mgr = ReminderManager(Cfg())
# 触发尽可能多的错误 reminder
hits = mgr.check_tool_error('bash', 'No space left on device, Permission denied, command not found, No such file or directory')
print(f'命中总数（截断后）: {len(hits)}，应 <= 3')
print(f'命中列表: {[r.name for r in hits]}')
"
```

**期望**：`命中总数（截断后）: 3`

---

### 场景七：自定义目录覆盖系统默认

**目的**：验证用户自定义 reminder 覆盖同名系统 reminder

**测试步骤**：

1. 创建自定义目录和覆盖文件：
   ```bash
   mkdir -p /tmp/my_reminders
   cat > /tmp/my_reminders/bash_permission_error.md << 'EOF'
   ---
   name: bash_permission_error
   trigger_event: tool_error
   condition:
     error_pattern: "Permission denied"
   inject_as: user
   priority: 99
   enabled: true
   ---
   **[自定义Reminder] 这是我的自定义版本，priority=99**
   EOF
   ```

2. 启动时指定自定义目录：
   ```bash
   python -m mini_agent --reminders-dir /tmp/my_reminders --reminder-verbose
   ```

3. 触发权限错误，观察注入的是哪条 reminder

**期望行为**：
- 启动日志显示 `system=8 custom=1`
- 触发错误后，注入的是自定义版本（`priority=99`，内容为"这是我的自定义版本"）
- 系统默认的 `bash_permission_error` 被完全覆盖

---

### 场景八：禁用 reminder 系统

**目的**：验证 `--no-reminders` 彻底禁用 reminder 注入

**测试步骤**：

1. 以禁用模式启动：
   ```bash
   python -m mini_agent --no-reminders
   ```

2. 触发任意工具错误（如权限错误、命令未找到）

**期望行为**：
- 无任何 `[reminder]` 日志输出
- 对话历史中不出现 `[Reminder: ...]` 消息
- agent 正常工作，只是没有 reminder 辅助

---

### 场景九：使用 reminder-generator skill 生成新 reminder

**目的**：验证从对话中提取并保存自定义 reminder 的完整流程

**测试步骤**：

1. 进行一次解决了特定问题的对话，例如：
   ```
   用 bash 执行 git pull，出现了 merge conflict，帮我解决
   ```

2. 问题解决后，输入：
   ```
   把刚才解决 git merge conflict 的方法保存为 reminder
   ```

3. skill 会生成草稿，展示类似内容：
   ```markdown
   ---
   name: git_merge_conflict
   trigger_event: tool_error
   condition:
     tool_name: "bash"
     error_pattern: "CONFLICT|merge conflict"
   inject_as: user
   priority: 75
   enabled: true
   ---
   **Git merge conflict 处理建议：**
   1. git status 查看冲突文件
   2. 手动解决冲突标记后执行 git add .
   3. git commit 完成合并
   ```

4. 确认后选择保存路径（系统目录 or 自定义目录）

**期望行为**：
- 生成的文件格式合法（YAML frontmatter 正确）
- 重启 agent 后该 reminder 被自动加载
- 再次遇到 git merge conflict 时自动触发

---

### 场景十：热重载验证

**目的**：验证修改 reminder 文件后无需重启即可生效（通过 reload）

**测试步骤**：

1. 启动 agent
2. 修改任意 reminder 文件的内容（例如修改正文）
3. 在对话中触发 reminder 系统重载：
   ```
   请执行：Python 代码调用 agent 的 reminder_mgr.reload()
   ```
   或直接重启 agent

**注意**：热重载 API 目前需通过代码层调用 `mgr.reload()`，
后续版本可通过 `/reload-reminders` 命令触发。

---

## 验证 Checklist

| 测试点 | 验证方式 | 通过标志 |
|--------|----------|----------|
| 系统默认 reminder 加载 | verbose 启动日志 | `merged(enabled)=8` |
| tool_error 触发 | 触发 bash 权限错误 | 日志出现 `[reminder] 注入` |
| user_intent 触发 | 输入写文件意图 | `write_large_file` 被注入 |
| priority 排序 | 多条命中时看顺序 | 高 priority 在前 |
| max_per_turn 截断 | 构造多条命中 | 最多返回 3 条 |
| 自定义目录覆盖 | `--reminders-dir` 启动 | 自定义版本被使用 |
| 禁用开关 | `--no-reminders` 启动 | 无任何 reminder 注入 |
| skill 生成 reminder | 对话中说"生成 reminder" | 文件被正确写入 |
