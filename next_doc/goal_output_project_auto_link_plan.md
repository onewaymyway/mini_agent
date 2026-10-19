# Goal 执行期自动关联产出项目 — 计划

> 状态：**计划（待确认后实施）**
> 上位文档：`management_output_project_model_plan.md`（§5.5 关联、§8.3 产出路由、§8.5 游离项目、§12 已决议）
> 关联实施记录：`management_output_project_model_impl_p2.md`（关联）、`_p4.md`（路由）、`_p7.md`（看板管理区）
> 约束：保守、默认关闭；不改动现有注册表数据结构；待确认关联**不参与**产出路由。

---

## 1. 问题

计划 §12 已决议"agent 可在无管理项目时自动创建游离项目，后续可绑定或派生管理项目"，
但代码里没有任何执行期触发点：

- `register_project` / `link_management_output` / `derive_management_from_project`
  只被命令行脚本、`lifecycle`、看板"接入"按钮调用；
- goal 执行时 agent 按 `output_path_policy` 规则 6 在 `output_projects_root/<name>/` 下建目录，
  之后没有任何环节登记它，更不会和当前 goal 关联；
- 项目只能事后由巡检发现为"未登记/游离"，再由人手动 `link`；
- 目标树界面看不到节点对应的产出项目，也没有任何关联确认入口。

结果：计划 §10 "产出可找到率接近 100%" 在现状下无法达成。

## 2. 总体方案

两层机制 + 一个确认界面：

| 层 | 机制 | 角色 |
|---|---|---|
| 第一层（主） | 执行期内置工具 `link_output_project`：agent 创建/使用项目时，带着执行上下文里的 goal 信息主动关联 | 主路径，关联来源 `agent_tool` |
| 第二层（补救） | 执行结束后的目录差异检测：发现本轮新增、且未被第一层关联的项目目录，生成待确认关联 | 补救，关联来源 `auto_detect` |
| 确认界面 | 目标树节点显示已关联/待确认项目，可就地确认；「🧰 产出项目管理」同步提供「待确认关联」汇总 | 用户确认后才生效 |

**触发范围**：只处理周期性 goal（cron goal cycle）和 objective 的执行；普通会话产出不在本计划内。

## 3. 数据模型：待确认关联账本

不动现有 `_registry/index.json` 与分表结构（零破坏），新增独立文件：

```
<output_projects_root>/_registry/pending_links.json
```

每条记录：

```
id              string   稳定 id
slug            string   产出项目目录名
goal_id         string   触发关联的 goal/objective 节点 id
management_id   string   解析出的管理项目根（见 §4.3）
role            "build" | "iterate" | "reference"
origin          "agent_tool" | "auto_detect"
state           "pending" | "rejected"        （确认后记录从账本移除并写入正式关联）
note            string   agent 给出的一句话理由（auto_detect 为空）
created_at      float
```

- **确认**：先 `mark_management_root(management_id)`（若尚未标记），再 `link_management_output(...)`，
  最后从账本删除该条。标记管理根的副作用延后到确认时才发生。
- **忽略**：`state=rejected` 保留，同 `(slug, goal_id)` 不再重复生成。
- **路由**：`routing.py` 只读正式关联，不读账本——待确认关联不影响任何产出路径。
- 账本文件可删除，不丢失任何已确认关联；写入沿用 `ExclusiveFileLock` + 原子写。

## 4. 第一层：执行期内置工具

### 4.1 执行上下文透传

沿用现有 thread-local 模式（`perception/turn_context.py`、`evolution/cron_context.py`）：

- 新增 `evolution/goal_exec_context.py`：`set/clear/get_current_goal_exec(goal_id, kind)`；
- 周期性 goal：`goal_cron_bridge._fire_goal_cycle` 创建本轮执行前设置，`reap_finished_cycles` 收尾清除；
- objective：`ObjectiveExecutor.start` 执行前设置，完成/失败回调清除；
- 未设置时（交互对话、CLI）工具返回 `{"ok": false, "error": "当前不在 goal 执行上下文中"}`，不报异常。

### 4.2 工具 `link_output_project`

放在 `tools/output_registry.py`，用 `@tool` 注册（`cli/app.py` 增加一行 import，与 `external_projects` 同模式）。

