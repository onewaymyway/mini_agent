# Mini Agent 看板 X（React SPA 版）

> **⚠️ 当前状态（2026-08 更新）：本项目是实验/试点看板，尚未成为日常使用的看板。**
> **`apps/mini_agent_kanban`（Streamlit）才是当前真正在用的看板，今后的看板相关
> 改动（bug 修复、功能调整）一律只改 Streamlit 一侧，除非用户明确要求同步到这里。**

用 `Vite + React + TypeScript + Ant Design + TanStack Query` 重构的新一代看板，
替代基于 Streamlit 的 `apps/mini_agent_kanban`（保留、不影响旧版继续使用）。

后端 100% 复用现有 mini-agent HTTP daemon（`src/mini_agent/api/routes.py`），
本目录只是一个纯前端工程。

完整方案设计见：`../../next_doc/kanban_react_spa_replacement_plan.md`
完整功能清单（旧看板 18 个 Tab 逐一梳理）见：`../../next_doc/kanban_feature_inventory.md`

## 当前完成度（P0~P11 已全部交付）

阶段划分覆盖旧看板全部 18 个 Tab，完整表格见方案文档第 5 节
"分阶段实施计划"，此处只列进度摘要：

| 阶段 | 覆盖范围 | 状态 |
|---|---|---|
| P0 | 方案设计文档 + 功能清单 | ✅ |
| P1 | 工程脚手架、鉴权（Token 登录）、布局、Dashboard 状态总览（含轮询） | ✅ |
| P2 | 💬 对话（基础流式收发）、🗂️ 会话管理（基础增删查） | ✅ |
| P2b | 对话内联权限/交互审批、事件流面板 | ✅ 已完成；会话置顶/并排对比/变化提醒仍待做 |
| P3 | Files 通用文件浏览、📁 产出物浏览、🖼️ 产出预览、DiffView 组件 | ✅ |
| P4 | Topbar 完整版（调度暂停恢复/权限交互提醒/Sentinel/全局待办）、🧠 自我状态、🔧 诊断信息、📛 错误日志统计 | ✅ |
| P5 | 📌 目标看板（看板视图+详情抽屉：CRUD/周期性/反馈/执行规范/执行控制/调优草案/周期诊断） | ✅ 核心能力已完成，见下方"已知限制" |
| P6 | 🔄 工作流（定义查看/运行面板/dry-run预览/历史统计/执行详情/运行控制） | ✅ 核心能力已完成，见下方"已知限制" |
| P7 | 🌱 成长顾问、🎓 能力学习 | ✅ |
| P8 | 🧬 进化提案、⏰ Cron 任务、🗓️ 全局日程 | ✅ |
| P9 | 🔌 外部输入网关、🔔 关注与通知、🧪 混合执行 | ✅ |
| P10 | ⚙️ 配置管理、Users 用户管理、账户登录门禁核对 | ✅ |
| P11 | 生产构建接入 FastAPI 静态资源挂载、代码分割优化、部署文档收尾 | ✅ |

至此旧看板 18 个 Tab + 4 个全局模块已全部在新版 SPA 中落地（核心 CRUD/审批闭环覆盖，
个别"锦上添花"项延后，见下方"已知限制"）。

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

## 生产构建与部署（P11）

```bash
npm run build      # 产出 dist/，生产模式下 Vite base 已设为 /kanban/
npm run preview    # 本地预览生产构建（默认 http://localhost:4173）
```

**推荐部署方式：daemon 同进程挂载（已自动接入，无需手动改代码）**

`src/mini_agent/api/server.py::create_app()` 在应用初始化时会检测
`apps/mini_agent_kanban_x/dist/` 是否存在：存在则自动用 `StaticFiles`
把它挂载到 `/kanban` 路径下（与 API 同源，天然免 CORS，不需要额外的
Nginx/反向代理）；`dist/` 不存在时静默跳过，不影响 daemon 正常启动
（纯 API 部署、或尚未执行 `npm run build` 时都不受影响）。也就是说：

```bash
cd apps/mini_agent_kanban_x
npm install
npm run build            # 产出 dist/
cd ../..
python -m mini_agent.cli.app daemon start
# 浏览器打开 http://<daemon-host>:8765/kanban 即可访问新看板，
# 无需再单独起一个前端进程
```

