---
name: bash_permission_error
trigger_event: tool_error
condition:
  tool_name: "bash"
  error_pattern: "Permission denied|Operation not permitted|EPERM|EACCES"
inject_as: user
priority: 85
enabled: true
---

**[Reminder] bash 权限错误处理建议：**

1. 检查目标文件/目录权限：`ls -la <path>`
2. 若需要写入系统目录，在 sandbox 环境中通常无法使用 `sudo`，请改用用户目录
3. 安装 Python 包时使用 `pip install --break-system-packages` 或 `pip install --user`
4. 若是脚本无执行权限，可先 `chmod +x <script>`
5. 检查当前用户：`whoami` 和 `id`
