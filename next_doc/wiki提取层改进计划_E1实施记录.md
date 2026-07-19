# wiki 提取层与组织层改进计划 · E1 实施记录

> 对应 `wiki知识库提取与组织层改进计划.md` §1（问题 E1：抽取时机与对话
> 粒度错配），实施 §1.2.1（独立触发器）与 §1.2.2（"仅抽取、不压缩"的
> 轻量抽取路径）。按 §8 排期，这是 O1、E2方案B、E3 之后的第四项，也是
> 第二批的最后一项。

## 1. 改动内容

### 1.1 §1.2.1 独立候选窗口探测器

- 新增 `src/mini_agent/history/extraction_trigger.py`：
  - `scan_for_extraction_window(raw_entries, *, last_extracted_index, ...)`：
    规则驱动、零 LLM 成本的候选窗口探测，操作对象是 **raw history**
    （`RawHistory.entries`，append-only、永不因 compact 被清空/重置），
    不是会被 compact 清空的 active history——这是"抽取时机与 compact
    解耦"的关键，`last_extracted_index` 可以稳定持久化为单调递增游标。
    - 触发规则 1：连接词密度（"因为/所以/决定/改为/放弃/取代/而不是"）
      超过阈值（默认每 100 字符 0.6 次命中，计划原文标注为需要用真实
      数据校准的参数）。
    - 触发规则 2：新增内容里的真实用户输入轮次（`is_turn_boundary`）
      达到 `min_window_turns`（默认 6），无论连接词密度如何——避免话题
      平淡但确实积累了内容的 session 永远不被抽取。
    - 触发规则 3（session 结束兜底）不在本函数内实现，由调用方在 session
      结束时以 `force=True` 单独处理（见 §1.2）。
  - `load_extraction_cursor`/`save_extraction_cursor`：游标持久化到
    `storage/paths.py::AgentPaths.extraction_cursor_path`
    （`.agent/extraction_cursor.json`），原子写入（临时文件 + `os.replace`），
    读取失败/文件损坏均静默降级为 0（"还没抽取过"），不阻断主流程。
  - `log_extraction_trigger_event`：候选窗口命中记录追加写入
    `AgentPaths.extraction_trigger_log`
    （`.agent/extraction_trigger_log.jsonl`），供计划 §1.4 校准阶段使用。

### 1.2 §1.2.2 独立触发入口 + "仅抽取、不压缩"的轻量抽取路径

- `src/mini_agent/history_manager.py::HistoryManager`：
  - 新增 `maybe_trigger_extraction(llm_client=None, *, force=False)`：
    每轮工具调用批次结束后调用（成本极低：默认关闭时只有一次属性检查）。
    `force=True` 时跳过规则判定，只要游标之后还有新内容就视为命中
    （session 结束兜底，触发规则 3）。
  - 新增 `_dispatch_lightweight_extraction(...)`：复用
    `history/compression.py` 现成的 `cap_oversized_messages` /
    `parse_decision_response` / `parse_world_response` /
    `wiki/decision_writer.py::queue_candidates` /
    `wiki/world_writer.py::queue_entities`/`queue_facts`，只是把 prompt
    换成 §1.3 新增的"轻量抽取"专用模板；产出的候选依然走既有 pending
    队列 + 巩固循环（`wiki/decision_writer.py`/`wiki/world_writer.py`
    的 `consolidate_pending`），**不新增任何落盘路径**。
    结果同样接入 §3（E3）的实体索引注入
    （`wiki/entity_digest.py::build_entity_digest_section`），与 compact
    路径的抽取质量保持一致。
- `src/mini_agent/agent/turn_loop.py::_agentic_loop()`：在既有的 compact
  触发器检查之后（同一 while 循环体内，每轮 LLM 调用前）新增
  `self._hist.maybe_trigger_extraction(llm_client=self._llm)` 调用，与
  compact 触发检查相互独立——同一轮里可以"不触发 compact、但触发一次
  独立抽取"。
- `src/mini_agent/agent/lifecycle.py::close()`：在关闭 raw_history 文件
  句柄之前，先以 `force=True` 调用一次 `maybe_trigger_extraction`
  （触发规则 3：session 结束兜底）。`maybe_trigger_extraction` 内部第一步
  就是检查 `extraction_trigger_enabled` 开关，默认关闭时这里是一次
  属性判断的开销，不影响任何既有测试对 `close()` 的调用。

