# flat / nested 配置写法统一迁移计划

- **版本**: v1.1
- **状态**: 已完成（Stage 1-3）
- **变更记录**:
  - v1.0：初版设计，聚焦"消灭 flat 手写构造代码"这一件事，不做其他改动。
  - v1.1：Stage 1（`load_nested_block_with_flat_compat()` 通用兼容层）、
    Stage 2（11 个 block 全部迁移）、Stage 3（`config_catalog.py` 看板
    字段目录同步迁移 + `docs/param-system-guide.md` 更新）已完成。
    Stage 4（移除兼容层）保持"可选、需团队决策后再排期"，未开始。

---

## 0. 背景与问题

`docs/param-system-guide.md` 已经把 `agent_config.json` 里两种历史遗留的
落盘方式讲清楚了：

1. **flat（历史遗留）**：顶层扁平键，键名往往不等于 dataclass 字段名（如
   `"memory_enabled"` 对应 `MemoryConfig.enabled`），`loader.py` 里为每个
   字段手写一行 `XxxConfig(field=_fb("json_key", cli_val, default), ...)`。
2. **nested（新约定）**：顶层是一个 dict block，block 内字段名与 dataclass
   字段名完全一致，`param_registry.load_nested_block()` 用
   `dataclasses.fields()` 自动展开，新增字段只需要改 `models.py`。

`param-system-guide.md` 当时的结论是"存量的 flat 调用点数量大、逐个改造
风险高，本次不做存量迁移，但新参数一律要求走 nested"。这个决定在当时是
合理的止血措施，但副作用是：**flat 这条路径被冻结在"新参数不许走这里"，
却从未真正退役**，仍有 11 个 block、151 个字段留在手写构造代码里，且已经
连续两轮暴露同类 bug：

- `AutonomyConfig`/`ObservabilityConfig`：曾经完全没有从文件读取（`docs/
  goal-execution-fairness-config.md` 记录的历史事故）。
- `SchedulerConfig`/`MemoryBackfillConfig`/`ExecutionPhaseConfig`：曾经
  注册进了 `param_registry` 但从未真正传给 `AppConfig(...)`（loader.py
  里对应的"遗留缺陷修复"注释）。
- `MemoryConfig`/`CompressConfig`/`ToolTrimConfig`/`SkillConfig`/
  `HttpConfig`/`RetryConfig`：这 6 个手写 block 里合计 40 个字段
  （`library_index_*`/`wiki_*`/`embedding_*`/`extraction_trigger_*`/
  `network_*`/`blocking_call_*` 等）此前从未被 `loader.py` 读取，最新一轮
  排查才发现并修复（见本文档 §5 关联记录）。

三次事故的根因完全一致：**flat 手写构造代码要求"新增字段时，`models.py`
和 `loader.py` 两处同步改"，而这件事没有任何机制强制，全靠人工细心**。
nested block 从设计上就不存在这个问题——`load_nested_block()` 用反射遍历
字段，`models.py` 加字段后 `loader.py` 不需要改一行代码。

**这份计划要解决的不是"再修一次 bug"，而是把这条会反复产生同类 bug 的
路径彻底关闭**：把剩下的 11 个 flat block 迁移成 nested-only，让"新增字段
忘记接入 loader"这类问题从代码结构上变得不可能发生，而不是靠 review 靠
测试兜底。

---

## 1. 现状盘点

`config/loader.py`（950 行）里仍然手写构造的 11 个 block：

| block | dataclass | 字段数 | flat key 与字段名不一致的典型例子 |
|---|---|---|---|
| memory | MemoryConfig | 30 | `memory_top_k` → `top_k`、`lesson_rules_enabled` → 同名（不一致的是前缀） |
| compress | CompressConfig | 40 | `compact_max_turns` → `max_turns_before_compact`、`auto_compress_threshold` → `threshold` |
| tool_trim | ToolTrimConfig | 15 | `tool_result_trim_enabled` → `enabled`、`tool_trim_bash_tail_ratio` → `bash_tail_ratio` |
| skill | SkillConfig | 11 | `skill_semantic_enabled` → `semantic_enabled` |
| perception | PerceptionConfig | 8 | `project_scan_enabled` → 同名 |
| session | SessionConfig | 7 | `session_fmt` → `fmt`、`auto_save_session` → `auto_save` |
| profile | ProfileConfig | 5 | `profile_enabled` → `enabled` |
| debug | DebugConfig | 3 | `debug_llm` → `llm_enabled` |
| http | HttpConfig | 13 | `http_ring_maxlen` → `ring_maxlen` |
| retry | RetryConfig | 9 | `llm_retry_max` → `max_retries` |
| ensemble | EnsembleConfig | 10 | `ensemble_n` → `n` |

