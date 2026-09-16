# Capability Learning 人设学习"研究优先 + 角色贴合度"改进方案

- **状态**：已实施（见 §7 实施记录）。

对应背景：`next_doc/persona_capability_learning_design.md`（§10 persona 型
Track 原始方案）、`next_doc/persona_draft_llm_quality_improvement_plan.md`
（上一轮"草稿质量"改进，已解决"大纲分场景/追问文案/LLM 润色/tone"四点）、
`src/mini_agent/evolution/capability_learning.py`
（`needs_user_context`/`draft_outline_with_llm`/
`generate_persona_topic_question`/`synthesize_persona_draft_with_llm`/
`draft_persona_markdown`）、`apps/mini_agent_kanban/app.py`（看板"待回答
问题"区块）。

## 0. 问题现象

用户在看板"待回答问题"区持续看到类似这样的问题：

> 当生产环境日志显示核心任务连续 3 次重试失败，而值班工程师开始慌了的
> 时候，你会怎么做？会说什么？
> ——来自 Track「自动化任务可靠性工程师」

反馈两点：

1. **不该问用户的问题在问用户**。这类问题本质是"这个角色在某类专业
   场景下的合理反应"，业界对应岗位（SRE/on-call 工程师）有大量成熟的
   最佳实践和行业惯例可供调研，不属于"互联网查不到、只有用户自己知道"
   的信息。能力学习的本意是**让 agent 自主调研生成人设和能力**，而不是
   靠采集用户偏好来拼人设——只有真正关键、调研无法替代的事项才应该
   打扰用户。
2. **即使抛开"该不该问"，生成出来的人设草稿本身读起来也不像一个正常
   的 agent 人设**，更像是在给一个虚构角色做性格测验，和"自动化任务
   可靠性工程师"这种功能性/操作性的 agent 身份定位不搭。

这两点是同一条链路上先后发生的问题，合在一起看、合在一起改。

## 1. 根因分析

### 1.1 问题一根因：`needs_user_context()` 的 P1 占位实现被当成了长期行为

```python
def needs_user_context(topic: OutlineTopic, track: CapabilityTrack) -> bool:
    """...P1 用非常保守的规则式占位实现：只有 persona 型 Track 默认判定为
    需要用户输入...knowledge 型默认不需要（P2 才接入更细致的判定...）"""
    return track.target_type == "persona"
```

设计文档当初就写明这是"P1 占位"，本该在 P2 替换成更细致的判定，但
一直没有后续实现——结果是 persona 型 Track 的**每一个**大纲维度（性格
特征/说话习惯/背景经历/价值观/……）无条件被判定为"必须问用户"，
`run_capability_learning_cycle()` 里遇到这类维度直接调用
`generate_persona_topic_question()` 生成追问，从不尝试调研。

### 1.2 问题二根因：大纲维度模板和草稿合成 prompt 都是按"虚构角色扮演"
写死的，没有区分"功能性/操作性 agent 身份"这另一大类场景

`target_type="persona"` 这个类型字段目前是"一刀切"的：无论用户建的是
一个聊天用的虚构角色（比如"傲娇猫娘"），还是一个功能性的 agent 操作
身份/职能定位（比如"自动化任务可靠性工程师"这种会被绑定到某类 cron
任务/某个子 agent 上、决定它遇到具体运维场景该怎么判断和表达的身份），
系统内部处理这两者的 prompt 完全共用一套，而这套 prompt 明显是照着
"虚构角色扮演"写的：

- `draft_outline_with_llm(target_type="persona")` 的维度模板：
  "性格特征、说话习惯/口头禅、背景经历、价值观与行为准则、人际关系或
  立场态度"——"口头禅""背景经历"这类是虚构角色的常见维度，对一个
  运维/工程类功能性身份没有意义；这类身份真正需要沉淀的维度应该是
  "职责边界、决策原则、升级/上报触发条件（escalation criteria）、
  沟通规范与信息密度、风险容忍度与止损线、可调用的工具与权限预期"
  这一类。
- `generate_persona_topic_question()` 和 `_PERSONA_SYNTHESIS_INSTRUCTIONS`
  两处 prompt 里反复出现"角色扮演""虚构角色人设""这个角色遇到某种
  情境会怎么说话"——同样是虚构角色框架，引导 LLM（以及被追问的用户）
  往"演出一个性格"的方向想，而不是"总结一套专业判断准则"。

