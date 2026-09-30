# 外生事件采样使用指南（第二十二轮 WP2）

> 设计依据：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md` §4 WP2。
> 代码：`world_simulator/event_sampler.py`。开关：`settings.event_sampling_enabled`
> （**默认关闭**，关闭时提示词、落盘、界面与没有这个功能时完全一致）。

## 它解决什么、不解决什么

LLM 倾向于每一步都写"戏剧性的突发事件"，而真实世界大多数步骤是平静的，重大外部冲击按某个
大致的频率发生。开启后，**引擎**按你声明的先验概率，在**每步调用 LLM 之前**抽样"本步是否发生某事件"，
把抽中的事件作为**既成事实**注入提示词；没抽中时明确告诉 LLM"本步没有重大外部事件，可以写平静的一步"。

**它不做的事**（请先看清，否则会高估它）：

- **没有内置任何先验，也不知道任何领域的真实频率。** `rate_per_year` 是你的声明；
  数字从哪来、准不准是你的责任。`verified` 默认 `false`，界面会标"⚠️ 未核对"。
- **无法验证 LLM 是否真的把事件写进了叙事**，也无法阻止它另外编造冲击，提示词只能要求。
- `affects` 只是提示信息，**不会**自动改任何字段/技术/因果线（那是 WP3 的事）。
- 不做事件之间的相关性（A 触发 B），各事件独立抽样。
- **没有在真实 LLM 下验证过**：LLM 遵守"既成事实"约束的程度、额外 token 成本都未知。
- 计划里"由 `generate_scenario` 提议先验初值并标 `llm_estimate`"**本阶段没有做**——先验目前由你在
  设置里手写（详见下文"与计划的偏离"）。

## 快速开始

设置页 → "高级：外生事件采样" → 勾选开启 → 在"事件先验"里写 JSON：

```json
[
  {"id": "drought", "description": "严重干旱", "rate_per_year": 0.1, "severity": "high",
   "affects": ["收成"], "cooldown_steps": 4,
   "condition": {"var": "resources.land", "op": ">", "value": 0},
   "source": "农业年鉴 2020", "verified": false}
]
```

详情页出现"🎲 外生事件采样"面板（每条先验的单步概率、冷却、条件状态；**不显示"下一步会不会抽中"**，
避免剧透）；抽中的事件在该步以"🎲 外生事件"提示显示。

## 先验字段

| 字段 | 说明 |
|---|---|
| `id` / `description` | 缺 `id` 时用 `description` 当 id；都没有则丢弃 |
| `rate_per_year` | 每年期望发生次数，**必须是非负数**，否则该条被忽略并在界面警告 |
| `severity` | `low`/`medium`/`high`，默认 `medium`，仅作提示 |
| `affects` | 字符串数组，仅作提示 |
| `cooldown_steps` | 发生后 N 步内不再发生，默认 0 |
| `condition` | 见下文，可选 |
| `source` / `verified` | 默认 `"user"` / `false` |
| `enabled` | 默认 `true`，设 `false` 暂时停用 |

## 每步发生什么

1. **估计本步跨度** `basis_days`：取最近 3 步 `elapsed_days` 的**中位数**（不被个别离群值拖走）；
   没有历史则用占位值 30 天（`event_params.default_days_per_step`，**不是领域事实**）。
2. **每个事件独立抽样**：`p = 1 - exp(-rate_per_year × basis_days / 365.25)`（泊松到达）；
   抽样值 = `sha256(sim_id | 分支 | 步序号 | salt | 事件 id)` 映射到 [0,1)。
   **同一分支同一步重跑结果一致**；增删别的先验不影响某个事件自己的抽样。
3. **过滤**：`enabled=false`、冷却中、条件不满足的事件不参与。
4. **上限**：命中数超过 `max_events_per_step`（默认 3，占位值）时，按抽样值从小到大保留，
   其余记为 `suppressed_by_cap`（不注入、不启动冷却）。
5. 抽中的事件写进提示词，并记入 `SimState.sampled_events`（审计，也是冷却的依据）。

## 为什么 `basis_days` 只是估计

事件必须在调用 LLM **之前**抽好（它们是注入提示词的事实），而这一步实际跨越多少天
（`elapsed_days`）要等 LLM 输出后才知道。所以引擎用前几步的中位数估计。
如果 LLM 实际报告的跨度和估计差得很远，事件的实际频率就会偏离先验——**这是已知误差，不是 bug**。
每条事件记录里都有 `basis_days`/`basis_source`（`history_median` 或 `default`）便于事后核对。

提示词会向 LLM 索要 `elapsed_days`；只要事件采样**或**技术模型任一开启，`elapsed_days` 就会落盘。
LLM 不给时基准退回占位天数，精度降低。

## 条件（`condition`）

- 变量比较：`{"var": "a.b", "op": ">=", "value": 5}`，`op` ∈ `< <= > >= == !=`；
  `var` 是 `vars` 里的点路径。大小比较要求两边都是数字。
- 技术阶段：`{"tech": "<技术 id>", "min_stage": "developer"}`（需开启技术模型并登记该技术）。
- 数组 = 全部满足（AND）。
- **变量不存在、技术未登记、写法非法 → 一律视为不满足**（保守：宁可不发生，也不在错误条件下发生）。

## 分支、复现与实验

- 冷却从**本分支历史**推导，天然按分支隔离：主线触发后，从触发前分叉出的分支不受主线冷却影响。
- 分叉出的分支名不同，所以 `run_repeated_experiment` 的各条分支**天然得到不同的事件序列**
  （真实的分布，而不只是 LLM 采样噪声），不需要额外操作。
- **比较不同策略**（`run_comparison_experiment`）时，各分支的事件序列默认也不同，会把"策略差异"和
  "事件差异"混在一起。此时可开启 `event_sampling_common_random_numbers`（公共随机数）：
  种子不含分支名，同一步上所有分支抽到相同事件。代价：此时重复采样的各分支事件序列相同，
  除非手动设不同 `event_sampling_salt`。
- 只对**新写入的步**有效；开启前的旧步没有 `sampled_events`，不迁移。

## 参数（`settings.event_params`）

| 参数 | 默认 | 说明 |
|---|---|---|
| `default_days_per_step` | 30 | 没有 `elapsed_days` 历史时的占位天数 |
| `max_events_per_step` | 3 | 单步注入上限（占位值） |
| `basis_window` | 3 | 取最近几步的 `elapsed_days` 求中位数 |

## 与计划的偏离

1. **先验由用户手写，未做 `generate_scenario` 提议。** 原因：这会改动场景生成流程与确认界面，
   且"LLM 猜的先验"本来就是垃圾进垃圾出的入口（计划 §7-3）。用户仍可把 LLM 给的数字填进去，
   并保持 `verified: false`、`source` 写明来源。后续如需，可作为独立小阶段补上。
2. 计划里 `condition?` 只写了"可选"，本阶段实现了最小谓词（变量比较 + 技术阶段），
   P5（WP3 树接地）可复用同一套求值。
3. 计划里的"每条分支不同 salt"改为**分支名本身进种子**，效果相同且无需改动实验代码；
   并额外提供了公共随机数开关（见上）。
4. 新增了 `max_events_per_step` 上限（计划未写）：防止把 `rate_per_year` 写得过大时每步都冲击叠加。

## 已知边界

- 独立推进路径 `advance_lines()` **不做**事件采样。
- 事件概率是按估计跨度算的；`elapsed_days` 不稳定会让频率偏离先验。
- 用户在设置页保存先验会立即生效，但已写入历史的 `sampled_events` 不会被改。

## 相关

- 测试：`tests/test_event_sampler.py`（39 个用例，见 `testing_guide.md`）
- 技术模型：`tech_model_guide.md`（`elapsed_days` 的另一个使用方）
- 设计/记录：计划文档 §4 WP2、§9 P4
