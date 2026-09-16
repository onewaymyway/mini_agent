# 阶段3：全局角色/地点定妆图生成

**依赖 skill**：`gen_image_with_text`（必须）。
**外部依赖**：`AGNES_API_KEY`。API 失败处理见 `error_handling.md`。
**产物**：`global/assets/character_*.png` / `location_*.png`（+ 若
`novel_project.json.cover.enabled == true`，还有
`global/assets/cover_bg.png` 和叠好标题文字的
`global/assets/cover.png`），并回填 `global/characters.json` /
`global/locations.json` 里对应的 `asset_path` 字段。

## 为什么要单独成一个阶段

这一步以前是阶段4（素材+配音）的 Step 1，按 `asset_path` 是否已生成
去重、"处理到哪个大场景就顺带生成这个场景新出现的角色/地点"。这个
设计对**阶段3详细规划过程中临时补抽取出来的新角色/地点**仍然合理（见
下面"例外"），但对**阶段2一次性抽取出来、已经确定要贯穿全书的角色/
地点主表**来说不合适：定妆图是跨大场景复用、决定角色/场景外观一致性
的锚点资源，如果拖到"用到哪个大场景才生成"，会出现"大场景A已经在等
生成视频，才发现角色B的定妆图从没生成过"这类本可以提前发现的阻塞，
而且没有一个统一的检查点确认"全书角色/地点是不是都已经有定妆图了"。

现在改为：**阶段2（角色/地点抽取）产出完整的 `characters.json`/
`locations.json` 之后，先集中生成全部条目的定妆图并校验完成，确认
"一个不落"，再进入阶段4（大场景切分）**。这样阶段4-7 处理任何一个
大场景时，只要该场景引用的是阶段2已抽取出的角色/地点，其定妆图必然
已经就绪，不需要每次都重新确认。

## Step 1：为角色主表生成定妆图

遍历 `global/characters.json` 的 `characters[]`，对每一条调用：

```bash
AGNES_API_KEY="..." python .claude/skills/gen_image_with_text/gen_image.py \
  gen "<description_en>" --size 2K --ratio <novel_project.json 里的 aspect_ratio> \
  --save-path <output_dir>/global/assets/character_<id>.png
```

`description_en` 优先使用该角色的 `visual_anchor_en`（锁定视觉锚点，
比 `description_en` 更适合定妆图，因为它就是后续阶段6每条 `prompt_en`
用来保证外观不漂移的引用基准，定妆图和 prompt 引用同一份锚点描述能
让参考图与后续镜头描述天然对齐）。生成后**立即回填** `asset_path`。

## Step 2：为地点主表生成定妆图

同理遍历 `global/locations.json` 的 `locations[]`，落到
`global/assets/location_<id>.png`，`description_en` 同样优先用
`visual_anchor_en`，生成后回填 `asset_path`。

## Step 2.5：片头封面生成（仅 `cover.enabled == true` 时执行）

**前置条件**：Step 1/Step 2 全部完成（需要完整的角色/地点
`visual_anchor_en` 清单）。不影响本阶段"完成"判定的硬性检查（Step 3
的"全部生成完成"只看角色/地点两份表，不包含封面）——封面失败或
跳过都不阻塞进入阶段4。

封面分两步产出，**故意拆成两个独立文件**：`cover_bg.png`（AI生成的
纯背景画面，不含文字）+ `cover.png`（本地叠加标题文字后的成品）。
好处是以后只想换标题文字/布局时，不需要重新调用生图 API，只跑下面
Step B 就够了。

### Step A：生成剧情向背景图 `cover_bg.png`

这一步**不是**简单复用某个角色/地点的定妆图逻辑——封面要体现的是
"整本书讲了个什么故事"，不是某一帧画面：

1. 通读 `script.md`（此时已经结构化，比原文好读），提炼全书的整体
   基调、核心冲突、以及**一个最能代表全书的视觉意象**（例如：主角
   的剪影 + 核心地点、或主角 + 贯穿全书的关键道具/矛盾焦点），
   **不要**逐场景堆砌元素，也不要选某个具体情节场景的画面去代表
   全书；
2. 结合 `novel_project.json.art_style`（全书统一美术风格）和涉及到
   的角色 `visual_anchor_en`（保证封面上的人物长相和后面各场景一致，
   不会看起来像另一个人），写成一句英文 prompt；