大纲维度选错之后，即使把"追问用户"换成"调研生成"，调研本身也会
被这套错的维度带偏——比如"口头禅"这种维度，调研业界惯例也无法产出
一个功能性 agent 身份该有的内容，回过头还是只能靠瞎编或者问用户
"你希望它有什么口头禅"，本质问题没解决。

**结论**：问题一和问题二共享同一个上游缺陷——`target_type="persona"`
把"虚构角色"和"功能性 agent 身份"这两类目的不同、维度不同、判断
标准也不同的场景，压缩成了一套 prompt。不先把这一层分开，"研究优先"
和"人设贴合度"两个问题都改不干净。

## 2. 设计目标与原则

1. **区分"虚构角色（roleplay）"与"功能性 agent 身份（operational）"
   两类 persona，各自用各自贴合的维度模板、追问措辞、调研措辞、
   草稿合成措辞**——但不强制用户在创建时选择（很多时候用户自己也说不
   清这是哪一类，或者一开始就是混合的），而是从 `persona_desc`（用户
   一开始写的角色描述）里用 LLM 做一次轻量判定，判不出来/无
   `llm_helper` 时按现状（roleplay 措辞）向后兼容，不引入破坏性变更。
2. **调研优先于提问，提问优先于编造**：所有大纲维度默认先走"调研生成
   草稿"路径（复用行业最佳实践/通用惯例），只有调研判定"这件事必须
   由用户决定"时才升级成真人问题；调研和提问都不能编造具体事实（不
   能凭空发明数字、人名、经历），这一条延续
   `persona_draft_llm_quality_improvement_plan.md` 已有的约束，本方案
   不放松。
3. **"关键判据"本轮先不改**（用户已确认）：问题一里"什么时候必须问
   用户"的判定逻辑维持上一轮方案定的措辞不变，本方案只补齐"调研生成
   草稿"这条路径本身、以及"调研内容该往哪个方向调"（即本文档的重点：
   角色维度分流）。
4. **能用就用，用不了就退回现状**：跟既有的
   `draft_outline_with_llm`/`generate_persona_topic_question` 同款克制，
   `llm_helper`/`retriever` 缺失或调用失败，一律退回当前已有行为
   （现有的通用维度模板 + 追问模板），不因为这轮改动让没接 LLM/检索的
   部署出现回归。
5. **可审计**：调研生成的维度内容要在草稿里标注"系统调研生成"来源，
   区别于用户亲口回答的内容，方便用户发布前重点核对。
6. **存量数据**：现有 pending 的、按老逻辑生成的"偏好式"问题，随本
   方案上线批量 `dismiss`（用户已确认），新一轮循环里对应维度会被
   新逻辑重新判定和处理。目前上传的代码包里没有附带运行时状态
   （`capability_questions.jsonl` 等不在包内），批量 dismiss 需要在
   实际运行环境执行，见 §4 的操作步骤。

## 3. 改动方案（按模块划分）

### 3.1 新增 `classify_persona_kind()`：轻量判定 roleplay / operational

```python
def classify_persona_kind(
    persona_desc: str, llm_helper: Optional[Callable[[str], str]],
) -> str:
    """返回 "roleplay" 或 "operational"。判不出来/无 llm_helper/调用异常
    一律返回 "roleplay"（等同现状，向后兼容）。"""
```

- Prompt 措辞：给出 `persona_desc`，问"这更接近一个用于聊天/陪伴/
  故事扮演的虚构角色，还是一个承担具体职能、会被绑定到某类任务或
  某个 agent 身份上、需要专业判断准则的功能性角色？只回答
  roleplay 或 operational 其中一个词。"
- 落点：`CapabilityTrackStore.create()` 计算一次，存进
  `CapabilityTrack.persona_kind` 新字段（`Optional[str]`，默认
  `None` 表示"未判定/走现状"），只在创建时判定一次并落盘，不在每轮
  循环里重复调用 LLM——这个属性理论上创建后不该因为后续问答而反复
  变化，重复判定只会浪费调用且可能因为一次异常输出而"角色摇摆"。
- 向后兼容：已存在的 Track（升级前创建的）`persona_kind` 字段缺省为
  `None`，所有依赖该字段的下游函数在 `None` 时按 `"roleplay"` 处理，
  等于完全不改变老 Track 的行为。