其中 `memory`/`compress`/`tool_trim`/`skill`/`http`/`retry` 6 个已经在上一
轮修复里包了一层 `load_nested_block()` 兜底（详见 §5），字段不再"读不到"，
但 flat key 手写构造代码本身还在，"两处同步改"的风险并未消除——只是从
"配置读不到"降级成了"nested 写法和 flat 写法可能互相打架，行为取决于
`apply_overrides()` 的覆盖顺序，理解成本更高"。`perception`/`session`/
`profile`/`debug`/`ensemble` 这 5 个则完全没有兜底，字段缺口只是运气好还
没被撞上。

## 2. 目标

1. 11 个 block 全部改成走 `param_registry.NESTED_CONFIG_BLOCKS` +
   `load_nested_block()`，`loader.py` 里不再有针对这些 block 的手写字段
   级构造代码。
2. 仍需要 CLI 覆盖的字段（如 `--memory`、`--yes`、`--session-fmt`），继续
   用 `apply_overrides()` 两段式组合（与 `reminder`/`privacy` 现有模式
   一致），但**只覆盖真正需要 CLI 的那几个字段**，不是整个 block。
3. 旧的 flat key（`memory_enabled`、`compact_max_turns` 等）保留读取兼容，
   避免存量 `agent_config.json` 用户升级后配置失效——但只在 nested 写法
   （`"memory": {"enabled": ...}`）缺省时才回退读取 flat key，且明确标记
   为"兼容层，计划 N 个大版本后移除"。
4. 迁移完成后，`config/loader.py` 行数预期从 950 行降到 400 行以内（对照
   `param-system-guide.md` 里给出的重构前后行数对比，nested 化后单个
   block 的构造代码通常从 10-30 行降到 1-3 行）。

## 3. 不做的事（边界）

- 不改字段的默认值、语义、类型——纯粹是"配置怎么读进来"的迁移，不涉及
  任何功能行为变化。
- 不强制要求现有 `agent_config.json` 用户立刻把 flat key 改写成 nested
  写法——兼容层保留期内两种写法都能用，只是 nested 优先。
- 不迁移 `mcp` block——`param_registry.py` 里已经说明它是"独立 dataclass
  组成的列表"，天然不适合这套机制，维持现状。
- 不在这次迁移里给 `AppConfig` 的向后兼容 property（`cfg.memory_enabled`
  这类委托属性）做任何改动，那是另一个独立的技术债，见其他计划。

## 4. 分阶段方案

### Stage 1：建立"flat 兼容层"通用工具函数（1 个 PR，风险最低，可独立验证）

在 `param_registry.py` 里新增一个通用函数，取代 §1 表格里那种"字段名不
一致"的手写映射：

```python
def load_nested_block_with_flat_compat(
    file_cfg: dict,
    attr_name: str,
    cls: type,
    flat_key_map: dict[str, str],   # {dataclass字段名: 旧flat key}
    env_fallback: Optional[dict] = None,
) -> Any:
    """nested block 优先；nested 里缺失的字段，回退读取 flat_key_map
    里登记的旧 flat key（若也缺失，则用 dataclass 默认值）。
    用于存量 flat block 迁移期的过渡兼容，不建议新 block 使用——新 block
    直接用 load_nested_block()，不需要 flat_key_map。
    """
```

这一步只加代码、不改任何调用点，可以先补单元测试验证行为符合预期
（nested 优先、flat 兜底、都没有则用默认值），风险为零。

### Stage 2：逐个 block 迁移，按"字段名一致性"从易到难排序

每个 block 一个独立 PR，方便单独 review、单独回滚：

1. **完全无 flat/字段名不一致问题、也没有 CLI 覆盖需求的**：
   `perception`（8 字段，flat key 与字段名基本一致）、`debug`（3 字段）——
   直接迁移，`flat_key_map` 里大部分条目是"flat key == 字段名"。
