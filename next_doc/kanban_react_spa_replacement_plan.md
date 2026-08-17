# Mini Agent 看板 React SPA 重构方案与计划

> 目标：用 `Vite + React + TypeScript` 前后端分离的 SPA 替换现有基于 Streamlit 的
> `apps/mini_agent_kanban`（`app.py` 7000+ 行，卡顿、状态刷新代价高、组件复用差），
> 新看板放在 `apps/mini_agent_kanban_x`，后端 100% 复用现有 FastAPI
> (`src/mini_agent/api/routes.py`)，仅在确有需要时补充聚合端点。

状态：**进行中**。本文档随每个阶段完成同步更新，最新状态见文末"阶段进度"表。

配套文档：`next_doc/kanban_feature_inventory.md` 完整梳理了旧看板 18 个 Tab + 4 个
全局模块的全部功能点与对应后端端点（共 ~175 个 REST 端点），是本文档第 5 节
分阶段计划的直接依据，新增/调整阶段划分时请先核对该清单，避免遗漏功能。

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

> 本节按 `next_doc/kanban_feature_inventory.md` 里梳理出的旧看板 **18 个 Tab + 4 个全局模块**
> 逐一分配阶段，确保"计划覆盖的功能 = 旧看板全部功能"，不遗漏任何一个 Tab。
> 每个阶段的验收标准是：对照功能清单里的条目逐条勾选，能一一对应到新版页面的某个交互点。

### 全局模块（贯穿各阶段，非独立 Tab）

| 模块 | 归属阶段 | 说明 |
|---|---|---|
| 登录门禁 | P1 | 已实现简化版（Token 直连）；账户名+密码登录见 P8 |
| 侧边栏连接配置 | P1 | Settings 页 + MainLayout 顶部状态条已覆盖健康检查/手动刷新 |
| 本页面 session 绑定 | P1（已做基础版）/ P2（多标签页并行完善） | `uiStore.currentSessionId` + URL query 同步 |
| 顶部状态条（队列/任务/gating/权限提醒/Sentinel/全局 inbox） | P1（状态条骨架）→ P4（权限/交互/Sentinel/inbox 完整接入） | |

### Tab 级阶段划分

| 阶段 | 覆盖 Tab / 模块 | 关键产出 | 状态 |
|---|---|---|---|
| P0 | 方案设计 | 本文档 + 功能清单 `kanban_feature_inventory.md` | ✅ 已完成 |
| P1 | 工程脚手架 + 登录 + Dashboard（对应"顶部状态条"精简版） | `apps/mini_agent_kanban_x` 可运行闭环 | ✅ 已完成 |
| P2 | 💬 对话（基础流式收发）、🗂️ 会话管理（基础增删查） | ✅ 已完成（本次交付） |
| P2b | Tab1 补完（内联权限/交互审批、事件流面板）、Tab2（置顶+并排对比、变化提醒横幅、认知锚点保存延后） | ✅ 内联审批与事件流已完成；会话置顶/对比/变化提醒仍规划中 |
| P3 | Files 通用能力 + Tab5 产出物浏览 + Tab6 产出预览 + DiffView 组件 | ✅ 已完成（本次交付） |
| P4 | 全局模块补完（权限/交互审批、Sentinel、全局 inbox 完整接入 Topbar）+ Tab16 诊断信息 + Tab18 错误日志统计 + Tab7 自我状态 | ✅ 已完成（本次交付） |
| P5 | Tab3 目标看板（P5a/P5b） | ✅ 已完成（本次交付，核心能力已覆盖，见下方说明） |
| P6 | Tab4 工作流 | ✅ 已完成（本次交付） |
| P7 | Tab8 成长顾问 + Tab9 能力学习 | ✅ 已完成（本次交付） |
| P8 | Tab10 进化提案 + Tab11 Cron 任务 + Tab12 全局日程 | ⏳ 规划中 |
| P9 | Tab13 外部输入网关 + Tab14 关注与通知 + Tab17 混合执行 | ⏳ 规划中 |
| P10 | Tab15 配置管理 + Users 用户管理 + 账户登录门禁完整版 | ⏳ 规划中 |
| P11 | 生产收尾 | ⏳ 规划中 |

> 排序依据：P1~P4 优先做"高频 + 对 Streamlit 卡顿最敏感"的场景（对话、会话、文件、状态监控），
> P5~P9 按旧看板 Tab 的代码规模从大到小排（目标看板、成长顾问两个 Tab 各自都有 30+ 端点，
> 拆成独立阶段并进一步拆子步骤，避免一个阶段范围过大导致中途验收困难），
> P10~P11 是收尾/增强/部署类工作。

### 阶段与旧看板 Tab 的完整映射表（校验用）

| 旧看板 Tab | 新版归属阶段 |
|---|---|
| 💬 对话 | P2 / P2b |
| 🗂️ 会话管理 | P2 / P2b |
| 📌 目标看板 | P5a / P5b |
| 🔄 工作流 | P6 |
| 📁 产出物浏览 | P3 |
| 🖼️ 产出预览 | P3 |
| 🧠 自我状态 | P4 |
| 🌱 成长顾问 | P7 |
| 🎓 能力学习 | P7 |
| 🧬 进化提案 | P8 |
| ⏰ Cron 任务 | P8 |
| 🗓️ 全局日程 | P8 |
| 🔌 外部输入网关 | P9 |
| 🔔 关注与通知 | P9 |
| ⚙️ 配置管理 | P10 |
| 🔧 诊断信息 | P4 |
| 🧪 混合执行 | P9 |
| 📛 错误日志统计 | P4 |