### 3.2 `draft_outline_with_llm` / `generate_persona_topic_question` /
`_PERSONA_SYNTHESIS_INSTRUCTIONS` 按 `persona_kind` 分流第三套措辞

在原有 `target_type in {"knowledge", "persona"}` 分流基础上，`persona`
分支内部再按 `persona_kind` 二次分流：

- **roleplay**：维持现状文案不变（"性格特征/说话习惯/口头禅/背景经历/
  价值观与行为准则/人际关系或立场态度"）。
- **operational**（新增）：
  - 大纲维度 prompt 改问："请帮我列出 N 个刻画一个专业/职能角色需要
    明确的维度，比如职责边界、决策原则、升级上报的触发条件、对外
    沟通的语气与信息密度、风险容忍度与止损线、可调用的工具与协作
    对象等（按实际情况取舍，不必照抄）。"
  - 追问/调研 prompt 改问："这个维度在同类角色（结合角色描述判断
    具体是哪一类，比如 SRE/客服/审核等）里通常遵循什么行业惯例或
    最佳实践？请给出一个可直接采用的具体处理原则，而不是泛泛的
    价值观表述。"
  - `_PERSONA_SYNTHESIS_INSTRUCTIONS` 去掉"虚构角色人设""角色扮演
    设定文档"这类措辞，替换成"这份文档最终会决定一个功能性 agent
    身份在真实任务场景里的判断和表达方式"，正文语言目标从"演出一种
    人格"改成"给出清晰、可执行的行为准则"，避免 LLM 混入"性格测验"
    式的语言。

三处改动都遵循现有函数"prompt 按 target_type/persona_kind 取值分流、
未知值退回现状"的既有模式，不改变函数签名的强制性（新增参数给默认值）。

### 3.3 新增"调研优先"路径：`draft_persona_topic_answer()`

对齐 knowledge 型已有的 `retriever` 结构，新增：

```python
def draft_persona_topic_answer(
    topic: OutlineTopic,
    track: CapabilityTrack,
    retriever: Optional[RetrieverFn],
    llm_helper: Optional[Callable[[str], str]],
) -> Optional[str]:
    """针对一个 persona 维度，先（可选）用 retriever 检索"这类角色/岗位
    在该维度上的行业惯例/最佳实践"，再用 llm_helper 综合成一段具体、
    可直接采用的草稿内容（不是问句）。retriever 缺失时跳过检索、只用
    llm_helper 基于其自身知识给出惯例性回答；llm_helper 也缺失/调用
    异常/返回空，返回 None，调用方退回现有的"生成问题"分支，行为与
    改动前一致。"""
```

- Prompt 会按 `track.persona_kind` 取对应措辞（见 §3.2）。
- 返回的草稿文本不能超过一个合理长度上限（比如 200 字），太长视为
  跑题，返回 `None` 退回提问分支——避免调研结果啰嗦到无法用作一条
  "维度答案"。

### 3.4 `run_capability_learning_cycle()` 接入调研优先路径

在现有 `needs_user_context(topic, track)` 判定为 `True` 的分支里，
**不直接生成问题**，而是先尝试 §3.3 的调研路径：

```
if needs_user_context(topic, track):
    draft = draft_persona_topic_answer(topic, track, retriever, llm_helper)
    if draft is not None:
        # 走"调研生成"分支：直接落一条 answered 状态的 CapabilityQuestion，
        # answer 前缀标注"（系统调研生成，非用户确认，可在看板修改）"，
        # 台账 action="researched"（而不是 question_raised），
        # 不占用 max_pending_questions 配额。
        ...
        continue
    # 调研失败（无 llm_helper / 调用异常 / 输出被判定跑题）才退回现有的
    # "生成问题" 分支，行为与改动前一致。
    ...
```

- 复用 `CapabilityQuestion` 而不是另起一套数据结构：
  `persona_draft_completeness()`/`draft_persona_markdown()` 的统计
  口径完全不用改，只是这条记录的 `answer` 有来源前缀标注、对应台账
  `action` 是新值 `"researched"`（区别于 `"question_answered"`），
  看板可以据此在"草稿完成度"里进一步细分"用户确认 / 系统调研"两种
  来源（可选的展示优化，见 §3.6，非必须）。
