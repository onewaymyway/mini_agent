# 产出项目介绍信息文件 + 看板全局浏览

> 状态：**已实施**。承接 `next_doc/output_projects_root_and_git_isolation_
> plan.md`（已给"调研/产出类项目"提供了统一的落脚根目录
> `output_projects_root`）。

## 0. 背景

用户诉求（简述）：

1. 每个产出项目应该有一份介绍信息，保存在特定名字的文件里面。
2. 在看板中增加相关功能，可以看到全局的产出项目目录下有哪些项目，以及
   项目的介绍信息。

看板确认为 Streamlit 看板（`apps/mini_agent_kanban/`），不是
`apps/mini_agent_kanban_x/`（后者是另一个独立的、尚在早期的 React 前端，
本次不涉及）。

## 1. 方案设计

### 1.1 介绍信息文件

固定文件名：`PROJECT_INFO.md`，放在每个产出项目目录**根**下：

```
<output_projects_root>/<项目名>/PROJECT_INFO.md
```

- 文件名固定（`evolution/output_projects_root.py::PROJECT_INFO_FILENAME`），
  方便代码/prompt 双向约定，不需要用户/agent 每次商量放哪。
- 内容格式不强制——纯文本/Markdown 均可，看板只做"有就展示、没有就
  提示缺失"，不做任何格式校验/解析（不强加 frontmatter 之类的结构，
  维持 `output_projects_root_and_git_isolation_plan.md` §6"不做目录
  内部结构规范"的既有取舍）。
- 通过 `output_path_policy.md` 新增第7条规则要求：agent 按第6条新建
  产出项目时必须创建这份文件，简要说明项目是做什么的、包含哪些主要
  文件/如何运行；后续对项目有实质性更新时顺手更新。

### 1.2 看板功能

Streamlit 看板新增一个只读 Tab「🗺️ 产出项目」：

- 列出 `output_projects_root` 下的一级子目录（每个视为一个产出项目），
  按最近修改时间倒序排列。
- 每张卡片展示：项目名、路径、最近修改时间；`PROJECT_INFO.md` 存在则
  可展开查看内容（超长自动截断），不存在则给出明显警示（方便发现历史
  遗留的、没按新规则创建介绍文件的产出）。
- 不提供新建/删除/注册按钮——这类目录本身定位就是"建了就是建了、不
  需要注册"的轻量产出，跟「🗂️ 外部项目」tab 管理的重量级注册项目
  （`project.yaml` 契约 + daemon 调度）是两个不同定位，不做混合。

数据链路：新增只读 API 端点 `GET /v1/output_projects`，扫描
`output_projects_root` 并读取每个子目录下的 `PROJECT_INFO.md`（存在则
截断到固定字符数上限），返回给看板渲染。

## 2. 落地涉及的模块

| 模块 | 改动内容 |
|---|---|
| `src/mini_agent/evolution/output_projects_root.py` | 新增 `PROJECT_INFO_FILENAME` 常量、`project_info_path()`、`list_projects(cfg_or_project_root) -> list[dict]`（扫描一级子目录 + 读取介绍文件摘要） |
| `src/mini_agent/evolution/output_path_policy.py` | `DEFAULT_POLICY` 追加第7条规则；新增 `_RULE_7_MARKER`/`_RULE_7_TEXT`，复用已有的 `_append_rule_if_missing()` 做老文件追加式迁移 |
| `src/mini_agent/api/routes.py` | 新增只读端点 `GET /v1/output_projects` |
| `apps/mini_agent_kanban/client.py` | 新增 `AgentClient.output_projects()` |
| `apps/mini_agent_kanban/app.py` | 新增 `render_output_projects_tab()`，接入 `_BASE_TAB_DEFS`（紧跟在「🗂️ 外部项目」之后） |
| `docs/output-projects-root-guide.md` | 新增 §6/§7 说明介绍信息文件约定 + 看板功能 |
| `docs/kanban-dashboard-guide.md` | Tab 总览表格新增一行 + 新增「🗺️ 产出项目 Tab」专节 + `AgentClient` 端点表新增一行 |

## 3. 非目标

- 不做介绍信息文件的格式校验/解析（不强制 frontmatter，不提取字段）。
- 不做新建/编辑/删除产出项目的看板操作入口——纯只读浏览。
- 不做旧数据迁移——本次改造上线之前已经存在、没有 `PROJECT_INFO.md`
  的产出项目不会被自动补建介绍文件，只在看板上明显提示缺失。
- 不递归扫描更深层级目录——只看 `output_projects_root` 的一级子目录，
  与"每个产出项目是一个独立顶层目录"的既有设计假设一致。

## 4. 实施记录

- `evolution/output_projects_root.py`：新增 `list_projects()`，对
  `resolve_root()` 结果做 `iterdir()` 扫描，单个子目录处理异常时静默
  跳过、不影响其余条目返回；介绍文件内容超过 2000 字符做截断
  （`intro_truncated=True`），避免一份很长的介绍信息把列表页撑爆。
  新增测试 `tests/test_output_projects_root.py` 补充 4 个 `ListProjects`
  相关用例（根目录不存在返回空列表、正常列出含/不含介绍文件的项目、
  忽略非目录条目、超长介绍内容截断），全部通过。
- `evolution/output_path_policy.py`：第7条规则文本 + 独立的
  `_RULE_7_MARKER`/`_RULE_7_TEXT`，`ensure_policy_file()` 对老文件依次
  检查第6条、第7条是否缺失（两条规则独立判定、独立追加，互不影响，
  覆盖"老用户已经手动/自动迁移过第6条但还没有第7条"的场景）。既有
  `tests/test_output_path_policy_stage3.py` 全部继续通过（未受影响）。
- `api/routes.py`：`GET /v1/output_projects` 复用
  `http_server.bridge.agent.cfg` 取 `AppConfig`，走
  `output_projects_root.resolve_root()`/`list_projects()`；`cfg` 拿不到
  时返回空结果而非报错，风格对齐同文件里其余"自省类"只读端点。
- `apps/mini_agent_kanban/`：`client.py` 加一行 `_get("/output_projects")`
  封装；`app.py` 新增 `render_output_projects_tab()`，接入
  `_BASE_TAB_DEFS`，紧跟在「🗂️ 外部项目」之后，与文档 Tab 总览表格顺序
  一致。
- 手动/静态验证：`python -c "from mini_agent.api import routes; ..."`
  确认 `GET /v1/output_projects` 路由已正确注册；`ast.parse()` 确认
  `routes.py`/`output_projects_root.py`/`output_path_policy.py`/
  `client.py`/`app.py` 五个改动文件语法均无误。
