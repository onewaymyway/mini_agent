# Workflow 体系下一阶段方向思考（P9 候选池）

> 状态：**思考稿，非实施计划**——目的是把"workflow 体系除了 P1-P8 已经做的
> 之外，还值得投入的方向"想清楚、想透，排出优先级和依赖关系，而不是每个
> 方向都写到可以直接动手的细节程度。等某个方向被选中要做时，再单独为它
> 写一份 `xxx_design.md`（沿用 `session_to_workflow_design.md` 的写法）。
>
> **进度更新**：§6 中"现在就能做"的四项——1a（执行历史汇总统计）、
> 3（condition 暴露 inputs + validate 静态一致性检查）、1b（生成后自动
> dry-run 预览）、2（save_workflow 的 git 集成提示 + `/workflow history`/
> `/workflow diff`）——已按建议顺序实施完成，详见
> `next_doc/workflow_system_p9_implementation_record.md`。§4（主动感知与
> 建议）、§5（权限与信任模型）按下文结论继续搁置，未启动。
>
> **再次更新**：1b/2/3 三项已补充对应的 `agent_config.json` 开关
> （`dry_run_preview_on_generate`/`git_hint_enabled`/
> `condition_static_check_enabled`），且 `/workflow stats`/`history`/`diff`
> 三个 CLI 子命令已补进 Tab 补全列表（此前遗漏）；详见
> `next_doc/p8_p9_config_toggle_and_cli_hint_record.md`。
>
> 编号延续：`workflow_mechanism_improvement_plan.md`（P1-P7）→
> `session_to_workflow_design.md`（P8）→ 本文档讨论的是 **P9 候选池**，
> 不是单一一个 P9，因为下面几个方向互相独立，可以任选其一先做，不要求
> 按顺序全部做完。

---

## 0. 先想清楚"现在缺什么"，而不是"还能加什么"

workflow 体系发展到 P8 为止，已经覆盖了相当完整的一条主线：

```
生成（自然语言描述 / 模板 / P8 从 session 反向生成）
  → 保存（WorkflowStore，支持单文件/文件夹模式）
  → 执行（WorkflowRunner，支持并发分批/条件分支/角色 Agent/审批门/
         人工输入/子工作流/插件化 step 类型）
  → 看护（后台执行/暂停/恢复/取消/断点续跑/单步编辑续跑）
```

这条主线本身是完整的、闭环的。所以下面不是"生成→执行→看护"里再加一环，
而是问：**这条主线跑起来之后，长期使用会在哪里感到不趁手？** 这个问题
比"还能加什么功能"更容易筛选出真正值得做的方向——凡是"能加但不知道
具体缺口在哪"的想法，都先放进这份思考稿观察，不直接立项。

按这个标准过滤后，留下六个方向，下面逐个展开。

---

## 1. 生成质量闭环：workflow 生成之后，没人知道它到底靠不靠谱

### 1.1 现状

`generate_workflow` / `create_workflow_from_template` / P8 的
`build_workflow_from_summary` 都是"生成一次、人工看一眼预览、确认保存"，
保存之后这个 workflow 就进入了"black box 使用期"——`list_workflow_runs`/
`get_workflow_run_status` 只能看**单次执行**的过程和结果，没有任何机制
回答"这个 workflow 这半年跑了 50 次，靠谱吗"这种问题。

这是个真实缺口：如果一个 workflow 的某个 step 长期偏低分（比如
`evaluator` 角色的 score 经常卡在临界值反复重跑）、或者某个 `condition`
长期判 False 导致某个分支形同虚设，用户完全无感知，除非自己去翻
`workflow_runs/` 下每一次的记录人工数。

### 1.2 值得做的两件事（独立，不互相依赖）

**a) 执行历史的汇总统计**——不是新功能，是把已经落盘的
`workflow_runs/<id>/session.json` 数据做一层聚合视图：

```
list_workflow_runs(name="code_review") 已有的"单次列表"之上，
加一个 get_workflow_stats(name) → {
  total_runs, success_rate,
  step_stats: {step_id: {avg_duration, avg_score(若为 evaluator),
                          fail_rate, avg_retries_used}},
  condition_stats: {step_id: {true_rate}}  # condition 命中率
}
```

