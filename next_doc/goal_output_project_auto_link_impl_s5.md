# 自动关联产出项目 — 实施记录 S5：第二层检测、待确认子页、超时提示

> 对应计划：`goal_output_project_auto_link_plan.md` 第 5、6.5、9 章与第 10 章 S5
> 前序记录：`_s1.md` 至 `_s4.md`
> 状态：**已完成（代码与单元测试）**；真实 daemon 上的端到端验证未做，见第 6 节

---

## 1. 交付内容

| 文件 | 类型 | 说明 |
|---|---|---|
| `src/mini_agent/output_registry/auto_detect.py` | 新增 | 第二层：执行前快照、执行后对比、新增目录写入待确认 |
| `src/mini_agent/output_registry/resolve.py` | 新增 | 管理根解析与角色判定（由 S2 工具内的私有函数抽出，供两层共用） |
| `src/mini_agent/output_registry/patrol.py` | 修改 | 新增 `pending_link_findings()`，并入 `run_report` 的 findings |
| `src/mini_agent/evolution/objective_executor.py` | 修改 | 第一步提交时快照；完成/失败时检测；取消时只清理快照 |
| `src/mini_agent/tools/output_registry.py` | 修改 | 改用 `resolve.py` 中的共享函数（行为不变） |
| `src/mini_agent/config/models.py` | 修改 | 新增 `auto_link_detect_enabled`（默认 false）、`pending_link_stale_days`（默认 7） |
| `agent_config.json` | 修改 | 显式写入上述两项 |
| `apps/mini_agent_kanban/app.py` | 修改 | 「🧰 产出项目管理」新增第五个子页「🔗 待确认关联」 |
| `tests/test_goal_output_auto_link_s5.py` | 新增 | 22 个后端测试 |
| `tests/test_kanban_pending_links_s5.py` | 新增 | 8 个看板测试 |
| `next_doc/goal_output_project_auto_link_impl_s5.md` | 新增 | 本文件 |
| `next_doc/goal_output_project_auto_link_plan.md` / `next_doc/README.md` | 修改 | 状态与索引 |

## 2. 第二层检测

**快照**：第一步提交（`_submit_step(ex, 0)`）时记录 `output_projects_root` 一级目录名集合（排除 `_`、`.` 开头）。同步启动与异步启动两条路径都会经过这一点，只记录一次（重试不覆盖）。

**对比**：`_on_objective_completed` 与 `_on_objective_failed` 中调用 `detect_new_project_links`：
1. 取当前目录集合，与快照求差，得到本轮**新增**目录（§12-3：不处理已有目录被修改）；
2. 管理根解析与第一层相同（`resolve_management_root`，触发节点为 objective）；找不到则跳过，交给巡检；
3. 对每个合法目录名：`register_project`（只登记），再 `add_pending(origin=auto_detect)`。

**去重**完全复用 S1 的 `add_pending`：已有正式关联 → `confirmed`；已有待确认 → `existing`；已被忽略 → `rejected`。因此与第一层工具并存时不会重复提示。

**取消**：`_on_objective_cancelled` 只清理快照，不检测。

**隔离**：快照与检测的每个入口都包在 try/except 中，异常只写日志；开关关闭时快照与检测都不执行，账本不变。

## 3. 待确认子页（§6.5）

「🧰 产出项目管理」新增第五个子页「🔗 待确认关联」：
- 每条显示 slug、建议角色、来源（执行中关联 / 执行后目录检测）、理由，以及面包屑「管理项目 ← 触发节点」（缺标题时退回 id）；
- 逐条：可改角色后「✅ 确认」，或「✖️ 忽略」；
- 批量：勾选条目后，需勾选「确认执行批量操作」才能「批量确认」或「批量忽略」，失败条目集中报错；
- 与目标树共用同一组端点，写后同时失效目标树的关联缓存。

## 4. 巡检超时提示（§9 / §12-1）

`pending_link_findings()` 对超过 `pending_link_stale_days`（默认 7 天）仍为待确认的条目生成一条 `pending_link_stale` 提示，并入 `run_report` 的 findings，因此沿用现有的去重通知机制。

- **只报告，不清理**（§12-1 决议）；
- detail 文本不含随时间变化的天数，只写阈值与来源，保证同一条目在多次巡检之间去重键稳定，不会每次都重复推送。

## 5. 设计决策与偏差

1. **快照放在内存中**，按 `execution_id` 索引。守护进程重启后丢失，该次执行不做检测。这是有意的保守选择：宁可漏检（由第一层工具或人工补救），也不在没有可靠快照时误报。计划未对此作出规定，记录于此。
2. **`resolve.py` 抽取**：计划 §4.3 的规则此前只在工具内部实现。S5 需要完全一致的规则，抽出后两层共用，避免分叉。这是对 S2 代码的重构，行为由 S2 的 28 个测试守住。
3. **执行钩子挂在完成/失败回调上**，而不是 `start()`，因为计划要求"执行后检测"。
4. **`pending_link_stale_days` 默认 7 天**：计划只说"超过 N 天"，未给数字。7 天是本实施的选择，可在配置中修改，并应在真实数据积累后校准。

## 6. 已知边界与未验证项

- **并行误归属**：多个 goal 同时执行时，同一时间段新增的目录可能被归到当前 goal。计划 §9 已列为风险，由确认环节兜底。
- **真实 daemon 未验证**：执行器钩子在测试中通过 `ObjectiveExecutor.__new__` 构造的桩对象调用，没有跑完整的 objective 执行流程。需要在可运行环境中实际执行一个会建目录的 objective，确认待确认项出现在「🔗 待确认关联」中。
- **浏览器未验证**：待确认子页只通过纯函数、接线检查与源码断言测试，未在浏览器中点击。
- **快照不跨重启**（见第 5 节第 1 点）。

## 7. 验证

```
pytest tests/test_goal_output_auto_link_s5.py                     → 22 passed
pytest tests/test_kanban_pending_links_s5.py                      → 8 passed
pytest S1–S5 相关及 goal_tree / kanban / output 相关（共 36 个文件）→ 519 passed, 2 failed
```

2 个失败仍是 `tests/test_kanban_growth_dragdrop.py` 中既有的 `FakeClient` 缺方法问题，在原始上传包上同样出现，与本次改动无关。

## 8. 计划收尾状态

S1–S5 已全部完成代码与单元测试。计划第 10 章的 S6（文档：看板指南、HTTP API 指南、实施记录、打包）尚未做。其中实施记录已逐阶段完成；看板指南与 HTTP API 指南需要在 `docs/` 中补充，建议在真实环境验证之后统一编写。
