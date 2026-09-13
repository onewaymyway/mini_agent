---
name: novel-video-studio
description: 把一篇小说自动转换成一支配旁白/角色对话配音的成片视频。一体化流程：全局角色地点抽取（含声音设定与锁定视觉锚点）→大场景切分→单大场景详细规划（旁白/对话拆分）→素材与差异化配音生成→角色/场景一致性校验+小场景视频生成与大场景内合成→最终拼接转场。每个阶段的具体操作步骤放在 references/ 下，进入某阶段前按需加载对应文件，完成后可从上下文中卸载。支持断点续跑、失败重试与 API Key 自动切换、跨 session 续接、以及用户对已生成内容提反馈后的定向回退重跑。当用户说"把这本小说做成视频"、"小说转视频"、"生成小说解说视频"时使用本 skill。
triggers: 小说转视频, 小说做视频, 小说解说视频, novel to video, 角色配音视频, 小说视频生成
resources:
  - id: entity-extraction
    path: references/01_entity_extraction.md
    description: 阶段1——全局角色/地点抽取（含 voice_profile 声音设定 + visual_anchor_en 锁定视觉锚点，供阶段5一致性校验使用），也覆盖阶段3触发的单点补抽取模式
    triggers: 角色抽取, 人物抽取, 地点抽取, voice_profile, 补抽取, 新角色, 新地点, visual_anchor_en, 一致性, 角色设定
  - id: macro-scene-split
    path: references/02_macro_scene_split.md
    description: 阶段2——按剧情/时间/地点把全文切分为大场景（macro_scene）
    triggers: 大场景切分, 场景切分, macro_scene
  - id: scene-detail-planning
    path: references/03_scene_detail_planning.md
    description: 阶段3——单个大场景详细规划：拆小场景（micro_scene，需按视频生成接口4-12秒硬限规划时长+粗估时长自查）、旁白/对话拆分、content_blocks 写法、check_scene_detail.py 校验规则与自查清单
    triggers: 小场景, micro_scene, 对话拆分, 旁白拆分, content_blocks, dialogue, narration, check_scene_detail, 时长, 4-12秒
  - id: assets-and-audio
    path: references/04_assets_and_audio.md
    description: 阶段4——角色/地点定妆图生成 + 按角色差异化配音（TTS）
    triggers: 定妆图, 配音, tts, voice_profile, asset_path, 差异化配音
  - id: scene-video-generation
    path: references/05_scene_video_generation.md
    description: 阶段5——手写 prompt_en/video_mode + 角色/场景一致性双重校验（脚本 check_character_consistency.py 关键词兜底 + Agent 语义人工复核，二者都要过）+ 小场景视频生成(generate_scene_videos_v2.py，timeout必须传-1)与大场景内合成(compose_macro_scene.py，逐场景慢放/快放对齐规划时长)
    triggers: 视频生成, 小场景视频, 大场景合成, clips, gen_video_with_text, 一致性, 角色不一致, 场景不一致, visual_anchor_en, 语义复核, 人工复核, timeout, 慢放, 快放, 时长不一致
  - id: final-compose
    path: references/06_final_compose.md
    description: 阶段6——所有大场景 done 后的最终拼接转场，产出 video.mp4
    triggers: 最终合成, 最终拼接, 转场, video.mp4, 成片
  - id: error-handling
    path: references/error_handling.md
    description: 调用 gen_image_with_text / gen_video_with_text / 配音脚本时的 API 失败处理规范（限流自动切 key 重试 vs 参数类错误定向重跑）
    triggers: api报错, 限流, 调用失败, 生成失败, key切换, 重试
  - id: revision-and-rollback
    path: references/revision_and_rollback.md
    description: 用户对已生成内容（台词/形象/画面）提反馈要求修改时，联动清空下游产物并从对应层级重新往后走的规则；invalidate.py 用法
    triggers: 改台词, 换形象, 重新生成, 修改内容, 回退, invalidate, 用户反馈
---

# 小说转视频一体化 Skill (Novel Video Studio)

## 0. 这个 skill 怎么用

本 skill 把"小说转视频"的完整流程放在一个 skill 里，但**不要求你一次性
读完所有细节**。主文件（本文件）只讲两件事：

1. 整体流程长什么样、各阶段产物/状态的契约是什么、阶段之间怎么衔接；
2. 通用规则：什么时候必须跑校验脚本、失败了怎么办、用户要改内容时怎么
   联动重跑、API 报错怎么处理。

