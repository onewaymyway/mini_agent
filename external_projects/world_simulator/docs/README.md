# world_simulator 项目文档

> 本目录是 `world_simulator` 的面向使用者的文档集合，回答"这是什么 /
> 怎么用 / 怎么验证它是好的"这三个问题。更偏工程/设计取向的内容
> （为什么这样设计、复用了宿主的哪些机制、分阶段实施记录）在项目根目录
> 的 [`PROJECT.md`](../PROJECT.md)；最初的完整方案讨论在仓库
> `next_doc/world_simulator_external_project_plan.md`。这两份不重复
> 抄写，`docs/` 下的文档需要背景时会直接链接过去。

## 目录

- [`overview.md`](./overview.md) —— **项目说明文档**：这是什么、能做
  什么、核心概念（模拟实例/时间步/分支/自动挡）、目录结构、怎么启动。
  新人第一次接触这个项目，从这份开始看。
- [`backtest_guide.md`](./backtest_guide.md) —— **回测与校准**（第二十二轮 WP5）：怎么写案例、跑回测/A-B、读指标，以及它**不能**证明什么（训练数据污染、精确率下界、示例案例的核对范围与口径）。
- [`tech_model_guide.md`](./tech_model_guide.md) —— **技术发展模型**（第二十二轮 WP1/P5b）：提议–审核机制、`elapsed_days`、违规码 T0–T11、可选修复调用、参数（默认值均为占位值）、分支行为，以及它**不能**保证什么（语义合理性、真实 LLM 下的表现）。
- [`event_sampling_guide.md`](./event_sampling_guide.md) —— **外生事件采样**（第二十二轮 WP2）：按用户声明的先验概率在每步开始前抽样外部事件、可复现种子、冷却/条件/上限、分支与实验行为，以及它**不能**保证什么（先验准确性、LLM 是否真的写进叙事）。
- [`causal_engine_guide.md`](./causal_engine_guide.md) —— **因果引擎**（第二十二轮 WP3 · P5a/P5b）：声明的因果边可执行（入队 → 到期提醒 → `effect_dispositions` 回报 → 兑现统计 → 回写知识库）、违规码 E0–E6、延迟/精度降级、分支行为，以及它**不能**保证什么（引擎不改数值、无法验证 LLM 是否真的兑现）。
- [`tree_effects_guide.md`](./tree_effects_guide.md) —— **树影响世界**（第二十二轮 WP3 · P5d / 3d，默认关闭，需同时开启因果引擎）：分支可声明 `effects_if_active`，分支新变为 active 时入因果引擎的待兑现队列、到期后由 LLM 用 `effect_dispositions` 交代；违规码 E5–E7，以及它**不能**保证什么（不改数值、不验证声明合理、不验证兑现）。
- [`causal_view_guide.md`](./causal_view_guide.md) —— **因果图着色与到期时间线**（第二十二轮 WP3 · P5e / 3e，无新开关、只读）：因果引擎面板新增"着色关系图"（假设/已观察/已证伪/未定/已停用）与"到期时间线"；状态由 **AI 自报**的兑现统计推出，不是世界里被验证；阈值可用 `causal_view_params` 覆盖。
- [`tree_grounding_guide.md`](./tree_grounding_guide.md) —— **因果树接地**（第二十二轮 WP3 · P5c / 3c，默认关闭）：前置强制、`exclusive_group` 互斥、结构化 `trigger_condition`（默认只建议，子开关 `tree_auto_transition` 才自动迁移）、`likelihood` 校准账本；裁决码 G0–G5，以及它**不能**保证什么（不判断条件语义、不改数值）。
- [`independent_line_mechanisms_guide.md`](./independent_line_mechanisms_guide.md) —— **独立推进路径上的新机制**（第二十二轮 P8）：`advance_lines()` 也跑技术模型/事件采样/因果引擎/树接地/一致性守卫；全局步长取各线自报跨度的最大值、事件投放规则、多线技术提议冲突（`T10`）、目标线没到点的待兑现项挂起，以及**线不产出 `tree_updates`** 等边界
- [`html_export_mechanisms_guide.md`](./html_export_mechanisms_guide.md) —— **静态 HTML 导出接入新机制**（第二十二轮 P10）：每步审计提示、真实性体检、技术树、事件先验、因果引擎着色图/到期时间线、树接地；有数据才渲染、旧实例导出逐字节不变、按分支取动态状态、转义与失败降级
- [`element_model_guide.md`](./element_model_guide.md) —— **统一元素模型**（第二十三轮，E1–E6 已全部完成）：因果线从领域级升级为元素级；E1 只改存储与读写（`causal_lines` 条目新增可选字段、技术节点并入元素 `lifecycle`、旧 `tech_state` 幂等折叠、新实例默认开启 `element_modeling_enabled`），不改任何 LLM 行为；E2 创建阶段先划领域再展开元素 + 创建预算（含"不限"）+ 候选元素；E3 推进阶段发现新元素（`discovered_elements`/`element_enrichments`、别名去重、候选池与关键性门槛、引用即登记、补全宽限与兜底、`lifecycle_seed` 经技术裁决、关系派生因果边）；E4 分层与 prompt 预算（派生分级 active/watch/dormant、`tier_pin`、`max_active_in_prompt`/窗口/休眠索引上限、`var_refs` 当前值展示、prompt 规模有界）；E5 元素运维（`element_ops` 的 merge/split/retire/reparent，合并后旧 id 读取时落到目标、退场不再入新的待兑现）与默认关闭的周期扫描（`element_scan_interval`、手动「扫描遗漏元素」，额外一次 LLM 调用）；E6 联动与收尾（领域端点 `domain_child` 触发与目标领域展示、事件 `affects` 领域展开、树影响目标经注册表解析、体检只读 C9、独立推进接入 `discovered_elements`、总览/导出按领域分组与徽标、被合并元素历史并入目标、设置页元素编辑与预算表单）
- [`anatomy_guide.md`](./anatomy_guide.md) —— **元素剖面**（第二十四轮，A1–A5 已完成）：元素线上新增可选 `anatomy`（构成/指标/瓶颈/路线/门槛/里程碑/假设/信号，每项带来源状态 `basis`）；A1 只做规整、存取、参数、只读体检与只读档案视图，**推进时不读不写**；A2 创建期对重点元素拆解（`anatomy_seed`，LLM 无权声明出处/引擎状态）并在向导逐条确认/驳回；A3 联网证据研究（`element_research`，只基于搜索摘要；来源状态由引擎裁决、用户确认过的内容不被覆盖、证据追加写存 `data/<sim_id>/evidence.jsonl`、按现实天数标过期，**不在推进里自动联网**）；**A4 引擎定量骨架**（`anatomy_engine`：趋势库 + 白名单表达式、瓶颈路径种子抽样与回退、里程碑/阶段派生、采用门槛、`A0–A11` 裁决与自洽流水；LLM 不能宣布瓶颈解决/里程碑达成，条件语法扩展到子项引用）；**A5 推进期深化**（`anatomy_updates` 轻量协议 + 深度模式 `anatomy_deepen`：重点元素按触发/上限/耗时预算额外调用，提议走同一套裁决，失败降级为轻量，不影响推进）；`sourced` 必须带证据 id、趋势不设白名单、旧实例默认关闭、带剖面的元素不能被并入。
- [`knowledge_writeback_guide.md`](./knowledge_writeback_guide.md) —— **跨实例知识库写入**（第二十二轮 P9）：未来树 likelihood 档位校准与树声明兑现统计写入共享知识库；开关与前置条件、绝对值覆盖幂等、分叉去重、小样本不写、"LLM 自报"标注、`entrypoints/knowledge.py` 撤销（及无法精确回退的合并条目）
- [`testing_guide.md`](./testing_guide.md) —— **如何测试当前项目**：
  分两条路径——① 跑自动化单元测试（不需要真实 LLM，几秒钟出结果）；
  ② 端到端手动验证主链路（需要真实 LLM 配置，逐条列出"输入什么 →
  预期看到什么 → 怎么判断通过/失败"）。想验证"我改的代码有没有搞坏
  东西"或者"这个项目到底能不能跑起来"，看这份。
- [`service_api.md`](./service_api.md) —— **作为独立外部服务调用**：
  不打开 Streamlit 看板，从命令行或 HTTP 请求创建/推进/查询/管理
  模拟实例（供脚本、未来的主 agent 等外部调用方使用）。

## 30 秒速览

```
一句话意图 → 生成提案草稿 → 编辑确认 → 落盘为模拟实例（step 0）
                                          │
                                    推进下一步（可选一个方向）
                                          │
                              next_state + 叙事 + 新的候选方向
                                          │
                          （重复推进 / 分叉分支 / 切自动挡托管）
```

两种用法：
1. **命令行 / headless**：`python entrypoints/create_simulation.py "..."`，
   适合脚本化、daemon 调度、CI 里跑冒烟测试。
2. **独立看板**：`streamlit run app.py`，"夜航日志"主题，模拟列表 /
   创建向导 / 实例详情 / 对比视图 / 存档管理 / 游戏化视图 六个页面，
   日常交互推荐走这条路。

两条路径背后是同一套 `world_simulator/` 业务代码（`engine.py` /
`store.py` / `spec_generator.py` / `branch_manager.py` /
`autopilot.py` / `achievements.py`），互不冲突，随时切换。
