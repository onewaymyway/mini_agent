# mini_agent 桌面常驻客户端

Windows 系统托盘常驻客户端（纯 HTTP/SSE，不依赖 mini_agent 核心包）。

```powershell
pip install -r requirements.txt
python -m mini_agent_desktop --project-root D:\path\to\mini_agent --check   # 无界面自检
pythonw -m mini_agent_desktop --project-root D:\path\to\mini_agent          # 托盘常驻
```

完整说明、配置、图标含义与 Windows 验证清单见 [docs/desktop-client-guide.md](../../docs/desktop-client-guide.md)；
设计与分阶段计划见 [next_doc/windows_desktop_resident_plan.md](../../next_doc/windows_desktop_resident_plan.md)。

当前阶段：S2 快速面板与全局热键（托盘左键唤起面板；热键需在 `desktop.json` 中设置 `hotkey_enabled: true`，默认关闭）。Toast 与审批（S3）、自启动（S4）尚未实现。