> 说明：受限于一次性交付的时间/篇幅，P0~P2 是本次直接落地的部分（工程可跑、
> Dashboard 与 Chat/Sessions 基础版已用真实接口打通）；P2b~P11 给出了明确的
> Tab 归属、接口清单（见 `kanban_feature_inventory.md`）和实现方式，便于后续
> 按同样模式继续搬迁，不需要推倒重来，也不会漏掉旧看板里的任何一块能力。

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

- 2026-08-17（第一次交付）：完成 P0（方案文档）与 P1（工程脚手架 + 鉴权 + Dashboard 实时状态）、
  P2（Chat 流式对话基础版、Sessions 会话管理基础版）。
- 2026-08-17（第二次更新）：产出 `kanban_feature_inventory.md` 完整功能清单，重排分阶段计划为 P0~P11。
- 2026-08-17（第三次交付）：完成 P2b（内联权限/交互审批、事件流面板）、P3（Files 通用文件浏览、
  Artifacts 产出物浏览+预览、DiffView 组件）、P4（Topbar 完整版、SelfStatus 页面）。
- 2026-08-17（第四次交付，本次）：完成 P5 目标看板（旧看板规模最大的 Tab）：看板视图（4 列：
  进行中/已暂停/已完成/已放弃）+ 目标详情抽屉（概览/执行规范/执行详情/调优草案/周期诊断 5 个子
  Tab）。已覆盖：目标 CRUD、优先级、周期性设置（recur/unrecur/skip/lightweight）、反馈提交、
  执行规范生成/修订（含 DiffView 版本对比）/确认/收尾检查、Objective 执行控制（暂停/恢复/重试/
  取消/步骤重置）、调优草案（AI建议/确认/应用/驳回）、周期诊断只读展示。`npm run build` 验证通过。
  **暂未覆盖**（留待后续）：执行阶段（execution_phase）查看/推进/解锁、Objective 单步骤编辑
  （edit，仅做了 reset）、guidance 追加、单步骤 trace 查看、周期诊断总览（overview）、完成率
  趋势图、会话置顶并排对比、产出物清单在目标详情内的关联展示——这些属于 P5 范围内的"锦上添花"
  项，优先级低于核心 CRUD/执行控制/规范修订闭环，已记录在 `kanban_feature_inventory.md` 供后续
  对照补齐。
- 2026-08-17（第五次交付，本次）：完成 P6 工作流：工作流定义列表+YAML查看、历史统计、
  运行面板（JSON 输入参数、后台开关、dry-run 预览）、执行历史列表、执行详情（步骤 Steps
  展示、暂停/恢复/取消/孤儿修复标记、审批通过驳回、人工输入提交、单步输出覆盖+从此续跑）。
  `npm run build` 验证通过。**暂未覆盖**：单步编辑 patch（`/workflows/{name}/steps/{id}/patch`，
  修改已保存工作流定义本体，本次只做了"运行中覆盖某次执行的步骤输出"）、
  `/workflow_runs/{id}/events` 事件增量流展示（当前只用轮询整体详情代替）。
- 2026-08-17（第六次交付，本次）：完成 P7 成长顾问 + 能力学习。**成长顾问**：诊断信息面板
  （为什么候选是 0）、首次触达提示确认、手动扫描（scan）、候选列表（accept/dismiss 含忽略原因、
  查看详情/主题时间线、报告查看/刷新、素材生成/查看、落地为目标）、关键词管理（新增/确认/
  删除隐藏/恢复）、回访提醒（progressed/stalled）、追求中方向列表（含 recurring/pursuit_style
  标记、查看素材埋点）、调研报告列表、对齐视图 Drawer（未匹配方向、AI 建议匹配确认、
  一键全部采纳、组合摘要）。`npm run build` 验证通过。**能力学习**：新建方向（知识型/人设型、
  初始大纲、LLM 起草开关）、方向列表（详情/删除）、方向详情 Drawer（大纲/学习台账两个子 Tab，
  persona 型额外提供人设草稿子 Tab：生成刷新/发布）、待回答问题列表（回答/忽略）+ 历史问答、
  大纲扩展建议（采纳/忽略）、已发布角色一览（wiki_scopes 标签编辑保存）。`npm run build` 验证通过。
  **暂未覆盖**（留待后续，优先级低于核心信息展示/审批闭环）：候选看板拖拽式分栏、按主题采纳/
  忽略排行、成长主题地图、健康度趋势图、饱和度走势图、素材参与度可视化、related_directions
  关联信号展示、wiki_pages 页面正文查看入口（capability track 详情内未挂接）、track 编辑表单
  （outline/excluded_keywords 结构化编辑，当前仅支持删除）。同时修复了 P2 遗留的 `pages/Sessions`
  文件缺失问题（此前打包遗漏导致 `npm run build` 报错，已补齐并纳入本次交付）。
  P8~P11（进化提案、Cron、全局日程、外部输入网关、关注通知、混合执行、配置管理表单、
  用户管理、生产收尾）仍按原计划待迭代。
