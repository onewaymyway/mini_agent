# 自动关联产出项目 — 实施记录 S6：文档与打包

> 对应计划：`goal_output_project_auto_link_plan.md` 第 10 章 S6
> 前序记录：`_s1.md` 至 `_s5.md`
> 状态：**文档已完成**；真实环境端到端验证仍未做（见第 4 节）

---

## 1. 交付内容

| 文件 | 类型 | 说明 |
|---|---|---|
| `docs/kanban-dashboard-guide.md` | 修改 | 目标树新增「🌳 目标树：产出关联」小节；「🧰 产出项目管理」由四个子区改为五个，补「🔗 待确认关联」；巡检行补充超时提示；「AgentClient 封装的 API 端点」表新增 6 个方法 |
| `docs/http-api-guide.md` | 修改 | 新增「/v1/output_registry/pending_links 与 /v1/goals/tree/output_links」小节：端点、确认门槛、请求示例、错误码、批量响应结构、三个配置键 |
| `next_doc/goal_output_project_auto_link_impl_s6.md` | 新增 | 本文件 |
| `next_doc/goal_output_project_auto_link_plan.md` / `next_doc/README.md` | 修改 | 状态与索引 |

## 2. 文档中写明的行为（已对照实现核对）

- 确认、手动关联、解除需要 `confirm=true`；忽略不需要。对应实现与测试：`tests/test_output_registry_pending_routes.py`。
- 错误码：缺 confirm / 角色非法 / 节点不存在 / 忽略或解除不存在的条目 → 400；确认时条目不存在 → 404；手动关联时目标树不可读 → 503。对应测试中的各个断言。
- `GET /v1/goals/tree/output_links` 的响应结构与 `tree_links.py` 一致；`goals.json` 不可读时返回带 `error` 的空结构而非 500。对应 `test_unreadable_goals_returns_error_not_exception`。
- 第一层工具只在 `auto_link_tool_enabled=true` 时出现在 agent 的工具列表中；第二层检测只在 `auto_link_detect_enabled=true` 时运行。对应 S2、S5 的门控测试。
- 三个配置键修改后需重启 daemon：执行器与巡检在进程内读取配置，文档按此表述。这一点是根据代码结构判断的，未在运行中的 daemon 上实测。

## 3. 文档修正记录

编写过程中发现并修正了一处表述：最初写的是"配置键均可热读"，但没有任何代码或测试证明这一点，因此改为"修改后需重启 daemon 才生效"。

## 4. 未完成的验证（需在可运行环境中补做）

以下事项需要真实 daemon 与浏览器，本环境无法完成：

1. 手动 curl 调用 `pending_links` 各端点（S3 退出条件）。
2. 实际运行一个会建目录的 objective，确认待确认项出现在「🔗 待确认关联」中，并能确认、忽略（S5 退出条件）。
3. 在浏览器中展开「⚙️ 管理」，点击确认、解除、手动关联，确认目标树徽标随之变化（S4 退出条件）。
4. 全量测试套件的原包/改后包失败集对比（本环境单核，全量耗时过长，只跑了相关的 36 个文件）。

## 5. 打包

累积补丁包 `auto_link_S1-S6_patch.zip` 包含 S1 至 S6 的全部改动与新增文件，目录结构与原始上传包一致（以 `mini_agent-master/` 为根），解压到项目父目录即可覆盖。每个阶段的 zip 都替代前一个，无需逐个叠加。

## 6. 计划收尾

第 10 章 S1–S6 的代码、测试与文档均已完成。计划第 11 章"与理念文档的对齐检查"和第 12 章三点决议已在计划文档中记录。剩余工作仅为第 4 节中的真实环境验证。
