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

1. 巡检仍然是手动触发（`output_patrol.py report`），没有定期调度，所以通知也只在手动运行时产生。定期调度需要在 cron 中注册，留到后续确认频率后再做。
2. 只登记档不记录 `title` 之外的介绍信息；若之后需要介绍，升级为受管时再补。
