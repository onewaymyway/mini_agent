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
