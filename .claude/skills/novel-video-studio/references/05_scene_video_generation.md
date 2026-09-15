# 阶段5：小场景视频生成 + 大场景内合成

**依赖 skill**：`gen_video_with_text`（必须）。
**依赖脚本**：
- `generate_scene_videos_v2.py`：遍历 `macro_scene_*/scene_detail.yaml`，
  按 `--macro-id`/`--micro-id` 双重过滤批量/定向生成 `micro_scene` clip，
  逐大场景独立计轮次重试，完成后回写各 `micro_scene` 的 `status`；
- `compose_macro_scene.py`：单大场景内把 `content_blocks` 对应的旁白/
  对话 wav 按序拼接、micro clip 按 `duration_sec` 独立缩放拼接、渲染
  字幕（对话用「」包裹），合成 `macro_scene_XX.mp4`，成功后回写
  `macro_scenes.yaml` 对应大场景 `status=done`，校验不通过则不回写；
- `check_consistency_report.py`：**不做任何语义判断**，只机械校验
  Agent 在 Step 0 产出的 `consistency_report.yaml` 是否覆盖了本次要
  生成的全部小场景、是否全部标记为 `pass`、有没有在核查通过之后
  `prompt_en` 又被改动过（过期检测）——见下面 Step 0/Step 0.5；
- `check_clips_v2.py`：校验 `micro_scene` clip 完整性 + 已合成大场景
  视频时长一致性。

**本阶段不再用脚本做"画面是否和角色档案/情节一致"这件事本身**：这类
判断需要理解语义（同义改写有没有改变原意、画面是不是这段情节该有的
样子），关键词匹配既会漏检也会误报，之前版本的 `check_character_consistency.py`
就属于这类不可靠的机械检测。现在改成**完全由 Agent 逐条核查、核查
结论写成结构化报告**，脚本只负责"报告有没有认真写、有没有过期"这个
机械把关，语义判断的正确性完全落在 Agent 身上，不能依赖脚本兜底。

## 前置：Agent 手写 `prompt_en`/`video_mode`

阶段3产出的 `scene_detail.yaml` 里**没有** `prompt_en` 字段（阶段3只
负责内容/引用规划，不负责画面 prompt）。跑生成脚本之前，需要为每个
`micro_scene` 结合 `visual_hint` + `novel_project.json.art_style` + 引用
到的角色/地点 `description_en` 手写 `prompt_en`（英文），并按需设置
`video_mode`。**脚本不代为生成 prompt**——`prompt_en` 为空时会直接把该
小场景标记为失败并给出明确错误，不会用空 prompt 调用视频接口。

### `video_mode` 默认是 `reference`，不是"有图就用没图就算了"

跨大场景保持角色/地点外观一致，是本 skill 存在的核心价值。因此：

- **默认（不设置或设置为 `reference`）**：脚本要求引用到的全部角色/地点
  都必须已有定妆图（`asset_path` 非空），缺失时**整个命令在生成任何
  clip 之前就会整体拒绝启动**（`generate_scene_videos_v2.py` 新增的硬性
  前置检查，见下），不会像旧版那样自动降级成无参考图的 `text` 模式悄悄
  跑完；
- **只有显式设置 `video_mode: text`** 才代表 Agent/用户主动确认"这段
  不需要参考图、不追求跨场景一致性"，这种场景不受前置检查约束；
- **不允许**因为"这个角色/地点还没生成定妆图，先随手把 video_mode 改成
  text 让脚本能跑"——这是在悄悄放弃一致性，正确做法永远是先回阶段4
  Step1 把缺的定妆图补生成，跑通 Step3 校验后再回来设 `reference`。

`keyframe` 模式（有首尾帧时）不受本检查约束，逻辑不变。

## 硬性前置检查：生成前先校验角色/地点定妆图是否齐备

`generate_scene_videos_v2.py` 的 `main()` 在真正开始生成任何 clip **之前**，
会先把本次实际要处理的全部 `micro_scene`（应用 `--macro-id`/`--micro-id`
过滤后）聚合起来，检查它们引用的角色/地点（`video_mode` 不是显式
`"text"` 的场景）是否都已经有 `asset_path`。只要有一个缺失，**整个命令
立即 fatal 退出（exit code 2），不生成任何 clip**，stdout 打印结构化的
缺失清单（哪个 `micro_scene` 缺哪个角色/地点）和 `action_required`。

