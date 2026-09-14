# novel-video-studio 问题复盘与改进计划 v4

本文档是 v1~v3（时长估算 bug、id 编号脱节、画面/字幕相对配音漂移，均已
修完）之后，针对用户反馈"同一个大场景内，生成的角色/场景前后不一致——
人物衣服对不上、场景环境对不上"这一现象的排查与修复计划。沿用同样的
流程：先定位根因、写清楚修复方案，评审通过后再改代码，改完在本文件底部
勾掉 checkbox 并注明改动文件。

> **状态**：问题1、2、3均已改完，详见各自 checkbox 下方记录的改动文件
> 清单。三处改动均已用构造的最小测试项目 + 用户提供的真实
> `scene_detail.yaml` 手工验证过行为符合预期（见下方"验证记录"）。

## 背景与结论先行

现有 skill 并不是完全没做一致性机制——`global/characters.json` /
`locations.json` 已有 `visual_anchor_en`（锚定外观）、`appearance_variants`
（外观变体）、定妆图 `asset_path`、`video_mode: reference` 参考图注入、
以及阶段5 Step 0 的 Agent 语义核查 + `consistency_report.yaml`。但拿实际
项目（`永生协议`，见用户提供的 `characters.json`/`locations.json`/
`scene_detail.yaml`）复盘后，定位到三个具体缺口，是当前"衣服不一致、
场景不一致"的直接原因，而不是"完全没做一致性设计"：

1. 地点条目粒度太粗，一个地点条目要覆盖差异很大的多个子空间，锚点/
   定妆图管不到；
2. 外观变体（`appearance_variants`）只有文字描述，从未生成对应定妆图，
   也从未被视频生成脚本读取，reference 模式在变体场景里形同虚设；
3. "prompt_en 是否真的锚定到角色/地点档案"目前只有阶段5 Step 0 一次
   事后语义核查，缺一道更早、更聚焦的专项核查，且历史上曾考虑过用脚本
   关键词匹配做前置检查——**本次计划明确不采用这个方向**，仍然坚持"机器
   只管报告有没有写、有没有过期，语义判断必须是 Agent/LLM 来做"这条已经
   在 `05_scene_video_generation.md` 里定下的原则，把新增的检查点也纳入
   同一套"Agent 输出结构化报告 + 脚本机械校验报告完整性/时效性"的模式。

---

## 问题1（P0）：地点条目粒度过粗，室内/室外/不同房间共用一个锚点和一张定妆图

### 现象
`locations.json` 里 `loc_01`（城郊小楼）只登记了一条 `visual_anchor_en`，
描述的是**雪夜外观**（`a modest two-story suburban house at night...`），
只字未提室内环境。但 `scene_detail.yaml` 里引用 `loc_01` 的 6 个
`micro_scene`（`micro_02` 客厅沙发、`micro_03` 门铃/门口、`micro_04` 猫眼
视角、`micro_05`~`micro_17` 玄关/客厅对话）全部是**室内**镜头。Agent 写
`prompt_en` 时，`loc_01` 唯一能摘抄的锚点是"雪夜外观"，跟这些镜头实际
要画的室内环境毫不相关，只能每次临场编一遍客厅/玄关长什么样——这正是
"同一场景每次生成的房间布局、光线、家具都不一样"的直接原因，和 reference
图/语义核查做得好不好完全无关，是**锚点本身就没有覆盖这个空间**。

### 根因
`01_entity_extraction.md` 对地点条目的登记规则里，没有"同一建筑内如果
剧情会展示视觉差异明显的多个子空间，必须拆分为独立地点条目"这条规则，
默认按"一个地名 = 一个地点条目"归并，导致外观差异巨大的室内/室外被
强行塞进同一条 `visual_anchor_en`。

### 修复方案
1. `01_entity_extraction.md` 新增地点粒度规则：识别地点时，若原文对
   同一建筑/地名描述了视觉差异明显的多个子空间（室内 vs 室外、不同房间、
   不同楼层等），**按子空间拆分为独立地点条目**，命名规则
   `loc_NN_<子空间简称>`（如 `loc_01_ext` 外观、`loc_01_living` 客厅、
   `loc_01_hallway` 玄关），每个子空间各自登记独立的 `visual_anchor_en`；
   只有确实通篇只出现单一空间的地点才保留一条。
2. 已存在但粒度过粗的历史地点条目，允许在后续大场景规划中按同样规则
   **追加拆分**（新增 `loc_01_living` 等，原 `loc_01` 保留作为外观条目，
   不删除、不改变已引用它的旧 `micro_scene`，避免引用失效）。