- **本方案不改动 §1.1 提到的"关键判据"本身**（用户已确认"先不改"）：
  `needs_user_context()` 维持当前实现，只是把它判定为 `True` 之后的
  "唯一出路是提问"，改成"先尝试调研，调研不行才提问"。换句话说，
  这一步改动即使不推进"关键判据"的后续 P2 细化，也能独立解决"能调研
  解决的事情不该问用户"这个问题的大部分场景——因为大部分 persona
  维度调研本身就能给出合理答案，会被升级成真人问题的只剩"调研确实
  给不出具体内容"的少数情况。

### 3.5 存量 pending 问题批量 dismiss

新增一个小工具，供 CLI 一次性调用（不是长期功能，用完即弃也可以，
但做成 CLI 子命令方便复查/审计更好）：

```
/capability questions dismiss-all-pending [--track <track_id>] [--persona-only]
```

- 默认 `--persona-only`（只清 persona 型 Track 的 pending 问题，
  knowledge 型不受这次改动影响，不用清）。
- 内部就是遍历 `CapabilityQuestionStore.list_questions(status="pending")`
  逐条调用现有的 `dismiss()`，不新增底层能力，只是补一个批量入口
  （当前 `dismiss()` 只有单条操作的 CLI/API 封装，没有批量封装）。
- 落地时机：本方案代码合并、`persona_kind` 判定和调研路径都跑通之后
  再执行一次性清理，避免清完之后新逻辑还没生效、老问题的空当被"什么
  都不问也不调研"的中间态占据。

### 3.6（可选，非本轮必做）看板展示优化

"待回答问题"区块过滤掉已经被 §3.4 调研分支自动 `answered` 的记录
（本来就是 `status="answered"`，理论上现有过滤逻辑已经不会显示，
不需要改动），在人设草稿预览区给"系统调研生成"来源的维度加一个角标，
方便用户一眼看出哪些内容需要重点核对、哪些是自己亲口确认过的。这一步
属于体验优化，不影响功能正确性，可以放在本方案落地并观察一段时间后
再排期。

## 4. 实施步骤与操作顺序

1. `CapabilityTrack` 加 `persona_kind` 字段（默认 `None`），
   `CapabilityTrackStore.create()` 接入 `classify_persona_kind()`。
2. `draft_outline_with_llm`/`generate_persona_topic_question`/
   `_PERSONA_SYNTHESIS_INSTRUCTIONS` 按 `persona_kind` 三向分流
   （knowledge / persona-roleplay / persona-operational）。
3. 新增 `draft_persona_topic_answer()`，`run_capability_learning_cycle()`
   接入"调研优先"分支（§3.4）。
4. 新增 `/capability questions dismiss-all-pending` 批量清理入口。
5. 补充单测（见 §5），本地跑通后合并。
6. 部署到实际运行环境后，执行一次
   `/capability questions dismiss-all-pending --persona-only`
   清理存量问题，观察下一轮 `sys:capability_learning_cycle` 产出的
   问题/调研记录是否符合预期（persona-operational 类 Track 不应该再
   出现"口头禅""值班工程师慌了"这类措辞的问题）。

## 5. 测试计划

- `classify_persona_kind()`：`llm_helper=None` → `"roleplay"`；LLM 返回
  `"operational"`/`"roleplay"`/无法解析的内容三种情况；调用异常兜底。
- `draft_outline_with_llm(target_type="persona")`：新增
  `persona_kind="operational"` 参数路径下，输出维度不包含"口头禅"等
  roleplay 专属措辞（可以用关键词黑名单做粗粒度断言，不追求语义级
  校验）。
- `draft_persona_topic_answer()`：`llm_helper=None` → `None`；正常返回
  → 非空字符串且不超过长度上限；输出跑题（比如返回一个问句而不是
  陈述句，可用简单启发式判断结尾是否为问号）→ 返回 `None`。
- `run_capability_learning_cycle()`：`needs_user_context` 为 `True` 且
  `draft_persona_topic_answer()` 返回非 `None` 时，落地的是
  `status="answered"` 的 `CapabilityQuestion` 而非 `pending` 问题，
  台账 `action="researched"`，且不占用 `max_pending_questions` 配额；
  `draft_persona_topic_answer()` 返回 `None` 时行为与改动前完全一致
  （回归测试，复用现有 `test_capability_learning_p1.py` 里对应用例）。
