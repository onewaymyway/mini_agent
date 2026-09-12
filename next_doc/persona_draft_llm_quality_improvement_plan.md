# Capability Learning 人设草稿质量改进方案（引入 LLM 润色）

对应背景：`next_doc/persona_capability_learning_design.md`（§10 persona 型
Track 原始方案）、`next_doc/roleplay_persona_design.md`（`.agent/personas/`
配置格式与字段语义）、`src/mini_agent/evolution/capability_learning.py`
（`draft_outline_with_llm`/`run_capability_learning_cycle`/
`draft_persona_markdown`/`publish_persona_draft`）、
`src/mini_agent/orchestrator/persona_profiles.py`（`render_persona_prompt`）、
`apps/mini_agent_kanban/app.py`（看板「人设草稿（§10.3）」区块）。

## 0. 问题与根因（沿完整链路排查）

用户反馈"生成的草稿效果很差"。沿"创建 Track → 收集问答 → 合成草稿 →
发布"整条链路核对代码，定位到四处真实缺陷，是叠加放大关系，不是单点问题：

1. **大纲子主题模板不分场景**：`CapabilityTrackStore.create()` 不论
   `target_type` 是 `knowledge` 还是 `persona`，起草初始大纲都统一调用
   `draft_outline_with_llm(title, persona_desc, llm_helper)`，其 prompt
   固定要求"循序渐进的子主题，覆盖从基础到进阶的关键知识点或能力维度"——
   这是为"学习一个知识领域"写的措辞，persona 型 Track 需要的是"性格特征/
   说话习惯/背景经历/价值观/人际关系"这类角色维度，两者完全文不对题。
   `run_capability_learning_cycle()` 里"空大纲自动补写"那处调用
   （约 1735 行）同样没有区分。
2. **追问文案是写死的通用模板**：`run_capability_learning_cycle()` 生成
   问题时，不论 topic 内容/Track 类型，问题文本永远是同一句
   `f"关于「{topic.name}」，能告诉我更多你的具体偏好/背景吗？这会影响后续
   推进的方向。"`，没有引导用户给出"人设需要的那种具体细节"（比如角色遇到
   某种情境会怎么说话/反应），用户随手一两句话作答，信息密度天然就低。
3. **草稿合成是纯拼接，没有"写手"这一步**：`draft_persona_markdown()`
   把每个 topic 下 `answered` 状态的原始回答逐条堆成 bullet list，不做
   语义去重、不做归纳、不组织成有语气的角色描述——本质是把问答记录换了
   排版，不是生成一份可用的人设文案。即便前两步收集到的信息足够好，到
   这一步也会被压扁成一堆干巴巴的事实条目。
4. **`tone` 字段的"设计意图"从未被兑现**：查 `next_doc/
   roleplay_persona_design.md` 第 3 节，`tone` 字段的注释是"可选，供
   persona-generator 参考/展示"——即设计意图是"persona-generator（这里
   就是 `draft_persona_markdown`）应该参考这个语气来写正文"，正文本身
   写出来就该"演出"这个语气，`tone` 只是给人看的一句摘要，不需要也不应该
   被机械拼进运行时 prompt。但现状是 `draft_persona_markdown` 从来没有
   生成过 `tone` 建议（frontmatter 里永远是空的 `tone: `），"persona-
   generator 参考 tone 来写正文"这个设计意图从未被实现过一次——不是
   `tone` 字段本身有 bug，是"生成 tone + 依据 tone 写正文"这一步根本
   不存在。

四点合在一起：**错的维度 → 笼统的问题 → 单薄的回答 → 机械的拼接（且从未
真正生成/使用语气信息）**，无论哪一环都会让最终草稿读起来像一份"资料卡"
而不是一份能立住的人设设定。

## 1. 设计目标与原则

1. **在关键环节引入 LLM，但不能编造用户没说过的具体信息**：LLM 只能
   基于用户已回答的材料做"组织/润色/补语气"，不能凭空发明角色的具体
   经历、数字、人名等事实性细节——这是本方案里所有 LLM 调用共同遵守的
   约束，写进各自的 prompt 里，且不做事后事实核查（做不到，只能靠
   prompt 约束 + 人工发布前审阅这道最后防线，与 §10.3 原方案"发布必须是
   显式用户动作"的原则一致）。
2. **LLM 是"锦上添花"而不是"关键路径"**：跟本文件其它 LLM 辅助函数
   （`draft_outline_with_llm`/`revise_outline_with_llm`）同款"能用就用，
   用不了就当没发生"的克制——没有 `llm_helper`、LLM 调用异常、返回格式
   解析失败，都要优雅退回现有的规则版实现，不能因为 LLM 这一步失败就让
   整个功能不可用，也不能因为解析失败而生成一份包含垃圾数据的草稿/大纲。
