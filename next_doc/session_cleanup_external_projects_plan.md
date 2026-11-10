# 会话清理支持外部项目 — 设计方案

> 状态：**已确认并实施完成**（实施记录见 `session_cleanup_design.md` §11）。
> 确认结论：看板范围既支持单选一个范围、也支持「全部外部项目」一键清理（Q1 两者都做）；
> 新增要求——清理 session 时同步清理它对应的 `raw_results/<session_id>/` 目录。
> Q2（抽取门槛）、Q3（CLI/cron）未明确答复，按推荐值执行：外部项目首版强制不抽取；
> 加 CLI `--project` / `--all-external-projects`，cron `sys:session_cleanup` 不变。
> 额外加固：agent 经 `run_slash_command` 不能触发外部项目范围的清理。
> 关联文档：`next_doc/session_cleanup_design.md`（会话清理原方案）、
> `next_doc/external_projects_workspace_plan.md`（外部项目 Workspace）

## 1. 背景与现状

看板「🗂️ 会话管理」里的「🧹 批量清理旧会话」目前只能清理**主 agent** 的
会话（`<主项目>/.agent/sessions/`，多用户模式下是 `.agent/users/<id>/sessions/`）。

已注册的外部项目（`~/.agent/external_projects.json`，`ExternalProjectRegistry`）
每个都有**自己的** `<项目根>/.agent/sessions/`（`Workspace.sessions_dir` →
`AgentPaths(root).sessions_dir`），目录格式与主项目完全相同
（`meta.json` / `history.json` / `raw_history.jsonl` / `goal_state.json` …）。
外部项目的 cron / workflow / goal 执行同样会不断产生会话目录，目前没有任何
入口能清理它们，只能手工去磁盘上删。

## 2. 关键发现：核心判定逻辑无需改动

`evolution/session_cleanup.py::cleanup_sessions(session_manager, project_root, ...)`
本身已经是**参数化**的：

- 会话目录由传入的 `SessionManager` 决定；
- goal 保护判定（`_running_goal_session_ids`）按传入的 `project_root` 扫描
  `<project_root>/.agent/sessions/*/goal_state.json`；
- 孤儿目录扫描同样基于 `session_manager.session_dir`。

只要把「外部项目的 `SessionManager(project_root=<项目根>)` + `project_root=<项目根>`」
传进去，pin / goal 在跑 / 最近 N 个 / 最近 N 天 / 孤儿目录年龄安全网 / 知识抽取门槛
全部原样生效。**写死主项目的是 API 层（`routes.py::cleanup_sessions_endpoint`）
和看板层**，改动集中在这两层，不重写判定规则。

## 3. 设计

### 3.1 后端 API

`POST /v1/sessions/cleanup` 请求体新增**可选**字段：

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `project` | `str \| null` | `null` | 已注册外部项目的**注册名**。`null` = 主 agent（与现有行为完全一致） |

响应新增 `project`（回显）和 `sessions_dir`（实际清理的目录，便于看板核对"清的是哪儿"）。

路径解析规则（安全相关，重点）：

1. **只接受注册名，不接受客户端传路径**。服务端用 `ExternalProjectRegistry().get(name)`
   解析出项目根，再算 `AgentPaths(root).sessions_dir`。名字未注册 → 404。
   这样客户端无法借这个接口让服务端删除任意目录。
2. 项目根目录在磁盘上不存在 / `sessions_dir` 不存在 → 返回明确错误或空报告
   （不自动创建目录；注意 `SessionManager.__init__` 会 `mkdir`，所以先判断存在再构造）。
3. 仍然 `_require_owner`——外部项目是全局资源，只有 owner 可操作。
4. 删除仍然只走 `SessionManager.delete()` / 孤儿目录 `shutil.rmtree`，沿用原有安全设计。
5. `enabled`（是否开启自动调度）与清理无关：**已注册但未启用的项目也可以清理**。
6. `exclude_ids`：外部项目的会话不由 daemon 的主 bridge/session pool 持有，没有
   "当前激活会话"的概念，传空集；"正在被执行的会话"靠已有安全网兜底
   （`goal_state.json` running/stuck、最近 N 个、最近 N 天、孤儿目录 6 小时最小年龄）。

新增一个只读辅助端点，供看板下拉框用：

- 复用现有 `GET /self/external_projects`（`client.external_projects_status()`）取注册名列表，
  **不新增端点**；若该接口字段不足再补，实施时确认。

### 3.2 `extract_first`（知识抽取）

外部项目会话的知识抽取会走 `history_manager.dispatch_extraction_for_entries()` +
当前 owner 的 LLM client/cfg，抽取结果会写进**主项目**的 wiki/记忆，而不是外部项目自己的——
这个归属对外部项目不一定正确。**首版保守处理**：

- `project != null` 时 `extract_first` 强制按 `False` 处理（请求里传了 True 也忽略，
  并在响应 `summary` 里注明"外部项目首版不支持先补抽取"）；
- 因此外部项目里"turns≥3 且 `knowledge_extracted != true`"的会话本次会进
  「待抽取，本次跳过」，**不会被删**。这是保守方向的行为（宁可少删）。

