# Mini Agent 看板 React SPA 重构方案与计划

> 目标：用 `Vite + React + TypeScript` 前后端分离的 SPA 替换现有基于 Streamlit 的
> `apps/mini_agent_kanban`（`app.py` 7000+ 行，卡顿、状态刷新代价高、组件复用差），
> 新看板放在 `apps/mini_agent_kanban_x`，后端 100% 复用现有 FastAPI
> (`src/mini_agent/api/routes.py`)，仅在确有需要时补充聚合端点。

状态：**进行中**。本文档随每个阶段完成同步更新，最新状态见文末"阶段进度"表。

---

## 1. 为什么要换掉 Streamlit

Streamlit 的执行模型是"任意一次交互 → 整个脚本从头到尾重跑一遍 → 重新渲染整棵树"。
`app.py` 里长期挂着 SSE 轮询 + 大量 `st.session_state` 读写 + 多个 tab 同时渲染，
在会话变多、事件变密（goal/cron 后台任务多）之后就会明显卡顿：

- 没有真正的组件级增量更新，一次 rerun 意味着整页 DOM 重新生成；
- 没有请求级缓存/去重，多个 widget 各自触发相同的 `/v1/status` 请求；
- SSE/流式聊天在 Streamlit 里只能靠 hack（轮询 + `st.rerun()`），体验和真正的
  `EventSource` 增量渲染差距很大；
- 7000+ 行单文件，UI 状态和业务逻辑耦合，改动成本和回归风险都高。

## 2. 主流 Agent 框架的 Web 管理端都怎么做

调研市面上几类"Agent/LLM 应用管理后台"的常见实现（不依赖具体某家产品的私有信息，
只总结**技术形态**，避免对号入座）：

| 类型 | 代表形态 | 技术栈规律 |
|---|---|---|
| Agent 框架自带的调试/观测 UI | 轨迹回放、工具调用时间线、会话列表 | 几乎清一色 **React SPA + 独立 API 服务**，SPA 用 Vite 打包成静态资源，由后端 `StaticFiles` 挂载或独立 Nginx 托管 |
| 对话类产品的管理后台 | 用户/会话/计费/审核 | React/Vue + Ant Design/Element Plus 之类中后台组件库，后端是 REST/GraphQL |
| 内部数据工具（次优先级场景） | 报表、原型 | Streamlit/Gradio —— 但仅限"数据科学家自用、访问量小、交互简单"的场景 |
| 实时性要求高的监控面板 | 任务队列、执行日志 | SSE 或 WebSocket 推送 + 前端增量渲染（React state / 状态管理库），而不是整页轮询重绘 |

结论和用户提出的方案一致：**长期使用、多 Tab、要流式聊天/实时状态的看板，
应该用前后端分离的 SPA，而不是 Streamlit/Gradio 这类"脚本重跑型"框架。**
Streamlit 更适合一次性原型或低频访问的内部小工具。

## 3. 技术选型（最终确认）

| 层 | 选型 | 理由 |
|---|---|---|
| 构建工具 | **Vite 5** | 内部管理后台不需要 SSR/SEO，Vite 的 dev server（原生 ESM + esbuild）比 CRA/Next 快得多，配置也更少 |
| 框架 | **React 18 + TypeScript** | 生态最成熟，类型安全能扛住这种"后端接口字段极多"的项目 |
| UI 组件库 | **Ant Design (antd) v5** | 中后台场景组件覆盖最全（Table/Tree/Drawer/Upload/Statistic 等现成看板里大量用到的控件），中文文档/生态好，антd 的 `ProComponents` 可选后续接入 |
| 数据请求/缓存 | **TanStack Query (React Query) v5** | 自带缓存、去重、轮询（`refetchInterval`）、失败重试；和 SSE 天然分工——**关键实时状态走 SSE 推送，其它数据用 Query 按需拉/轮询** |
| 全局状态 | **Zustand** | 只用来放"当前 session_id / token / 侧边栏折叠"这类跨组件轻状态，避免把请求缓存也塞进全局 store（这是 Redux 类方案容易踩的坑） |
| 路由 | **React Router v6** | 标准选择，SPA 内的 Tab（会话/文件/目标/自我状态…）映射成路由，可以直接分享 URL 定位到具体 Tab |
| 图表 | **@ant-design/plots (G2Plot)** | 和 antd 视觉一致，够用；如后续需要更定制化图表可以换成 ECharts |
| 代码高亮/Diff | **`react-diff-viewer-continued` + `react-syntax-highlighter`** | 对应 Streamlit 版里 `diff_view.py` 的能力 |
| 后端 | **不新建服务，直接复用 `src/mini_agent/api/routes.py` 现有 FastAPI** | 全部业务能力已经在这一层实现完毕（chat/history/stream/sessions/fs/users/self/… 90+ 个端点），SPA 只是换了一层前端 |
| 生产部署 | Vite `build` 产出静态文件，由现有 FastAPI 用 `StaticFiles` 挂载在 `/kanban` 路径下（同源，天然免 CORS）；开发期用 Vite dev server + `/v1` 反向代理到 daemon | 避免引入 Nginx 之类额外基建，保持"一个进程就能跑起来"的部署简单性 |

