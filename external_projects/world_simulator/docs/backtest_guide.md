# 回测与校准指南（第二十二轮 WP5）

> 设计依据：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md` §4 WP5。
> 代码：`world_simulator/backtest.py`、`entrypoints/backtest.py`、
> `workflows/backtest_match.yaml`、`backtest_cases/`。

## 它是干什么的

**有答案才能说"更真实"。** 给定一个带已知里程碑的案例，对引擎隐藏答案地推进
N 步，把涌现的里程碑与真值对账，得到一组可比较的数字。同一案例在设置开关
ON/OFF 下各重复几次，比较指标分布——这是后续技术模型/事件采样/因果引擎
是否真的有效的唯一量化手段。

## ⚠ 先读这段：它不能证明什么

- **训练数据污染只能缓解不能根除。** LLM 读过真实历史，可能背诵而非模拟。
  别名化只替换案例里列出的名称，描述性文字仍可能泄露；年份平移不影响对
  "这是哪段历史"的识别。**绝对分数不可信，只有同一案例、同一匹配方式下的
  A/B 相对差异才有参考价值。**
- **精确率是下界**：真值列表是稀疏的，引擎合理涌现的其它里程碑会被算作未命中。
- **规则匹配器偏保守、LLM 匹配器偏宽松**，两者的分数不要混着比。
- **`run_ab` 只给分布，不做显著性检验**；样本数 < 5 时报告会写"不足以下结论"。
- **案例里的里程碑与年份不是事实来源。** 仓库自带的两个案例
  （`internet_early`、`personal_computer`）的**年份**已于 2026-10-02 由 Claude
  对照公开资料逐条核对（核对记录写在各 yaml 头部注释里），`verified: true`；
  这不是专家审定，里程碑的 `stage`/`requires` 是建模判断，没有核对；同一事件有多个
  日期口径（如 TCP/IP：DoD 宣布标准 vs ARPANET 整体切换，本案例取 1983 年切换），
  换口径会改变打分。你自己写的新案例默认仍是 `verified: false`。
- **模拟跨度必须覆盖最后一条真值**：`steps × years_per_step` 小于最后一条真值年份时，
  该真值属于"范围外"、不计入召回，永远测不到。原示例草稿有此缺陷（互联网案例 8 步×2 年
  只到 1985，6 条真值里只有 2 条在范围内），核对时已修正（互联网 13 步、PC 11 步；
  步数变多，单次回测的 LLM 调用也更多）。
- 候选时间的来源见下文"时间基准"：有 `elapsed_days` 的步用它（LLM 对每步跨度的估计，只宜作量级判断），
  没有的步退回案例声明的 `years_per_step`。
- 本功能**没有在真实 LLM 下运行过**（测试全部用桩），真实输出上的匹配质量、
  涌现里程碑的数量、单次运行成本都是未知数。

## 案例文件（YAML）

```yaml
id: my_case
title: ...
verified: false            # 你（或他人）核对过里程碑与年份后再改 true
disclaimer: "..."
steps: 8                   # 默认推进步数（--steps 可覆盖）
year_shift: 37             # 引擎看到的年份 = 真实年份 + 37
alias_map: {真实名称: 中性别名}   # 意图与真值名称里出现的键会被替换（长名优先）
start:
  template: group_evolution
  time_granularity: "2 年"
  start_year: 1969         # 真实年份（会加上 year_shift）
  years_per_step: 2        # 一步约多少年；引擎没记录 elapsed_days 的步用它换算时间
  intent: "..."            # 给引擎的一句话意图（写真实名称即可，运行时被别名化）
  settings: {}             # 创建实例时的 settings（A/B 的 B 臂在此基础上覆盖）
milestones:
  - id: m1
    name: ...
    year: 1969
    aliases: [...]         # 规则匹配器用；别名也会被别名化
    stage: lab             # 可选：lab/expert/developer/consumer/cheap_at_scale/infrastructure
    requires: [m0]         # 可选：前置里程碑 id（会校验存在、无自环、无成环）
```

## 用法

```bash
cd external_projects/world_simulator

# 1) 先核对案例（不调 LLM、不写任何数据）：看引擎将看到的意图和真值
python entrypoints/backtest.py check backtest_cases/internet_early.yaml

# 2) 跑一次（真实 LLM，成本 ≈ 步数；默认规则匹配器）
python entrypoints/backtest.py run backtest_cases/internet_early.yaml
python entrypoints/backtest.py run backtest_cases/internet_early.yaml --matcher llm --set tech_model_enabled=true

# 3) A/B：A 臂不改设置，B 臂覆盖设置，各重复 R 次（成本 ≈ 2×R×步数）
python entrypoints/backtest.py ab backtest_cases/internet_early.yaml --arm-b tech_model_enabled=true --repeats 5

# 4) 你不同意某个自动匹配：覆盖并重算
python entrypoints/backtest.py rescore reports/backtest/internet_early/<时间戳>-run/result.json \
    --override m_web=c4 --override m_browser=none
