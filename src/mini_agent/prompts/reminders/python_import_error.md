---
name: python_import_error
trigger_event: tool_error
condition:
  tool_name: "bash"
  error_pattern: "ModuleNotFoundError|ImportError|No module named"
inject_as: user
priority: 80
enabled: true
---

**[Reminder] Python 模块导入错误处理建议：**

1. 确认包是否已安装：`pip show <package_name>`
2. 安装缺失的包：`pip install <package_name> --break-system-packages`
3. 若项目有 requirements.txt：`pip install -r requirements.txt --break-system-packages`
4. 注意 python/python3 和 pip/pip3 的对应关系，确保安装到正确的环境
5. 检查包名和导入名是否一致（有时不同，例如 `pip install pillow` 但 `import PIL`）
