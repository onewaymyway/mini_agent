# 多结果合并取优（Ensemble / Best-of-N）指南

## 概述

`ensemble` 模块为 mini-agent 提供"多结果合并取优"能力：对同一任务获取多个候选结果，
再综合评判出最优结果（或合并出一份更好的结果），用以降低单次模型输出的偶然性误差。

支持两种**粒度**：

| 粒度 | 说明 | 成本/速度 | 适合场景 |
|------|------|----------|----------|
| `llm_call` | 相同 messages/system，多次独立调用模型（温度抖动制造多样性） | 便宜、快 | 单个开放式问题/子任务的答案质量兜底 |
| `subagent` | 多个 SubAgent 用不同上下文/人设（保守/激进/自我复核…）各自完整跑一遍任务（可用工具、多轮） | 较贵、较慢 | 整个任务的解法本身存在多种合理路径 |

两种粒度都支持**串行（serial）**与**并行（parallel）**执行：

- 并行：所有候选同时跑，速度快，资源占用高
- 串行：逐个跑，配合"提前停止"可以省钱——一旦满足条件（如已通过校验、已有多数共识）立即停止，不再跑剩余候选

是否触发由 `ensemble.mode` 控制，分四档：

| mode | 行为 |
|------|------|
| `off`（默认） | 完全关闭，工具调用会被拒绝，自动触发也不生效 |
| `manual` | 仅当显式调用 `run_ensemble_llm` / `run_ensemble_subagents` 工具，或显式传 `explicit=True` 时才执行 |
| `auto` | 框架在每轮用户输入前自行判断是否值得做 ensemble（规则层 + 模型自判层），判断"值得"时自动用 `subagent` 粒度跑完整这一轮，**Agent 无需主动调用任何工具** |
| `always` | 强制对所有匹配的任务触发（调试/评测用，会显著增加成本，慎用） |

---

## 快速上手

### 配置文件（agent_config.json）

```json
{
  "ensemble_mode": "manual",
  "ensemble_granularity": "both",
  "ensemble_n": 3,
  "ensemble_execution": "parallel",
  "ensemble_judge_strategy": "llm_judge",
  "ensemble_judge_model": null,
  "ensemble_early_stop_on_consensus": true,
  "ensemble_max_concurrency": 3,
  "ensemble_max_extra_cost_ratio": 2.0
}
```

所有字段都是平铺 key，加载后会被组装为 `AppConfig.ensemble`（`EnsembleConfig` 数据类）。

### CLI 斜杠命令（运行时临时调整，不写回配置文件）

```
/ensemble                              # 查看当前状态
/ensemble on                           # off → manual 的快捷开关
/ensemble off                          # 完全关闭
/ensemble mode auto                    # 切到自动判断模式
/ensemble granularity subagent         # 只允许 subagent 粒度
/ensemble n 5                          # 候选数改为 5
/ensemble execution serial             # 改为串行执行
/ensemble strategy first_success       # 评判策略改为"第一个通过就停"
```

### 工具调用（manual / auto 模式下 Agent 主动使用）

system prompt 会让 Agent 知道有这两个工具，Agent 可以在判断需要时主动调用：

- `run_ensemble_llm(prompt, system="", n=None, execution=None, strategy=None)`
  — 同一问题多次调用模型取优，不涉及工具调用，便宜快速。
- `run_ensemble_subagents(prompt, n=None, execution=None, strategy=None, variant_prompts=None)`
  — 派发多个不同人设的 SubAgent 各自完整完成任务（可用工具），再评判/合并。

两个工具的返回都是 JSON，包含 `final_content`（最终结果）、`chosen_idx`、`judge_reason`、
`early_stopped`、`n_candidates`、`total_latency_s` 等字段。

---

## 任务类型自动识别 与 评判策略

`ensemble.decision.classify_task_type()` 会根据任务文本粗粒度判断：

- **`verifiable`**（可验证类）：任务带有明确验收标准（`acceptance_criteria`）、可调用验证工具，
  或文本命中"运行/测试/编译/通过/校验/debug/报错"等关键词 → 评判策略自动切换为 **`first_success`**
  （跑到第一个通过校验的候选就停止，串行模式下能显著省成本）
- **`open_ended`**（开放式）：写作、方案设计、分析、对比等，没有单一客观对错 → 默认使用配置中的
  `judge_strategy`（默认 `llm_judge`，综合多个候选选优）

四种评判策略：

| strategy | 行为 |
|----------|------|
| `llm_judge`（默认） | 用模型从 N 个候选中选出质量最高的一个 |
| `first_success` | 跑到第一个通过校验（`passed_check=True` 或传入的 `checker` 函数返回 True）的候选就用它；都没通过则回退到第一个未出错的候选 |
| `vote` | 多数投票，适合输出可直接字符串比较的场景（分类、固定格式结果） |
| `merge` | 让模型把多个候选的优点合并成一份新答案，而不是二选一 |

---