这是防止"阶段4的定妆图还没生成，就已经开始生成大场景视频片段"这类问题
的**代码层面硬拦截**，不再只依赖"Agent 记得先跑
`check_assets_and_audio_v2.py`"这种文档层面的自觉——过去出现过 Agent
图省事，在完全没有生成任何定妆图的情况下就直接进入阶段5，脚本遇到缺图
只是打印一行提示后自动降级为 `text` 模式继续跑，产物"看起来生成成功"，
但角色/地点在不同大场景之间的外观一致性完全没有保障，这正是本 skill
的核心卖点被悄悄放弃的情况。

**没有绕过开关**：这是硬性前置条件，不提供任何命令行参数跳过——跨大
场景保持角色/地点外观一致是本 skill 存在的核心价值，缺资产就必须先去
补，不能图省事跳过。唯一合法的例外路径是 Agent 逐条主动把该
`micro_scene` 的 `video_mode` 显式设为 `"text"`（代表确认这段不需要
一致性），这是一个需要对着具体场景做判断的动作，不是一个命令行参数
就能一次性豁免一批场景的。

`check_assets_and_audio_v2.py`（阶段4 Step3）仍然建议先跑一遍——它能给
出更完整的可读性报告（包括配音是否齐备），本检查只是在真正调用付费的
视频生成 API 之前的最后一道硬性防线，两者不冲突，前者是"体检报告"，
后者是"手术前最后一次核对"。

### 写 `prompt_en` 前先做一次结构化事实提炼，不允许直接跳到英文句子

真实发生过的失败模式：Agent 跳过"这段情节到底讲了什么"直接拼句子，
注意力被前面已经写好的句式带走，内容部分退化成一句可以套用在任何
镜头上的空话（例如把三个情节完全不同的 micro_scene 全部写成同一句
`"cinematic sci-fi scene"`），后续核查也顺着"我刚写完"的惯性直接
判了 pass，没人真的回头核对过原文。

为了不出现"没读懂就先写"的情况，为每个 `micro_scene` 写 `prompt_en`
之前，**必须先在草稿里逐项列出下面四项**（不写这一步、或写得和本条
`content_blocks` 对不上，不允许进入下一步拼句子）：

```
- 地点/天气/时间：____（来自 uses_locations 锚点 + visual_hint，不是凭空猜）
- 在场角色及此刻的状态/动作：____（来自 uses_characters 锚点 + content_blocks，
  没有台词/动作可归的角色不要出现在画面里）
- 画面里必须出现的具体物件/细节：____（直接来自本条 content_blocks 原文的
  具体名词，比如"泛黄的照片""白色大衣""猫眼"，不能是"一些物品"这类泛指）
- 相比上一个 micro_scene，这一刻画面上变了什么：____（同一地点连续出现多次
  时尤其要写，避免话虽不同、画面却和上一条无差别地复制）
```

只有这四项都能从 `content_blocks`/`visual_hint`/锚点里找到明确依据，
才能开始拼 `prompt_en`：

```
prompt_en = [引用角色/地点锚点原文摘抄，见下方铁律] + [上面四项列出的
             具体要素，翻译/组织成画面描述] + [art_style]
```

这四项本身不用写进任何产物文件，只是写作过程中的必经草稿——但如果
最终的 `prompt_en` 里体现不出这四项的内容，说明这一步被跳过了，等于
没有真正读过这段情节。

### 一致性铁律：`prompt_en` 里角色/地点的外观必须锚定到档案，不能现场
### 重新描述

每个 `micro_scene` 的 `uses_characters`/`uses_locations` 都已经在
`global/characters.json`/`locations.json` 里有对应的 `visual_anchor_en`
（阶段1锁定的核心外观特征，见 `01_entity_extraction.md`）。写
`prompt_en` 时，**把引用到的每个角色/地点的 `visual_anchor_en` 原文
摘抄或轻度改写后嵌入这条 prompt**，而不是凭这一个镜头的画面感觉重新
组织一遍外观描述——同一个角色在十个大场景里出现十次，如果每次都重新
描述"一个年轻人"，视频生成模型没有任何跨场景记忆，十次画出来的人可能
完全是十张不同的脸/十套不同的穿着，这正是"角色/场景前后不一致"问题的
根源。

**如果这个 micro_scene 在 `character_variant_overrides`/
`location_variant_overrides` 里指定了外观变体**（见
`03_scene_detail_planning.md` Step 4.5），改成摘抄该变体的
`visual_override_en`，而不是默认的 `visual_anchor_en`——变体本身就是
"这段时间内的默认外观换成了什么"，不是额外叠加在默认锚点之上。

