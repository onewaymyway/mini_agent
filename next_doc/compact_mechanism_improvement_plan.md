# Compact 机制主动化改进计划

> 基于对 `history/triggers.py`（5 种触发器 + 优先级 + 冷却）、
> `history/compression.py`（`SelectiveStrategy` 等压缩策略）、
> `agent/compaction.py`（chunked 压缩、skill 重附、notepad 提示）、
> `history/raw_history.py`（全量原始记录持久化）、
> `wiki/decision_writer.py`（决策候选沉淀）现有实现的梳理。
>
> 本计划延续项目一贯的落地习惯：**新增能力先加配置开关（默认关闭）→
> observe 模式收集数据 → 再决定是否默认开启**；优先复用已有框架
> （`CompactTrigger` 体系、`SelectiveStrategy` 打分框架、
> `decision_writer.py` 的 pending 队列），不新建平行系统。

---

## 0. 结论先行：七个方向按"复用程度 / 改造成本 / 价值"排序

| # | 方向 | 复用现有框架程度 | 改造成本 | 价值 | 建议阶段 |
|---|------|------------------|----------|------|----------|
| 2 | 目标相关性动态权重 | 高（复用 `SelectiveStrategy` 打分框架） | 低 | 高 | P0 |
| 7 | Compact 兼做经验沉淀检查点 | 高（复用 `decision_writer.py` pending 队列） | 低 | 中高 | P0 |
| 6 | 安全点判定（避免打断执行中片段） | 中（新增判定，接入现有冷却机制） | 低 | 中（配合后续主动 compact 才有意义） | P1 |
| 1 | 触发信号强度叠加（软触发） | 高（扩展 `TriggerResult`） | 中 | 中 | P1 |
| 4 | 压缩质量事后自检 + 反馈闭环 | 中（新增校验步骤，接 lesson 系统） | 中 | 高 | P2 |
| 5 | Raw history 按需找回工具 | 高（`raw_history.py` 已全量保留） | 中 | 高 | P2 |
| 3 | 预测式压缩节奏（起跑前预估） | 中（依赖 #7 积累的沉淀数据） | 中高 | 中 | P3 |

排序逻辑：P0 两项都是"在现有框架里多接一个信号源"，不新增控制流，风险最低、立即可做；P1 是在 P0 基础上让触发判断更细腻；P2 需要新增一次事后 LLM 校验，属于新的执行路径，放在数据基础打好之后；P3 依赖 P0/#7 积累的历史数据才有得预测，必须排在最后。

---

## 1. P0-A：目标相关性动态权重（对应方向 #2）

### 1.1 现状

`compression.py::SelectiveStrategy.DEFAULT_WEIGHTS` 按 `_type` 静态定价值（`user_input=1.0`、`tool_result=0.4` 等），不感知当前任务进行到哪一步、还差什么。

### 1.2 改造内容

在 `SelectiveStrategy` 的打分函数里增加一个**可选的相关性因子**，来源是 goal_mode 的 `criteria_status`（已实现的验收标准逐条追踪）：

```python
# compression.py::SelectiveStrategy._score_message() 新增参数
def _score_message(
    self,
    msg: dict,
    base_weight: float,
    relevance_hint: Optional["RelevanceHint"] = None,
) -> float:
    if relevance_hint is None:
        return base_weight
    if relevance_hint.is_related_to_unmet_criteria(msg):
        return min(1.0, base_weight + relevance_hint.boost)      # 上调
    if relevance_hint.is_related_to_met_criteria(msg):
        return max(0.0, base_weight - relevance_hint.discount)   # 下调
    return base_weight
```

`RelevanceHint` 由调用方（Goal 模式下的 GoalRunner）在触发 compact 时传入，构建方式：

```python
@dataclass
class RelevanceHint:
    unmet_criteria_keywords: set[str]   # 从未通过的验收标准里提取关键词
    met_criteria_keywords: set[str]     # 从已通过的验收标准里提取关键词
    boost: float = 0.25
    discount: float = 0.2

    def is_related_to_unmet_criteria(self, msg: dict) -> bool:
        # 复用 triggers.py 里 TopicShiftTrigger 已有的 _simple_keywords 粗粒度匹配，
        # 不新增分词/向量化依赖
        ...
```

