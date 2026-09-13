# 阶段1：全局角色/地点抽取

**依赖**：无外部 API，纯 Agent 推理 + 文件读写。
**校验脚本**：`scripts/check_entities.py`。

## 模式 A：全文抽取（新建项目时）

### Step 0：项目初始化

向用户确认：目标视频时长（默认 3 分钟）、横竖屏（默认横屏 16:9）。
立即写入 `<output_dir>/novel_project.json`：

```json
{
  "source_title": "小说名",
  "target_duration_sec": 180,
  "art_style": "",
  "tts": {"engine": "cosyvoice", "fallback": "edge-tts", "voice": null},
  "bgm_enabled": false,
  "transition_mode": "cut",
  "transition_duration_sec": 0.5,
  "orientation": "landscape",
  "aspect_ratio": "16:9"
}
```

`transition_mode` 先按默认值 `cut` 写入，用户明确要转场效果时改成
`fade`（也可以留到阶段6再问）。

### Step 1：确定全书统一美术风格

结合题材/情绪基调/时代背景提炼一段英文风格描述，回填
`novel_project.json.art_style`，后续所有画面 prompt 都要带上这段风格
描述以保证视觉统一。

### Step 2：分块通读 + 抽取 + 增量合并

全文按章节（优先）或固定字数（无明显章节时，约 3000-5000 字一块）切分
成若干块，依次处理，不要求一次性把全文塞进上下文。

对每一块：
1. 通读，识别人物和地点；
2. 与当前 `global/characters.json`/`locations.json` 已有条目**合并去重**
   （同名/别名归并到同一 id；新出现的才新建 id，命名 `char_NN`/
   `loc_NN` 按当前库里最大编号递增，不要从头重新编号，避免下游已引用
   的 id 失效）；
3. 每个角色新增/更新时判断/补充下面这组**结构化视觉/声音属性**（结合
   原文描写 + 年龄性格身份合理推断，没有足够信息时给一个合理默认值而
   不是留空——留空会导致下游一致性校验直接拦截，见 Step 3.5）：
   - `age_range`：`child`/`teen`/`youth`/`middle_aged`/`elderly` 五选一，
     选一个跟原文年龄描述最贴近的粗粒度分组（哪怕原文没写具体岁数，也
     要结合身份关系给一个合理分组，比如"少年侠客"→`youth`）；
   - `gender`：`male`/`female`（原文明确是其它情况时如实填，不强行套）；
   - `nationality_or_ethnicity`：国籍/种族背景，没有明确设定时可以按
     小说的时代/地域背景给一个合理默认（如古代背景小说默认"Chinese"）；
   - `voice_profile`：音色描述（性别/年龄段/音色特点，如"青年女声，清亮
     活泼"），下游阶段4用它映射 TTS 音色；
   - `visual_anchor_en`：**锁定视觉锚点**，一句话英文短语，浓缩这个角色
     "无论出现在哪个场景都不能变"的核心外观特征——年龄段+性别+体型+
     发型发色+标志性穿着/配饰+显著特征（疤痕/眼镜等），例如：
     `"a young Chinese man in his twenties, lean build, short black hair,
     wearing a worn grey robe, faint scar on left cheek"`。
     这句话会被阶段5的每一条 `prompt_en` 引用/复用，是保证同一角色在
     不同大场景画面里不跑偏的关键字段，**必须具体到能直接塞进画面
     prompt 里使用的程度，不能写成"外貌普通""气质出众"这类无法转化为
     画面元素的空泛描述**。写这句话时逐项过一遍下面几类特征，原文有
     明确描写的照抄，没有的结合身份/年代/性格给一个具体而非笼统的
     合理推断（宁可写"深棕色齐肩直发"也不要写"头发有特点"）：
     - 年龄段+性别的具体化表述（不是重复 `age_range` 分组，而是给一个
       更具画面感的说法，如 `"a woman in her late thirties"`）；
     - 体型（瘦削/健壮/微胖/高挑等）；
     - 发型发色（长度、颜色、造型，如"披肩黑色卷发"）；
     - 标志性穿着/配饰（材质、颜色、款式，越具体越好，如"洗得发白的
       靛蓝长衫"而不是"古装"）；
     - 显著体貌特征（疤痕、痣、眼镜、胡须、纹身等，没有的可以不写这一
       项，但不要用"气质""神态"这类无法画出来的词填充）。
     前四类（年龄性别、体型、发型发色、标志性穿着/配饰）原则上每个
     角色都要写到，第五类视原文是否有描写决定要不要写。
4. **立即把合并后的完整 `characters.json`/`locations.json` 覆盖写回
   磁盘**（不是追加），保证任何时刻磁盘都是最新完整状态，中途中断不
   丢已处理部分。

只收录**会被可视化到画面里**的角色（有具体外貌/动作描写）；地点只收录
**反复出现（至少 2 次）** 的。