正确做法示例：

```
visual_anchor_en（char_01）：a young Chinese man in his twenties, lean
  build, short black hair, wearing a worn grey robe, faint scar on
  left cheek
visual_anchor_en（loc_01）：a rustic wooden inn at night, dim lantern
  light, stone-paved entrance, weathered wooden signboard

写出的 prompt_en（结合本镜头的 visual_hint："客栈门口，夜晚，林然推门
而入，暖光从门内透出"）：
"A young Chinese man in his twenties, lean build, short black hair,
wearing a worn grey robe, faint scar on left cheek, pushes open the
door of a rustic wooden inn at night, dim lantern light, stone-paved
entrance, weathered wooden signboard, warm light spilling from inside.
<art_style 风格描述>"
```

镜头特写等确实只需要体现局部特征（比如只拍手部动作）的场景，可以只
摘抄 `visual_anchor_en`（或生效变体的 `visual_override_en`）里与本镜头
相关的那部分，不强求整句照搬，但不能整句都不提、凭空另写一套外观。

## Step 0：Agent 逐条语义核查 + 写核查报告（写完 `prompt_en` 之后，强制
## 执行，产出结构化报告文件，不可用脚本代替）

对本大场景**每一条即将拿去生成的 `prompt_en`**，Agent 必须**把下面几
份材料放在一起同时对照阅读**后再下结论——不是分开单独看，尤其"这条
prompt_en 是否符合情节"和"是否符合角色/地点外观设定"必须放在同一次
比对里一起看（同一处画面矛盾经常同时暴露在这两个维度上，比如某条
prompt_en 把地点和人物外观都换掉了，分开看容易顾此失彼）。

**核查的起点必须是 `content_blocks` 原文，不是刚写完的 `prompt_en`**：
先重新读一遍这条 `micro_scene` 的原文，把画面要素在脑子/草稿里过一遍，
再去看 `prompt_en` 有没有体现——顺序反过来（从"我刚写的 prompt_en 看着
像不像原文"出发去确认）非常容易变成自我确认式的走过场，因为"看着眼熟"
不代表内容对得上，这正是这次三条记录全部误判为 pass 的实际成因。

- 本 `micro_scene` 的 `visual_hint` + 全部 `content_blocks[*].text`
  （旁白/对话原文——情节真相的唯一来源，**从这里开始读，不是从
  prompt_en 开始**）；
- 本 `micro_scene` 引用到的每个角色的 `visual_anchor_en`/`age_range`/
  `gender`（若命中 `character_variant_overrides`，改用对应变体的
  `visual_override_en`）；
- 本 `micro_scene` 引用到的每个地点的 `visual_anchor_en`（若命中
  `location_variant_overrides`，改用对应变体）；
- 同一大场景内**此前已核查通过**、引用了同一角色/地点（且用的是同一
  默认锚点或同一变体）的其它 `prompt_en`（横向对比用，防止漂移）。

对每条 `prompt_en` 过下面四项核查，**四项都要单独下结论**（不能只看
整体印象）：

1. **角色/地点外观一致性**：`prompt_en` 里对角色/地点外观的描述，是否
   在语义上与上面对照的锚点（或生效变体）一致——重点看同义改写有没有
   悄悄改变原意（比如把 `"worn grey robe"` 改写成了 `"pristine silk
   robe"`，词都换了但语义相反）、有没有新增一个和锚点冲突、但锚点本身
   没提到因而无法机械检测的细节（比如锚点没写发型长度，这条却写了和
   其它场景明显不同的发型）；
2. **情节内容一致性**：`prompt_en` 描述的地点/天气/时间/人物状态/正在
   发生的动作，是否与 `visual_hint`+`content_blocks` 交代的一致，
   在场人物有没有漏画/多画、台词或动作有没有被归错给别的角色——**这是
   本次新增的核查维度，专门用来抓"和角色档案本身不矛盾，但和这一段
   情节矛盾"的问题**。典型反例：`content_blocks` 写的是"雪地里站着一个
   穿白大衣的女人"，`prompt_en` 却写成了"走廊里，一个穿深色连帽衫的
   女人"——地点、天气、服装全部被换掉了，如果只对照角色档案（档案里
   `visual_anchor_en` 本身没规定她必须穿白大衣），这类错误完全查不出，
   必须对照这条情节原文才能发现。**这一项下结论时必须同步摘录证据**（见
   下面 `content_alignment_evidence` 字段），不能只写"符合"两个字——写
   不出具体摘句，本身就说明这一项没有真的核对过；