3. 新增一条 warning（非阻断）：某个地点 id 被同一大场景内多个
   `micro_scene` 引用、且这些 `micro_scene` 的 `visual_hint` 彼此完全
   没有任何字面（2-gram）重叠时，提示"这个地点条目可能覆盖了多个子
   空间，建议评估是否需要拆分"——**这是唯一允许保留的机械提示**，
   定位是"提醒 Agent 去看一眼"，不做最终判断，也不阻断流程，最终是否
   拆分、拆得对不对，仍由 Agent 结合原文判断。**实现时放在
   `check_scene_detail.py`（阶段3）而不是 `check_entities.py`（阶段1）**：
   这条提示需要对比同一地点被引用的各个 `micro_scene.visual_hint`，
   而 `visual_hint` 是阶段3 `scene_detail.yaml` 里的字段，阶段1跑
   `check_entities.py` 时这个文件还不存在，没有数据可比对。
4. 阶段4定妆图生成（`04_assets_and_audio.md` Step1）逻辑不用改，本来就是
   按"扫描所有 `asset_path` 为空的地点条目"生成，拆分出的新地点条目会
   自然被扫描到并生成各自的定妆图。

- [x] 已完成。改动文件：
  - `references/01_entity_extraction.md`（新增 Step 2.55"地点粒度"规则，
    含判断标准、拆分做法、与 `appearance_variants` 的边界区分）；
  - `scripts/check_scene_detail.py`（新增
    `_check_location_granularity_hint()`，在 `check()` 里对每个大场景
    调用，新增校验项10；docstring 补充说明）。用用户提供的真实
    `scene_detail.yaml` 验证：正确报出 `loc_01` 被14个 micro_scene 引用、
    `micro_01`（雪夜外观）与 `micro_02`（客厅室内）无字面重叠，提示可能
    需要拆分——与用户反馈的实际问题吻合。

---

## 问题2（P0）：外观变体（`appearance_variants`）只有文字，没有定妆图，且从未接入视频生成的参考图逻辑

### 现象
`char_02`（苏晴）的 `var_01`（地下室冰冷全息态）已经写好了
`visual_override_en`，但 `asset_path: null`。即使后续大场景规划里给某个
`micro_scene` 打上了 `character_variant_overrides: {char_02: var_01}`，
实际生成视频时用到的参考图依然是 `char_02` 默认状态（温柔白大衣）的
定妆图——变体在"文字规划"层面被认真设计了，却在"参考图"层面完全没起
作用，reference 模式对这类镜头等于没有真正生效，只能靠文字 prompt 硬猜，
这是变体场景画面漂移的直接原因。

### 根因
两处代码/文档都没有覆盖变体定妆图：
1. `04_assets_and_audio.md` Step1 的定妆图生成范围只是"扫描
   `global/characters.json`/`locations.json` 里 `asset_path` 为空的
   顶层条目"，从未遍历每个条目的 `appearance_variants[]`；
2. `generate_scene_videos_v2.py::resolve_asset_paths()` 只读取
   `char_by_id[cid]["asset_path"]`/`loc_by_id[lid]["asset_path"]`，完全
   不检查该 `micro_scene` 是否带有 `character_variant_overrides`/
   `location_variant_overrides`，即使变体真的生成了定妆图也不会被选用。

### 修复方案
1. `04_assets_and_audio.md` Step1 定妆图生成范围扩大：对每个角色/地点条目，
   除了检查自身 `asset_path`，再遍历其 `appearance_variants[]`，对
   `asset_path` 为空的变体，用该变体的 `visual_override_en` 单独生成一张
   定妆图，落到 `global/assets/character_<id>_<variant_id>.png`（地点同理
   `location_<id>_<variant_id>.png`），生成后回填到该变体自己的
   `asset_path` 字段。触发时机同现有逻辑——处理某个大场景时，顺带给这个
   场景实际用到的变体（`character_variant_overrides`/
   `location_variant_overrides` 里出现过的）生成一次即可，不需要提前
   生成全部理论上可能存在的变体。
2. `generate_scene_videos_v2.py::resolve_asset_paths()` 改造：新增参数把
   当前 `scene` 的 `character_variant_overrides`/`location_variant_overrides`
   传入，解析每个 `uses_characters`/`uses_locations` 时先查这个映射，命中
   变体且变体 `asset_path` 非空 → 用变体图；命中变体但变体图还没生成 →
   打印明确 warning（"该镜头指定使用变体 var_01，但变体定妆图尚未生成，
   本次仍使用默认锚点图，一致性会打折，建议先回阶段4补生成"）并退回默认
   条目的 `asset_path`；未指定变体 → 沿用现有逻辑读默认 `asset_path`。