> 待确认 Q2：这个保守处理你是否接受？如果外部项目的会话大多没有
> `knowledge_extracted` 标记，首版可能清不掉多少；备选是对外部项目放宽为
> "不看抽取门槛，只看时间/保护规则"（opt-in 勾选），见 §6。

### 3.3 看板

「🧹 批量清理旧会话」面板顶部新增「清理范围」选择：

- 默认 `主 agent`（现有行为，默认值不变——保守 opt-in）；
- 其余选项为已注册外部项目名（带路径提示 `st.caption`）；
- 选外部项目时：`先补跑知识抽取` 勾选框置灰并提示"外部项目首版不支持"；
- 预览/确认执行沿用现有两步流程；预览结果顶部显示"范围：<项目名> · 目录：<sessions_dir>"，
  防止用户误以为清的是主 agent；
- 切换范围时清掉已有预览（`session_cleanup_preview`）和确认勾选，避免拿 A 项目的预览
  去确认执行 B 项目。**执行请求带的是当前选择的范围，并且服务端按该范围重新扫描**
  （与现有"确认时重新 dry_run=False 扫描"一致，不依赖预览结果）。

会话列表（「🗂️ 会话管理」上半部分）与 pin/unpin **首版不扩展**到外部项目，
只做清理（你的诉求）。外部项目会话若 `meta.json` 里已有 `pinned=true`，清理时照常受保护。

### 3.4 CLI / cron（可选，见 Q3）

- `/session cleanup --project <name>`：同一套 `cleanup_sessions`，路径解析同 §3.1；
- 内置 cron `sys:session_cleanup` **首版不改**（仍只清理主 agent）。外部项目清理不自动
  定时，避免默认行为突变。

## 4. 实施落点

| 文件 | 改动 |
|------|------|
| `src/mini_agent/evolution/session_cleanup.py` | 新增 `resolve_external_project_session_env(name)`：注册名 → `(SessionManager, project_root, sessions_dir)`，含未注册/目录缺失的错误类型；`cleanup_sessions` 本体不改 |
| `src/mini_agent/api/models.py` | `SessionCleanupRequest.project`；`SessionCleanupResponse.project` / `sessions_dir` |
| `src/mini_agent/api/routes.py` | `cleanup_sessions_endpoint` 在 owner 校验后按 `project` 分支取 `(mgr, project_root, exclude_ids)`，其余流程共用；外部项目强制 `extract_first=False` |
| `apps/mini_agent_kanban/client.py` | `cleanup_sessions()` 新增 `project` 参数 |
| `apps/mini_agent_kanban/app.py` | `_render_session_cleanup_panel` 新增范围选择、切换范围清预览、结果顶部范围提示 |
| `src/mini_agent/cli/commands/sessions.py` | （Q3 选是）`--project <name>` |
| `docs/http-api-guide.md`、`docs/kanban-dashboard-guide.md`、`next_doc/session_cleanup_design.md` | 补充外部项目范围说明（§11 追加） |
| `tests/test_session_cleanup_external_projects.py`（新增） | 见 §5 |

## 5. 测试计划

1. 构造临时注册表（`ExternalProjectRegistry(store_path=tmp)`）+ 临时外部项目目录，
   在其 `.agent/sessions/` 下造：近期 / 过旧小会话 / 过旧大会话未抽取 / pinned /
   goal running / 孤儿目录。验证 `project=<name>` 时判定结果与主项目同规则一致。
2. **隔离性**：同时存在主项目 sessions 与外部项目 sessions，清理其一，另一方的目录**逐一原样保留**。
3. 未注册名 → 404；注册了但项目根已不存在 → 明确错误，不创建目录。
4. 非 owner 调用 → 拒绝。
5. 外部项目 + `extract_first=True` → 被忽略，未抽取的会话进"待抽取跳过"，不被删。
6. 不传 `project`（旧调用方）→ 行为与改动前完全一致（回归）。
7. 路径穿越：`project="../x"` 之类 → 因按注册名查表，直接 404。
8. 看板 `app.py` 做 `ast.parse` 静态检查；如实记录没有 Streamlit 运行时/浏览器级烟测（此前几个看板改动一致的限制）。

## 6. 待确认问题

- **Q1 范围粒度**：看板里是「单选一个范围（主 agent / 某个外部项目）」（推荐，最保守、预览清晰），
  还是增加「全部已注册外部项目」一键批量？
- **Q2 知识抽取门槛**：外部项目首版 `extract_first` 强制关闭（推荐，保守），
  未抽取的大会话会被跳过；还是给外部项目增加一个 opt-in 勾选
  "忽略知识抽取门槛（这些会话的知识不需要抽取）"？
- **Q3 CLI / cron**：是否同时加 `/session cleanup --project <name>`（推荐加，改动很小）？
  cron `sys:session_cleanup` 保持只清理主 agent（推荐），还是后续可配置覆盖外部项目？

## 7. 风险与取舍

- 误删风险：集中在"外部项目正在跑的会话"。依赖既有安全网（goal 状态 / 最近 N 个 /
  最近 N 天 / 孤儿 6 小时），与主项目同等强度；预览→确认两步流程不变。
- 单个外部项目会话量很大时，扫描耗时 → 沿用 `run_blocking` 线程池 + 30s 超时，不阻塞事件循环。
- 向后兼容：`project` 缺省即旧行为；新增响应字段均有默认值。