3. **跨场景/大场景内横向一致**：与上面收集到的"此前已核查通过的同
   角色/地点/变体的 prompt_en"逐条对比，没有产生新的外观漂移；如果
   拿不准，把两条 prompt_en 并排贴出来比较，不要凭印象判断；
4. **变体使用正确性**（如果本场景引用了角色/地点且存在
   `appearance_variants`）：该用变体的地方确实用了，不该用变体的地方
   没有误延续上一场景的变体外观。

**任一项不通过**：回到上面"前置"步骤直接改 `prompt_en`，改完这一条要
**重新过一遍全部四项**（不能只重新看被改的那一项——改动可能带来新的
问题），直到四项都确认通过。

核查通过后，把结论写入
`<output_dir>/macro_scene_<id后缀>/consistency_report.yaml`（**每次
新增/修改 `prompt_en` 后都要同步更新这个文件，不能只在脑子里记得
"核查过了"**）：

```yaml
macro_id: macro_01
checked_at: "2026-09-14 10:30"   # date 命令或当前对话实际时间
entries:
  - micro_id: micro_06
    prompt_en_hash: "sha1:xxxxxxxxxxxx"   # 见下方"哈希怎么算"
    status: pass                          # pass | fail
    checks:
      character_appearance: pass
      location_appearance: pass
      cross_scene_drift: pass
      content_alignment: pass
    anchor_source:
      char_01: visual_anchor_en
      char_02: var_01
    anchor_coverage_judgement: "char_01 对照默认 visual_anchor_en：
      grey wool cardigan/tired eyes/blanket 三处特征均已体现，无同义
      改写导致语义反转；char_02 对照变体 var_01（地下室冰冷全息态）：
      pale blue glowing eyes/translucent shimmer 均已体现，'warm gentle
      smile' 这类默认态特征本条正确地没有出现，符合变体应替换默认外观
      而非叠加的原则。实际会传入的参考图：character_char_01.png、
      character_char_02_var_01.png。"
    content_alignment_evidence:            # 新增字段，见下方说明
      - content_block_quote: "她把毛毯裹得更紧，盯着地下室的方向"   # 从
                                            # content_blocks 原文直接摘抄，
                                            # 不能转述/改写
        prompt_en_span: "clutches the wool blanket tighter, gazing
          toward the basement doorway"     # prompt_en 里对应体现这句的片段
      - content_block_quote: "地下室里传来微弱的电子嗡鸣声"
        prompt_en_span: "a faint electronic hum drifting from the
          basement"
    notes: "对照 char_01 默认锚点与 char_02 变体 var_01 核对，地点/
      服装/天气均一致；与本大场景内 micro_04 的 prompt_en 横向对比
      外观描述一致。"
```

- `prompt_en_hash`：对当前这条 `prompt_en` **原文**（一个字符都不能改）
  算 `sha1`，取十六进制前12位并加 `sha1:` 前缀，即
  `"sha1:" + hashlib.sha1(prompt_en.encode("utf-8")).hexdigest()[:12]`
  （与 `check_consistency_report.py` 内部算法完全一致，可以直接执行
  这段等价代码得到结果）。这是防止"核查通过之后又顺手改了几个字但
  报告没更新"的过期检测依据，必须如实填算出来的值，不能随便写；
- `status`：四项子检查任一项不是 `pass`，整体就不能写 `pass`；
- `anchor_source`（**新增字段**）：这条 `prompt_en` 里引用到的每个
  角色/地点，实际对照的是哪一个锚点——顶层 `visual_anchor_en`，还是
  某个 `variant_id` 的 `visual_override_en`。取值直接写
  `visual_anchor_en` 或对应的 `variant_id`（如 `var_01`），逐个角色/
  地点列出。这个字段本身不是新的判断，只是把"核查项1核对的到底是
  哪份材料"显式记录下来，避免"核对过了"但对照错了锚点（比如该用
  变体的地方漏用了默认锚点）这类问题被 `notes` 的自然语言描述模糊
  带过；