不选 Next.js 的原因：这是内网/本地管理后台，不需要 SSR、不需要 SEO、也不需要文件路由带来的心智负担；
Next 的构建产物和运行时都比 Vite SPA 重，收益为负。

不选 shadcn/ui 的原因（可选项，非否定）：shadcn 更适合"从零设计一套独特视觉语言"的产品，
但看板类目要求的是"组件覆盖全、开发速度快"，antd 现成的 Table/Tree/Statistic/Timeline 等组件能省下大量工作量；
如果后续想要更现代的视觉，可以在 antd 基础上做主题定制（antd v5 的 `ConfigProvider` token 系统足够灵活）。

## 4. 目录与代码结构

```
apps/mini_agent_kanban_x/                  # 新看板根目录（与旧 mini_agent_kanban 并存，互不影响）
├── README.md                              # 使用说明（开发/构建/部署）
├── package.json
├── vite.config.ts                         # dev server 代理 /v1 -> daemon
├── tsconfig.json
├── index.html
├── .env.example                           # VITE_API_BASE 等
├── public/
└── src/
    ├── main.tsx                           # 入口：QueryClientProvider / AntdConfigProvider / Router
    ├── App.tsx                            # 顶层布局：侧边栏 + 顶部状态条 + 路由出口
    ├── vite-env.d.ts
    ├── api/
    │   ├── client.ts                      # 封装 fetch：注入 Authorization、统一错误处理（对应旧 client.py）
    │   ├── types.ts                       # 后端 Pydantic 模型对应的 TS 类型（StatusResponse/SessionInfo/...）
    │   ├── endpoints.ts                   # 端点常量与函数化调用（getStatus/getHistory/postChat/...）
    │   └── sse.ts                         # EventSource 封装 + 自动重连（对应 /v1/stream）
    ├── stores/
    │   ├── authStore.ts                   # token / baseUrl（持久化到 localStorage）
    │   └── uiStore.ts                     # 侧边栏折叠、当前 session_id 等
    ├── hooks/
    │   ├── useStatus.ts                   # useQuery(status) + SSE 增量合并
    │   ├── useSessions.ts
    │   ├── useChatStream.ts               # 聊天专用：SSE 增量 token 拼接
    │   └── usePermissions.ts              # 权限/交互待处理轮询
    ├── layouts/
    │   └── MainLayout.tsx                 # antd Layout：Sider + Header + Content
    ├── pages/
    │   ├── Login.tsx                      # 对应旧 auth.py 的登录门禁（可选开启）
    │   ├── Dashboard/                     # 总览：状态卡片、活跃度、诊断摘要
    │   ├── Chat/                          # 对话（流式）
    │   ├── Sessions/                      # 会话列表 / 详情 / resume / delete
    │   ├── Files/                         # 文件浏览（对应 fs/* 接口）
    │   ├── Permissions/                   # 权限与交互待处理审批
    │   ├── SelfStatus/                    # 自我诊断 / 调度总览 / LLM 池状态
    │   ├── Users/                         # 用户管理（owner 权限）
    │   └── Settings/                      # API Base / Token 配置
    ├── components/
    │   ├── StatusBadge.tsx
    │   ├── DiffView.tsx                   # 对应旧 diff_view.py
    │   ├── EventTimeline.tsx
    │   └── ...
    └── utils/
        └── format.ts
```

后端侧（`src/mini_agent/api/`）改动原则：**默认不改**。仅当 SPA 需要"一次请求聚合多个字段"
（例如 Dashboard 首屏同时要 status + diagnostics + pending permissions）时，
才在 `routes.py` 新增一个 `GET /v1/dashboard/summary` 之类的聚合端点，避免前端一次拉 5~6 个接口。
是否新增会在对应阶段的实施记录里注明。

## 5. 分阶段实施计划