3. **保留可审计性**：LLM 生成的内容要能追溯"这是模型润色过的"，参考
   `distiller.py` 三条蒸馏路径给脚本打来源注释的做法，人设草稿也要在
   frontmatter 注释里标注"本次草稿是否经过 LLM 润色"，方便用户判断要不要
   多花心思核对细节。
4. **不改变现有的显式发布流程**：`publish_persona_draft()` 的行为、
   看板「发布」按钮的二次确认，都不受本方案影响；LLM 只作用于"草稿生成"
   这一步，不影响"发布"这一步的既有安全设计。
5. **`tone` 字段语义保持不变**：仍然是"供 persona-generator 参考/展示"，
   本方案让 `draft_persona_markdown` 真正承担起"generator"这个角色——
   LLM 润色时会同时产出 `tone` 建议并让正文风格与之呼应；额外在
   `render_persona_prompt()` 里把 `tone` 也作为一行显式提示词追加进
   运行时 prompt（见阶段 D），这是低成本的锦上添花（哪怕正文已经写出了
   对应语气，显式再提示一遍也无害），不是必须项。

## 2. 改动方案（按实施阶段划分）

### 阶段 A —— `draft_outline_with_llm` 按 `target_type` 分流 prompt

- 改动文件：`src/mini_agent/evolution/capability_learning.py`。
- `draft_outline_with_llm(title, persona_desc, llm_helper, target_type="knowledge")`
  新增 `target_type` 参数；`target_type == "persona"` 时使用一份新的
  prompt，明确要求"人设塑造维度"（性格特征、说话习惯/口头禅、背景经历、
  价值观与行为准则、人际关系/立场态度等），而不是"知识点递进"；其余
  校验逻辑（条数范围、清洗前缀、异常兜底）不变。
- 两处调用方同步传入 `target_type`：`CapabilityTrackStore.create()`、
  `run_capability_learning_cycle()` 里"空大纲自动补写"那处。
- 测试：新增用例断言 persona 场景下 prompt 文案包含"人设"相关关键词、
  knowledge 场景 prompt 保持原文案不变（防止误改共用逻辑）。

### 阶段 B —— persona 型 Track 的追问文案改为 LLM 生成（失败退回原模板）

- 改动文件：`src/mini_agent/evolution/capability_learning.py`
  （`run_capability_learning_cycle`）。
- 新增 `generate_persona_topic_question(topic_name, persona_desc, llm_helper)`：
  prompt 要求"针对角色的「{topic_name}」这个维度，设计一个能问出**具体
  细节**（比如角色在某个情境下会怎么说/怎么做）的问题，一句话，不要
  解释"，成功解析出非空单行文本则使用；`llm_helper` 为空、异常、返回
  空/超长（防御性截断到 60 字）都退回现有的通用模板，行为与之前完全
  兼容。
- 调用位置：`run_capability_learning_cycle()` 里原来写死
  `question_text = f"关于「{topic.name}」……"` 的地方，`track.target_type
  == "persona"` 时改为调用上面的新函数（附带 fallback），knowledge 型
  Track 不受影响。
- 测试：桩 `llm_helper` 场景验证生成文案被使用；`llm_helper=None`/抛异常/
  返回空文本三种场景验证正确退回原模板。

### 阶段 C —— 引入 LLM 人设草稿润色（核心改动）

- 改动文件：`src/mini_agent/evolution/capability_learning.py`。
- 现有 `draft_persona_markdown()` **保留不动**，改叫"素材整理"（它产出的
  bullet list 本身也要喂给 LLM 当"事实来源"，防止 LLM 编造），继续作为
  没有 `llm_helper` 时的兜底路径。
- 新增 `synthesize_persona_draft_with_llm(track, questions, llm_helper) ->
  Optional[str]`：
  1. 先调用 `draft_persona_markdown()` 拿到规则版草稿（含 frontmatter），
     把其中"正文"部分（去掉 frontmatter）连同 `persona.tone` 当前为空
     这一事实一起交给 LLM 当素材；
  2. prompt 明确要求：只能使用给定材料里出现过的信息组织成**通顺、有
     语气、像一份角色设定说明书**的正文（可以合并同类回答、调整语序、
     补充"这个角色会怎么说话"这类**文风示范**，但不能加入材料里没提到的
     具体经历/数字/人名等新事实）；同时输出一个不超过 20 字的 `tone`
     建议（例如"沉稳、简练、偶尔调侃"这种短语，与
     `next_doc/roleplay_persona_design.md` 示例同风格）。
  3. 要求 LLM 按固定 JSON 结构返回（`{"tone": "...", "body": "..."}`），
     跟 `distiller.py` 里几处"要求 LLM 只回 JSON"的既有约定一致；解析
     失败/字段缺失/`body` 为空，直接返回 `None`（调用方退回规则版）。
  4. 不在这个函数内部做"缺失维度提示""真人模仿安全检测"这些工作——
     这些校验统一收敛到调用方（见下面"整合"一条），避免 LLM 输出和规则版
     输出各自重复一遍这些逻辑、后续要改两处。