- `anchor_coverage_judgement`（**新增字段**）：Agent 用自然语言写清楚
  这条 `prompt_en` 有没有把 `anchor_source` 指向的那份锚点材料里的
  关键外观特征体现出来——**这是一次完整的语义判断，不是关键词匹配**，
  要点出：锚点原文里哪些特征在 `prompt_en` 里体现了、哪些被省略（省略
  是否合理，比如特写镜头只需局部特征）、有没有出现"关键词还在但语义
  已经被同义改写反转"的情况（例如把 `"worn grey robe"` 改写成了
  `"pristine silk robe"`）；如果本条用了变体，还要写清楚变体应该
  替换掉的默认态特征是不是正确地没有出现（变体是"替换"不是"叠加"）；
  末尾注明这条镜头实际会传给视频生成接口的参考图文件名（对照
  `resolve_asset_paths()` 实际会解析出的路径），让报告的可追溯性从
  "文字锚点级别"落到"实际会用的参考图文件级别"。不能写"已核查""特征
  一致"这类空话——判断依据必须具体到锚点原文的哪几处特征，和 `notes`
  字段"必须写清楚实际对照了哪些依据"的要求一致，只是把"锚点覆盖"这一
  件事从笼统的 `notes` 里单独拆出来，强制写明细，防止被一句话带过；
- `content_alignment_evidence`（**新增字段**，对应核查项2"情节内容
  一致性"）：至少 2 条"原文摘句 ↔ prompt_en 对应片段"的成对引用。
  `content_block_quote` 必须是从本 `micro_scene` 的 `content_blocks[*].text`
  **直接摘抄的原文片段**（不能转述、不能是别的 `micro_scene` 的原文），
  `prompt_en_span` 是 `prompt_en` 里体现这句原文的对应片段。这个字段
  存在的意义是把"情节对得上"从一句自然语言结论，变成一份可以核对
  真伪的具体清单——摘不出属于本条的具体原文，往往就说明这一项根本
  没有真正核对过，比写一句"符合原文"更容易被单独复核出问题；**不同
  `micro_scene` 的这个字段理应各不相同**（因为原文本来就不同），如果
  发现自己在为好几条 `micro_scene` 摘出相似的句子，先停下来确认是不是
  记混了场景，而不是继续往下写；
- `notes`：除了上面拆出去的锚点覆盖细节、情节比对证据，继续按现有
  要求写清楚跨场景横向对比（第3项核查）实际对照了哪些依据，不能写
  "已核查""没问题"这类空话——这是留痕，也是防止走过场的最低要求；
- **逐条处理、逐条落盘，不要把核查攒到写完全部 `prompt_en` 之后再
  批量补**：同一大场景内每写完一条 `prompt_en` 就立刻核查、立刻追加/
  更新这条 `entries`（`micro_id` 已存在则覆盖该条）。批量补写是最容易
  出现"用同一套话应付多条"的场景——写完全部 `prompt_en` 之后，记忆已经
  模糊，容易对着好几条不同的情节写出高度雷同的 `anchor_coverage_judgement`/
  `content_alignment_evidence`，这正是下面反例发生的方式。

### 反例（真实发生过，务必对照自查）

某大场景有三个情节完全不同的 `micro_scene`（雪夜独坐客厅看照片 /
门口相认，妻子穿白大衣站在雪地里 / 对方自述死后意识被数字化、回来的
是仿生体），三条 `prompt_en` **全部**写成了同一句
`"cinematic sci-fi scene"`——既没有嵌入 `loc_01`/`loc_02` 的
`visual_anchor_en`（雪地小楼、旧沙发），也完全没有体现雪、照片、
门铃、白大衣、仿生体这些具体情节要素。对应的 `consistency_report.yaml`
三条全部标了 `content_alignment: pass`，且三条的 `anchor_coverage_judgement`
**几乎逐字相同**，只是复述了地点锚点原文，只字未提上面任何一个情节
细节。

这类"锚点句子看起来在，但翻回原文一个具体情节要素都对不上；好几条
判断文本高度雷同"的报告，即使四项子检查都写了 `pass`，也必须视为
未认真核查，打回重做——不能因为字段都填了、字数也够长就当作通过。

## Step 0.5：跑报告校验脚本（机械把关，不做语义判断，替代不了 Step 0）

```bash
python .claude/skills/novel-video-studio/scripts/check_consistency_report.py \
  <output_dir> macro_01
```

本脚本**不理解画面/情节语义**，只做三件纯机械的事：

1. 本次要生成的每个已写 `prompt_en` 的 `micro_scene`，是否都能在
   `consistency_report.yaml` 里找到对应条目——漏查的场景直接报错，
   不允许被生成脚本悄悄放过；
2. 每条记录的 `status` 和四项子检查是否都是 `pass`——只要有一项不是
   `pass` 就报错，不允许"大部分通过就先生成"；
