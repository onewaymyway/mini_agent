---
name: command_not_found
trigger_event: tool_error
condition:
  tool_name: "bash"
  error_pattern: "command not found|not found|No such file or directory.*bin|which.*not found"
inject_as: user
priority: 82
enabled: true
---

**[Reminder] 命令未找到处理建议：**

1. 确认命令是否安装：`which <command>` 或 `type <command>`
2. 安装缺失工具（Debian/Ubuntu）：`apt-get install -y <package>`
3. Python 工具可通过 pip 安装：`pip install <tool> --break-system-packages`
4. Node.js 工具：`npm install -g <tool>`
5. 检查 PATH 是否包含所需目录：`echo $PATH`
6. 若是新安装的工具，可能需要重新加载 shell：`source ~/.bashrc` 或使用完整路径