## AUTO 模式：两层触发判定

`auto` 模式下，每轮用户输入会先经过 `should_trigger_ensemble()`：

1. **规则层**（便宜，先跑）：
   - 命中"重要/谨慎/务必/critical/important/生产环境/不可逆"等高风险关键词 → 直接判定触发
   - 上一次输出被格式校验/纠错检测判定失败、本轮属于重试升级 → 直接判定触发
   - 任务文本过短（< 12 字符，如简单问答/单行指令）→ 直接判定不触发
   - 其余情况规则层不确定，进入第 2 层
2. **模型自判层**：用一次低成本调用（`max_tokens=200`）问模型"这个任务是否值得花多次采样/多
   路径比较来提高质量"，模型只输出 `{"trigger": true|false, "reason": "..."}`。任何异常都视为
   "不触发"，避免判定本身出错拖垮主流程。

触发后，AUTO 模式固定使用 **subagent 粒度**（因为要让 Agent 自主决定是否要"完整地多跑几遍"，
更贴近"多路径取优"，而不仅是单次答案抖动），结果直接替换本轮常规单路输出，整个过程对用户透明，
只在终端打印一行 `[ensemble] auto-triggered (...)` 提示。

---

## 串行 + 提前停止（省成本的关键）

`ensemble.early_stop_on_consensus=true`（默认开启）时，串行执行会在每个候选产出后检查：

- 策略为 `first_success`：某个候选一旦通过校验，立即停止，不再跑剩余候选
- 其他策略：已产出候选中，若某个内容出现次数达到"半数以上"（`>= n//2 + 1`），视为已有共识，
  提前停止

并行模式不做提前停止（候选已经同时在跑，停不下来），如果想要"省钱优先"，建议串行 + 提前停止；
想要"速度优先"，用并行。

---

## 成本保护

`ensemble.max_extra_cost_ratio`（默认 2.0）用于约束 AUTO 模式下的额外开销上限（相对单次调用的
倍数），避免自治场景（如 Stage 9 的 cron/autonomous 循环）里被无限放大调用次数。**建议在
daemon/cron 自治任务中保持 `ensemble.mode=off` 或较低的 `n`，避免自治循环里成本失控。**

---

## 落盘与可观测性

每次 ensemble 运行结束后：

1. 若能定位到当前 session，会落盘一份 `ensemble_run.json` 到
   `<project_root>/.agent/sessions/<session_id>/ensemble/<时间戳>_<粒度>.json`，
   内容包含全部候选（含失败原因）、评判理由、是否提前停止、总耗时等。
2. 会触发 `EnsembleJudged` hook 事件（payload 为完整运行记录），可在 `.agent/hooks/hooks.json`
   中挂自定义 hook 脚本做进一步处理（详见 [hooks.md](./hooks.md)）。

落盘与 hook 触发都做了静默容错：拿不到 session_id、没有注册 hook、磁盘写入失败等情况都不会
影响 ensemble 本身的结果返回。

---

## 架构与代码位置

```
src/mini_agent/ensemble/
  __init__.py     — 公共 API 导出
  types.py        — Candidate / EnsembleResult 数据结构
  decision.py     — classify_task_type() + should_trigger_ensemble()（规则层+模型自判层）
  judge.py        — 四种评判策略：llm_judge / first_success / vote / merge
  strategies.py   — 候选生成：make_llm_call()（同输入多次调用） / build_subagent_variants()（多人设变体）
  runner.py       — run_llm_ensemble() / run_subagent_ensemble()，串行/并行调度 + 落盘 + hooks
```

配置：`config/models.py` 的 `EnsembleConfig` + `AppConfig.ensemble`；解析逻辑在 `config/loader.py`。

工具：`tools/orchestration.py` 的 `run_ensemble_llm` / `run_ensemble_subagents`。

CLI：`cli/commands/ensemble.py`，挂载在 `cli/repl.py` 的 `/ensemble` 命令。

自动触发：`agent.py` 的 `run_turn()`，在 `_agentic_loop()` 之前插入 AUTO 模式判定与执行。

---

## 已知限制 / 后续可扩展方向

- AUTO 模式自动触发目前只接入了 `subagent` 粒度（整轮替代），`llm_call` 粒度的自动触发仍需 Agent
  通过工具主动调用；如果需要在单次模型调用层面也做到完全自动（不经过工具调用），需要更深地侵入
  `agent.py` 内部的流式调用点（`_do_single_call`），改动成本和风险较高，当前版本暂未实现。
- `max_extra_cost_ratio` 目前只是配置项，尚未接入实际的 token/成本统计做硬性熔断，仍依赖
  `n`/`mode`/规则层关键词来间接控制成本，使用 AUTO 模式时建议先在小流量场景观察实际触发频率。
- 评判模型当前默认与主模型相同（`judge_model=None` 时复用 `cfg.model`），如果想用更便宜的模型
  专门做评判，可单独配置 `ensemble_judge_model`。