- CLI 批量 dismiss 命令：对若干条 pending 问题执行后全部变为
  `dismissed`，且不影响其它状态（`answered`/`expired`）的记录。

## 6. 风险与兼容性

- 所有新增判定/调研步骤都遵循"缺 LLM/检索就退回现状"的既有克制原则，
  未接入 `llm_helper`/`retriever` 的部署行为不变。
- `persona_kind` 只在创建时判定一次，不会因为后续维度调研结果而反复
  漂移，避免"角色定位跳来跳去"的不稳定观感。
- 批量 dismiss 是不可逆操作（保留记录、只改状态，不删除数据），执行
  前需要确认目标环境（生产/测试）范围正确，建议先加 `--track` 支持
  按 Track 精确清理，不必一次性清全量。

## 7. 实施记录

改动落地在 `src/mini_agent/evolution/capability_learning.py` 与
`src/mini_agent/cli/commands/capability_cmd.py`，按 §3 方案逐项对应：

1. `CapabilityTrack` 新增 `persona_kind: Optional[str] = None` 字段
   （`to_dict`/`from_dict` 均已接线，旧数据缺省 `None`，向后兼容）。
2. 新增 `classify_persona_kind(persona_desc, llm_helper)`；
   `CapabilityTrackStore.create()` 在 `target_type="persona"` 时调用一次
   并落盘，同时把结果传给 `draft_outline_with_llm()`。
3. `draft_outline_with_llm()` 新增 `persona_kind` 形参，
   `persona_kind="operational"` 时启用新的"职责边界/决策原则/升级触发
   条件/……"维度模板；`None`/`"roleplay"` 时行为不变。
   `run_capability_learning_cycle()` 里"空大纲自动补写"分支同步传入
   `track.persona_kind`。
4. 新增 `draft_persona_topic_answer()`（调研优先路径），
   `run_capability_learning_cycle()` 在 `needs_user_context()` 判定为
   `True` 后先尝试这条路径：成功则落一条 `status="answered"` 的
   `CapabilityQuestion`（答案前缀"（系统调研生成，非用户确认，如需修改
   可在看板编辑）"），台账 `action="persona_researched"`，计入新增的
   `summary["persona_topics_researched"]`，不占用 `max_pending_questions`
   配额；失败则退回原有"生成问题"分支（`generate_persona_topic_question()`
   同步接入 `persona_kind` 形参调整措辞）。**`needs_user_context()` 本身
   未改动**，符合"关键判据本轮先不改"的约定。
5. `_PERSONA_SYNTHESIS_INSTRUCTIONS_OPERATIONAL`（新增常量）+
   `synthesize_persona_draft_with_llm()` 按 `track.persona_kind` 选择
   对应的合成指令，`operational` 时去掉"虚构角色扮演"框架，改为"行为
   准则文档"框架。
6. `CapabilityLedgerEntry.action` 枚举补充 `persona_researched` 说明。
7. CLI 新增 `/capability questions --dismiss-all-pending [track_id]
   [--all-types]`，默认只清 persona 型 Track 的 pending 问题，
   `--all-types` 可扩大到 knowledge 型，`track_id` 可选精确到单个
   Track；只调用既有 `dismiss()` 逐条标记，不删除记录、不新增底层
   存储能力。
8. 新增/补充单测于 `tests/test_capability_learning_p1.py`：
   `classify_persona_kind`（无 LLM/正常解析/无法识别/异常四种情况）、
   `draft_outline_with_llm` 的 `operational` 分流、
   `draft_persona_topic_answer`（无 LLM/跑题判定/超长判定/正常生成）、
   `run_capability_learning_cycle()` 调研优先分支端到端（含调研失败
   退回提问的回归用例）。改动后完整跑通
   `tests/test_capability_learning_p1.py`（50 项，含本次新增 13 项）、
   `tests/test_capability_cmd.py`、`tests/test_capability_notification_v021.py`、
   `tests/test_capability_learning_empty_retrieval_fix.py`，均通过，无
   回归。

**本轮未做（按 §3.6 / 用户确认延后处理）**：
- 看板"系统调研生成"来源角标（体验优化，非功能性必需，答案本身已有
  文字前缀标注来源，不影响可审计性）。
- 存量 pending 问题的批量 dismiss 需在实际运行环境执行（本次交付的
  代码包不含运行时状态文件），命令见上面第 7 点。