```
link_output_project(slug, role="build", note="", create=false, title="")
```

- `slug`：`output_projects_root` 下的目录名；目录不存在且 `create=true` 时，调用
  `lifecycle.create_output_project` 受管新建（只创建、不覆盖，沿用其校验）；
- `role` 默认 `build`；该项目已有**其他**管理项目的 `build` 关联时默认改为 `iterate`；拿不准一律 `build`；
- 解析管理项目根（§4.3），写入待确认账本，`origin=agent_tool`；
- 幂等：同 `(slug, goal_id, role)` 已有正式关联或待确认记录时直接返回现状；
- 返回 `{"ok": true, "state": "pending"|"confirmed", "management_id": ..., "slug": ...}`，失败 `{"ok": false, "error": ...}`；
- `requires_approval=False`（只写待确认账本，不改变任何产出路径）。

### 4.3 管理项目根的选择

从触发节点沿 `parent_id` 向上：

1. 优先取**最近的已标记管理根**（`management_roots` 中存在的祖先，含自身）；
2. 找不到则退回**最近的 `goal` 层级祖先**（含自身）；
3. 仍找不到（objective 无 goal 祖先等异常）→ 不建关联，记 `floating`，交给巡检。

### 4.4 把 goal 信息传给 agent

在 `goal_cron_bridge._append_output_workspace_context` 与 objective 提示词构造处，
追加一段"产出项目关联"提示（仅在开关开启时）：

> 本次执行属于目标 `<goal_id>`《标题》。如果你在 `output_projects_root` 下新建了独立产出项目，
> 或继续向已有项目写入，请调用 `link_output_project(slug=..., role=...)` 把它关联到本目标。
> 关联先进入待确认，由用户在目标树里确认后才影响产出路径。

提示里已经带 goal 信息，工具本身从 thread-local 读取，agent 不需要传 goal_id（避免传错）。

## 5. 第二层：执行后目录差异检测（补救）

- **快照**：执行开始前记录 `output_projects_root` 一级目录名集合（排除 `_` 开头）及各目录最近修改时间；
- **对比**：`reap_finished_cycles` / objective 完成回调里再取一次，得到：
  - 新增目录；
  - 已有目录且本轮被修改、且尚未与当前管理根建立任何关联的目录（仅记录，不自动提议，避免误伤，见 §9 风险）；
- **处理**：对"新增目录"中尚未被第一层关联/忽略的，`register_project`（只登记）+ 待确认账本
  `origin=auto_detect`；管理根解析同 §4.3；
- **去重**：与第一层共用账本幂等键；第一层已处理的不重复；
- **失败隔离**：整个检测包在 try/except 内，任何异常只写日志，不影响 cycle 收尾。

## 6. 目标树界面

### 6.1 节点标题行

在 `_render_goal_tree_node_body` 标题 markdown 末尾追加徽标，与现有 ⭐ / 💡 同风格：

- `🗂️N`：该节点（或其管理根）已确认关联的产出项目数；
- `⏳N`：待确认关联数（醒目显示，便于发现）。

### 6.2 「⚙️ 管理」区新增「🔗 关联项目」子区块

- **已关联**：每行 `slug`｜角色｜最新版本｜来源（agent_tool/auto_detect/user）｜「解除关联」；
  非管理根节点显示"继承自 <管理根标题>"，只读；
- **待确认**：每行 `slug`｜建议角色｜理由/来源｜按钮 `✅ 确认` `✖️ 忽略`，角色可下拉修改后再确认；
- 「➕ 手动关联」：下拉选已登记项目 + 角色，直接建立正式关联（现在只有命令行能做的事）；
- 解除关联、确认都需要点击即生效的二次确认文案，沿用目标树现有风格。

### 6.3 性能（吸取 N+1 的教训）

- 不在递归渲染时逐节点请求；`_render_goal_tree_view` 渲染前**一次**批量调用
  `GET /v1/goals/tree/output_links`，返回 `{by_node: {node_id: {linked: [...], pending: [...]}}}`，
  并带 TTL 缓存（同 `_gt_next_steps_cached` 模式），确认/忽略/解除后主动失效；
- 「🔗 关联项目」子区块随「⚙️ 管理」懒加载，不展开不构建控件。

### 6.4 节点详情弹窗（📄）