**非 Goal 模式场景**（普通对话/TurnJudge 驱动的自主续跑）没有 `criteria_status` 可用，`relevance_hint=None`，行为与现在完全一致——这是保证向后兼容的关键：新增因子只在有结构化目标时生效，不影响通用场景。

### 1.3 配置

```yaml
compress:
  goal_aware_weighting_enabled: false
  goal_aware_weight_boost: 0.25
  goal_aware_weight_discount: 0.2
```

### 1.4 涉及文件

- `src/mini_agent/history/compression.py`（`SelectiveStrategy` 打分逻辑）
- `src/mini_agent/goal_mode/runner.py`（触发 compact 时构建并传入 `RelevanceHint`）
- `src/mini_agent/config/models.py`（新增配置字段）

### 1.5 验收标准

- 单测：构造一个有 3 条未通过、2 条已通过验收标准的 GoalState，验证与未通过标准相关的 tool_result 在 compact 后被保留、与已通过标准相关的被优先丢弃。
- 回归：`goal_aware_weighting_enabled=false` 时行为与改造前逐字节一致（现有 `tests/test_goal_mode.py` 全部通过不需要改动）。

---

## 2. P0-B：Compact 兼做经验沉淀检查点（对应方向 #7）

### 2.1 现状

`decision_writer.py` 的决策候选提取目前只在巩固循环（`evolution/consolidation.py::run_consolidation`）里被调用，与 compact 的触发时机完全独立调度。deep compact 时模型已经把这段时期的历史捋了一遍，这个产出被浪费了。

### 2.2 改造内容

在 `agent/compaction.py` 的压缩流程末尾（压缩摘要生成之后），当满足以下条件之一时，顺带调用一次 `history/decision_extraction.py` 提取候选并 `queue_candidates()` 入队（不直接落盘，沿用现有节流去重逻辑）：

- 触发原因是 `topic_shift`（天然的一个决策/阶段边界）
- 触发原因是 `stuck_recovery` 关联的 deep compact（goal_mode 卡住恢复触发的压缩，本身就意味着"这里有一次值得记录的教训"）

```python
# compaction.py，压缩摘要生成之后
def _maybe_extract_decision_candidates(
    self,
    compact_reason: str,
    pre_compact_history: list[dict],
    summary_text: str,
) -> None:
    if not getattr(self.cfg.compress, "decision_extraction_on_compact_enabled", False):
        return
    if compact_reason not in ("topic_shift_heuristic", "topic_shift_llm", "stuck_recovery_deep"):
        return
    try:
        from mini_agent.history.decision_extraction import extract_candidates
        from mini_agent.wiki.decision_writer import queue_candidates
        candidates = extract_candidates(pre_compact_history, summary_text)
        if candidates:
            queue_candidates(self.paths, candidates)
    except Exception:
        pass  # 沉淀失败不影响 compact 主流程，静默跳过
```

关键设计取舍：**提取失败/异常不能影响 compact 本身**——compact 是主循环的关键路径，沉淀是锦上添花，必须做成完全旁路、任何异常吞掉不上抛。

### 2.3 配置

```yaml
compress:
  decision_extraction_on_compact_enabled: false
  decision_extraction_compact_reasons: ["topic_shift_heuristic", "topic_shift_llm", "stuck_recovery_deep"]
```

### 2.4 涉及文件

- `src/mini_agent/agent/compaction.py`
- `src/mini_agent/history/decision_extraction.py`（确认 `extract_candidates` 签名可直接接受"压缩摘要 + 压缩前原始片段"作为输入，若签名不匹配需要加一个适配函数，不改动原有巩固循环调用路径）
- `src/mini_agent/config/models.py`

### 2.5 验收标准

- 单测：mock `queue_candidates`，验证 `topic_shift` 触发的 compact 会调用一次，`turn_count` 触发的不会调用。
- 验证异常场景：`extract_candidates` 抛异常时 compact 流程仍正常完成，摘要正常写入历史。

---

## 3. P1-A：安全点判定（对应方向 #6）

### 3.1 现状

`CompositeTrigger` 的冷却机制只管"距上次 compact 隔了多久"，不管"当前是否处于一次不适合被打断的多步骤执行序列中间"。这一项本身收益有限，但**是后续 P1-B（软触发信号叠加会显著增加触发频率）和未来主动 compact 的前置安全网**，必须先做。

### 3.2 改造内容

在 `history/triggers.py` 新增 `SafePointGate`，不是一个触发器，而是包裹在 `CompositeTrigger.check()` 外层的一道过滤：