`characters.json` 条目字段：
```json
{"id": "char_01", "names": ["林然", "小林"],
 "age_range": "youth", "gender": "male",
 "nationality_or_ethnicity": "Chinese",
 "description_zh": "...", "description_en": "...(含 art_style)",
 "visual_anchor_en": "a young Chinese man in his twenties, lean build, "
   "short black hair, wearing a worn grey robe, faint scar on left cheek",
 "voice_profile": "青年男声，清朗略带江湖气",
 "first_appear": "第1章 第2段", "relations": ["char_02:挚友"],
 "asset_path": null, "face_reference_id": null,
 "appearance_variants": []}
```

`locations.json` 条目字段：
```json
{"id": "loc_01", "name": "青石客栈",
 "location_type": "inn", "era_setting": "ancient China",
 "description_zh": "...", "description_en": "...(含 art_style)",
 "visual_anchor_en": "a rustic wooden inn at night, dim lantern light, "
   "stone-paved entrance, weathered wooden signboard",
 "asset_path": null,
 "appearance_variants": []}
```

地点的 `visual_anchor_en` 同样要具体到画面元素级别：建筑类型/材质、
典型光照氛围、一两个一眼能认出"就是这个地方"的标志性视觉细节，不要写
"环境优美""古色古香"这类无法直接转化成画面的描述。

### Step 2.6：外观变体（`appearance_variants`）——只在原文明确交代外观
### 变化时才登记，不要滥用

`visual_anchor_en` 假设的是"这个角色/地点无论出现在哪个场景外观都不变"，
但小说里经常有原文明确写出的外观变化：换装、变装、季节更替、受伤后
包扎绷带、地点昼夜/天气/装饰变化等。这类变化如果不登记，会出现两种
坏结果：要么阶段5硬套着原锚点写 prompt_en，画面和原文明确交代的外观
对不上；要么每次都各写各的，同一次变化期间的多个小场景外观互相也对
不上（这也是"同一大场景内场景/角色前后不一致"的常见来源之一）。

**什么时候要登记一条 `appearance_variants`**（缺一不可）：
1. 原文有明确文字依据（换装描写、时间/季节跳跃、场景陈设变化等），
   不是"这一镜头感觉应该换个造型"这种主观判断；
2. 这个变化会在**不止一个** micro_scene 里持续存在（如果只是单个镜头
   一次性的细节，比如"她低头看了看自己沾了泥的裙摆"，直接写进那一条
   `prompt_en` 即可，不需要登记变体——登记变体是为了让"这段时间内的
   多个镜头保持同一套新外观"，不是给每个镜头的临时细节建档）。

不满足以上任一条时，不要新建变体，按 Step 2 的默认 `visual_anchor_en`
处理即可——变体机制是为了解决"持续性、有原文依据的外观变化"，滥用会
让 `appearance_variants` 膨胀成"每个场景一条"，反而失去锚点该有的
约束力。

登记方式：在对应角色/地点条目的 `appearance_variants` 数组追加一条：

```json
{"variant_id": "var_01",
 "label_zh": "婚礼当日红色婚服",
 "trigger_zh": "第7章：'她换上了那件绣金红裙，鬓边簪了一支金步摇'",
 "visual_override_en": "a young Chinese woman in her twenties, same "
   "build and face as before, now wearing a red embroidered wedding "
   "gown with gold trim, hair pinned up with a golden hairpin",
 "applies_scope": ["macro_05", "macro_06"],
 "asset_path": null}
```

字段说明：
- `variant_id`：`var_NN`，在该角色/地点内部唯一（不要求全局唯一）；
- `label_zh`：一句话中文标签，方便人工快速识别这是哪个变体；
- `trigger_zh`：**必填**，引用原文依据（章节+摘录或转述），阶段5/
  阶段3回补时用来判断"这个变体现在该不该生效"，也是防止滥用变体的
  留痕；
- `visual_override_en`：完整的、可以直接整句替换 `visual_anchor_en`
  使用的英文描述——**要在不可变的核心特征（脸型/体型/发色/显著特征）
  基础上，只替换服装/发型等确实变化的部分**，不是重新写一套完全不
  相关的描述，保证"这是同一个人，只是换了身衣服"而不是"变成了另一
  个人"；
- `applies_scope`：这个变体生效的范围，写 `macro_id` 列表（该大场景
  内该变体默认全程生效）或具体 `micro_id`（只在个别镜头生效，更精确）；
  阶段3/阶段5会校验当前场景是否落在这个范围内，不在范围内不允许使用
  这个变体（见 `check_scene_detail.py` 校验项8）；
  超出这个范围后默认自动回落到主 `visual_anchor_en`，不需要显式声明
  "变体结束"；
- `asset_path`：可选，只有这个变体的外观差异较大、会在多个大场景反复
  用到、值得单独生成一张定妆图（用于阶段5 `video_mode: reference`）
  时才生成，见 `04_assets_and_audio.md`；多数情况下靠 `prompt_en`
  文本里的 `visual_override_en` 覆盖即可，不必每个变体都出图。

