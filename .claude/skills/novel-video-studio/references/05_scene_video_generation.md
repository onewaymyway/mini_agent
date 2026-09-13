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
- `check_character_consistency.py`：**手写完 `prompt_en` 之后、跑生成
  脚本之前**校验每条 `prompt_en` 是否与引用的角色/地点档案
  (`visual_anchor_en`/`age_range`/`gender`) 一致，防止画面里的角色
  长相/穿着、场景外观跟阶段1抽取出的素材对不上；
- `check_clips_v2.py`：校验 `micro_scene` clip 完整性 + 已合成大场景
  视频时长一致性。

## 前置：Agent 手写 `prompt_en`/`video_mode`

阶段3产出的 `scene_detail.yaml` 里**没有** `prompt_en` 字段（阶段3只
负责内容/引用规划，不负责画面 prompt）。跑生成脚本之前，需要为每个
`micro_scene` 结合 `visual_hint` + `novel_project.json.art_style` + 引用
到的角色/地点 `description_en` 手写 `prompt_en`（英文），并按需设置
`video_mode`（`reference`/`keyframe`/`text`：有可用参考图时优先
`reference`，无参考图时用 `text`），直接写回对应 `scene_detail.yaml`
的 `micro_scenes[*]` 条目。**脚本不代为生成 prompt**——`prompt_en` 为
空时会直接把该小场景标记为失败并给出明确错误，不会用空 prompt 调用
视频接口。

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
根源。正确做法示例：

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
摘抄 `visual_anchor_en` 里与本镜头相关的那部分，不强求整句照搬，但
不能整句都不提、凭空另写一套外观。

## Step 0：脚本校验（写完 `prompt_en` 之后立刻跑）

```bash
python .claude/skills/novel-video-studio/scripts/check_character_consistency.py \
  <output_dir> --macro-id macro_01
```

- **errors 非空 → 必须回上面"前置"步骤修正 `prompt_en`，重新跑校验，
  直到 errors 清空才能进入 Step 0.5**：常见 error 是角色 `visual_anchor_en`
  /`age_range`/`gender` 缺失（回阶段1补），或 `prompt_en` 里出现了与
  角色锁定年龄/性别明显矛盾的描述（比如把老年配角的台词场景写成了
  年轻人）；
- warnings（`prompt_en` 里没有出现某个角色/地点 `visual_anchor_en` 的
  任何核心特征词）不阻断，但要带进 Step 0.5 一起看，不能因为脚本没
  报 error 就当作已经检查完毕；
- **这一步的退出码是"必要不充分条件"，不是"通过=可以生成"**：本脚本
  只做关键词级别的启发式检测，能查出"完全没提角色/地点""明显互斥的
  年龄性别关键词"这类粗暴错误，但查不出"用了同义词但其实没矛盾"、
  "两个角色的外观描述被写反但双方关键词各自合法"、"角色数量/在场
  人物跟原文对不上"、"服装道具细节和锚点冲突但锚点没提到这个细节"
  这几类需要理解语义才能发现的问题。**errors 清空只代表可以进入
  Step 0.5，不代表一致性已经检查完，禁止脚本一过就直接跳到 Step 1。**

## Step 0.5：Agent 语义一致性人工复核（脚本之外，强制执行，不可跳过）

Step 0 的脚本是关键词兜底，**不能替代 Agent 自己逐条核对**。写完/改完
一批 `prompt_en` 且 Step 0 errors 清空后，在调用 `generate_scene_videos_v2.py`
之前，Agent 必须对这个大场景里**每一条即将拿去生成的 `prompt_en`**
逐条过一遍下面的复核清单，而不是只看脚本 exit code：