### 1.3 新增"轻量抽取"专用 prompt

- `src/mini_agent/prompts/system/lightweight_extractor.md`：
  schema 与 `system/compress_summarizer.md` 基本一致（`decisions[]`/
  `entities[]`/`facts[]`，`entities[]` 同样带 E3 的 `reused_existing_id`
  字段并接受 `{{ entity_digest_section }}` 注入），**但要求
  `compact_summary` 字段固定为空字符串**——这不是摘要任务，只是复用同一个
  JSON 形状，好让 `history/decision_extraction.py::parse_decision_response`
  （该函数要求 `compact_summary` 键必须存在，否则整段判定为解析失败）
  不用改动就能直接复用。
- `src/mini_agent/prompts/user/lightweight_extraction_request.md`：对应的
  user 请求文本，说明这段内容是被启发式规则标记为"可能值得抽取"，但要求
  模型不要为了填满数组而编造内容。

### 1.4 配置开关（`CompressConfig`，`src/mini_agent/config/models.py`）

| 字段 | 默认值 | 作用 |
|---|---|---|
| `extraction_trigger_enabled` | `True`（**[2026-07 更新]** 原为 `False`） | 独立抽取触发器总开关 |
| `extraction_trigger_dispatch_enabled` | `True`（**[2026-07 更新]** 原为 `False`） | 命中候选窗口后是否真的发起 LLM 调用 |
| `extraction_trigger_min_window_turns` | `6` | 触发规则 2 的轮次阈值 |

三者最初设计为默认全部关闭/保守：这条路径在既有 compact 触发器之外新增
了一次"每轮工具调用批次结束后"的扫描，即使零 LLM 成本，原计划设想是先跑
一段真实使用周期、用 `extraction_trigger_log.jsonl` 校准连接词密度阈值是否
合理，再逐步打开 `extraction_trigger_dispatch_enabled`——这是吸取原计划
反复提到的 P4"零数据切换"教训后的做法，与 O1/E2/E3 一贯的执行纪律一致。

**[2026-07 更新]**：应用户明确要求，`extraction_trigger_enabled` 与
`extraction_trigger_dispatch_enabled` 两者均已改为默认 `True`，跳过了上面
描述的观察期——这是本条记录里唯一与原计划执行纪律不一致的地方，如果后续
发现触发过于频繁或抽取质量不理想，可以查 `extraction_trigger_log.jsonl`
的命中率反推调低 `EXTRACTION_TRIGGER_MIN_CONNECTOR_DENSITY`（见
`history/extraction_trigger.py` 里的默认阈值常量），或者单独把
`extraction_trigger_dispatch_enabled` 重新设为 `False`，退回"只记录不抽取"
的观察模式，不需要改代码。

## 2. 验收方式

- `tests/test_extraction_trigger.py`（14 项用例，全部通过）：
  - `scan_for_extraction_window`：连接词密度命中、轮次计数命中（密度阈值
    故意调到不可能命中，只测轮次信号）、两个信号都不命中时返回
    `None`、游标越界/无新内容时返回 `None`。
  - `load_extraction_cursor`/`save_extraction_cursor`：往返读写、文件不
    存在时默认 0、文件损坏时容错返回 0。
  - `log_extraction_trigger_event`：append 格式校验（`dispatched` 字段
    区分校准阶段 vs 实际触发）。
  - `HistoryManager.maybe_trigger_extraction` 端到端场景：默认关闭时
    完全不产生副作用（不写日志、不推进游标、不调用 LLM）；开启但
    `dispatch` 关闭时只写候选窗口日志、不调用 LLM、不推进游标；`dispatch`
    开启时正确发起 LLM 调用、解析出的 decision 候选正确写入
    `decision_candidates_pending_path`、游标推进到当前 raw 长度；同一段
    内容不会被重复抽取（幂等性——第二次调用时因为没有新内容，
    `scan_for_extraction_window` 直接返回 `None`，LLM 调用次数不增加）；
    `force=True` 能无视规则阈值强制触发（session 结束兜底）。