**每个阶段具体"这一步要做什么、跑哪个脚本、参数怎么传"的操作细节，
登记在上面 frontmatter 的 `resources` 里，对应 `references/` 目录下的
文件，用 `skill_resource_load(skill_name="novel-video-studio",
resource_id=..., reason=...)` 按需加载，读完执行完这一阶段就可以把这
份细节从上下文里卸载**（不需要的时候不用一直带着，下次要用再加载一次
即可，文件不会变）。八份子资源分别对应六个阶段 + 两条贯穿性规范
（`error-handling`、`revision-and-rollback`），加载时机见各自
`description`；也可以用 `skill_resource_list` 查看当前完整清单。

不要在还没进入某个阶段之前就把该阶段的子资源通读一遍——这样会造成
上下文浪费；也不要凭记忆猜测某阶段的脚本参数，进入该阶段前先加载
对应文件。

配套脚本统一放在 `scripts/`（不分子目录，直接按文件名调用）：

```
.claude/skills/novel-video-studio/scripts/
├── check_entities.py             # 阶段1校验
├── check_macro_scenes.py         # 阶段2校验
├── check_scene_detail.py         # 阶段3校验
├── voice_mapping.py / tts_engine.py / synthesize_scene_audio.py
│                                  # 阶段4配音（synthesize_scene_audio.py 是入口）
├── check_assets_and_audio_v2.py  # 阶段4校验
├── generate_scene_videos_v2.py   # 阶段5生成小场景视频
├── check_character_consistency.py # 阶段5前置：prompt_en 与角色/地点档案一致性校验
├── compose_macro_scene.py        # 阶段5大场景内合成
├── check_clips_v2.py             # 阶段5校验
├── compose_final_video_v2.py     # 阶段6最终合成
├── check_project_state.py        # 通用：查看项目当前进度到哪一步、下一步做什么
└── invalidate.py                 # 通用：用户改内容后，联动清理下游产物+回退状态
```

角色/地点定妆图依赖 `gen_image_with_text` skill，视频生成依赖
`gen_video_with_text` skill，两者都是本 skill 的外部依赖（不重复实现，
按需调用即可）。

## 1. 整体流程与产物契约

```
阶段1 novel-entity-extractor（角色/地点抽取，含 voice_profile，全文一次性）
  → 阶段2 大场景切分（macro_scene，按剧情/时间地点切，全文一次性）
  → 对每个大场景 macro_scene_XX 依次循环：
        阶段3 详细规划(该场景) → 阶段4 素材(按需补)+配音(该场景)
        → 阶段5 小场景视频生成+大场景内合成(该场景) → 向用户展示、等确认
  → 全部大场景 done 后，阶段6 最终拼接转场
```

**关键点：阶段3-5 是"以单个大场景为循环体"跑的，不是"全部大场景先
过完阶段3、再全部过阶段4、再全部过阶段5"的批处理流水线**——用户需要
每做完一个大场景就能看到、检查这个场景的成片，而不是等到最后一刻才看
到第一份可检查的结果。角色/地点定妆图（阶段4的一部分）例外：它按
`asset_path` 是否已生成去重，天然是"用到哪个角色就顺带生成一次，后面
场景复用"，不需要、也不应该攒到所有场景一起生成。详见 §2.3。

目录结构：

```
novel_output/小说名_20260911/
├── novel_project.json          # 全局配置（时长/画风/TTS方案/转场模式）
├── PROGRESS.md                 # 跨 session 记忆日志（决策/坑/用户要求），见 §3.3
├── global/
│   ├── characters.json         # 角色库，含 voice_profile、asset_path
│   ├── locations.json          # 地点库，含 asset_path
│   └── assets/                 # character_*.png / location_*.png / cover.png
├── macro_scenes.yaml           # 大场景清单，status: pending→planned→done
├── macro_scene_01/
│   ├── scene_detail.yaml       # 本大场景的小场景规划
│   ├── audio/                  # narration_seg_<mid>_<i>.wav / dialogue_<charid>_<mid>_<i>.wav
│   ├── clips/                  # <micro_id>.mp4，如 micro_01.mp4（不是 micro_scene_01.mp4）
│   └── macro_scene_01.mp4      # 本大场景合成结果
├── macro_scene_02/ ...
└── video.mp4                   # 最终产物
```

### 1.1 各产物文件字段格式速查