2. **有少量 CLI 覆盖需求，但字段名基本一致的**：
   `profile`、`session`（`--session-dir`/`--session-fmt`/
   `--no-save-session` 等）——用 `apply_overrides()` 处理 CLI 覆盖的那
   几个字段，其余走 nested。
3. **字段名不一致较多、但没有 CLI 覆盖的**：
   `skill`、`ensemble`、`http`（`http_ring_maxlen` → `ring_maxlen` 等）——
   工作量主要在写 `flat_key_map`，逻辑本身简单。
4. **最复杂的三个，放最后，且建议分别独立成 PR**：
   `memory`（30 字段，`store_path` 涉及 `str→Path` 转换）、`compress`
   （40 字段，`audit_compact_reasons` 是 list 类型需要特殊处理）、
   `retry`（9 字段但 `network_aware` 等已在上一轮加过 nested 兜底，需要
   核对不要重复定义覆盖逻辑）。

每个 PR 的验收标准：
- 迁移前后，用同一份 `agent_config.json`（同时包含 flat 写法和 nested
  写法的测试夹具）跑 `load_config()`，两次产出的 `AppConfig` 对应字段值
  完全一致（写一个"迁移前后行为不变"的对照测试，通过后即可删除该测试，
  它只是这一步的安全网，不是长期维护的用例）。
- `loader.py` 里针对该 block 的手写字段级构造代码清零。
- `config_catalog.py` 里该 block 对应的 `_XXX_FIELDS`（flat 版本）替换成
  `_NESTED_BLOCKS` 条目，看板展示的 `json_key` 从 `"memory_enabled"` 变成
  `"memory.enabled"`（`apply_updates()`/`_is_customized()` 已经原生支持
  这种带 `.` 的 json_key，不需要额外改动，Stage 1 的迁移已经验证过这一
  点）。

### Stage 3：清理与文档更新

- 全部 11 个 block 迁移完后，检查 `loader.py` 里的 `_f`/`_fb`/`_fn` 三个
  闭包是否还有调用点——如果只剩核心运行参数（`verbose`/`model`/
  `max_turns` 等，本来就不属于任何 block，不在本计划范围内），可以考虑
  是否也纳入后续的 `FLAT_PARAM_SPECS` 机制，但那是另一个计划的范围。
- 更新 `docs/param-system-guide.md`：删除"存量不做迁移"的说明，改成
  "存量 flat block 已于 vX.Y 全部迁移完成，仅保留只读兼容层"。
- 给 `load_nested_block_with_flat_compat()` 的 docstring 和相关配置文档
  加上"计划 N 个大版本后移除 flat_key_map 兼容层，届时只接受 nested 写
  法"的时间表。

### Stage 4（可选，需要团队决策后再排期）：移除 flat 兼容层

设定一个明确的版本号或时间点，移除 `flat_key_map` 回退逻辑，`loader.py`
只接受 nested 写法。需要提前一个大版本在 CHANGELOG / 启动时警告里通知
存量用户迁移自己的 `agent_config.json`。

## 5. 关联记录

- 本次迁移动机直接来自一次配置加载缺陷排查：`memory`/`compress`/
  `tool_trim`/`skill`/`http`/`retry` 6 个 block 合计 40 个字段被发现从未
  被 `loader.py` 读取，已用"`load_nested_block()` 通用加载 +
  `apply_overrides()` 覆盖 flat key"的方式临时修复（见 `loader.py`/
  `config_catalog.py` 对应位置的注释）。**这次临时修复是本计划 Stage 2
  第 4 类 block 的起点，不是终点**——临时修复只解决了"字段读不到"，
  没有解决"flat 手写构造代码继续存在、两处需要同步改"的根本问题。
- `docs/param-system-guide.md` §1 记录了 `autonomy`/`observability` 的
  历史事故，是这套 nested 通用机制最初被引入的原因。
- `docs/goal-execution-fairness-config.md` 记录了 `autonomy` block 曾经
  配置不生效对用户造成的实际影响，可作为"为什么值得投入时间做这次迁移"
  的具体案例引用。

## 7. 实施记录（Stage 1-3 完成情况）

