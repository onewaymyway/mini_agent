# 静态 HTML 导出：新机制区块指南（第二十二轮 P10）

> 对应代码：`world_simulator/html_export_mechanisms.py`（新）、`world_simulator/html_export.py`（接线）。
> 测试：`tests/test_html_export_mechanisms.py`（28 个）。设计依据：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md` §10 P10。

## 1. 做了什么

"导出 HTML"（`export_simulation_html()`）原先不认识第二十二轮的任何新机制。现在，**有数据才渲染**：

| 位置 | 内容 | 出现条件 |
|---|---|---|
| 时间线每步卡片末尾 | 🧭 一致性提示 / ⚙️ 技术违规与 🛠️ 修复调用 / ⏱️ 时间精度降级 / 🎲 抽样事件（含"先验未核对""AI 提议、用户未确认""超上限未注入"）/ 🔗 因果入队与交代（AI 自报）/ 🔗 因果违规 / 🌳 树接地降级与自动迁移 | 该步对应字段非空 |
| 因果线总览之后 | 🩺 真实性体检摘要（C1–C8、校准账本、最近 20 条告警，带"结构性代理指标，非评分"） | 历史里出现过一致性守卫痕迹（任一步有 `consistency_warnings` 或 `dynamic_snapshot`） |
| 同上 | 🧬 技术发展模型：节点表（阶段/进度条/停留/状态/瓶颈/公众以为）+ 前置 DAG（按状态着色，软前置虚线） | `tech_model_enabled` 且该分支有节点 |
| 同上 | 🎲 外生事件先验：频率、严重度、"AI 提议未确认/未核对/来源/理由" | `event_sampling_enabled` 且有先验（或有被丢弃的先验） |
| 同上 | 🔗 因果引擎：按边状态着色的关系图 + 图例、边状态与兑现率表、⏰ 到期因果时间线（待兑现 + 最近处置）、🌳 树分支声明的影响 | `causal_engine_enabled` 且有边/待兑现/处置 |
| 同上 | 🌳 因果树接地：当前建议/约束（含自动迁移是否开启的说明） | `tree_grounding_enabled` 且有建议 |

## 2. 铁律（测试固定）

1. **没数据就不输出**：没开新机制/没有新数据的实例，导出与接入前**逐字节相同**（额外 CSS 也只在渲染了新内容时才追加）。
   验证方式：用接入前的 `html_export.py` 与接入后对同一实例导出，去掉"导出时间"后逐字节比较，相同（该比较是一次性脚本核对，
   不是常驻测试——常驻测试断言的是"没有任何新区块标记/CSS"）。过程中发现并修掉一处：每步卡片模板里单独一行的空占位会多出一行空白，改为与上一占位同行。
2. **复用纯函数，不重算业务逻辑**：`tech_model.summarize`、`causal_view.build_edge_view/edges_to_dot/build_due_timeline`、
   `consistency_guard.analyze_history`（经 `quality_signals`）、`event_sampler.get_priors`、`tree_grounding.pending_suggestions`、
   `tree_effects.declared_effects`。每步提示的文案与 `app.py` 同名 helper 各自独立实现一份（沿用"导出不 import app.py"的约定），文案需人工保持一致。
3. **按分支取动态状态**：导出某条分支时，`tech_state`/`causal_pending`/`causal_lines` 取该分支最近一份 `dynamic_snapshot`；
   没有快照时，**只有导出分支就是当前活跃分支**才退回 `manifest.settings`（它是活跃分支的工作副本），否则不显示动态状态——
   宁可缺，不拿别的时间线冒充（有测试）。
4. **转义**：所有自由文本走 `_esc`（有测试：`<script>`、`<b>`、`<i>` 均被转义）。
5. **失败降级**：任一文档级区块渲染抛异常 → 该区块变成一行"（xxx渲染失败：异常类型，已跳过）"；每步提示出错 → 该步不显示提示；
   Graphviz 不可用 → 不画图，表格/列表仍在。导出不会因此整体失败。
6. **措辞不夸大**：兑现统计与边状态标注"AI 自报，不等于世界里被验证/被证伪"；体检标注"结构性代理指标，存在误报"；
   未核对/未确认的先验显式标出；事件先验区块**不预告**"下一步会抽中什么"。

## 3. 已知边界

- **没有浏览器级目视验证**：只有断言型测试与一次真实 `dot` 的端到端渲染（确认生成 SVG、各区块标题出现），真实配色/布局/移动端显示没见过。
- 技术树、因果引擎、树接地区块反映的是**导出分支最后一步之后**的状态，不是逐步演化；逐步变化看时间线里的每步提示。
- 因果引擎的"到期/还差多少"按导出时的下一步（最后一步 + 1）计算，与看板同口径；`elapsed_days` 是 LLM 估计值，缺数据时按步计且标注精度。
- 只导出一条分支，不对比分支（沿用既有约定）。
- `advance_lines()` 路径产生的步，其字段同样会被渲染，但线不产出 `tree_updates`/`causal_links`（P8 既有边界），所以相关区块可能为空。
- 体检区块在历史很长时每次导出都从历史重算（纯 Python，不调 LLM）。
- 没有在真实 LLM 产出的数据上看过导出效果——真实数据里的字段分布/文本长度可能让某些块很长。
- 没有新开关、不落盘、不调 LLM；`app.py` 没有改动（"下载 HTML"按钮原样调用 `export_simulation_html()`）。