这一步纯粹是"读已有数据、算汇总"，不涉及执行逻辑改动，成本很低，
价值立等可见——尤其是"某个 step 平均重试次数很高"这种信号，直接指向
"这个 step 的 prompt 该调了"或"这个质检门的阈值设太严了"。

**b) 生成后自动带一次 dry-run 预览，而不是让用户脑内模拟**

P7 已经有 `preview_workflow`（dry-run，走并发分批/占位符替换/condition
求值但不真正执行）。目前 `generate_workflow`/P8 的
`build_workflow_from_summary` 生成完 YAML 后只给用户看 YAML 文本和
`preview()` 的静态摘要（步骤列表），没有自动跑一次 `preview_workflow`
把"这个 workflow 大概会怎么分批执行、condition 大概会怎么判"也带出来。
这是个小改动（在生成结果里追加一段 dry-run 输出），但能让用户在保存前
就发现"这两个 step 我以为会并发，其实因为 allow_parallel 没设对会串行"
这类问题。

### 1.3 暂不做的部分

"根据历史执行数据自动建议怎么改 workflow"（比如自动提示"要不要把
threshold 从 60 降到 50"）——这需要判断"改了之后会不会更好"，光靠历史
数据本身回答不了（没有反事实），做了也只是"看起来智能但经不起推敲"的
建议。**先把数据汇总出来给用户自己看，不要越俎代庖替用户下结论**，
这条规则同样适用于其它几个方向。

---

## 2. Workflow 定义的变更追溯：不需要重新发明版本控制

### 2.1 现状

`.agent/workflows/` 不在 `.gitignore` 里（只有 `.agent/sessions`、
`.agent/logs`、`.agent/permissions.json` 被排除），说明 workflow 定义
文件本来就设计成"应该被 git 追踪"的产物——这意味着**不需要在
`WorkflowStore` 里造一套自己的版本历史机制**，那是重复造轮子，而且
自造的版本历史天然弱于 git（没有分支、没有多人协作场景下的合并）。

真正的缺口不是"没有版本历史"，是**`save_workflow` 完全不 aware 它在
一个 git 仓库里**：改一个 workflow 就是覆盖写文件，不会自动 commit，
用户手动生成/调整 workflow 的过程如果不自己记得 `git add && git commit`，
版本历史照样是空的。

### 2.2 值得做的事

- `WorkflowStore.save()` 成功后，如果 `project_root` 是一个 git 仓库
  （`git rev-parse --is-inside-work-tree` 之类的轻量探测），提示用户
  （不是自动 commit——自动 commit 属于"代用户做决定"，违反上面 1.3 提到
  的原则）："workflow 已保存，建议 `git commit` 记录这次改动"，
  或者更进一步给一条 `git log --oneline -- .agent/workflows/<name>` 的
  快捷方式（CLI `/workflow history <name>`），直接复用 git 已有的历史，
  不重新存一份。
- 如果确实想要"看这次改动改了什么"（而不是"看历史列表"），
  `git diff` 已经能做到；可以包一层 `/workflow diff <name>` 调用
  `git diff -- .agent/workflows/<name>` 并把结果转成对人类更友好的
  "step 级别差异"展示（比如"gate_candidate 从 false 改成了 true"），
  这一步有一点点增量价值（原始 YAML diff 不如结构化 diff 好读），
  但不是刚需，可以放在这条方向里优先级最低的位置。

### 2.3 为什么强调"不重新发明"

这条方向最大的风险是"想当然地做一套 workflow 专属版本系统"，投入
不小、还带来"和 git 历史脱节、两套真相"的新问题。**这条方向如果要做，
第一步永远是"怎么更好地利用已经存在的 git"，不是"怎么造一个新的存储"**。

---

## 3. Condition 表达式：能力已经不弱，缺的是"验证"和"输入可见性"

### 3.1 现状纠偏

最初讨论时以为 `condition` 只能引用单个 step 的 score，重新看代码后
发现并非如此——`WorkflowRunner._eval_condition()` 是真正的沙箱化
`eval()`（`__builtins__` 清空），命名空间里包含**所有已执行 step** 的
`SimpleNamespace(output=..., score=..., status=..., passed=...)`，
所以像 `"analyze.passed and evaluate.score >= 60"` 这种跨多个 step 的
布尔组合表达式**现在就能写**，这一点比最初设想的能力强。