3. `check_assets_and_audio_v2.py` 的定妆图完整性校验，新增一项：对
   `scene_detail.yaml` 里出现过的每个 `character_variant_overrides`/
   `location_variant_overrides`，检查对应变体的 `asset_path` 是否已生成，
   缺失时报错（而不仅仅检查顶层条目的 `asset_path`），确保变体定妆图
   缺失这件事在阶段4就被拦下，不会拖到阶段5生成视频时才发现。

- [x] 已完成。改动文件：
  - `references/04_assets_and_audio.md`（Step1 新增变体定妆图生成说明：
    按需扫描 `character_variant_overrides`/`location_variant_overrides`
    实际引用到的变体，用 `visual_override_en` 生成图并回填变体自己的
    `asset_path`；产物路径清单同步更新）；
  - `scripts/generate_scene_videos_v2.py`（`resolve_asset_paths()` 改为
    读取该 micro_scene 的变体 override，新增 `_find_variant()`/
    `_resolve_entry_asset_path()`：命中变体且已有变体图 → 用变体图；
    命中变体但变体图为空 → 打印 warning 并退回默认图；未命中变体 →
    行为不变）；
  - `scripts/check_assets_and_audio_v2.py`（新增 `_variant_asset_path()`
    辅助函数 + 校验逻辑：`character_variant_overrides`/
    `location_variant_overrides` 引用到的变体若缺少 `asset_path`，报
    error 而不是放行；docstring 同步更新）。
  已用构造的最小数据手工验证三种路径（无 override / 命中变体但变体图
  为空 / 命中变体且变体图已生成）均按预期返回参考图路径并在该 warning
  的路径上打印提示。

---

## 问题3（P1）：`prompt_en` 是否真的锚定到角色/地点档案，只能靠阶段5 Step0
一次性核查，缺一道更早、更聚焦的专项检查

### 现象与设计取舍
`05_scene_video_generation.md` 的 Step 0 已经要求 Agent 对每条 `prompt_en`
做四项语义核查并写 `consistency_report.yaml`，`check_consistency_report.py`
负责校验报告完整性/是否过期。这套机制本身是对的方向，但目前只有这一道
关卡，且四项核查是"角色外观/情节内容/横向漂移/变体正确性"混在一起一次
过，没有专门针对"这条 prompt_en 有没有真的把锚点/变体的关键外观特征
带进去"单独出一份可追溯的检查记录。

**明确不采用的方案**：不引入任何基于关键词匹配/字符串相似度的脚本化
前置检查。锚点原文和 `prompt_en` 之间允许同义改写、允许只摘抄与本镜头
相关的局部特征（现有文档已经这么规定），关键词覆盖度这类指标既会在
正常的同义改写上误报，也拦不住"关键词都在但语义已经被改反"这类真正的
问题（比如把 `"worn grey robe"` 改写成 `"pristine silk robe"`，关键词
`robe` 还在，语义已经相反）——这正是 `05_scene_video_generation.md` 里
已经明确否定过 `check_character_consistency.py` 这类机械检测的原因，
本次不重蹈覆辙。

### 修复方案：把"锚点/变体覆盖核查"拆成一个独立的、专门由 Agent/LLM
判断的检查项，输出可追溯报告，机械脚本只管报告本身的完整性/时效性

1. 在阶段5 Step 0 的四项核查基础上，把"角色/地点外观一致性"这一项的
   核查粒度细化，要求 Agent 在 `consistency_report.yaml` 每条 `entries`
   记录里，除了现有的 `checks.character_appearance`/
   `checks.location_appearance` 结论，新增两个字段：
   - `anchor_source`：这条 `prompt_en` 里每个角色/地点实际对照的是哪个
     具体锚点（顶层 `visual_anchor_en`，还是某个 `variant_id` 的
     `visual_override_en`），逐一列出，例如
     `{"char_01": "visual_anchor_en", "char_02": "var_01"}`；
   - `anchor_coverage_judgement`：Agent 用自然语言写清楚"这条 prompt_en
     有没有把对照锚点里的关键外观特征体现出来，有没有出现同义改写导致
     语义反转的情况"，必须具体点出锚点原文里的哪些特征在 prompt_en 里
     体现了、哪些被省略（省略是否合理，比如特写镜头只需局部特征）、
     有没有发现语义冲突——不能写"已核对，没问题"这类空话，和现有
     `notes` 字段"必须写清楚实际对照了哪些依据"的要求一致，只是把
     锚点覆盖这一件事单独拆出来强制要求写明细，而不是被裹在笼统的
     `notes` 里容易被一句话带过。
   这一步**完全是 Agent 结合语义理解做的判断，不新增任何脚本判断逻辑**，
   只是把要求写的报告字段变得更具体、更难糊弄过去。