3. **过期检测**：报告里记录的 `prompt_en_hash` 是否与 `scene_detail.yaml`
   里**当前** `prompt_en` 文本的哈希一致——不一致说明核查通过之后
   `prompt_en` 又被改动过，报告已经不能代表当前这版内容，视为未核查；
4. **`anchor_source`/`anchor_coverage_judgement` 字段完整性**（error）：
   两个字段是否都非空，`anchor_source` 是否覆盖了该 `micro_scene` 全部
   `uses_characters`/`uses_locations`——缺失说明这条锚点覆盖判断没有做
   或没写全，必须打回 Step 0 补齐。本脚本**只检查字段是否存在、是否
   覆盖了应该覆盖的角色/地点 id**，不判断 `anchor_coverage_judgement`
   里写的内容是否属实——这依然是纯语义判断，只能靠 Agent 自己认真写，
   脚本没有能力、也不负责验证这一点（字数过短的弱提示归入下面第5点的
   warning，和 `notes` 的处理方式一致）。

**errors 非空 → 必须回 Step 0 重新核查（不是回去随便改改报告文件让它
"看起来"通过）**，修正/补全对应条目后重新跑本脚本，直到 exit code 为
0 才能进入 Step 1。**退出码是这一步唯一的判断依据**——`check_project_state.py`
等其它脚本、以及 Agent 自己的记忆都不能替代这次校验，防止"上次核查过
了应该没问题"这类基于记忆的误判。

只有 Step 0（Agent 四项核查全部通过并写好报告）+ Step 0.5（脚本确认
报告完整、全部 pass、未过期）都完成，才能进入 Step 1 调用视频生成
接口——**跳过写报告直接生成、报告写了但没让脚本过就直接生成、或者
脚本过了但报告本身是应付了事写的，都是不允许的**，这正是"角色/场景
和抽取出的素材不一致""生成画面和小说内容不一致"这两个问题最终流入
成片的常见漏洞。

发现脚本查不出、靠 Agent 人工比对才发现的问题，属于"已知会反复出问题
的坑"，按 SKILL.md §3.3 追加一条到 `PROGRESS.md`（比如"char_03 和
char_05 的服装描述容易在 prompt_en 里写混，后续场景写完要额外交叉
核对一遍"），避免后面大场景重复踩坑。


## Step 1：批量生成小场景视频

```bash
python .claude/skills/novel-video-studio/scripts/generate_scene_videos_v2.py \
  <output_dir> --macro-id macro_01 --aspect-ratio <novel_project.json 里的 aspect_ratio>
```

⚠️ **调用 bash 工具执行本命令时，`timeout` 参数必须传 `-1`**：单场景
视频生成常常要几分钟，一次批量生成动辄超过默认超时（默认 300 秒会在
脚本还在正常工作时就把它强制杀掉，导致已经成功的场景也可能因为进程
被杀而来不及汇总；已成功的 clip 不会丢失，断点续跑机制会在下次重跑
时自动跳过，但仍然会打断当前这一轮的进度汇总，应当避免）。

**调用示例（system-prompt 模式工具调用格式，直接照抄，只替换
`<output_dir>` 和 `<aspect_ratio>`）**：

```
<tool_use>
{"name": "bash", "input": {"command": "python .claude/skills/novel-video-studio/scripts/generate_scene_videos_v2.py <output_dir> --macro-id macro_01 --aspect-ratio <aspect_ratio>", "timeout": -1}}
</tool_use>
```

（这是 `llm/system_tool_call.py` 里定义的
`<tool_use>{"name":..,"input":..}</tool_use>` 协议；若走的是原生
function-calling 的 provider，则等价于对 `bash` 工具传入
`{"command": "...", "timeout": -1}` 这个 `input`/`tool_input`，参考
`mv-generator` skill Step 5 里的同款写法。）不要省略 `timeout: -1`
这一项，也不要照搬"timeout 用默认值就行"的写法——本 skill 里
`generate_scene_videos_v2.py`（本步骤）和 `compose_macro_scene.py`/
`compose_final_video_v2.py`（见 Step 4、`06_final_compose.md`）都建议
传 `-1`，其余不涉及批量视频生成/本地渲染的步骤沿用默认超时即可。

- **按 SKILL.md §2.3 的逐场景循环，默认必须带 `--macro-id` 只处理当前
  刚配完音的这一个大场景**——不要跑完一个大场景的阶段4就去跑下一个
  大场景的阶段4，而是紧接着用这一步把当前大场景的视频也生成、合成
  完，让用户看到这一个大场景的完整成片后再开始下一个大场景。不传
  `--macro-id`/`--micro-id` 会处理全部大场景下的全部小场景，只在用户
  明确要求"全部重新生成一遍"时才这么用；`--macro-id macro_01 macro_03`
  可以指定多个（正常循环里只会传当前这一个）；`--micro-id micro_02`
  进一步只处理指定小场景（两者可组合）；