```python
class SafePointGate:
    """
    判断当前是否处于"安全点"（可以被 compact 打断而不破坏执行连续性）。
    安全点定义：
      - turn 边界（is_turn_boundary 为真的位置）
      - 或者：最近连续 N 次工具调用属于同一批"只读探索型"调用
        （复用 permissions.py::_RISKY_TOOLS 的反向判断：不在 _RISKY_TOOLS
        里的连续调用视为安全，命中 _RISKY_TOOLS 的调用链条中间不安全）
    """
    def is_safe_point(self, ctx: TriggerContext) -> bool: ...
```

`CompositeTrigger.check()` 改造：

```python
def check(self, ctx: TriggerContext, cfg: "AppConfig") -> TriggerResult:
    best = ...  # 原有逻辑不变
    if not best.triggered:
        return best
    if best.bypass_cooldown:          # token_threshold 硬约束，无条件执行，不受安全点限制
        return best
    if not getattr(cfg.compress, "safe_point_gating_enabled", False):
        return best                    # 开关关闭时行为不变
    if self._safe_point_gate.is_safe_point(ctx):
        return best
    return _PENDING(best)              # 新增一种"挂起"结果，下次安全点时重新校验后执行
```

`_PENDING` 挂起的触发结果需要暂存（挂在 Agent 实例上的一个临时字段即可，不需要落盘），下一轮 `check()` 调用时优先检查是否有挂起项、且当前是否已到安全点。

### 3.3 配置

```yaml
compress:
  safe_point_gating_enabled: false
```

### 3.4 涉及文件

- `src/mini_agent/history/triggers.py`
- `src/mini_agent/agent/turn_loop.py`（挂起态的暂存与下一轮重新校验）

### 3.5 验收标准

- 单测：构造"当前处于连续 3 次 `bash` 调用中间"的 history，验证软触发（如 `turn_count`）被挂起而不是立即执行；`token_threshold` 硬约束不受影响，正常执行。

---

## 4. P1-B：触发信号强度叠加（对应方向 #1）

### 4.1 现状

`TriggerResult` 只有 `triggered: bool`，多个触发器命中时只取 `priority` 最高的一个，弱信号叠加（如 redundancy 轻度命中 + turn_count 轻度命中）会被忽略。

### 4.2 改造内容

给 `TriggerResult` 新增 `intensity: float`（0-1，触发器即使未越过自身硬阈值也可以给出一个"接近程度"分数）：

```python
@dataclass
class TriggerResult:
    triggered: bool
    ...
    intensity: float = 0.0   # 新增：0=完全不接近触发，1=达到阈值
```

每个触发器的 `should_trigger()` 除了返回是否命中，额外提供一个"未命中但接近程度"的分支（不 breaking 现有返回值，只是新增字段）。`CompositeTrigger` 新增一个软触发汇总路径：

```python
def check(self, ctx, cfg) -> TriggerResult:
    hard = ...  # 原有硬命中逻辑不变，优先级最高
    if hard.triggered:
        return hard
    if not getattr(cfg.compress, "composite_intensity_enabled", False):
        return _NOT_TRIGGERED
    total_intensity = sum(t.intensity_hint(ctx, cfg) for t in self._triggers)  # 各触发器独立给出接近程度
    if total_intensity >= cfg.compress.composite_intensity_threshold:
        return TriggerResult(triggered=True, reason="composite_intensity", ...)
    return _NOT_TRIGGERED
```

### 4.3 配置

```yaml
compress:
  composite_intensity_enabled: false
  composite_intensity_threshold: 1.2   # 需要 observe 模式收集数据后再定
```

### 4.4 涉及文件

- `src/mini_agent/history/triggers.py`

### 4.5 验收标准

- 单测：redundancy 单独命中不到阈值（intensity=0.6）+ turn_count 单独命中不到阈值（intensity=0.7），二者相加超过 `composite_intensity_threshold` 时触发 `composite_intensity`，单独任一存在时不触发。

---

## 5. P2-A：压缩质量事后自检 + lesson 反馈闭环（对应方向 #4）

### 5.1 现状

Compact 执行后没有质量校验，压缩是否丢失关键信息只能等下游任务失败时才被动发现。`raw_history.py` 已经全量保留原始记录，`append_compact_event` 已记录每次 compact 事件，具备做校验的原材料。