- 整合：`draft_persona_markdown()` 新增可选参数 `llm_helper=None`：
  - 不传或调用 LLM 失败：行为与现在完全一致（纯规则拼接）。
  - 传入且 `synthesize_persona_draft_with_llm()` 成功返回结果：用 LLM
    给的 `body`/`tone` 替换规则版对应部分，但 frontmatter 里除 `tone`
    外的字段（`name`/`display_name`/`description`/其余留空字段）、
    末尾的"缺失维度提示"、`detect_real_person_reference()` 安全提示，
    仍然沿用规则版的计算方式和文本，只是拼接对象换成 LLM 润色过的正文。
  - 在正文最上方那条"本文件由 Capability Learning ... 合成于 xxx"注释里
    追加一句"（本次草稿已经过 LLM 润色）"或"（本次草稿为规则拼接，未
    经 LLM 润色，可能是未接入 LLM 或本次调用失败）"，实现可审计性目标。
- API/CLI 接线：
  - `src/mini_agent/api/capability_routes.py::draft_persona()` 用已有的
    `_get_llm_helper(request)`（跟 `revise_outline`/`draft`大纲那两处
    同款约定）取 `llm_helper`，传给 `draft_persona_markdown()`。
  - `src/mini_agent/cli/commands/capability_cmd.py` 的
    `/capability persona draft` 子命令同理，用已有的
    `_get_llm_helper(agent)` 取值传入。
  - 看板侧不需要改动：`client.draft_capability_persona()` 只是转发 POST
    请求，LLM 是否参与由服务端决定，前端展示逻辑不变。
- 测试：
  - 桩 `llm_helper` 返回合法 JSON 场景：断言最终草稿正文来自 LLM、
    `tone` 非空、frontmatter 其余字段/安全提示仍然正确。
  - `llm_helper` 返回非法 JSON / 缺字段 / 抛异常三种场景：断言退回
    规则版输出（与现有行为逐字节一致，防止静默改变兜底路径）。
  - `llm_helper=None`：断言与阶段 C 之前的行为完全一致（回归测试）。

### 阶段 D —— `tone` 生效闭环的低成本补强（可选增强，不是必须项）

- 改动文件：`src/mini_agent/orchestrator/persona_profiles.py`
  （`render_persona_prompt`）。
- `persona.tone` 非空时，在渲染出的 system prompt 片段里追加一行显式
  提示（例如"语气/说话风格：{tone}"），紧跟在"当前角色扮演设定"标题之后、
  正文之前。`tone` 为空（老的手写人设文件本来就没填）时行为不变。
- 这一步不改变 `tone` 字段"供 generator 参考"的原始语义，只是在正文
  已经体现了该语气的基础上，再显式提示一遍，帮助模型更稳定地贯彻，属于
  低风险的强化，不依赖阶段 C 是否启用（老的手写人设文件如果自己填了
  `tone`，同样会受益）。
- 测试：`tone` 非空/为空两种场景断言渲染结果差异，且安全边界后缀
  （`_SAFETY_SUFFIX`）位置/内容不受影响。

## 3. 明确不做的事情

- 不对已经发布到 `.agent/personas/` 的历史人设文件做批量"重新润色"——
  发布是用户的显式产物，本方案只改"生成草稿"这一步，不会反过来动已发布
  文件；用户如果想让老角色也享受到润色效果，需要自己重新走一遍
  草稿生成 + 发布流程（覆盖同名文件）。
- 不引入"LLM 事后核实草稿是否忠于原始问答"这类二次校验——目前没有可靠、
  低成本的自动化事实核查手段，过度设计成本高、收益不确定，安全网仍然是
  "发布前用户自己审阅"（§10.3 原则），阶段 C 的 frontmatter 注释已经
  标注"是否经过 LLM 润色"，足够提示用户在这种情况下多留意。
- 不改 `persona_desc`（Track 创建表单里那句一句话概括）本身的生成方式——
  这是用户手写输入，不是自动生成产物，不在本方案范围内；如果用户填得
  单薄，阶段 A/C 能缓解（追问会引导出更多细节、LLM 润色能把零散信息
  组织得更像样），但没法从根上解决"用户一开始就没想清楚"这个问题。

