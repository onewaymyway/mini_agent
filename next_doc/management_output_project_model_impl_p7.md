# 产出注册表 P7 实施记录：接入两档、阈值配置、巡检通知与看板管理

> 对应：P6 实施记录第 7 节的三个待确认问题，用户的决定作为本阶段需求。
> 分两个阶段实施，每阶段单独打包。

---

## 阶段一：后端（两档接入 / 配置化阈值 / 巡检通知）

### 1. 用户决定

| P6 待确认问题 | 用户决定 | 本阶段做法 |
|---|---|---|
| 接入是否直接设为受管？ | 增加"只登记、不受管"第二档 | `adopt_output_project(mode="registered"|"managed")`；只登记不写目录、不写 PROJECT.json；只登记可升级为受管 |
| 看板是否需要一键接入按钮？ | 需要 | 阶段二实现 |
| 巡检阈值 | 默认 3 天，通过 agent_config.json 配置 | `OutputRegistryConfig`（`stale_days`、`version_gap_days`，默认 3.0）；`agent_config.json` 增加 `output_registry` 块 |
| 巡检报告通知，看板可见 | 需要 | `run_report` 对新出现的建议派发通知（source=`output_patrol`），经 kanban 渠道落到「关注与通知」tab |

### 2. 设计要点

**两档接入**

| 档位 | 目录内写入 | 登记 | managed | PROJECT.json |
|---|---|---|---|---|
| registered（只登记） | 无 | `_registry/` | False | 不写 |
| managed（受管） | `PROJECT.json`，以及缺失时的 `PROJECT_INFO.md`（需给介绍） | `_registry/` | True | 写 |

- 两档都需要 `confirm=True`。
- 只登记 → 受管：升级，同样需要确认，写入 `PROJECT.json`，历史追加 `upgraded_to_managed`。
- 受管 → 只登记：不支持。重复接入不会降级，只返回已登记。

**阈值**

- 优先级：显式参数 > `agent_config.json` 的 `output_registry` 块 > 默认值（3 天）。
- 报告的 `calibration` 字段由 `uncalibrated` 改为 `configured`：3 天是用户指定值，不是数据校准结论，报告中如实标注。
- 3 天较短，停滞与版本缺口建议会比之前多。这是用户的选择；如果噪音太大，改配置即可。

**通知**

- 只对相对上一次 `latest.json` **新出现**的 `(项目, 类型, 描述)` 推送一条，重复的不推送。
- `notify_enabled=false` 时完全不推送，报告照常生成。
- 派发失败只记日志，不影响巡检结果。
- 通知走 `NotificationDispatcher`，kanban 渠道恒开启，所以一定能在看板看到。

**只读边界**：巡检仍不修改产出项目目录、`_registry/`、`goals.json`。写入范围是 `.agent/output_registry/`（报告）和 `.agent/notification/`（通知）。

### 3. 新增与改动文件

| 文件 | 类型 | 说明 |
|---|---|---|
| `src/mini_agent/config/models.py` | 修改 | 新增 `OutputRegistryConfig`；`AppConfig.output_registry` |
| `src/mini_agent/config/param_registry.py` | 修改 | 注册 nested block |
| `src/mini_agent/config/loader.py` | 修改 | 装配 `output_registry` |
| `src/mini_agent/config/config_catalog.py` | 修改 | 看板配置面板可见此块 |
| `agent_config.json` | 修改 | 增加 `output_registry` 块（3 天、通知开） |
| `src/mini_agent/output_registry/adoption.py` | 修改 | 两档接入、升级 |
| `src/mini_agent/output_registry/patrol.py` | 修改 | 配置阈值、通知去重、`notified` / `new_findings_total` 字段 |
| `tests/test_output_patrol.py` | 修改 | calibration 断言改为 `configured` |
| `tests/test_output_registry_p7_stage1.py` | 新增（16 项） | 两档、升级、阈值、通知去重 |
| `next_doc/management_output_project_model_impl_p7.md` | 新增 | 本文档 |
| `next_doc/README.md` | 修改 | 索引 |

### 4. 验证