- 已存在且非空的 `clips/<micro_id>.mp4` 默认跳过（断点续跑），`--force`
  强制全部重新生成；
- 单场景失败自动重试 3 次，一轮跑完仍有未成功场景自动从头再跑一轮
  （每个大场景独立计轮次），直到全部成功或判定为持续性失败（连续一轮
  没有新增成功即停止，把剩余失败场景连同错误信息汇报出来，交给 Agent
  判断——常见原因见 `error_handling.md`）；
- 每个小场景处理完，把对应 `scene_detail.yaml` 里该 `micro_scene` 的
  `status` 回写为 `done`/`failed`。

**关于生成出的 clip 实际时长与规划时长不一致**：`gen_video_with_text`
接口虽然接受 `seconds` 参数（本脚本已按 `duration_sec` clamp 到
4-12 秒范围传入），但**无法精确控制生成结果的实际时长**——同一个
`seconds=8` 的请求，生成出来的 clip 实际可能是 7.6 秒或 8.3 秒，这是
接口本身的已知限制，**属于正常现象，不需要在这一步做任何特殊处理**、
也不需要因为时长对不上就判定生成失败或要求重新生成。真正解决"实际
时长和规划的 `duration_sec` 对不上"这个问题的地方是下面 Step 4 的
`compose_macro_scene.py`：它会对每个 `micro_scene` 独立做慢放/快放
（`setpts` 缩放）把实际 clip 精确对齐到规划的 `duration_sec`，做法和
`mv-generator` skill 的 `compose_mv.py` 完全一致（参见该 skill
SKILL.md「关于视频节奏对齐」一节）。这个前提是阶段4回填的
`duration_sec` 本身要落在合理范围内（对应 `04_assets_and_audio.md`
的时长超限处理规则，以及 `03_scene_detail_planning.md` 新增的粗估
时长自查）——如果规划阶段本身时长设计就有问题，缩放只能救"生成结果
和规划不一致"，救不了"规划本身就不合理"。

## Step 2：定向重跑（按需）

```bash
python .claude/skills/novel-video-studio/scripts/generate_scene_videos_v2.py \
  <output_dir> --macro-id macro_01 --micro-id micro_03 --force
```

补齐失败场景不需要 `--force`；对某个小场景的画面不满意想重新生成才
需要 `--force`（或用 `invalidate.py --macro-id macro_01 --micro-id
micro_03 --level video` 先清理再直接重跑，两种方式等价，前者更轻量）。

## Step 3：校验小场景 clip

```bash
python .claude/skills/novel-video-studio/scripts/check_clips_v2.py <output_dir>
```

- 校验每个 `micro_scene` 是否都有非空 `clips/<id>.mp4`，`status` 字段
  与磁盘状态是否一致；
- 若某个大场景已经合成过 `macro_scene_XX.mp4`，顺带校验其时长是否约
  等于该大场景所有 `micro_scene.duration_sec` 之和；
- 不通过 → 回 Step 2 用 `--micro-id` 补齐，重新跑校验直到通过。

## Step 4：大场景内合成

小场景 clip 全部就绪（Step 3 通过）后，逐个大场景跑：

```bash
python .claude/skills/novel-video-studio/scripts/compose_macro_scene.py \
  <output_dir> macro_01
```

⚠️ 大场景内 `micro_scene` 数量较多、分辨率较高时，本地 ffmpeg 逐场景
缩放+拼接的耗时可能超过默认超时，**调用 bash 工具执行本命令时建议
`timeout` 同样传 `-1`**（或至少 `600`）：

```
<tool_use>
{"name": "bash", "input": {"command": "python .claude/skills/novel-video-studio/scripts/compose_macro_scene.py <output_dir> macro_01", "timeout": -1}}
</tool_use>
```

- **逐 `micro_scene` 独立缩放对齐规划时长**（不是整体拉伸）：脚本内部
  按每个 `micro_scene` 的 `duration_sec`（规划时长）与该 clip 实际生成
  时长的比例，各自计算 `setpts=SCALE*PTS`——clip 比规划短就慢放、比
  规划长就快放，让每个小场景在大场景内出现的时刻严格贴合规划，不会
  因为整体拉伸导致后面小场景的画面/字幕/配音错位。这一步就是承接
  上面 Step 1 提到的"生成结果时长与规划不一致属正常现象"，具体做法
  与 `mv-generator` skill 的 `compose_mv.py` 一致；