```

输出在 `reports/backtest/<case_id>/<UTC 时间戳>-<label>/`：`result.json`（真值、
候选、匹配、覆盖、指标、WP4 结构性告警计数、caveats）、`data/`（该次运行的
独立数据目录，**与真实 `data/` 隔离**，含 `reality_checks.jsonl`）；A/B 另有
`ab_report.json`。`--set`/`--arm-b` 的值按 JSON 解析（`true`/`3`/`"x"`），
解析失败按字符串。**没有把它登记进 `project.yaml`**（长时间、按步数计费的
LLM 调用不适合放进 daemon 调度/看板一键触发），需要时手动运行。

## 指标怎么读

| 指标 | 含义 | 注意 |
|---|---|---|
| `recall` | 范围内（真值年份 ≤ 起点+已推进跨度）真值中被命中的比例 | `recall_all` 含范围外，对短模拟不公平 |
| `precision` | 候选中被某个真值命中的比例 | **下界**，见上 |
| `kendall_tau` | 命中项上"真值时间序 vs 涌现步数序"的 τ-b，±1=完全一致/相反 | 命中 < 2 个时为无数据 |
| `interval_error` | 以第一个命中项为原点，涌现间隔与真值间隔之差的平均绝对误差（年）；带符号均值 **正=引擎比真实更慢** | 只看间隔，不看绝对年份 |
| `prerequisite_violations` | 前置与后继都命中、后继涌现步数早于前置的对数（同步不算） | 检验因果先后是否被违反 |
| `stage` | 真值声明了 `stage` 且候选带 `maturity_stage` 时的阶段一致率 | 依赖模型填 `maturity_stage` |

分母为 0 的指标是 `None`（无数据），**不是 0 分**。

## 候选是怎么抽出来的

`capabilities_gained` 每项一个候选；因果树里 `resolved` 的分支一个候选（首次
resolved 的步数取自 WP0 的快照链，找不到快照时保守地取最后一步）。不含
`structural_change` 等其它信号——需要时再扩展。

## 匹配与人工裁定

默认规则匹配器（名称/别名子串，一对一，零成本、可复现）。`--matcher llm` 走
`workflows/backtest_match.yaml`（一次 LLM 调用，输出会被校验：只认存在的 id、
强制一对一）。**无论哪种，你都可以用 `rescore` 覆盖**；覆盖会累积、只把发生
变化的真值追加进 `reality_checks.jsonl`（`model_version=backtest-override`），
不删旧记录。每条范围内的真值都会以 `reality_check.record_and_apply()` 写一条
（命中→`matched`，未命中→`diverged`）；范围外的不记，因为引擎没跑到那么远，
不是预测错误。

## 与 WP4 的关系

每次运行的 `result.json` 里带 WP4 体检的告警计数（`structural_health`），这样
"里程碑对账"与"结构性作弊"两类基线数字在同一处出。

## 时间基准与阶段迁移时点偏差（P6）

**候选时间怎么算。** `SimState.elapsed_days` 是该步自己跨越的天数。`time_basis: auto`（默认）时，有合法
`elapsed_days` 的步累加 `elapsed_days / 365.25`，没有的步按 `years_per_step` 补；模拟跨度（决定哪些真值算"范围内"）
同样按累计时间算。结果里 `time_basis.basis_used` 记录实际用的是 `elapsed_days`（全部步有记录）/ `mixed` / `years_per_step`，
`caveats` 随之变化，`result.json` 另存 `timeline`（步号→距起点年数）与 `horizon_years`。

**什么时候才有 `elapsed_days`。** 只有开启技术模型、事件采样、因果引擎（或带延迟的关系）时引擎才会向 LLM 索要并落盘它。
所以 **A/B 两臂开关不同时，可能只有一臂有 `elapsed_days`——两臂的时间基准不同，区间误差、阶段迁移时点偏差不能直接比较**。
`ab` 报告会在检测到基准不一致时给出 `time_basis_warning`。要比时间类指标，用 `--time-basis years_per_step`（或案例里写
`time_basis: years_per_step`）把所有运行固定在同一基准上——代价是丢掉引擎记录的真实跨度；要用 `elapsed_days`，就让两臂都开启
某个会索要它的机制。

**阶段迁移时点偏差。** 开启 `tech_model_enabled` 后，`tech_state` 随分支快照落盘。回测从快照链还原每个技术节点各阶段的到达时点：
`initial`（起点就有，不计时）、`registered`（起点之后新登记）、`transition`（观测到阶段上升）。节点与**带 `stage` 的真值**用
同一个匹配器配对（`--matcher llm` 时会多一次 LLM 调用；没有技术节点则不多调）。口径与"区间误差"一致：可计时的配对按真值
年份排序，以第一个为原点，各项（引擎到达时间差 − 真值时间差）的平均绝对误差与带符号均值（年，正 = 引擎更慢），至少 2 个
可计时配对才有值。同时给出 `n_initial`（起点已在）、`n_not_reached`（范围内却没到该阶段，引擎太慢/停滞）、`n_out_of_horizon`
（范围外，不算错）、`stage_reached_rate`。没有任何技术快照时 `available=False`——是"无数据"，不是 0 分。A/B 汇总新增
`tech_timing_mean_abs_years` 与 `tech_stage_reached_rate`。

**示例。** 技术模型 ON/OFF 对照、两臂固定同一时间基准：
`python entrypoints/backtest.py ab backtest_cases/internet_early.yaml --arm-b tech_model_enabled=true --time-basis years_per_step`
（OFF 臂没有 `tech_state`，阶段迁移指标只会在 ON 臂有值，这是预期的；真正可比的是里程碑召回/顺序/区间等指标）。

**P6 的局限（如实写明）。**
- 节点↔真值的配对是**字面匹配**（规则匹配器）或 LLM 语义匹配，配不上的真值不进入该指标；
- `rescore` 的覆盖只作用于里程碑候选，**技术节点配对不随覆盖重算**，原样保留；
- `elapsed_days` 是 LLM 估计，长跨度/变步长时误差可能很大，且回测不会像技术模型那样夹值（只取落盘的合法正数）；
- 旧的 `result.json`（P6 之前）没有 `horizon_years`/`tech_timing`，`rescore` 退回旧口径、不补该指标；
- 没有在真实 LLM 下运行过：涌现的技术节点数量与命名、能否被匹配、`elapsed_days` 质量都是未知数。