### 5.2 改造内容

新增 `history/compact_audit.py`，在 deep compact（非高频的 `token_threshold` 触发，避免每次都增加一次 LLM 调用成本）完成后异步执行一次校验：

```python
def audit_compact_quality(
    pre_compact_history: list[dict],
    summary_text: str,
    llm_client: "LLMClient",
) -> CompactAuditResult:
    """
    单次 LLM 调用，输入压缩摘要 + 被丢弃的原始片段（截断到预算内），
    判断是否存在决定性信息（约束条件/失败原因/用户明确要求）被遗漏。
    失败时静默返回 no_issue=True，不影响主流程（这是事后校验，不应阻塞 compact）。
    """
```

- 触发条件：`compact_reason in ("topic_shift_*", "stuck_recovery_deep")` 且 `cfg.compress.audit_enabled=true`，执行时机放在 compact **完成之后**、不阻塞当前轮次返回（可以用现有的异步/后台任务机制，参考 `orchestrator/task_manager.py` 已有的任务提交模式）。
- 校验发现遗漏时：① 补一条 `compact_supplement` 类型的历史条目把遗漏信息追加回去；② 写入 `activity_digest.jsonl`，`type: compact_audit_issue`，累计统计"哪个触发原因/哪种压缩策略遗漏率更高"。
- 反馈闭环：`evolution` 的 lesson 系统消费这批 `compact_audit_issue` 统计，长期跑下来若某个触发器（如 `redundancy`）对应的遗漏率显著偏高，作为软目标候选（复用 `SoftGoalDeriver` 已有的 lesson→goal 衍生链路）自动提出"调整 redundancy 触发对应的压缩策略"这类改进建议，而不是人工凭感觉调参。

### 5.3 配置

```yaml
compress:
  audit_enabled: false
  audit_compact_reasons: ["topic_shift_heuristic", "topic_shift_llm", "stuck_recovery_deep"]
  audit_async: true
```

### 5.4 涉及文件

- 新增 `src/mini_agent/history/compact_audit.py`
- `src/mini_agent/agent/compaction.py`（触发调用点）
- `src/mini_agent/evolution/soft_goal_deriver.py`（消费 `compact_audit_issue` 统计，可放到后续小改造，不阻塞本项落地）

### 5.5 验收标准

- 单测：mock LLM 返回"发现遗漏"，验证补充条目正确追加到 history 且不影响原摘要；mock 返回异常，验证主流程不受影响。
- 成本控制：仅对 deep compact 生效（明确排除 `token_threshold` 高频触发场景），避免显著增加 LLM 调用开销。

---

## 6. P2-B：Raw history 按需找回工具（对应方向 #5）

### 6.1 现状

`raw_history.py` 全量持久化了压缩前的所有原始记录，但目前只是"死档案"，只能人工翻文件查看，agent 自己无法在运行中主动检索找回。

### 6.2 改造内容

新增一个工具 `tools/recall_history.py`，注册为标准工具（走现有 `ToolRegistry` 机制，非侵入式）：

```python
def recall_from_raw_history(query: str, max_results: int = 5) -> str:
    """
    按语义/关键词在 raw_history 中检索被压缩掉的原始片段。
    实现分两档：
      - 轻量档（默认）：复用 triggers.py 已有的 _simple_keywords 做关键词匹配 + 时间倒序，
        不引入向量检索依赖
      - 增强档（可选）：若项目已有 embedding/向量检索基础设施（需确认 perception/ 目录下
        是否已有可复用组件），则用语义检索提升召回质量
    返回：命中片段 + 对应的原始 turn 编号 + 距今轮数，供 agent 判断要不要采信
    """
```

- 挂到工具清单里但**不在 permissions.py 的 `_RISKY_TOOLS`**（纯读取，无副作用）。
- 在 `prompts/system` 里给主 agent 增加一条使用引导：当发现自己"隐约记得处理过某事但当前上下文里找不到细节"时，可以调用这个工具而不是靠猜测或重新执行。
- 这一项的意义在于**给前面几项更激进的压缩策略（P0-A 的目标相关性降权、未来更狠的压缩比例）兜底**：反正删掉的东西找得回来，压缩策略可以更敢于"压狠一点"，把"怕删错"这个心理负担从压缩阶段转移到"按需找回"阶段。

### 6.3 配置

```yaml
tools:
  recall_history_enabled: false
  recall_history_mode: keyword   # keyword | embedding（若可用）
```

