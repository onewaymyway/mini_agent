# Mini Agent 看板 (Kanban Dashboard)

基于 Streamlit 的一体化观测/交互面板，在 `apps/mini_agent_webdemo`（纯聊天）的基础上，
补充了会话管理、目标/Cron 看板、产出物浏览、自我状态与诊断信息。

## 前置条件

1. 先启动 mini-agent 的 HTTP daemon（提供 `/v1/*` 接口），例如：
   ```bash
   python -m mini_agent.cli.app daemon start
   ```
   默认监听 `http://127.0.0.1:8765`，Token 通常写在 `agent_api.key` 文件里。

2. 安装依赖：
   ```bash
   pip install -r apps/mini_agent_kanban/requirements.txt
   ```

## 启动看板

```bash
cd apps/mini_agent_kanban
streamlit run app.py
```

在左侧栏填入 API Base URL（默认 `http://127.0.0.1:8765/v1`）与 Token 即可连接。

## 功能一览

| Tab | 内容 |
|---|---|
| 💬 对话 | 聊天、历史消息、事件流、发送/中断 |
| 🗂️ 会话管理 | 会话列表、新建 / 恢复 / 删除会话 |
| 📌 目标看板 | Goal / Objective 看板（按状态分列，可拖动状态）、新建目标、Cron Job 管理、Objective 执行进度 |
| 📁 产出物 | 浏览 `.agent/` 等目录下产出文件，预览与下载 |
| 🧠 自我状态 | 具身智能自省信息（自主循环摘要、活跃目标数、最近活动、多用户会话池） |
| 🔧 诊断 | `/diagnostics` 原始信息，便于排障 |

顶部状态条常驻展示：运行状态、当前 Turn、自主等级、距下次 Tick 时间、Tick 计数、
订阅者数量，以及待审批权限请求（点击展开后可逐条允许/拒绝）。

## 后续可扩展方向

- SSE 真流式渲染（当前对话为轮询式刷新，简单可靠但非逐 token 流式）
- Ensemble 多候选结果对比展示
- 进化流水线（Skill 提案 / git worktree diff）可视化
- 权限历史与安全网风险等级（T0-T3）统计图表