### 3.2 真正的两个缺口

- **condition 引用不到 workflow 级输入/参数**：命名空间只有
  `step_results`，没有把 `inputs`（`run_workflow` 传入的动态参数）一并
  暴露进去。所以现在写不出"`{env} == 'prod' and check.passed`"这种
  "既要看某个 step 结果、又要看外部输入"的条件——只能通过把
  `{env}` 塞进某个 step 的 prompt、再判断这个 step 的 output 里有没有
  出现某个关键词来间接实现，绕了一圈。补一个 `inputs` 命名空间对象
  进 `_eval_condition()` 是很小的改动，收益却不小。
- **condition 只在真正执行到那一步时才求值，写错了（引用了不存在的
  step_id）只会在运行期被 `except Exception` 吞掉、打一条 warning、
  默认跳过该步骤**——这意味着一个写错的 condition 表达式，在
  `save_workflow`/`WorkflowDef.validate()` 阶段完全检测不出来，
  要等真正跑一次、恰好跑到那一步才会暴露，而且暴露的方式是"这步骤被
  跳过了"而不是"这个表达式有问题"，很容易被误解成业务逻辑判断结果
  而不是表达式写错了。`validate()` 里可以加一轮**静态检查**：
  对每个 `condition`，用 `ast.parse()` 抽取表达式里引用的所有
  "顶层名字"（`analyze.passed` 里的 `analyze`），检查这些名字是否都
  出现在 `depends_on`（直接或传递依赖）里——不需要真的 `eval`，只是
  语法级别的"这个表达式引用的 step，是不是这个 step 真实能看到的"
  一致性检查，能在保存阶段就拦住一大类笔误。

### 3.3 暂不做的部分

引入一个更复杂的自定义 DSL（替代裸 `eval()`）——现在的沙箱 eval 已经
表达能力够用，且用户/生成 LLM 都熟悉 Python 表达式语法，换一套 DSL是
学习成本换安全性的交易，而当前沙箱（清空 `__builtins__`）的安全性已经
可以接受，不值得为了"看起来更规范"重做。

---

## 4. 主动感知与建议：从"用户开口"到"Agent 主动提议"

### 4.1 现状

不管是 `generate_workflow` 还是 P8 的 session→workflow，触发方式都是
**用户主动要求**。P8 的 `TaskSummary.repeated_pattern` 字段已经具备
"检测同一次 session 内是否有阶段组合重复出现"的能力，但只在用户已经
主动发起总结之后才会被算出来、且只提示"存成 snippet"，不会主动去
建议"要不要把整个流程存成 workflow"。

`evolution/` 目录下已经有一套"自我进化/巩固"的机制（lesson 提炼、
自我画像更新等），本质上也是"从历史行为里发现模式、反馈给用户或系统"，
跟这里想做的事是同一类思路，如果做，应该**复用同一套触发节奏**
（比如巩固循环的 cron 节奏），而不是另起一套扫描机制。

### 4.2 值得做的事（但优先级应该放在偏后）

- 复用 `evolution/consolidation.py` 或类似的巩固循环节奏，定期
  （而不是每次 session 结束都跑，避免噪音）扫描最近 N 次 session 的
  `IntentActionMapper` 分组结果，如果发现**跨多个不同 session**、
  处理不同输入但阶段组合高度相似的模式，生成一条"你最近几次都在做
  类似的事，要不要我帮你整理成一个 workflow"的**建议**（走
  `perception/system_events.py` 的事件总线机制，跟现有
  `proprioception.uncertainty_sustained` 之类的信号走同一条管道，不是
  直接打断用户），用户确认后才真正调用 P8 的两阶段流程。

### 4.3 为什么优先级放后

这是六个方向里**唯一一个"跨多个 session 才能生效"**的方向，天然依赖
P8 已经上线一段时间、积累了足够多"手动重复"的真实样本才能验证"检测
准不准"，做早了没有数据验证效果，容易做出一个自己都不确定有没有用的
东西。**应该等 P8 上线并观察一段真实使用之后再评估要不要做**，不是
现在就动手。

