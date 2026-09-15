# 阶段3：全局角色/地点定妆图生成

**依赖 skill**：`gen_image_with_text`（必须）。
**外部依赖**：`AGNES_API_KEY`。API 失败处理见 `error_handling.md`。
**产物**：`global/assets/character_*.png` / `location_*.png`（+ 可选
`cover.png`），并回填 `global/characters.json` / `global/locations.json`
里对应的 `asset_path` 字段。

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

封面图（可选）逻辑相同，落到 `global/assets/cover.png`，供阶段8可选
叠加，不影响本阶段"完成"判定。

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

全部角色/地点定妆图生成并校验通过后，进入阶段4（大场景切分）。
