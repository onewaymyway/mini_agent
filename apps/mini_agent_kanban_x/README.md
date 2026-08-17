# Mini Agent 看板 X（React SPA 版）

用 `Vite + React + TypeScript + Ant Design + TanStack Query` 重构的新一代看板，
替代基于 Streamlit 的 `apps/mini_agent_kanban`（保留、不影响旧版继续使用）。

后端 100% 复用现有 mini-agent HTTP daemon（`src/mini_agent/api/routes.py`），
本目录只是一个纯前端工程。

完整方案设计见：`../../next_doc/kanban_react_spa_replacement_plan.md`
完整功能清单（旧看板 18 个 Tab 逐一梳理）见：`../../next_doc/kanban_feature_inventory.md`

## 当前完成度（随开发持续更新）

阶段划分覆盖旧看板全部 18 个 Tab，完整表格见方案文档第 5 节
"分阶段实施计划"，此处只列进度摘要：

| 阶段 | 覆盖范围 | 状态 |
|---|---|---|
| P0 | 方案设计文档 + 功能清单 | ✅ |
| P1 | 工程脚手架、鉴权（Token 登录）、布局、Dashboard 状态总览（含轮询） | ✅ |
| P2 | 💬 对话（基础流式收发）、🗂️ 会话管理（基础增删查） | ✅ 基础版 |
| P2b | 对话/会话管理补完（内联权限审批、事件流面板、置顶对比等） | ⏳ 规划中 |
| P3 | Files 通用文件浏览、📁 产出物浏览、🖼️ 产出预览、DiffView 组件 | ⏳ 规划中 |
| P4 | Topbar 完整版（权限/交互/Sentinel/inbox）、🧠 自我状态、🔧 诊断信息、📛 错误日志统计 | ⏳ 规划中 |
| P5 | 📌 目标看板（拆 P5a/P5b，规模最大的 Tab） | ⏳ 规划中 |
| P6 | 🔄 工作流 | ⏳ 规划中 |
| P7 | 🌱 成长顾问、🎓 能力学习 | ⏳ 规划中 |
| P8 | 🧬 进化提案、⏰ Cron 任务、🗓️ 全局日程 | ⏳ 规划中 |
| P9 | 🔌 外部输入网关、🔔 关注与通知、🧪 混合执行 | ⏳ 规划中 |
| P10 | ⚙️ 配置管理、Users 用户管理（增强项）、账户登录门禁完整版 | ⏳ 规划中 |
| P11 | 生产构建接入 FastAPI 静态资源挂载、代码分割优化、回归核对、旧版下线评估 | ⏳ 规划中 |

## 前置条件

1. 启动 mini-agent HTTP daemon（和旧看板要求一致）：
   ```bash
   python -m mini_agent.cli.app daemon start
   ```
   默认监听 `http://127.0.0.1:8765`，Token 在项目 `.agent/agent_api.key`
   （单用户模式）或 `.agent/users/tokens/owner.key`（多用户模式 owner）里。

2. 安装 Node.js 18+ 和 npm。

## 开发模式

```bash
cd apps/mini_agent_kanban_x
npm install
npm run dev
```

默认打开 `http://localhost:5173`，首次进入会跳转到登录页：
- API Base 填 `/v1`（Vite dev server 会把 `/v1` 代理到 daemon，见 `vite.config.ts`）
- Token 填上面提到的 Token 文件内容

如果 daemon 不在默认地址 `http://127.0.0.1:8765`，启动时用环境变量覆盖：
```bash
VITE_DAEMON_TARGET=http://192.168.1.10:8765 npm run dev
```

## 生产构建

```bash
npm run build      # 产出 dist/
npm run preview    # 本地预览生产构建（默认 http://localhost:4173）
```

推荐的生产部署方式：由 mini-agent 的 FastAPI 应用用 `StaticFiles` 把
`dist/` 挂载到 `/kanban` 路径下（与 daemon 同源，天然免 CORS，也不需要额外的
Nginx/反向代理）。示例（在 daemon 的 FastAPI `app` 初始化处追加，不属于本次
自动改动范围，需要时手动加）：

```python
from fastapi.staticfiles import StaticFiles

app.mount(
    "/kanban",
    StaticFiles(directory="apps/mini_agent_kanban_x/dist", html=True),
    name="kanban-x",
)
```

也可以完全独立部署（例如 Nginx / Vercel 之类静态托管），此时把 `.env` 里的
`VITE_API_BASE` 指向 daemon 的完整地址（如 `https://your-domain/v1`），
并确保 daemon 侧允许对应来源的 CORS。

## 目录结构

```
src/
├── api/          # 后端 HTTP/SSE 封装（client.ts / endpoints.ts / sse.ts / types.ts）
├── stores/       # zustand 全局状态：鉴权、UI（当前 session 等）
├── hooks/        # 业务 hooks：useStatus / useSessions / useChatStream
├── layouts/      # 页面整体布局（侧边栏 + 顶部状态条）
├── pages/        # 各功能页面（Login/Dashboard/Chat/Sessions/Settings/…）
└── components/   # 可复用 UI 组件（后续 P3+ 补充 DiffView 等）
```

## 已知限制 / TODO

- 目前只打通 Dashboard / Chat / Sessions / Settings 四个页面，其余能力
  （文件浏览、权限审批、自我状态、用户管理）尚未从 Streamlit 版迁移，
  期间可以继续使用旧版 `apps/mini_agent_kanban` 处理这些场景。
- 生产构建产物体积提示（>500KB 单 chunk）：后续可以引入路由级 `React.lazy`
  做代码分割，当前阶段优先保证功能正确、暂不做该优化。
- 登录页当前只做"能否连通 + 存储 Token"的校验，如果后端启用了旧版
  `--require-login` 的账户体系，需要先用其它方式（如旧看板）换出账户 Token，
  再粘贴到这里；后续可以在 P5 补一个真正的用户名+密码换 Token 的登录端点。