下面把贯穿全流程、会被多个阶段读写的几份文件的**完整字段列表**集中列
一遍（各阶段 reference 里也有，但分散在各自文件里；这里是跨阶段的
权威速查表，字段类型/取值范围以这里为准，若某阶段文档描述与这里不一致
以这里为准并视为待修文档的 bug）。**新建/修改这些文件时，只写下面列出
的字段，不要自造字段名或改变已有字段的类型**，这是保持多阶段脚本之间
数据契约稳定的基础。

**`novel_project.json`**（阶段1创建，全程只读，除非用户明确要求改
项目级配置）：

| 字段 | 类型 | 取值/说明 |
|---|---|---|
| `source_title` | string | 小说标题 |
| `target_duration_sec` | number | 目标总时长（秒） |
| `art_style` | string | 全书统一美术风格英文描述，阶段1 Step1 填 |
| `tts.engine` | string | `"cosyvoice"` \| `"edge-tts"` |
| `tts.fallback` | string | 目前只实现 `"edge-tts"` |
| `tts.voice` | string \| null | 旁白默认音色，null 时脚本按引擎默认值 |
| `bgm_enabled` | bool | 目前恒为 `false`（本版不接 BGM） |
| `transition_mode` | string | `"cut"` \| `"fade"` |
| `transition_duration_sec` | number | `fade` 模式下的转场时长 |
| `orientation` | string | `"landscape"` \| `"portrait"` |
| `aspect_ratio` | string | 如 `"16:9"`/`"9:16"` |

**`global/characters.json`** 单条 `characters[]` 元素：`id`
(`char_NN`)、`names` (string[])、`age_range` (`"child"`\|`"teen"`\|
`"youth"`\|`"middle_aged"`\|`"elderly"`)、`gender` (`"male"`\|
`"female"`)、`nationality_or_ethnicity` (string)、
`description_zh`/`description_en` (string)、`visual_anchor_en`
(string，锁定视觉锚点：年龄段+性别+体型+发型发色+标志性穿着/显著特征
的一句话英文短语，阶段5每条 `prompt_en` 引用它保证角色外观跨场景不
漂移)、`voice_profile` (string)、`first_appear` (string)、`relations`
(string[]，如 `"char_02:挚友"`)、`asset_path` (string \| null，阶段4
回填)、`face_reference_id` (预留字段，恒 null，未实现)。

**`global/locations.json`** 单条 `locations[]` 元素：`id` (`loc_NN`)、
`name`、`location_type` (string，如 `"inn"`/`"forest"`)、`era_setting`
(string)、`description_zh`/`description_en`、`visual_anchor_en`
(string，同角色的锁定视觉锚点，建筑/环境类型+光照氛围+一两个标志性
视觉细节)、`asset_path` (string \| null，阶段4回填)。

**`macro_scenes.yaml`** 单条 `macro_scenes[]` 元素：`id` (`macro_NN`)、
`title`、`source_span`、`raw_text` (原文逐字，不可改写)、`summary`、
`estimated_duration_sec` (number)、`char_count` (number)、
`uses_characters`/`uses_locations` (id 数组)、`status`
(`"pending"` → `"planned"` → `"done"`，阶段3/5分别推进)。

**`macro_scene_XX/scene_detail.yaml`** 单条 `micro_scenes[]` 元素，
字段随阶段推进逐步补齐（同一份文件，不同阶段各自负责自己那部分字段，
不要覆盖其它阶段已写的字段）：

| 字段 | 类型 | 由哪个阶段写入 | 取值/说明 |
|---|---|---|---|
| `id` | string | 阶段3 | `micro_NN`，全项目范围唯一 |
| `macro_id` | string | 阶段3 | 所属大场景 id |
| `uses_characters` / `uses_locations` | string[] | 阶段3 | 引用的全局库 id |
| `visual_hint` | string | 阶段3 | 画面提示（中文，供阶段5写 prompt_en 参考） |
| `content_blocks` | object[] | 阶段3 | 见下方 `content_block` 结构 |
| `duration_sec` | number \| null | 阶段4回填 | 4-12 秒范围内，阶段3阶段写入时恒为 `null` |
| `prompt_en` | string | 阶段5（Agent 手写） | 阶段3不产出，脚本不代为生成，为空视频生成会直接失败；必须嵌入引用到的角色/地点 `visual_anchor_en`，见 05 文档"一致性铁律" |
| `video_mode` | string | 阶段5（Agent 手写） | `"reference"` \| `"keyframe"` \| `"text"`，不设置时脚本按 `"text"` 处理 |
| `status` | string | 阶段5回写 | `"pending"` → `"done"` \| `"failed"` |

