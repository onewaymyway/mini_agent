# Personal AI 架构对齐升级 —— 阶段三实施记录

> 对应方案：`next_doc/personal_ai_alignment_upgrade_plan.md` §4.3 / §6
> 阶段三（Context Pack 组装器，试点接入）。

## 1. 做了什么

新增 `context_builder.py::build_context_pack(paths, goal_text, query="")`，
组装方案 §4.3 定义的字段固定的结构化 `ContextPack`：

```
Goal: <goal_text>
Current State: <阶段二 personal_state_snapshot 摘要>
Relevant Decisions: <find_relevant_decisions 命中的历史决策>
Relevant Experience: <wiki experiences/ 目录检索命中的经验页>
World Context: <source_kind 属于外部知识类别的 wiki 页面>
Current Evidence: <阶段一 UserProfile.derived 中 user_stated/ai_observation
                    记录；ai_inference 记录单独列出并标注"推测，非用户明确事实">
Risk: <健康告警数 / 卡住比例 / 低置信度待决策候选数>
```

各字段的数据来源全部复用已有模块，本阶段不新增任何采集点：

1. **Current State** —— 直接调用阶段二的
   `perception/personal_state_snapshot.py::personal_state_snapshot()`，
   取 `active_goals` 拼一句摘要，`progress`/`pending_initiatives` 拼进
   `Risk`（`_render_risk_summary()`）。
2. **Relevant Decisions** —— 直接复用系统关联性断点改进方案 F1 已经跑通的
   `wiki/decision_consumption.py::find_relevant_decisions()`，不重新实现
   检索、不改变其既有行为（GoalJudge 原有的 `decision_consumption_enabled`
   路径完全不受影响，两者各自独立调用）。
3. **Relevant Experience** —— 新增 `_find_relevant_experience()`，是
   `find_relevant_decisions()` 同一手法在 `wiki_experiences_dir` 命名空间
   上的复用（同样调用 `wiki_shelf_search()`，只是按 `experience` 标识
   过滤），不新增检索算法。
4. **World Context** —— 新增 `_collect_world_context()`，直接调用
   `evolution/external_trend_capability_link.py::_load_external_knowledge_pages()`
   已有的扫描逻辑取前 3 条摘要。当前项目里没有专门的外部知识信号源时
   返回空列表——方案原文允许"若无归零，不强求"。
5. **Current Evidence** —— 新增 `_collect_current_evidence()`，读阶段一
   `UserProfile.derived["values"/"risk_preference"/"constraints"]`，按
   `source` 字段严格分成 `factual`（`user_stated`/`ai_observation`）与
   `inferred`（`ai_inference`）两组，`to_prompt_block()` 渲染时后者必定
   跟在"以下为 AI 推测，非用户明确事实，仅供参考"提示之后，绝不与
   前者混排（方案核心理念第 3 条的字面落地）。

`ContextPack.to_prompt_block()` 按上述顺序拼接非空小节，任一字段为空
时整节省略，不留空标题——用 `paths=None` 或全空项目调用时，只剩
`Goal: <goal_text>` 一行。

### 试点接入 GoalJudge

新增 `cfg.goal_mode.context_pack_enabled`（默认 `False`），与已有的
`decision_consumption_enabled` 完全同构（同一个 F1 手法的复制）：

- `role_agents/goal_judge.py::run_goal_judge()` 在开关打开且调用方传入
  `paths` 时，调用 `build_context_pack(paths, goal_spec.goal_text)
  .to_prompt_block()`，拼进 `context_pack_block` 参数。
- `build_goal_judge_prompt()` 新增同名参数，透传进
  `prompts/user/goal_judge_request.md` 新增的 `{{context_pack_block}}`
  占位符，位置紧跟在既有的 `{{referenced_decisions_block}}` 之后。
- `goal_mode/runner.py::_run_judge()` 已经在调用
  `run_goal_judge(paths=self._paths, ...)`（阶段一之前 F1 落地时就已经
  传入），因此本阶段**不需要改动 runner.py 的调用点**，只需要把配置
  开关打开即可生效——这也是方案 §5 里"先在小范围试点验证不引入回归"
  的字面落地方式：默认关闭，功能已就绪但未默认开启。

## 2. 为什么这样设计（对齐方案 §5 的划分理由）

- 不替换 `context_builder.py` 现有的检索式拼接逻辑（`ContextBuilder`
  类负责组装每轮 system prompt），`build_context_pack()` 是模块级独立
  函数，服务于不同粒度的需求：前者解决"这一轮 system prompt 里塞什么"，
  后者解决"某个具体判断点需要一份怎样的结构化快照"，两者并行存在，
  互不干扰（方案 §2 "不改变 context_builder.py 现有的检索注入逻辑"）。