### 6.4 涉及文件

- 新增 `src/mini_agent/tools/recall_history.py`
- `src/mini_agent/tools/__init__.py`（工具注册）
- `src/mini_agent/prompts/system/` 对应的系统提示片段（引导何时调用）

### 6.5 验收标准

- 单测：构造一段已被 compact 掉的 raw_history，验证按关键词能正确检索回对应片段及其原始 turn 编号。
- 手工验证：故意让 P0-A 的目标相关性权重把某条重要 tool_result 压掉，验证后续可以通过该工具找回。

---

## 7. P3：预测式压缩节奏（对应方向 #3）

### 7.1 前置依赖

必须在 P0-B（compact 沉淀检查点）和 P2-A（压缩质量审计）积累至少数十次真实运行数据之后才启动，理由与项目一贯的"先攒数据再定参数"原则一致——预测节奏的参数（"这类任务平均多少轮触发一次 compact"）不能拍脑袋。

### 7.2 改造内容（设计，暂不排入具体阶段的实现清单）

任务起跑时（与 P0-A 同一个钩子——GoalSpec 构建阶段），检索历史相似任务的 compact 触发统计（来自 `activity_digest.jsonl` 累积的 `compact_reason` 分布），将 `compress.max_turns_before_compact` / `compress.redundancy_tool_result_ratio` 等阈值按任务类型做**运行时覆盖**（不改全局默认值，只在本次会话生效），起跑时就采用更贴合该类任务特征的压缩节奏，而不是所有任务共用一套静态阈值、被动等信号出现。

### 7.3 配置

```yaml
compress:
  predictive_pacing_enabled: false   # 先占位，具体参数留待 P0-B/P2-A 数据积累后设计
```

---

## 8. 落地顺序总览

```
P0（可立即并行开工，互不依赖）
  ├─ P0-A 目标相关性动态权重      [compression.py + goal_mode/runner.py]
  └─ P0-B Compact 兼做沉淀检查点   [compaction.py + decision_extraction.py]

P1（依赖 P0 跑稳，安全网优先于强化触发）
  ├─ P1-A 安全点判定               [triggers.py + turn_loop.py]
  └─ P1-B 触发信号强度叠加         [triggers.py]

P2（依赖 P0/P1 的埋点数据，且各自新增一条执行路径，需要独立评估）
  ├─ P2-A 压缩质量事后自检+反馈闭环 [新增 compact_audit.py]
  └─ P2-B Raw history 按需找回工具  [新增 recall_history.py]

P3（依赖 P0-B + P2-A 积累的真实数据，暂不列入具体实现清单，先设计）
  └─ 预测式压缩节奏
```

所有项统一遵循：**新增配置默认为 `false`，先以 `false` 状态跑完整回归测试确认零行为变化，再逐项在测试/预发环境打开观察，最后才考虑默认值调整**。这与项目现有的 `light_compact_max_recoveries=0` 一键回退、`replan_proposal_mode` 三态开关等做法保持一致的风格。

---

## 9. 需要新增/修改的测试清单（对应现有 `tests/` 目录风格）

| 测试文件 | 覆盖内容 |
|---------|----------|
| `tests/test_compression.py`（若不存在需新建） | P0-A 目标相关性权重打分；开关关闭时零行为变化回归 |
| `tests/test_triggers.py`（若不存在需新建） | P1-A 安全点挂起/恢复；P1-B 信号强度叠加触发 |
| `tests/test_compaction.py`（若已存在则追加） | P0-B 沉淀候选提取调用时机；P2-A 审计流程的正常/异常路径 |
| `tests/test_goal_mode.py`（追加用例） | Goal 模式下 `RelevanceHint` 正确构建并传入 compact 流程 |

---

## 10. 一句话总结

七个方向里，**P0 两项（目标相关性权重、compact 兼做沉淀检查点）都是"在现有框架里多接一根线"，没有新执行路径、没有新增 LLM 调用成本，应该最先做**；P1 是给后续更激进的主动触发上安全带；P2 两项各自新增了一条独立执行路径（审计调用、检索工具），价值最高但要独立评估成本，且互为犄角——P2-B（按需找回）让 P0-A/P2-A 揭示出的"压缩策略可以更激进"变得敢于落地；P3 完全依赖前面积累的数据，不应该在没有数据支撑前设计具体参数。