`content_block` 结构（`content_blocks[]` 单个元素）：`type`
(`"narration"` \| `"dialogue"`)、`text` (string，`dialogue` 必须原文
逐字摘录不可改写)、`speaker` (`narration` 恒 `null`，`dialogue` 必须是
`uses_characters` 里的角色 id)。

**音频文件命名**（阶段4写入 `macro_scene_XX/audio/`，统一 `.wav`
后缀）：`narration_seg_<micro_id>_<block序号两位数>.wav`（如
`narration_seg_micro_01_00.wav`）、
`dialogue_<角色id>_<micro_id>_<block序号两位数>.wav`（如
`dialogue_char_02_micro_01_01.wav`）；`<block序号>` 是该 `micro_scene`
的 `content_blocks` 列表下标，`%02d` 补零。

**视频 clip 命名**（阶段5写入 `macro_scene_XX/clips/`）：
`<micro_id>.mp4`（如 `micro_01.mp4`，不带 `micro_scene_` 前缀）。

**`PROGRESS.md`**：不是结构化数据文件，是追加型 Markdown 日志，专门
装状态字段覆盖不到的跨 session 信息（决策/坑/用户要求），格式和写入
规则见 §3.3，新建/新 session 接手项目时都要处理它。

状态字段是阶段推进和"该不该回退"的唯一依据：
- `macro_scenes.yaml` 每条大场景：`pending`（未详细规划）→`planned`
  （规划完成、待生成/待合成）→`done`（大场景视频已合成且校验通过）；
- 每个大场景 `scene_detail.yaml` 里每个 `micro_scene`：`pending`→
  `done`/`failed`（视频 clip 生成结果）；
- 顶层 `video.mp4` 是否存在，代表整个项目是否已交付。

任何时候想知道"现在进度到哪、下一步该干什么"，跑：

```bash
python .claude/skills/novel-video-studio/scripts/check_project_state.py <output_dir>
```

输出里的 `overall_stage`/`next_actions`/`warnings` 直接告诉你该加载哪个
子资源（对照上面 frontmatter `resources` 里的 `id`）、该处理哪些大
场景，`warnings` 里如果出现"状态和磁盘不一致"，先处理这个再继续往下走
（通常是上次执行中途被打断导致的）。

## 2. 通用规则（贯穿所有阶段）

### 2.1 先落盘，再校验，通不过不进入下一阶段

每个阶段的产物写完立刻落盘（不等用户确认），然后跑该阶段对应的
`check_*.py` 脚本。**校验脚本退出码非 0，绝对不允许进入下一阶段**，
也不允许把状态字段推进（比如 `pending`→`planned`）。不通过时回到本阶段
内部修正，重新写文件、重新校验，直到通过。这是保证整条流水线不带着
"看似完成实则有缺陷"的产物往下传的唯一手段。

**特别地，`check_character_consistency.py`（阶段5角色/场景一致性）
这类涉及"画面语义是否和素材档案一致"的校验，脚本本身只能做关键词级别
的启发式检测，退出码 0 只是必要条件，不是充分条件**——脚本查不出同义词
替换后语义已经变了、角色写反、在场人物对不上原文这类问题。这类校验
必须是"脚本兜底 + Agent 逐条语义复核"两层都做完才算通过，不能只跑脚本
看 exit code 就往下走，具体复核清单见子资源 `scene-video-generation`
Step 0.5。

### 2.2 阶段间的"回补"是正常流程，不是异常

阶段3（详细规划）在处理某个大场景时，如果发现引用了阶段1没抽取到的
角色/地点，**不是报错终止**，而是：

1. 加载子资源 `entity-extraction`（`references/01_entity_extraction.md`）
   的"单点补抽取"模式，只处理当前这段原文，把新角色/地点并入
   `global/characters.json`/`locations.json`；
2. 加载子资源 `assets-and-audio`（`references/04_assets_and_audio.md`），
   只给这几个新增实体生成定妆图；
3. 回到阶段3继续规划，重新校验。

这个回补循环必须在阶段3内部闭环完成，不能把"引用了不存在的资源"这种
问题遗留到阶段4/5才发现——那样定位问题的成本高很多。类似地，阶段4如果
发现某个 micro_scene 配音后时长超过 12 秒硬限，也不是丢给阶段5硬着头皮
生成，而是回阶段3把这个小场景拆成两个，再重新走阶段4。

**判断"该往前回退几步"的通用原则**：定位到"是哪个阶段的产物不满足
下游契约"，就回到那个阶段修正，而不是在下游阶段里硬编码补丁绕过去。