- 严格依赖阶段一 + 阶段二的产出（Current Evidence 依赖阶段一的
  `source`/`confidence` 字段，Current State 依赖阶段二的快照），方案
  §5 判断"阶段三依赖前两阶段都就绪后才有实际内容可填"在实现时得到
  验证：如果先做阶段三会拿不到这两个字段的真实数据。
- 复用 F1 的"配置开关 + 传 paths 才生效 + 默认关闭"手法而不是新发明一套
  接入方式，降低了这次改动被审查/回滚的心智负担——阅读过
  `decision_consumption_enabled` 的人可以直接理解
  `context_pack_enabled` 的行为边界。
- 只在 GoalJudge 一个判断点试点，没有扩大到其它 LLM 调用点（比如
  CoachAgent、SpecBuilder），符合方案"第一阶段只在 Goal 执行相关的
  关键决策点试点，不铺开到所有 LLM 调用点"的要求。

## 3. 已知限制（如实记录）

- **Relevant Experience / World Context 目前大概率长期为空**：项目里
  `wiki/experiences/` 目录、`source_kind` 属于外部知识类别的页面本身
  是否有内容，取决于其它模块（`experience_writer.py`/
  `world_writer.py`/`external_trend_capability_link.py`）是否已经在跑、
  是否已经积累了数据——本阶段只负责"有则读出来"，不负责"确保有数据"，
  这是方案 §4.3 原文明确允许的取舍（"若无归零，不强求"），如实记录
  而不是掩盖。
- **Context Pack 与 `referenced_decisions_block` 存在部分信息重叠**：
  两者都可能包含相关历史决策内容（一个是 Context Pack 内部的
  `Relevant Decisions` 小节，一个是 GoalJudge 独立的既有注入），本阶段
  刻意没有去重合并——两者是各自独立、可分别开关的功能，合并会让"关闭
  其中一个"的语义变得模糊，且目前尚未验证 Context Pack 试点本身对
  判断质量的影响，暂不做进一步耦合。
- **`build_context_pack()` 尚未接入除 GoalJudge 之外的任何调用点**：
  CoachAgent、SpecBuilder 等其它可能受益的判断点本阶段未涉及，需要先
  观察 GoalJudge 试点效果（判断质量/稳定性是否有回归）再决定是否
  按方案 §6 阶段三原文"评估是否扩大接入范围"。
- **没有做"接入前后判断质量对比"的量化评估**：方案原文要求"对比接入
  前后的判断质量/稳定性，确认无回归后再评估是否扩大接入范围"，本阶段
  只完成了功能实现和单元测试，真实效果对比需要在实际使用（打开开关）
  一段时间后人工或后续巡检 job 评估，本次实施本身不包含这一步。

## 4. 改动文件清单

```
src/mini_agent/context_builder.py                              修改（新增 build_context_pack / ContextPack）
src/mini_agent/config/models.py                                修改（新增 goal_mode.context_pack_enabled）
src/mini_agent/role_agents/goal_judge.py                       修改（build_goal_judge_prompt/run_goal_judge 接入 context_pack_block）
src/mini_agent/prompts/user/goal_judge_request.md               修改（新增 {{context_pack_block}} 占位符）
tests/test_context_pack.py                                      新增
next_doc/personal_ai_alignment_upgrade_plan.md                  修改（标注阶段三已完成）
next_doc/personal_ai_alignment_upgrade_stage3_implementation_record.md  新增（本文档）
```

## 5. 测试情况

```
tests/test_context_pack.py                — 6 项（新增，覆盖 paths=None
                                              兜底、空项目、证据分级分列、
                                              决策检索接入、State 快照接入、
                                              空字段小节省略）
```

本地执行 `python -m pytest tests/test_context_pack.py
tests/test_decision_consumption.py tests/test_profile.py
tests/test_personal_state_snapshot.py` 共 35 项全部通过。`goal_judge.py`/
`goal_mode/runner.py` 未编写针对 `context_pack_enabled=True` 路径的
专属集成测试——与该文件里 `decision_consumption_enabled` 的既有测试覆盖
现状一致（仓库里对这条注入路径同样只有 `build_goal_judge_prompt` 层面
的字符串拼装可以被静态检查，运行时集成依赖真实 LLM，未纳入单测范围）。
另外确认了 `tests/test_goal_mode.py` 里 5 项与 `spec_builder`
`detection_text` 参数相关的失败是改动前就已存在的既有缺陷（在原始
代码库上复现一致），与本阶段改动无关，如实记录，不在本阶段修复范围内。

## 6. 阶段四预告（尚未开始）

方案 §4.4 的 Daily Digest 消费 `personal_state_snapshot()` +
`initiative_inbox` + Goal 进度趋势即可合成，不依赖本阶段的
`build_context_pack()`（两者是并列的下游消费者，不是链式依赖）。阶段四
改动面集中在展示层，风险最低，可以开始。