## 4. 实施记录

阶段 A/B/C/D 已按上述方案实现并通过测试。

### 改动文件清单

- `src/mini_agent/evolution/capability_learning.py`
  - `draft_outline_with_llm()` 新增 `target_type="knowledge"` 参数，
    `target_type=="persona"` 时切换成"人设塑造维度"prompt；两处调用点
    （`CapabilityTrackStore.create()`、`run_capability_learning_cycle()`
    的空大纲自动补写）同步传入 `track.target_type`。
  - 新增 `generate_persona_topic_question(topic_name, persona_desc,
    llm_helper)`：persona 型 Track 追问优先用 LLM 生成更容易问出具体
    细节的问题；`run_capability_learning_cycle()` 里生成 `question_text`
    的地方按 `track.target_type == "persona"` 分流，失败/无 llm_helper
    退回原通用模板。
  - 新增 `_PERSONA_SYNTHESIS_INSTRUCTIONS` + `synthesize_persona_draft_
    with_llm(track, raw_body_material, llm_helper)`：把规则版正文材料
    交给 LLM 润色，要求只重组/润色已有信息、不编造新事实，输出
    `{"tone": ..., "body": ...}` JSON，解析失败/字段缺失一律返回
    `None`。
  - `draft_persona_markdown()` 新增可选 `llm_helper=None` 参数：不传时
    行为与改动前逐字节一致；传入且 LLM 润色成功时用其 `body`/`tone`
    替换规则版对应部分，缺失维度提示/真人模仿安全提示仍基于规则版材料
    计算，frontmatter 上方注释追加"是否经过 LLM 润色"的说明。
- `src/mini_agent/api/capability_routes.py`
  - `draft_persona()` 路由用已有的 `_get_llm_helper(request)` 取
    `llm_helper` 并传给 `draft_persona_markdown()`。
- `src/mini_agent/cli/commands/capability_cmd.py`
  - `/capability persona draft` 子命令同理，用已有的
    `_get_llm_helper(agent)` 取值传入。
- `src/mini_agent/orchestrator/persona_profiles.py`
  - `render_persona_prompt()`：`persona.tone` 非空时在角色扮演设定标题
    后追加一行"语气/说话风格：{tone}"显式提示；`tone` 为空时行为不变。
- `tests/test_persona_draft_llm_quality.py`（新增文件）
  - 覆盖阶段 A（persona/knowledge prompt 文案分流）、阶段 B（LLM 追问
    的成功/None/异常/空/过长五种场景）、阶段 C（`synthesize_persona_
    draft_with_llm` 的合法 JSON/代码块包裹/非法 JSON/缺字段/异常/tone
    截断六种场景，以及 `draft_persona_markdown` 的无 LLM/LLM 成功/LLM
    失败回退/安全提示保留四种整合场景）、阶段 D（tone 非空/为空两种
    渲染场景）。

### 验证情况

- 上述新增测试与既有 `tests/test_capability_learning_p1.py`、
  `tests/test_capability_learning_empty_retrieval_fix.py`、
  `tests/test_capability_persona_wiki_scopes_binding.py`、
  `tests/test_capability_cmd.py`、`tests/test_capability_routes_mount.py`
  一并跑过，共 109 个用例全部通过，无回归（后两个测试文件在本沙盒环境
  下需要额外安装 `fastapi`/`uvicorn`/`python-multipart`/`httpx` 等依赖，
  与本次改动无关，是既有的环境依赖缺口）。

### 已知遗留

- 阶段 C 的 LLM 润色只对"新生成的草稿"生效，不会反向重新润色已经落盘
  的旧草稿/已发布的 `.agent/personas/*.md`（见第 3 节"明确不做的事情"）；
  用户想让老角色受益需要手动重新走一遍生成+发布流程。
- `synthesize_persona_draft_with_llm()` 对 LLM 是否"忠于原始材料"没有
  做任何事后校验（技术上做不到可靠核查），完全依赖 prompt 约束 + 用户
  发布前审阅；frontmatter 里"已经过 LLM 润色"的提示是目前唯一的
  事中信号。
- `generate_persona_topic_question()` 目前只对 `target_type=="persona"`
  的 Track 生效，knowledge 型 Track 的追问文案仍是原来的固定模板——
  如果后续发现 knowledge 型 Track 的追问质量也有类似问题，需要单独
  评估是否要为它也引入 LLM 生成（可能需要不同的 prompt 措辞，不能直接
  复用 persona 版本）。