### 2.3 长篇/多大场景：必须逐个大场景端到端跑完，不要跨大场景批量处理

`macro_scenes.yaml` 有几个大场景，阶段3-5 就要跑几轮，且**以"单个大
场景"为最小闭环单位**：对某个 `macro_scene_XX`，依次做完

```
阶段3详细规划(该场景) → 阶段4配音(该场景, --macro-id macro_XX)
→ 阶段5小场景视频+大场景内合成(该场景, --macro-id macro_XX)
→ 该场景 status=done → 向用户展示这个大场景的结果，等待反馈/确认
```

才能开始下一个大场景的阶段3。**禁止的反模式**：把全部大场景先一起跑完
阶段3（或阶段4的批量配音，不带 `--macro-id`），攒够所有大场景的配音后
再统一进入阶段5——这样用户要等到最后才能看到第一份可检查的成片，一旦
早期大场景的规划/人设/配音方向有问题，返工成本是按全部大场景计算的，
而不是一个大场景。

例外（这些确实是全局一次性资源，跟"逐场景处理"不矛盾）：
- 阶段1的角色/地点抽取——全文通读一次抽出全局角色地点表，不能只看
  单个大场景（会漏掉后面场景才出现的角色）；
- 阶段2的大场景切分——同样需要看全文划分边界；
- 阶段4 Step 1 的角色/地点定妆图——按 `asset_path` 是否已生成去重，
  同一角色贯穿多个大场景只需生成一次，天然应该在处理第一个用到该角色
  的大场景时顺带生成，之后其它大场景直接复用，不用重复生成。

阶段4 Step 2（配音）、阶段5（视频生成+合成）**默认都要带
`--macro-id macro_XX` 只处理当前这一个大场景**，不要在还没看到当前
大场景成片之前就去动下一个大场景。每跑完一个大场景的 Step 4 合成并
校验通过、`status=done` 后，把这个大场景的产物（时长、配音引擎使用
情况、任何持续性失败场景）汇报给用户，用户确认没问题或提完修改意见
处理完后，再开始下一个大场景的阶段3。

用 `check_project_state.py` 随时查看整体进度（哪些大场景 `done`、哪个
正在处理）。这样任何一个大场景中途失败、需要重新调整，都不影响其它
已完成的大场景，也不会出现"配了十个场景的音，视频一个都还没生成，
用户完全看不到进展"的情况。

### 2.4 API 调用失败

跑到 `synthesize_scene_audio.py`（配音）、`generate_scene_videos_v2.py`
（视频生成）、或直接调用 `gen_image_with_text`（定妆图）这几步时，
不可避免会遇到 API 报错（限流/参数错误/网络问题）。处理规范见子资源
`error-handling`（`references/error_handling.md`），核心原则一句话
说完：**限流类错误脚本会
自动切换 key 重试，不需要人工介入；非限流的参数类错误脚本重试几次后
会打印结构化错误直接放弃该条目并继续处理其它条目，需要 Agent 读懂错误
信息、去修对应的配置字段，然后只对失败的条目定向重跑，不要整体重跑，
也不要绕过错误直接忽略失败项**。

### 2.5 用户反馈要求修改内容

用户看了中间产物或成片后要求"这句台词改一下"/"这个角色形象换一个"/
"这段画面重新生成"，处理规范见子资源 `revision-and-rollback`
（`references/revision_and_rollback.md`）。
核心原则一句话说完：**先改源头内容，再跑 `invalidate.py` 联动清空所有
依赖它的下游产物和状态，然后从被清空的那一层重新往后走，不允许只改
源头文件却不清理下游——那样下游文件会和新内容对不上（比如台词改了但
字幕、配音还是旧的）**。

## 3. 项目启动与跨 session 续接

### 3.1 新项目

新项目从"进入阶段1"开始，向用户确认目标时长/横竖屏后立即写
`novel_project.json`（细节见子资源 `entity-extraction`，
`references/01_entity_extraction.md` Step 0），随后按上面的阶段顺序
推进。同时在 `<output_dir>/PROGRESS.md` 写入第一条记录（格式见 §3.3），
作为这个项目的跨 session 记忆起点。

### 3.2 新 session 接手已有项目——标准接手流程

任何一次新 session 里被要求"继续之前那个小说转视频项目"/"接着做"，
**不要凭对话上文的印象直接接着写产物**（新 session 很可能完全没有
上文，或者上文只是一段被压缩过的摘要，细节不可靠），而是固定按下面
四步重新建立事实基础，再决定下一步做什么：