变体的登记时机：可以在阶段1全文抽取时就发现并登记（如果通读时已经
看到换装剧情），更常见的是阶段3处理到具体大场景时才发现"这段原文里
角色的外观和当前锚点对不上"，此时按模式B的方式，只更新这一个角色的
`appearance_variants`（不需要重新跑全文抽取），登记完立即覆盖写回
`characters.json`/`locations.json`，再回阶段3把这条 micro_scene 的
`character_variant_overrides`/`location_variant_overrides` 填上对应
`variant_id`（字段说明见 `03_scene_detail_planning.md`）。

### Step 2.5：一致性字段自查（跑 Step 3 校验之前）

对每个新写/更新的角色/地点过一遍：`visual_anchor_en` 是否具体到能直接
被阶段5摘抄进 `prompt_en` 里（反例：`"看起来很有气场"`；正例：见上面
示例）；`age_range`/`gender` 是否跟 `description_zh`/`description_en`
里的年龄性别描述一致，不要出现"描述里写着中年人，`age_range` 却填
`youth`"这种同一份档案内部自相矛盾的情况——这类矛盾脚本本身检测不出来
（脚本只检查字段是否非空，不比对字段之间是否互相印证），需要 Agent
自己核对。

### Step 3：校验

```bash
python .claude/skills/novel-video-studio/scripts/check_entities.py <output_dir>
```

不通过（重复 id、角色缺 `voice_profile`/`visual_anchor_en`/`age_range`/
`gender` 等必填字段）→ 回 Step 2 修复对应条目，重新跑校验，直到通过才
能进入阶段2。

## 模式 B：单点补抽取（供阶段3回调）

阶段3规划某个大场景时，若发现引用了全局库里不存在的角色/地点：

- 输入：**只给该大场景的 `raw_text` 片段**（不是全文），提示"这段文字
  提到了但全局库没有的角色/地点是什么"；
- 流程：只对这一小段文本做与 Step 2 相同的抽取+合并+写回逻辑（合并
  对象仍是完整的全局库文件，只是本次处理范围缩小到一个片段）；
- 完成后同样跑 `check_entities.py`，通过后把新增角色/地点 id 返回给
  调用方，供其触发阶段4补生成对应素材。

不需要重新跑全文，只处理指定片段，长篇小说增量维护成本可控。

## 校验脚本说明 `check_entities.py`

检查：id 无重复；每个角色 `voice_profile`/`description_zh`/
`description_en`/`visual_anchor_en`/`age_range`/`gender`/
`nationality_or_ethnicity` 非空，`age_range` 取值必须是
`child`/`teen`/`youth`/`middle_aged`/`elderly` 之一；每个地点
`description_zh`/`description_en`/`visual_anchor_en` 非空；同名未合并
的启发式提示（仅警告不阻断）。退出码非 0 时不允许交付下游——
`visual_anchor_en`/`age_range`/`gender` 是阶段5 Agent 语义一致性核查
（见 `05_scene_video_generation.md`）能否有依据可查的前提，这里不把关，
下游的核查就无从查起。

**`appearance_variants` 不是本脚本的校验范围**（它是可选字段，本脚本
只检查非空数组内每条记录的字段是否完整：`variant_id`/`trigger_zh`/
`visual_override_en` 非空，`variant_id` 在同一实体内不重复；`variant_id`
被哪些 `micro_scene` 实际引用、引用范围是否越界，属于阶段3
`check_scene_detail.py` 的校验范围，见该文档校验项8）。

**向用户展示**：识别出的角色/地点清单（数量+简要身份+声音设定一句话）、
校验通过结果。

## 已知限制

- 分块大小是经验值，超长章节实体密度很高时仍可能需要进一步细分；
- `voice_profile` 只是文字描述，具体映射到 TTS 音色由阶段4负责；
- 人脸参考照片机制未实现，`face_reference_id` 先占位；
- `visual_anchor_en`/`age_range`/`gender` 之间是否互相印证（比如
  `visual_anchor_en` 里写的年龄描述和 `age_range` 分组是否对得上）只能
  靠 Step 2.5 人工自查，`check_entities.py` 只检查字段非空，不做跨字段
  语义校验；`age_range` 只有五档粗粒度分组，无法表达"看起来比实际年龄
  年轻"这类细节，下游一致性核查（阶段5，全部由 Agent 语义核查完成，见
  `05_scene_video_generation.md`）也依赖这五档粗粒度分组，查不出更细微
  的年龄描述偏差；
- `appearance_variants` 的 `trigger_zh` 是否真的对应原文、`visual_override_en`
  是否真的只改了服装/发型而没有意外改变角色的核心特征，都依赖 Agent
  自己写的时候把关，脚本只检查字段"非空"，不做语义层面的"这条变体
  写得对不对"的判断；变体之间 `applies_scope` 是否有意外重叠/空隙
  （比如两个变体同时声明覆盖同一个 macro_id）也不在脚本校验范围，出现
  这种情况时以 `scene_detail.yaml` 里 micro_scene 实际填的
  `character_variant_overrides` 为准，脚本会校验那个引用本身是否合法
  （见阶段3 `check_scene_detail.py` 校验项8），但不检测变体定义之间
  是否互相矛盾。