2. `check_consistency_report.py` 相应扩展**机械**校验范围（依旧不做任何
   语义判断）：检查每条 `entries` 是否都有非空的 `anchor_source` 和
   `anchor_coverage_judgement` 字段，缺失即报错（视为"该条目没有认真
   核查锚点覆盖情况"），字数过短（比如少于某个很宽松的下限，纯粹用来
   拦"写了三个字应付了事"这种极端情况，不做任何内容层面的语义判断）
   也报 warning 提示人工复查是否写得太敷衍。
3. 变体场景额外要求：当 `anchor_source` 里出现某个角色/地点的取值是
   `variant_id` 时，同步要求 `notes`/`anchor_coverage_judgement` 里写明
   "本次生成实际会传入的参考图是哪个 `asset_path`"（呼应问题2的修复：
   变体定妆图生成后，应该在报告里能看到"这条 prompt 配的到底是不是这张
   变体图"，而不仅仅是文字层面核对锚点），把报告的可追溯性从"文字锚点
   级别"提升到"实际会用的参考图文件级别"。
4. 上述改动只涉及 `references/05_scene_video_generation.md` 的报告字段
   要求说明、`check_consistency_report.py` 的字段完整性校验、以及
   `common.py`（如果需要新增共享的报告 schema 校验函数），**不涉及**
   任何新的关键词匹配/相似度计算逻辑。

- [x] 已完成。改动文件：
  - `references/05_scene_video_generation.md`（`consistency_report.yaml`
    示例新增 `anchor_source`/`anchor_coverage_judgement` 字段及详细写法
    要求，含变体场景要点出实际会用的参考图文件名；Step 0.5 说明新增
    第4项字段完整性校验）；
  - `scripts/check_consistency_report.py`（新增字段完整性校验：
    `anchor_source` 必须覆盖该 micro_scene 全部 `uses_characters`/
    `uses_locations`，`anchor_coverage_judgement` 必须非空，过短给
    warning；docstring 重新编号为5项机械检查）。未新增任何关键词匹配/
    相似度计算——判断内容是否属实完全留给 Agent，脚本只校验字段存不
    存在、覆不覆盖齐全。
  已用构造的最小测试项目验证：报告缺字段时正确拦截（分别验证
  `anchor_source` 缺角色、`anchor_coverage_judgement` 缺失两种情况），
  补全后正确放行（exit code 0）。

- [ ] 尚未验证：Agent 在真实项目里按新字段要求写报告时，
  `anchor_coverage_judgement` 的实际内容质量（脚本管不到，需要人工抽查）

---

## 影响范围确认

- 问题1、问题2 涉及 `01_entity_extraction.md`、`04_assets_and_audio.md`、
  `check_scene_detail.py`、`check_assets_and_audio_v2.py`、
  `generate_scene_videos_v2.py`；不改变现有 `micro_scene` 的字段结构
  （`character_variant_overrides`/`location_variant_overrides` 字段已
  存在，只是补上"真正被消费"这一环），不会让已经跑到一半的项目产生
  兼容性问题——旧项目里没有变体图的场景，行为退化为"打印 warning + 用
  默认锚点图"，跟现状一致，不会报错中断。
- 问题3 只涉及阶段5报告的字段要求和 `check_consistency_report.py` 的
  完整性校验，不影响阶段1~4，也不改变"语义判断完全由 Agent 负责，脚本
  只管报告完整性/时效性"这条既定原则，是在同一原则下把检查粒度做细，
  不是引入新的检查范式。
- 三个问题彼此独立，可以分开落地，但因为都指向同一个用户反馈（同一
  大场景内角色/场景不一致），建议一次性改完再让用户验证效果，避免
  改一半看不出改善。

## 后续验证方式

三项都改完后，建议在 `永生协议` 这个真实项目上重新跑一遍 `macro_01`~
`macro_02`（`macro_02` 会真正触发 `char_02` 的 `var_01` 变体），重点看：
1. `loc_01_living`/`loc_01_hallway` 等拆分出的室内子地点是否有了独立
   定妆图，室内镜头之间的房间布局/光线是否明显比之前更一致；
2. `macro_02` 地下室场景生成时终端日志里是否出现"使用变体定妆图
   `character_char_02_var_01.png`"这样的提示，而不是继续用默认白大衣图；
3. 新版 `consistency_report.yaml` 里每条记录是否都能看到具体的
   `anchor_source`/`anchor_coverage_judgement`，人工抽查几条对照原始
   `prompt_en` 看写得是否属实、有没有敷衍。