| 阶段 | 内容 | 产出 | 状态 |
|---|---|---|---|
| P0 | 方案设计与文档 | 本文档 + README 骨架 | ✅ 已完成 |
| P1 | 工程脚手架：Vite+React+TS+antd+TanStack Query+Router，鉴权（Token/登录门禁）、布局、Dashboard（状态总览，SSE 实时） | `apps/mini_agent_kanban_x` 可运行的最小闭环 | ✅ 已完成（本次交付） |
| P2 | Chat 流式对话页 + Sessions 会话管理（列表/详情/resume/delete/新建） | Chat/Sessions 页面 | ✅ 已完成（本次交付） |
| P3 | Files 文件浏览（list/read/download/upload/mkdir/delete/rename）+ DiffView | Files 页面 | ⏳ 规划中 |
| P4 | Permissions/Interactions 待处理审批、SelfStatus（调度总览/LLM 池/公平性诊断/错误日志统计等只读面板） | Permissions/SelfStatus 页面 | ⏳ 规划中 |
| P5 | Users 用户管理（owner）、Settings、生产构建接入 FastAPI `StaticFiles` 挂载、旧 Streamlit 看板下线评估 | 生产可部署形态 | ⏳ 规划中 |

> 说明：受限于一次性交付的时间/篇幅，P1+P2 是本次直接落地的部分（工程可跑、
> Dashboard 与 Chat/Sessions 三个高频、对 Streamlit 卡顿最敏感的页面已用真实接口打通）；
> P3~P5 给出了明确的目录占位、接口清单和实现方式，便于后续按同样模式继续搬迁，
> 不需要推倒重来。

## 6. 与后端接口的映射关系（节选，完整清单见 `routes.py`）

| 前端能力 | 后端端点 | 前端实现方式 |
|---|---|---|
| 健康检查/状态总览 | `GET /v1/status`、`GET /v1/whoami`、`GET /v1/diagnostics` | TanStack Query `refetchInterval` 轮询 + SSE 事件触发 `invalidateQueries` |
| 实时事件流 | `GET /v1/stream`、`GET /v1/stream/{turn_id}` | `EventSource`封装（`api/sse.ts`），组件卸载自动关闭连接 |
| 发送消息 | `POST /v1/chat` | mutation，发送后订阅 `/v1/stream/{turn_id}` 拼接增量 |
| 中断 | `POST /v1/interrupt` | mutation |
| 历史记录 | `GET /v1/history`、`DELETE /v1/history` | query + mutation |
| 会话列表/详情/新建/恢复/删除 | `GET /v1/sessions`、`GET /v1/sessions/{id}`、`POST /v1/sessions/new`、`POST /v1/sessions/{id}/resume`、`DELETE /v1/sessions/{id}` | antd Table + Drawer 详情 |
| 权限/交互审批 | `GET/POST /v1/permissions*`、`/v1/interactions*` | 轮询 + antd List + 审批按钮 |
| 文件系统 | `GET /v1/fs/list|read|stat|download|search`、`POST /v1/fs/write|mkdir|upload`、`DELETE /v1/fs/delete`、`POST /v1/fs/rename` | antd Tree + 代码编辑/预览面板 |
| 用户管理 | `/v1/users*` | antd Table + Modal 表单 |
| 自我状态 | `/v1/self/*`（llm_pool_status/fairness_diagnostics/scheduling_overview/…） | 只读 Query，antd Descriptions/Statistic |

## 7. 如何使用（开发/构建/部署）

详见 `apps/mini_agent_kanban_x/README.md`，摘要：

```bash
# 1. 先启动 mini-agent HTTP daemon（和旧看板一样）
python -m mini_agent.cli.app daemon start

# 2. 开发模式
cd apps/mini_agent_kanban_x
npm install
npm run dev            # 默认 http://localhost:5173，自动代理 /v1 到 daemon

# 3. 生产构建
npm run build           # 产出 dist/，可用 `npm run preview` 本地预览
                         # 或由 FastAPI 侧挂载为静态资源对外提供服务
```

## 8. 风险与注意事项

- **鉴权模型沿用旧看板**：Token 存于浏览器 `localStorage`（仅本机/内网使用场景），
  如需要更强的登录门禁，P1 已包含一个简化版登录页（用户名+密码 → 换取/校验 Token），
  行为上对应旧 `auth.py` + `--require-login`，但服务端校验逻辑不变，仍由现有 FastAPI 完成。
- **SSE 断线重连**：沿用后端已支持的 `Last-Event-ID` 语义，前端在 `api/sse.ts` 里做指数退避重连。
- **CORS**：开发期由 Vite `server.proxy` 转发，天然同源；生产构建后建议用 `StaticFiles`
  挂载到 daemon 同一进程/同一域名下，避免额外配置 CORS。
- **新旧并存**：本次改造不删除 `apps/mini_agent_kanban`（Streamlit 版），
  两者可以同时指向同一个 daemon 独立运行，验证无误后再决定是否下线旧版本。

## 9. 阶段进度（随实施更新）

- 2026-08-17：完成 P0（本文档）与 P1（工程脚手架 + 鉴权 + Dashboard 实时状态）、
  P2（Chat 流式对话、Sessions 会话管理）。详见 `apps/mini_agent_kanban_x/README.md`
  中的"当前完成度"章节。P3~P5 待后续迭代。