---

## 5. 权限与信任模型：只有在"workflow 会被分享"的前提下才紧迫

### 5.1 现状

现在的审批逻辑（`step_requires_approval`）基于 **step 类型**做静态规则
（`tool_call` 默认需要审批、`agent` 默认不需要等），这套规则的前提假设
是"workflow 是使用者自己写的或者自己确认过生成结果的"——**信任的锚点
是'这份 YAML 是我自己看过的'，不是'这份 YAML 本身是安全的'**。

这个假设目前是成立的：无论是手写、`generate_workflow` 生成、还是 P8
从**自己的** session 反向生成，YAML 的来源始终是"用户自己的操作痕迹"。

### 5.2 什么时候这个假设会破裂

只有出现"**别人生成的 workflow 被引入到你的环境里**"这种场景时，
上面的假设才会失效——比如：
- workflow 分享/市场（下载别人发布的 workflow 定义直接用）
- P8 如果未来支持"总结别人（团队里其他人）的 session"来生成 workflow
  （当前设计明确是"只读取自己项目内的 session"，没有这个风险）

### 5.3 结论：现在不需要做，但要记住这条边界

**这条方向不建议现在投入**，因为触发条件（引入外部来源的 workflow）
目前不存在。但如果未来任何一个方向（比如 workflow 市场）被提上日程，
第一件事应该是回来看这一条——引入外部 workflow 之前，`tool_call`/
`script` 类型 step 的审批规则需要从"看 step 类型"升级成"看 workflow
来源是否可信"，且默认对"外部来源"的 workflow 一律要求审批，不能沿用
现在"本地生成默认信任"的规则。这里先记一笔，不展开设计。

---

## 6. 优先级与依赖关系

```
现在就能做、互相独立、无需等待数据积累：
  1a. workflow 执行历史汇总统计（get_workflow_stats）
  1b. 生成后自动带一次 dry-run 预览
  2.  save_workflow 的 git 集成提示 + /workflow diff
  3.  condition：暴露 inputs 命名空间 + validate() 静态一致性检查

需要先有一段真实使用数据才能验证价值，暂缓：
  4.  主动感知与建议（依赖 P8 上线后的真实使用样本）

只在特定前提成立时才紧迫，现在不必投入：
  5.  权限与信任模型升级（依赖"workflow 分享/市场"这类前提出现）
```

建议顺序：**1a → 3 → 1b → 2 → （观察一段时间）→ 4**。选择这个顺序的
理由：
- 1a（统计汇总）和 3（condition 补全）都是纯读取/局部增量修改，
  改动范围小、没有交互设计成本，投入产出比最高，适合先做。
- 1b（自动 dry-run）依赖 1a 类似的"生成后追加信息"模式，做完 1a 之后
  顺手做，复用同一套"生成结果展示格式追加一段"的习惯（P8 里
  `build_workflow_from_summary` 已经是这个模式）。
- 2（git 集成）是纯提示性质，不涉及执行逻辑，随时可以插入日程，
  优先级本身不高但风险也最低，放在中间位置。
- 4（主动感知）明确依赖前面几项（尤其是 1a 的统计数据）先落地、
  且需要观察真实使用效果，排在最后。
- 5（权限模型）不进入本轮排期，只作为"未来触发 workflow
  分享/市场时必须先做"的前置记录。

---

## 7. 下一步

以上六个方向里，1a/1b/2/3 都足够小、足够独立，**任选其一确认后**，
即可参照 `session_to_workflow_design.md` 的写法单独展开一份可实施的
设计文档（数据结构、承载函数、工具/CLI 改动点、检查清单），不需要
等其它方向一起排期。

> **更新**：1a/1b/2/3 已全部实施完成，实现细节与验证记录见
> `workflow_system_p9_implementation_record.md`，未再单独展开
> `xxx_design.md`——四项改动都足够小、且互相独立，直接照本文档 §1-3
> 的分析实现，没有引入本文档未讨论过的新设计决策。下一步是观察一段
> 真实使用效果后再评估是否启动 §4（主动感知与建议）。