1. **逐字对照 `visual_anchor_en`**：这条 `prompt_en` 里对该角色/地点的
   外观描述，是否在语义上（不要求字面重复）与档案里的 `visual_anchor_en`
   一致？重点看脚本查不出的地方——同义改写是否改变了原意（比如把
   `\"worn grey robe\"` 改写成了 `\"pristine silk robe\"`，关键词都合法，
   语义却相反）、有没有新增一个和锚点冲突但锚点本身没提到、脚本自然
   也检测不到的细节（比如锚点没写发型长度，这一条却写了和其它场景
   明显不同的发型）；
2. **核对在场人物是否与原文/`content_blocks` 一致**：这个小场景
   `uses_characters` 列出的角色是否都出现在了 `prompt_en` 里、有没有
   漏画应该在场的角色、有没有多画出不该出现在这个镜头里的角色，或者
   把台词/动作的归属写给了错误的角色（脚本不检查"画面里到底画了几个
   人、分别是谁"，只检查关键词是否互斥）；
3. **跨场景横向对比同一角色/地点**：如果这个角色/地点在之前的大场景
   已经生成过，回看之前那一条（或那一批）`prompt_en`，确认这一条延续
   的是同一套外观/环境细节，没有因为写作时凭这一镜头的感觉重新发挥
   而产生漂移；如果拿不准，直接把之前的 `prompt_en` 和这一条并排放在
   一起比较，而不是凭印象判断；
4. **确认没有互相写反**：同一大场景里如果有多个角色/地点，检查有没有
   把 A 角色的锚点特征写进了 B 角色的 `prompt_en`（脚本只按角色 id 分别
   检查各自命中率，两边关键词各自合法时脚本查不出写反）。

**复核方式**：这不是走流程式地"看一眼确认没问题"，而是要求 Agent 真的
把 `prompt_en` 原文和 `visual_anchor_en`/`age_range`/`gender`/
`description_zh` 摆在一起对比阅读后再下结论；发现问题就回上面"前置"
步骤直接改 `prompt_en`，改完重新跑一次 Step 0 脚本（脚本仍要过，且这次
修改不能引入新的关键词冲突），再重新过一遍本清单，直到确认无误。

复核过程不需要写额外报告文件，但如果发现了脚本没查出来、靠人工比对才
发现的问题，属于"已知会反复出问题的坑"，按 SKILL.md §3.3 追加一条到
`PROGRESS.md`（比如"char_03 和 char_05 的服装描述容易在 prompt_en 里
写混，后续场景写完要额外交叉核对一遍"），避免后面大场景重复踩坑。

只有这两步（脚本 errors 清空 + Agent 语义复核清单过完确认无误）都完成，
才能进入 Step 1 调用视频生成接口——**脚本通过但没做语义复核就直接生成，
或者语义复核发现问题却没有回去改 `prompt_en` 就直接生成，都是不允许
的**，这正是"角色/场景和抽取出的素材不一致"这个问题最终流入成片的
两个常见漏洞。


## Step 1：批量生成小场景视频

```bash
python .claude/skills/novel-video-studio/scripts/generate_scene_videos_v2.py \
  <output_dir> --macro-id macro_01 --aspect-ratio <novel_project.json 里的 aspect_ratio>
```

⚠️ **调用 bash 工具执行本命令时，`timeout` 参数必须传 `-1`**：单场景
视频生成常常要几分钟，一次批量生成动辄超过默认超时。

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

- 把该大场景全部 `micro_scene` clip 按顺序硬切拼接，配音（每个
  `micro_scene` 的 `content_blocks` 对应若干段旁白/对话 wav，按顺序
  首尾相接）混入，按每个 `micro_scene` 拼出的字幕文案（旁白原样、
  对话用「」包裹）渲染字幕；
- 非 Windows 环境需要 `--font-path <本地中文字体路径>`（如 Linux 上的
  `/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc`）；
- 缺 clip 时默认拒绝合成，`--allow-missing-clips` 才允许借用相邻小
  场景画面强制拉伸填补（仅用于明确知情的场景）；
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
   即可从中断处继续。