- 把该大场景全部 `micro_scene` clip（已按上面缩放对齐）按顺序硬切
  拼接，配音（每个
  `micro_scene` 的 `content_blocks` 对应若干段旁白/对话 wav，按顺序
  首尾相接）混入，按每个 `micro_scene` 拼出的字幕文案（旁白原样、
  对话用「」包裹）渲染字幕；
- 字体/ffmpeg/ffprobe 路径自动探测（无需手填）：优先级依次是
  `--font-path`/`NOVEL_FONT_PATH`（`NOVEL_FFMPEG_PATH`/
  `NOVEL_FFPROBE_PATH`）显式指定 > `imageio_ffmpeg`（仅 ffmpeg/ffprobe）
  > conda 环境自动探测（当前激活环境优先，其次是名字含
  `novel`/`mv`/`video` 的环境） > 跨平台常见路径/系统 PATH；多数
  Linux 环境如果装了常见中文字体（Noto CJK / 文泉驿）能自动找到，
  `--font-path`/`NOVEL_FONT_PATH` 只是自动探测失败时的兜底手段，不再
  是非 Windows 环境的必填项；都找不到时脚本会明确报错并列出已尝试
  过的查找方式，不会静默使用一个不存在的路径；
- 缺 clip 时始终报错终止，不再有"借用相邻小场景画面强制拉伸填补"这
  个口子——clip 缺失是真实问题，请先回阶段5用
  `generate_scene_videos_v2.py`（`--macro-id --micro-id` 定向重跑）
  补齐，用 `check_clips_v2.py` 校验通过后再执行本命令；
- 合成并校验通过后，自动把 `macro_scenes.yaml` 里该大场景的 `status`
  从 `planned` 回写为 `done`；**校验不通过则不回写**，保持 `planned`，
  避免下游误以为已完成；
- 产物：`macro_scene_XX/macro_scene_XX.mp4`。

当前大场景 Step 1-4 跑完、`status` 变成 `done` 后，**先向用户展示这个
大场景的结果并停下来等确认**（见下），确认没问题（或反馈处理完）之后
才回到阶段3开始下一个大场景的规划——不要在用户还没看过这个大场景之前
就接着跑下一个大场景的阶段3/4。全部大场景都变成 `status: done` 后，
再进入阶段6。可以随时用 `check_project_state.py` 查看还有哪些大场景
没到 `done`。

**向用户展示**（每个大场景合成完都要做一次，不要攒到最后一起汇报）：
这个大场景的小场景生成成功/失败数量、合成结果（时长/分辨率/文件
路径，方便用户直接打开看）、持续性失败场景的可能原因；如果方便，
可以提示用户这是第几个/共几个大场景，接下来准备开始哪一个。

## 常见问题

1. **某个场景反复重试仍失败**：先看脚本汇报的最后一次错误信息，按
   `error_handling.md` 判断是限流/参数/资源缺失哪一类，对症处理后用
   `--macro-id --micro-id` 定向重跑，不需要动其它已成功的场景；
2. **`video_mode: reference` 但提示"没有可用的参考图片"**：说明对应
   角色/地点条目缺少 `asset_path`，脚本会自动降级为 `text` 模式继续
   生成（不会整体失败），但画面一致性会打折扣，建议回阶段4补生成缺失
   定妆图，再用 `--force` 重新生成这个场景；
3. **`compose_macro_scene.py` 报错"缺少配音"**：说明阶段4的配音脚本
   还没跑完该大场景，回去补跑（可用 `--macro-id` 只跑这一个大场景）；
4. **`timeout` 忘记传 `-1` 导致命令被提前杀掉**：已成功的场景不会丢失
   （断点续跑机制），直接重新执行同一条命令（记得加 `timeout: -1`）
   即可从中断处继续；
5. **`check_consistency_report.py` 报"报告已过期"**：说明 Step 0 核查
   通过、写完报告之后，`prompt_en` 又被改动过（比如核查通过后又顺手
   调整了几个字的措辞）——不是脚本误报，按 Step 0 对这条 `prompt_en`
   重新走一遍四项核查，用改动后的文本重新算 `prompt_en_hash` 并覆盖
   报告里对应条目，不能直接把旧的 hash 抄过去糊弄过去。