| 测试集 | 结果 |
|---|---|
| P7 阶段一新增 | 16/16 通过 |
| 产出注册表既有（P0–P6 + 巡检） | 通过（合计 81 项，含原 P5 巡检测试） |
| 配置相关回归（catalog / nested / flat compat / kanban config routes / param registry） | 74/74 通过 |
| 真实 `agent_config.json` 加载 | `OutputRegistryConfig(stale_days=3.0, version_gap_days=3.0, notify_enabled=True)` |

### 5. 已知限制（阶段一）

1. （已在阶段三解决：定期调度见下节。）
2. 只登记档不记录 `title` 之外的介绍信息；若之后需要介绍，升级为受管时再补。

---

## 阶段二：看板（路由 / 客户端 / 产出项目管理 Tab）

### 1. 范围

| 用户要求 | 本阶段做法 |
|---|---|
| 看板增加"一键接入"按钮 | 「🧰 产出项目管理」→「📥 未登记目录」，每个候选两个提交按钮：只登记 / 一键接入（受管） |
| 看板里有个区域专门管理本次新增功能 | 新 Tab「🧰 产出项目管理」，四个子区：未登记目录、已登记项目（含升级）、发布版本、巡检 |
| 巡检报告通知在看板可见 | 通知经 kanban 渠道进入既有「🔔 关注与通知」tab，分类「关注提醒」；巡检子区展示最近报告 |

### 2. 设计要点

- **路由**（`api/routes.py`）：6 个端点，全部 owner-only，复用 `output_registry` 已有函数，不重新实现判断逻辑。
  - `adopt` / `publish` 请求体必须带 `confirm=true`，否则 400，并且在调用函数之前就返回，不产生写入。
  - 阻塞 IO 用 `asyncio.to_thread`，不占 event loop（吸取外部项目卡顿的教训）。
- **客户端**（`apps/mini_agent_kanban/client.py`）：6 个方法，与路由一一对应。
- **看板**（`apps/mini_agent_kanban/app.py`）：
  - 写操作全部放在 `st.form` 内，表单内编辑不触发整页重跑（吸取外部项目参数输入卡顿的教训）。
  - 每个写操作都要先勾选确认框，未勾选只显示提示。
  - 接入/升级/发布成功后显示提示，列表需点「🔄 刷新」查看最新状态（避免成功后立刻 rerun 把提示冲掉）。
- **通知分类**（`notification/reports_store.py`）：`output_patrol` 映射到既有「关注提醒」，没有新增分类，因此分类相关测试不需要改。

### 3. 新增与改动文件

| 文件 | 类型 | 说明 |
|---|---|---|
| `src/mini_agent/api/routes.py` | 修改 | 新增 6 个 `/v1/output_registry/*` 端点 |
| `src/mini_agent/notification/reports_store.py` | 修改 | `output_patrol` → 关注提醒 |
| `apps/mini_agent_kanban/client.py` | 修改 | 6 个客户端方法 |
| `apps/mini_agent_kanban/app.py` | 修改 | 新 Tab「🧰 产出项目管理」及其四个子区渲染函数 |
| `tests/test_output_registry_routes.py` | 新增（5 项） | confirm 门槛、两档接入与升级、发布语义化校验、巡检 run/latest、只读列表 |
| `docs/kanban-dashboard-guide.md` | 修改 | Tab 一览、专节、AgentClient 端点表 |
| `next_doc/management_output_project_model_impl_p7.md` | 修改 | 本节 |

### 4. 验证

| 测试集 | 结果 |
|---|---|
| 路由测试（新增） | 5/5 通过 |
| P7 阶段一 + 产出注册表全部 + 通知 + 配置相关 | 309 通过，1 失败 |

**唯一失败**：`tests/test_notification_dispatcher.py::test_kanban_writes_alert_record`。这是**原始代码就已失败**的既有问题（在未修改的原始包中同样失败）：测试期待 kanban 渠道写 `alerts.jsonl`，但该渠道早已改为写 `reports.jsonl`（见 `reports_store.py` 顶部说明）。本次没有修改它，建议单独修测试。

`app.py` 与 `client.py` 已通过编译检查。Streamlit 页面没有在浏览器中实际点击验证，这一点需要在真实看板上确认。

