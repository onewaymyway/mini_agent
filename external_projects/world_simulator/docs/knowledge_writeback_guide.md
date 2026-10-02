# 跨实例知识库写入指南（第二十二轮 P9）

> 对应计划：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md` §10 P9。代码：`world_simulator/knowledge_base.py`
> （写入/撤销/汇总）、`tree_grounding.kb_calibration_tiers`、`tree_effects.kb_stats`、`engine/knowledge._safe_record_p9_stats`、
> `entrypoints/knowledge.py`（命令行）。测试：`tests/test_p9_kb_writeback.py`。

## 写什么

跨实例知识库（`data/_knowledge/causal_knowledge.jsonl`）此前有两类来源：`record_causal_links()`（因果链沉淀）与 P5b 的边兑现回写。P9 新增两类，
都是**一个实例里 LLM 自报统计**的聚合，条目字段 `origin` 区分：

| `origin` | 内容 | `validated_count` | `contradicted_count` |
|---|---|---|---|
| `likelihood_calibration` | 某分支上、某个 likelihood 档位（high/medium/low）的未来树分支终结情况，每档一条 | resolved 数 | expired + invalidated 数 |
| `tree_declaration` | 分支 `effects_if_active` 声明的兑现统计，每条声明一条 | `realized` 数 | `countered` 数 |

两类条目都带 `self_reported=true`、`source_instance`（写入实例 id）、`confidence=hypothesis`。**它们不是现实世界的校准率或验证过的因果**：
档位是 LLM 主观给的，终结状态与处置结论也是 LLM 自己标的，引擎无法核验。

## 开关（`manifest.settings`，默认都开，但各有前置条件）

| 开关 | 默认 | 前置条件 |
|---|---|---|
| `kb_calibration_writeback` | True | 必须开启 `tree_grounding_enabled` |
| `tree_kb_writeback` | True | 必须同时开启 `tree_effects_enabled` 与 `causal_engine_enabled` |
| `kb_min_samples` | 3（占位值） | 校准：档位内 resolved+expired+invalidated ≥ 它；树声明：realized+countered ≥ 它（`dampened`/`postponed`/自动结案不计） |

默认开是沿用 `causal_kb_writeback` 的先例；加前置条件是为了让**没用过这些机制的旧实例**升级后不会悄悄开始往共享库写。设置页（因果树接地面板内）有三个控件。

## 行为口径

- **绝对值覆盖，幂等**：每次从分支历史重算计数并覆盖，不是增量累加；条目以 `evidence` 里的引用 `sim@branch#likelihood:档位` / `sim@branch#treedecl:边id` 定位。重复推进、补跑不会重复计数；没有变化时不重写文件。
- **只统计本分支自己产生的事件**：分叉分支的历史前半段是从源分支拷贝来的，只统计 `step > from_step` 的事件，否则同一事件会被两条分支各写一遍。分叉元信息缺失（`from_step` 取不到）时**宁可不写**。
- **P9 条目与旧来源互不污染**：不参与 `record_causal_links()` 的 Jaccard 合并、不被 P5b 边结论命中、不被现实反馈（`update_confidence_from_reality_check`）证伪。
- **校准条目不进检索**：它是"某档位命中几成"的元信息而不是一条因果关系，`search()` 默认排除（不进提示词），只在知识库页面显示；树声明条目照常可被检索。
- **读取侧标注**：提示词里自报条目后缀"【LLM 自报统计，非验证事实】"；知识库页标 🧾 并显示警告；P5b 的边兑现条目（旧来源）也补了 `self_reported`。
- **旁路兜底**：写入在推进落盘之后，两路各自独立 try/except，失败不影响推进。`advance()` 与 `advance_lines()` 共用同一个函数。
- **旧文件向后兼容**：新字段只在有值时才落盘；旧条目与旧文件逐字节不变。

## 撤销

```bash
python entrypoints/knowledge.py list                         # 各实例写了什么
python entrypoints/knowledge.py retract <sim_id> --dry-run   # 先看会发生什么
python entrypoints/knowledge.py retract <sim_id>             # 移除该实例的 P9 条目
python entrypoints/knowledge.py retract <sim_id> --include-legacy   # 另外移除"确认只由该实例产生"的旧来源条目
```

- 默认只移除该实例的 P9 条目。`--include-legacy` 另外移除满足全部条件的旧来源条目：`source_sim_id` 是它、`evidence` 非空且**全部**来自它、没有观察标注。
- **与别的实例合并过计数的旧条目不会动**：计数器是合并后的总和，没有记录各来源各加了多少，无法精确回退；命令只报告数量（`left_shared`）。
- 引用匹配用分隔符精确匹配，`s1` 不会误伤 `s10`。
- **删除实例不会自动撤销**，需要手动运行上面的命令。

## 已知边界

1. **跨世界污染只能缓解不能消除**：一个设定离谱的实例会把偏差写进共享库，影响别的实例的提示词（树声明条目可被检索）。撤销、标注、小样本门槛是缓解手段。
2. **第一个被推进的步没有基线快照**，在这一步就终结的分支不被计入（P5c 账本既有口径，`tests` 里有用例固定）。
3. 旧实例只统计 WP0 之后写入快照的步。
4. `merge_branch` 之后，被并入的那一段事件可能被目标分支再算一次（未处理）。
5. 与别的实例合并过计数的旧条目无法精确撤销（见上）。
6. 样本阈值 3 是占位值，没有数据依据。
7. P9 顺带补了一处 P8 遗漏：`advance_lines()` 路径此前从未调用 P5b 的边兑现回写，现已补上，所以开启因果引擎的独立推进实例会开始回写。
8. 没有在真实 LLM 下运行过；`app.py` 只做了 `streamlit.testing.AppTest` 冒烟（知识库页标注与设置面板新控件渲染、无异常），无浏览器级目视验证。