1. **定位项目目录**。用户明确给了 `output_dir` 就直接用；没给的话，
   扫描 `novel_output/*/novel_project.json`，按目录 mtime 倒序列出候选
   项目（`source_title` + 路径），只有一个的话直接确认后使用，有多个
   且用户没指明是哪本小说时，报清单让用户选一个，不要猜。
2. **跑 `check_project_state.py`**，拿到 `overall_stage`/`next_actions`/
   `warnings`/每个大场景的 `status` 和 `micro_scenes` 明细——这是关于
   "磁盘上实际完成到哪"的唯一权威事实来源，比对话记忆和上次的口头总结
   都可靠。
3. **读 `<output_dir>/PROGRESS.md`**（如果存在，通常存在，见 §3.3），
   补上 `check_project_state.py` 覆盖不到的"为什么"这一层：上次中断
   前用户提过的定制要求、已知会反复出问题的坑（比如某个 TTS 引擎对某
   类文本容易超时/失败）、上次做到一半时定下来但还没落到任何结构化
   文件里的决定。**这一步不能跳过**——`check_project_state.py` 只反映
   机械状态（哪些文件存在、字段是否回填），反映不了"用户上次说这版
   人设不满意但还没来得及改"这类还停留在对话里的信息，跳过这步很容易
   在新 session 里重复上次已经被否掉的做法。
4. **处理完 `warnings` 里的状态/磁盘不一致后，才从 `next_actions`
   指向的阶段/大场景继续**，不要在明知不一致的情况下继续往下堆产物
   （常见于上次执行中途被打断：比如某个大场景 `scene_detail.yaml` 已经
   写完但校验没跑完，`macro_scenes.yaml` 里状态还停在 `pending`）。

以上四步本身很轻量（一次目录扫描 + 一次脚本调用 + 一次小文件读取），
每次接手都固定跑一遍，比"信任上一个 session 留下的摘要"更可靠，也不
需要用户重新讲一遍项目背景（`novel_project.json` 里已经有的时长/画风
/横竖屏等配置，直接读，不要再问用户一遍）。

### 3.3 `PROGRESS.md`：弥补状态文件覆盖不到的跨 session 记忆

`macro_scenes.yaml`/`scene_detail.yaml` 等状态字段解决的是"做到哪一步
了"，但解决不了"这一步是怎么做的决定、踩过什么坑、用户提过什么还没
落地的要求"——这些信息只存在于当时那次对话里，session 一断就丢失，
下一个 session 只能重新试错。`PROGRESS.md` 是专门装这类信息的**追加型
纯文本日志**，落在 `<output_dir>/PROGRESS.md`，格式：

```markdown
## 2026-09-13 14:20
- 完成 macro_01 全流程（配音+视频+合成），用户确认通过，无需修改。
- 用户要求：全书人物对话语气偏克制，旁白可以更煽情一些——后续大场景
  规划台词/旁白基调按这个来，不用每次都问。
- 已知坑：edge-tts 对超过 40 字的单段文本经常 300s 超时，配音时长的
  content_blocks 尽量控制在 40 字以内，超过的提前在阶段3多拆一刀。
- 下一步：从 macro_02 开始阶段3详细规划。
```

**写入规则**：
- 每完成一个大场景的端到端循环（§2.3）、或遇到一次需要记住的持续性
  问题、或用户给出一条会影响后续大场景处理方式的要求时，**追加**一段
  （不要改写/删除历史记录，这是日志不是状态文件）；
- 只记"下一个 session 需要知道、但不会体现在其它结构化文件里"的信息
  ——已经在 `macro_scenes.yaml`/`scene_detail.yaml` 里能看到的机械状态
  不用重复记（比如"macro_01 已完成"这种 `check_project_state.py` 一
  眼能看到的，除非附带了额外的、脚本看不出来的上下文才值得记，如上面
  例子里"用户确认通过，无需修改"这句就是脚本状态之外的信息）；
- 每条前面带时间戳（`date` 命令或当前对话的实际日期），方便按时间线
  回看；文件变长后不需要主动精简/归档，追加成本远低于误删有用信息的
  风险，真的长到影响阅读时再考虑摘要归并最早的若干条。

`check_project_state.py` 现在会顺带把 `PROGRESS.md` 最后几条打印出来
（见脚本说明），接手项目时两者一起看，不需要分别手动读一遍。
