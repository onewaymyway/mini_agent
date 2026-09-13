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
prompt_en 把地点和人物外观都换掉了，分开看容易顾此失彼）：

- 本 `micro_scene` 的 `visual_hint` + 全部 `content_blocks[*].text`
  （旁白/对话原文——情节真相的唯一来源）；
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
   必须对照这条情节原文才能发现；
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
    notes: "对照 char_01 变体 var_01（雪夜白大衣）与 content_blocks
      原文'雪地里站着一个穿白大衣的女人'核对，地点/服装/天气均一致；
      与本大场景内 micro_04 的 prompt_en 横向对比外观描述一致。"
```

- `prompt_en_hash`：对当前这条 `prompt_en` **原文**（一个字符都不能改）
  算 `sha1`，取十六进制前12位并加 `sha1:` 前缀，即
  `"sha1:" + hashlib.sha1(prompt_en.encode("utf-8")).hexdigest()[:12]`
  （与 `check_consistency_report.py` 内部算法完全一致，可以直接执行
  这段等价代码得到结果）。这是防止"核查通过之后又顺手改了几个字但
  报告没更新"的过期检测依据，必须如实填算出来的值，不能随便写；
- `status`：四项子检查任一项不是 `pass`，整体就不能写 `pass`；
- `notes`：**必须写清楚实际对照了哪些依据**（引用了哪个变体/哪条原文
  /和哪条 prompt_en 做的横向对比），不能写"已核查""没问题"这类空话——
  这是留痕，也是防止走过场的最低要求；
- 同一大场景内每处理完一批 `prompt_en` 就追加/更新对应的 `entries`
  条目（`micro_id` 已存在则覆盖该条），不需要一次性写完整个大场景才
  落盘一次。

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
   `prompt_en` 又被改动过，报告已经不能代表当前这版内容，视为未核查。

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