前端相应地做了两处适配，保证子路径挂载下路由/资源都能正确解析：
- `vite.config.ts`：生产构建 `base: "/kanban/"`（开发模式仍是 `/`，不受影响）；
- `src/main.tsx`：`BrowserRouter` 在生产环境下设置 `basename="/kanban"`，
  确保刷新 `/kanban/goals` 之类子路径不会 404（`StaticFiles(html=True)`
  会把未命中的路径 fallback 到 `index.html`，交给前端路由接管）。

**独立部署（可选）**：也可以完全独立部署（例如 Nginx / Vercel 之类静态托管），
此时把 `.env` 里的 `VITE_API_BASE` 指向 daemon 的完整地址
（如 `https://your-domain/v1`），并确保 daemon 侧允许对应来源的 CORS；
独立部署场景下不需要 `basename`/子路径挂载这套逻辑，按静态站点根路径部署即可。

**新旧并存**：本次改造全程不修改、不下线 `apps/mini_agent_kanban`
（Streamlit 版），两者可同时指向同一个 daemon 独立运行；`/kanban` 是新增的
独立挂载点，不影响旧版任何现有访问路径。是否下线旧版本留给使用者在验证
新版功能对等后自行决定，方案文档不代为决定。

## 生产构建体积优化（P11）

- 路由级代码分割：除登录页/首屏 Dashboard/高频对话页外，其余全部页面
  用 `React.lazy` + `Suspense` 按路由懒加载（见 `src/App.tsx`），只有访问到
  对应 Tab 时才下载该页面的 JS chunk。
- 第三方依赖单独分包：`vite.config.ts` 的 `build.rollupOptions.output.manualChunks`
  把 `react`/`react-dom`/`react-router-dom`、`antd`/`@ant-design/icons`、
  `@tanstack/react-query` 拆成独立的 vendor chunk，与业务代码分开缓存——
  这几个依赖的升级频率远低于业务页面代码，拆开后浏览器可以长期复用这块缓存。
- 拆分后仍有 `vendor-antd` 单个 chunk 超过 500KB（gzip 后约 320KB）的构建提示，
  这是 antd 组件库本身的体积决定的，进一步拆分收益有限（组件库内部耦合），
  本阶段不做进一步优化；如未来需要，可以评估换用更细粒度的按需引入方案。

## 目录结构

```
src/
├── api/          # 后端 HTTP/SSE 封装（client.ts / endpoints.ts / sse.ts / types.ts）
├── stores/       # zustand 全局状态：鉴权、UI（当前 session 等）
├── hooks/        # 业务 hooks：按功能域拆分，一个 Tab 对应一个 use*.ts
├── layouts/      # 页面整体布局（侧边栏 + 完整 Topbar：调度控制/权限提醒/Sentinel/全局待办）
├── pages/        # 各功能页面，一个目录对应旧看板一个（或多个合并的）Tab
└── components/   # 可复用 UI 组件：PermissionsPanel（内联审批）、EventTimeline（事件流）、DiffView
```

## 已知限制 / TODO

以下均为优先级较低的"锦上添花"项，不影响核心 CRUD/审批闭环，留给后续按需补齐：

- **目标看板（P5）**：执行阶段（execution_phase）查看/推进/解锁、Objective 单步骤
  编辑（目前只做了"重置"）、guidance 追加、单步骤 trace 查看、周期诊断总览
  （cycle_diagnostics_overview，目前只做了单目标诊断）、完成率趋势图、会话置顶并排对比。
- **工作流（P6）**：单步编辑 patch（修改已保存工作流定义本体，目前只做了
  "运行中覆盖某次执行的步骤输出"）、`/workflow_runs/{id}/events` 事件增量流展示
  （当前用轮询整体执行详情代替）。
- **成长顾问 + 能力学习（P7）**：候选看板拖拽式分栏、按主题采纳/忽略排行、成长主题
  地图、健康度/饱和度趋势图、素材参与度可视化、track 编辑表单（outline/
  excluded_keywords 结构化编辑，当前仅支持删除）。
- **外部输入网关（P9）**：来源健康趋势图（`health_history` 端点已接入但未做可视化）、
  归档查询面板（`archive/query` 仅接入 endpoint，未做查询表单 UI）。
- **登录门禁**：后端目前只有 Bearer Token 鉴权，没有独立的用户名+密码换 Token 端点；
  P1 的 Token 登录页是当前后端能力下的完整实现。如果未来后端补充账号密码登录接口，
  再补齐 SPA 侧对应表单。
- Files 页面的 `fs/write` 目前假定文件是文本；对二进制文件会读取失败并提示，
  这与旧看板行为一致（旧看板同样只处理文本类文件的在线编辑）。