### 5. 已知限制

1. （已在阶段三解决：定期调度见下节。）
2. 看板无法把已登记项目绑定到管理项目（P2 的绑定操作仍然只有函数/命令行入口），本阶段只覆盖 P6/P7 的接入、升级、发布与巡检。
3. 接入后的 `unregistered` 建议随即消失，但"游离"建议仍在，因为项目仍未绑定管理项目，这是有意的。
4. 通知去重依据是 `(项目, 类型, 描述)`。描述里若包含会变化的数字（如"已 4 天没有文件变动"），每次天数变化都会被当作新建议重新推送。这是后续需要观察的点，可能需要改成只按 `(项目, 类型)` 去重。

---

## 阶段三：定期巡检调度

### 1. 用户决定

- 巡检需要定期调度，默认 3 天，可通过 `agent_config.json` 修改。
- 这里的"3 天"指**巡检运行间隔**，与阶段一的停滞/版本缺口阈值（同样默认 3 天）是两个独立配置。

### 2. 设计

| 项 | 值 |
|---|---|
| job id | `sys:output_patrol` |
| 调度 | `interval:<天数×86400>`，默认 `interval:259200` |
| 执行方式 | 本地回调（零 LLM 成本），调用 `run_report` |
| 通知 | 沿用 `run_report` 的去重与 `notify_enabled` 逻辑 |
| 可见位置 | 「⏰ Cron 任务」Tab（可手动触发）、「🔔 关注与通知」Tab（新建议） |

**配置**（`agent_config.json` 的 `output_registry` 块）

| 字段 | 默认 | 说明 |
|---|---|---|
| `patrol_interval_days` | 3 | 巡检间隔（天） |
| `patrol_schedule_enabled` | true | false 时 job 保留但 disabled |
| `stale_days` / `version_gap_days` | 3 | 阈值（与间隔无关） |
| `notify_enabled` | true | 新建议是否推送 |

**启动对齐**：`ensure_output_patrol_job` 在 daemon 启动时运行。job 已存在时不会覆盖用户手动改过的其它属性，但会把 `schedule` 与 `enabled` 对齐到配置，并重算 `next_run_at`。因此改配置后**下次启动**生效，无需改代码。

### 3. 新增与改动文件

| 文件 | 类型 | 说明 |
|---|---|---|
| `src/mini_agent/output_registry/patrol_job.py` | 新增 | `ensure_output_patrol_job`、`interval_schedule`、本地回调 |
| `src/mini_agent/api/server.py` | 修改 | daemon 启动时注册 `sys:output_patrol` |
| `src/mini_agent/config/models.py` | 修改 | `OutputRegistryConfig` 新增 `patrol_interval_days`、`patrol_schedule_enabled` |
| `agent_config.json` | 修改 | 增加两个字段（3 天、启用） |
| `tests/test_output_patrol_job.py` | 新增（6 项） | 默认间隔、配置变更对齐、禁用、幂等、回调运行、配置默认值 |
| `docs/kanban-dashboard-guide.md` | 修改 | 去掉"无定期调度"的旧说明 |

### 4. 验证

| 测试集 | 结果 |
|---|---|
| 定期巡检 job（新增） | 6/6 通过 |
| 产出注册表、通知、cron、配置相关回归 | 673 通过，1 失败 |

**唯一失败**：`tests/test_cron_command_job.py::test_cli_add_cmd_end_to_end`。在未修改的原始包上同样失败，与本次改动无关。

真实 `agent_config.json` 加载结果：`OutputRegistryConfig(stale_days=3.0, version_gap_days=3.0, notify_enabled=True, patrol_interval_days=3.0, patrol_schedule_enabled=True)`，`api/server.py` 可正常导入。

### 5. 已知限制

1. 修改间隔需要重启 daemon 才生效（启动时对齐，而不是运行中热更新）。
2. 没有在真实 daemon 中跑满一个周期做端到端观察，间隔逻辑靠 cron 既有机制与单元测试覆盖。
3. 巡检本身仍然是只读建议，定期运行不会执行任何归档、删除或关联。
