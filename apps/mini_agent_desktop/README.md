# mini_agent 桌面常驻客户端

Windows 系统托盘常驻客户端（纯 HTTP/SSE，不依赖 mini_agent 核心包）。

```powershell
pip install -r requirements.txt
python -m mini_agent_desktop --project-root D:\path\to\mini_agent --check   # 无界面自检
pythonw -m mini_agent_desktop --project-root D:\path\to\mini_agent          # 托盘常驻
```

完整说明、配置、图标含义与 Windows 验证清单见 [docs/desktop-client-guide.md](../../docs/desktop-client-guide.md)；
设计与分阶段计划见 [next_doc/windows_desktop_resident_plan.md](../../next_doc/windows_desktop_resident_plan.md)。

当前阶段：S3 Toast 与待处理事项（需 daemon 开启 `desktop_attention_events_enabled`，桌面端 `toast_enabled` 默认关闭）。快速面板与热键见 S2；自启动（S4）尚未实现。