- 回归：`tests/test_compact_audit.py`、
  `tests/test_compact_autopilot_improvements.py`、
  `tests/test_extraction_stats.py`、`tests/test_selective_compression.py`、
  `tests/test_session_end_reflection.py`、`tests/test_undo.py`、
  `tests/test_entity_digest.py`、`tests/test_wiki_index_reuse.py`
  共 111 项用例全部保持通过——覆盖了本次改动接触到的
  `history_manager.py`/`agent/turn_loop.py`/`agent/lifecycle.py`/
  `history/compression.py` 相关的既有测试面。
- 另外核对过：`tests/` 全量跑了一遍，除本次新增测试外还有若干
  预先存在、与本次改动无关的失败（`anthropic`/`nvidia` SDK 未安装、
  `SkillLoader._auto_activate_blocked` 属性缺失等），逐一抽查确认均与
  本次改动的文件无关（详见下方"已知的预先存在问题"）。

## 3. 与原计划的差异说明

- **§1.2.2 提到的"compact 与独立抽取路径共存时跳过重复抽取"，本次未实现
  为动态的、按 compact 范围精确判定的逻辑**。原因：`CompressionStrategy.
  compress(history, cfg, llm_client)` 的入参只有 active history，没有
  raw history 的游标信息，要做到"精确判断这段 compact 范围是否已经被
  独立触发器抽取过"需要打通 active history 索引 ↔ raw history 索引的
  映射（active history 会因为历次 compact 不断变成新的连续序列，与
  append-only 的 raw history 之间没有现成的位置对应关系），改动面明显
  超出本次范围。**改为复用原计划自己在 §2.2.2 提出的更粗粒度方案**：
  `CompressConfig.extract_decisions`/`extract_world_model` 这两个开关
  本来就能完全关闭 compact 路径的结构化抽取；原计划 §2.2.2 第二步原文
  就是"E1 落地并稳定运行后，逐步把这两个开关默认改为 `False`"——即人工
  观察 `extraction_trigger_log.jsonl`/`extraction_stats.jsonl` 一段时间、
  确认独立触发路径工作正常后，再手动关闭 compact 路径的重复抽取，而不是
  本次就实现自动化的动态跳过判定。
- 连接词密度阈值（`0.6/100 字符`）是没有真实数据支撑的初始猜测值，计划
  §1.4 本身也说明这需要校准；因此默认让 `extraction_trigger_dispatch_
  enabled=False`，先只观测 `extraction_trigger_log.jsonl` 里的命中频率，
  再决定要不要调整阈值、打开实际抽取开关。

## 4. 已知的预先存在问题（与本次改动无关，仅记录供参考）

在跑全量回归测试时发现以下失败与本次改动无关（改动前同样会失败，涉及的
文件本次均未触碰）：
- `anthropic`/`nvidia` 等 LLM provider SDK 在本次工作环境里未预装，相关
  测试因 `ModuleNotFoundError`/`LLMProviderError` 失败（`anthropic` 已在
  本次工作环境里临时装上后大部分恢复通过）。
- `src/mini_agent/skills/__init__.py::SkillLoader.activate()` 引用了
  `self._auto_activate_blocked`，但该属性名与类里实际定义的
  `auto_activate_blocked` 不一致，导致 `AttributeError`，波及
  `test_skill_manager.py`/`test_skill_cli.py`/`test_skill_compact.py`/
  `test_subagent_inheritance.py` 等一大片 skill 相关测试。这是一个独立
  的既有 bug，与 wiki 提取层/组织层改进计划无关，建议另开 issue 处理，
  本次不顺带修复（保持改动范围聚焦）。

## 5. 未在本次实施范围内的项

- 按 §8 排期，第二批（E3、E1）已全部完成。下一批（第三批）是：
  - E2 方案 C（compact 与独立抽取路径的开关切换）——依据本记录 §3 的
    说明，其核心机制（`extract_decisions`/`extract_world_model` 开关）
    已经就位，"切换"本身是观察期后的人工操作，不需要额外代码；如果
    后续希望自动化这一决策，可以在此基础上再实现。
  - O2（多跳衰减图扩展）、O3（topic 再巩固），均依赖 O1（已完成）。
- 第四批 O4（统一知识生命周期状态机）依赖第二、三批全部验证稳定后再做。