3. **显式加入** `no text, no letters, no typography, no watermark`
   一类约束——文生图模型画中文字几乎必错，宁可完全不让它画字，标题
   全部交给 Step B 后期叠加；
4. 调用：
   ```bash
   AGNES_API_KEY="..." python .claude/skills/gen_image_with_text/gen_image.py \
     gen "<剧情向英文prompt>, no text, no letters, no typography" \
     --size 2K --ratio <novel_project.json 里的 aspect_ratio> \
     --save-path <output_dir>/global/assets/cover_bg.png
   ```
5. API 失败按 `error_handling.md` 常规重试规则处理；这一步失败**不
   拦截**进入阶段4——如实告知用户"封面背景图生成失败，稍后可用同一份
   `references/revision_and_rollback.md` §事后补建封面 流程单独重试"，
   继续往下走主流程即可。

### Step B：叠加标题文字，产出 `cover.png`

```bash
python .claude/skills/novel-video-studio/scripts/render_cover_title.py \
  <output_dir>/global/assets/cover_bg.png \
  <output_dir>/global/assets/cover.png \
  --title "<novel_project.json.cover.title_text 或 source_title>" \
  --layout <novel_project.json.cover.layout，默认 center>
```

脚本用本地 PIL 画字（不调用任何外部 API，免费且是秒级操作），自动
处理换行/字号/描边，三种 `layout`（`center`/`bottom_bar`/
`top_classic`）效果说明见 `references/01_entity_extraction.md` Step 0。
失败（通常是字体探测失败）按脚本打印的报错信息处理，不影响阶段4。

## Step 3：校验——全部生成完成才能继续

本阶段没有独立的 `check_*.py` 脚本（当前未实现，可后续按
`check_assets_and_audio_v2.py` 里角色/地点定妆图完整性那部分逻辑抽出
一个 `check_global_assets.py`），由 Agent 直接读取两份 JSON 逐条核对：

- `global/characters.json` 里 `characters[]` 的每一条，`asset_path`
  必须非空字符串，且指向的文件在磁盘上真实存在；
- `global/locations.json` 里 `locations[]` 的每一条，同上；
- 逐条核对完，向用户汇报"共 N 个角色 / M 个地点，全部生成完成"，
  如果有生成失败（API 报错重试后仍失败）的条目，**列出清单，不允许
  静默跳过**，需要处理完（重试成功，或经用户确认后接受占位/延后处理）
  才能进入阶段4。

**这是一个硬性检查点**：只要还有一条 `asset_path` 是空的，就不允许
推进到阶段4（大场景切分）。这一步不像阶段4-7那样按大场景循环，而是
和阶段2一样的全局一次性操作（见 SKILL.md §2.3 的例外说明）。

## 例外：阶段5详细规划中途补抽取出的新角色/地点

阶段5（单大场景详细规划）如果发现某个大场景引用了阶段2没抽取到的
角色/地点，走 SKILL.md §2.2 的"回补"流程：先用阶段2子资源
（`entity-extraction`）的单点补抽取模式把新实体并入
`characters.json`/`locations.json`，**再回到本文件描述的同一套生成
逻辑**，但只对这一两个新增条目生成定妆图（不需要、也不应该重新检查
一遍已经在本阶段生成过的全部旧条目）。这类"按需补生成"因为发生在
循环体内部、且只涉及个别新条目，实际操作步骤仍记录在阶段6
（`assets-and-audio`）文档里，本文件只覆盖阶段2之后、阶段4之前的
那次全局一次性生成。

外观变体（`appearance_variants`）的定妆图**不在本阶段生成**：变体是
否会被用到、用在哪些场景，要等阶段5详细规划时通过
`character_variant_overrides`/`location_variant_overrides` 才能确定，
提前生成属于"理论上可能用到就生成"，浪费且可能生成了从未用上的变体。
变体定妆图的按需生成逻辑保留在阶段6文档里。

## 下一步

全部角色/地点定妆图生成并校验通过后，进入阶段4（大场景切分）——
封面（`cover_bg.png`/`cover.png`）是否生成成功不阻塞这一步。

## 事后补建封面

项目已经跑完全部阶段、`video.mp4` 已存在之后，用户才决定要加封面
（或者想换标题文字/换背景图），**不需要**回到这里重跑一遍整个阶段3，
走独立的轻量流程即可，见
`references/revision_and_rollback.md` §事后补建封面。