- **Stage 1**：`param_registry.py` 新增 `load_nested_block_with_flat_compat()`
  及共享的 `_convert_field_value()`（从 `load_nested_block()` 里提取出来，
  避免两处各写一份类型转换规则）。单元测试见
  `tests/test_flat_nested_config_compat.py::TestLoadNestedBlockWithFlatCompat`。
- **Stage 2**：`loader.py` 里 11 个 block 的手写字段级构造代码全部清零，
  改成 `load_nested_block_with_flat_compat()` + 少量
  `apply_overrides()`（仅覆盖真正有 CLI/函数参数需求的字段）。端到端测试
  见 `tests/test_flat_nested_config_compat.py::TestElevenBlocksEndToEnd`。
  过程中顺带修正了几处"旧 flat key 无条件覆盖 nested 写法"的反向优先级
  问题（如 `memory.enabled`、`skill.semantic_enabled`、
  `perception.project_scan_enabled` 等）——迁移前这些字段即使写了 nested
  形式也会被 `apply_overrides()` 里硬编码的 flat 默认值覆盖回去，现在
  统一成"nested 优先"。`compress.strategy` 和
  `compress.max_message_chars_for_compact` 的历史遗留默认值不一致问题见
  §7.1，前者原样保留，后者按 dataclass 默认值修正（见下）。
- **Stage 3**：`config_catalog.py` 里 `memory`/`compress`/`tool_trim`/
  `skill`/`perception`/`session`/`profile`/`debug`/`http`/`retry`/
  `ensemble` 对应的手写 `_XXX_FIELDS`（约 185 行）全部删除，改成加入自动
  展开的 `_NESTED_BLOCKS` 列表，json_key 从 `"memory_top_k"` 变成
  `"memory.top_k"`（看板 PATCH 写回统一使用 nested 形式）。
  `docs/param-system-guide.md` 新增第 7 节说明新旧写法与优先级。

### 7.1 迁移中发现的行为差异（已确认按"贴近 dataclass 真源"原则处理）

- `compress.max_message_chars_for_compact`：迁移前硬编码的 flat 默认值是
  `10000`，但 `CompressConfig.max_message_chars_for_compact` 的 dataclass
  默认值实际是 `40_000`——`agent_config.json` 里没有配置这个字段的用户，
  迁移前实际生效的是 `10000`，迁移后变成 `40_000`。这属于本计划 §0 提到
  的"两处默认值不同步"同类问题，按迁移目标（`models.py` 是唯一真源）修正
  为 `40_000`。**如果这个改动影响到存量用户的实际压缩行为，需要单独评估
  并在 CHANGELOG 里说明**。
- `compress.strategy`：迁移前硬编码默认值 `"turn_aligned"` 与 dataclass
  默认值 `"compact_with_skills"` 不一致，且这个硬编码值此前会无条件覆盖
  nested 写法。这处**原样保留**（未按 dataclass 默认值修正），因为
  `strategy` 是压缩策略这种影响面较大的字段，改动需要更谨慎的评估，留给
  后续独立的技术债处理。
- 其余 9 个 block 的所有字段，迁移前后默认值/优先级完全一致，无行为差异
  （已用 `tests/test_flat_nested_config_compat.py` 里的对照测试验证）。

## 8. 风险与缓解

| 风险 | 缓解措施 |
|---|---|
| 迁移过程中改错字段名映射，导致存量配置静默失效 | Stage 2 每个 PR 强制要求"迁移前后行为一致"的对照测试；`load_nested_block()` 对类型不匹配的脏字段本身就有 `_fallback_field()` 兜底（回退默认值 + 记 warning log），不会崩溃，但仍需测试覆盖避免"静默换了个值" |
| `flat_key_map` 兼容层本身又变成新的技术债 | Stage 3 明确写入移除时间表，不做"没有截止日期的兼容层" |
| 11 个 PR 分批合并期间，`config_catalog.py` 的 `_NESTED_BLOCKS`/`_FLAT_CATEGORIES` 会有一段时间新旧混杂 | 每个 PR 保证自己涉及的 block 端到端一致（loader + catalog 同一个 PR 一起改），不跨 block 混合修改 |
| 复杂 block（memory/compress）字段多、映射表长，人工核对容易漏 | 用 §1 的字段统计脚本生成 `flat_key_map` 初稿，人工只做校对，不从零手写 |