在详情面板"产出"区块前加"关联的产出项目"，列出已关联项目及其 `output/` 路径，实现"从节点一步找到产出"。

### 6.5 「🧰 产出项目管理」

新增子页「🔗 待确认关联」：全局汇总所有待确认条目，展示所属 goal 面包屑，支持批量确认/忽略。
与目标树共用同一批 REST 端点，状态一致。

## 7. REST 与客户端

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/output_registry/pending_links` | 待确认列表 |
| POST | `/v1/output_registry/pending_links/{id}/confirm` | 确认，可带 `role` 覆盖；必须 `confirm=true` |
| POST | `/v1/output_registry/pending_links/{id}/reject` | 忽略 |
| POST | `/v1/output_registry/links` | 手动关联（已登记项目 + 管理根/节点 + 角色） |
| DELETE | `/v1/output_registry/links` | 解除关联 |
| GET | `/v1/goals/tree/output_links` | 目标树批量数据（§6.3） |

`apps/mini_agent_kanban/client.py` 增加对应方法，风格同现有 `output_registry_*`。

## 8. 配置（全部默认关闭）

`agent_config.json` 的 `output_registry` 块新增：

```
auto_link_tool_enabled      false   注册工具并在提示词里注入关联说明（第一层）
auto_link_detect_enabled    false   执行后目录差异检测（第二层）
auto_link_auto_confirm      false   true 时跳过待确认，直接建立正式关联（不建议，默认不开）
```

三个开关独立。全部为 false 时，行为与现状完全一致。

## 9. 风险与应对

| 风险 | 应对 |
|---|---|
| agent 漏调用工具 | 第二层补救；巡检仍会报告游离项目 |
| 误把无关目录判为本 goal 产出（并行多个 goal 同时建目录） | 第二层只处理"新增"目录，且只进待确认，用户可忽略；并行场景下按触发节点分别快照，仍可能混淆，由确认环节兜底 |
| 待确认堆积 | 目标树徽标 + 管理页汇总；巡检增加"待确认超过 N 天"提示（只报告） |
| 错把已有项目判为 build | 已有其他 build 关联时默认 iterate；确认时可改角色 |
| 工具被滥用造成路由改变 | 待确认不参与路由；仅确认后生效 |
| 管理根标记副作用 | 延后到确认时才 `mark_management_root` |

## 10. 分阶段实施

| 阶段 | 内容 | 退出条件 |
|---|---|---|
| S1 | 待确认账本模块 + 确认/忽略/手动关联/解除的纯函数与单测；`routing` 不变的特征测试 | 账本幂等、可删除可重建；确认后路由生效、待确认不生效 |
| S2 | `goal_exec_context` + 工具 `link_output_project` + 提示词注入（开关 `auto_link_tool_enabled`） | 开关关闭时提示词与工具列表与现状一致；开启后工具可在 goal cycle / objective 内关联 |
| S3 | REST 端点 + client 方法 | 端点单测 + 手动 curl 验证 |
| S4 | 目标树界面：徽标、「🔗 关联项目」子区块、批量接口与缓存、详情弹窗 | 节点上可见已关联与待确认，可就地确认/忽略 |
| S5 | 第二层目录差异检测（开关 `auto_link_detect_enabled`）+ 管理页「待确认关联」子页 | 漏调工具的执行也能产生待确认项；检测异常不影响 cycle |
| S6 | 文档：看板指南、HTTP API 指南、实施记录；打 diff-only zip | — |

## 11. 与理念文档的对齐检查

- **让用户下次少解释了什么**：不再需要手动 `link`；产出从目标节点一步可达。
- **新增数据有无消费者**：待确认账本由目标树和管理页消费；关联由 P4 路由消费。
- **可靠先于主动**：全部开关默认关；自动产生的只是待确认建议，不改变产出路径。
- **自我诊断**：来源（agent_tool / auto_detect）可统计第一层漏调率，作为后续调整提示词的依据。

## 12. 待确认的小问题

1. 待确认关联是否需要过期自动清理（建议不清理，只在巡检里提示）；
2. `auto_link_auto_confirm` 是否保留（建议先不实现，保持必须人工确认）；
3. 第二层是否需要处理"已有目录被本轮修改"的情形（建议首版不做，只处理新增目录）。
