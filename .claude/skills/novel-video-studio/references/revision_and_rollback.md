# 用户反馈修改内容：联动回退与重新生成

用户在任何阶段之后（哪怕已经拿到最终 `video.mp4`）都可能提出修改意见。
本 skill 要求：**先改源头内容 → 跑 `invalidate.py` 联动清空所有依赖它
的下游产物和状态 → 从被清空的那一层，按阶段顺序重新往后走**。绝不允许
只改了源头文件就认为"改完了"而不清理下游——那样磁盘上会出现"新内容 +
旧衍生物"并存的不一致状态，下一次 `check_project_state.py`/校验脚本
也会因此报出状态不一致的告警。

## Step 0：先判断这条反馈对应流程里的哪一层

| 用户反馈类型 | 对应回退 level | 说明 |
|---|---|---|
| "这段剧情切分不对，应该在这里断开/这两段大场景该合并" | `--level macro` | 影响大场景边界本身，`scene_detail.yaml` 作废重写 |
| "这句台词/旁白文案要改"、"这个小场景引用的角色不对"、"镜头描述要调整" | `--level detail` | 保留大场景切分，但该 micro_scene 的音频/视频都要用新内容重新生成 |
| "配音音色不对"、"这段配音语气不满意，文案没问题" | `--level assets` | 只清音频+视频 clip，保留 scene_detail 文案 |
| "画面不满意，配音没问题，重新生成一下这个镜头" | `--level video` | 只清视频 clip（和依赖它的大场景合成结果），音频保留 |
| "这个角色的形象/声音设定要改" | 用 `--global-entity <角色id>`，不用 `--macro-id` | 联动回退所有引用该角色的大场景（assets 级） |
| "想加个片头封面"、"封面标题文字/布局要改"、"封面背景图要换" | 不走本表，不需要 `invalidate.py` | 封面只影响阶段8最终拼接，不影响任何大场景内部产物，见文末 §事后补建封面 |

## Step 1：修改源头内容

按反馈类型手动编辑对应文件：
- 大场景边界/原文范围 → 编辑 `macro_scenes.yaml`；
- 小场景文案/引用/镜头描述 → 编辑对应 `macro_scene_XX/scene_detail.yaml`；
- 角色/地点设定（外形描述、`voice_profile`）→ 编辑
  `global/characters.json`/`locations.json`。

**硬规则：修改 `scene_detail.yaml` 里已有的 micro_scene 必须原地编辑，
禁止删除旧条目、再把改完的内容当成新场景追加到 `micro_scenes` 列表
末尾**（不管是拿全局最大 id+1，还是在原 id 后加后缀）。`micro_scenes`
列表的书写顺序就是真实叙事顺序，`compose_macro_scene.py` 按列表顺序
（不是 id 数字顺序）合成——"删除+追加到末尾"不会让现有脚本立刻报错，
但会让列表顺序和 id 数字顺序脱节，掩盖"这个大场景被大幅重写过"这个
事实，且一旦追加时插错位置，现有校验完全发现不了。

如果确实需要把一个 micro_scene 拆成两个（比如原来一条内容太长要拆成
两条），新拆出来的那一条允许使用 `<原id>b` 这种后缀 id，但**必须插入
在紧邻原条目之后的列表位置上**，不能追加到全局末尾——列表顺序永远要
和叙事顺序保持一致。

编辑之后**不要直接去跑生成脚本**，先执行 Step 2，再跑
`check_scene_detail.py` 确认列表顺序（脚本会给出"列表顺序 vs id 数字
顺序"不一致的 warning，见该脚本说明）。

## Step 2：跑 `invalidate.py` 联动清理

```bash
# 大场景切分本身要调整
python .claude/skills/novel-video-studio/scripts/invalidate.py \
  <output_dir> --macro-id macro_02 --level macro

# 改了某个小场景的文案/引用（保留切分结构）
python .claude/skills/novel-video-studio/scripts/invalidate.py \
  <output_dir> --macro-id macro_02 --level detail

# 只改配音效果，文案不变
python .claude/skills/novel-video-studio/scripts/invalidate.py \
  <output_dir> --macro-id macro_02 --level assets

# 只对某几个小场景的画面不满意（配音不变）
python .claude/skills/novel-video-studio/scripts/invalidate.py \
  <output_dir> --macro-id macro_02 --micro-id micro_05 micro_06 --level video

# 角色设定变了（形象/声音），联动回退所有引用它的大场景
python .claude/skills/novel-video-studio/scripts/invalidate.py \
  <output_dir> --global-entity char_03
```

脚本会：
- 按 level 清空对应的音频/clip/大场景合成视频文件；
- 把 `macro_scenes.yaml`/`scene_detail.yaml` 里的状态回退到对应阶段
  （`level=macro` 回到 `pending`，其余回到 `planned` 且相关
  `micro_scene.status` 回到 `pending`）；
- 只要有大场景被拉低于 `done`，顺带删除已存在的旧 `video.mp4`（避免
  用户误把过时成片当最终结果）；
- stdout 输出 `affected_macro_scenes`/`removed_files`/`next_step`，
  照着 `next_step` 提示继续。

## Step 3：从被清空的那一层重新往后走

- `--level macro` → 回 `references/04_scene_detail_planning.md` 重新
  规划该大场景，然后正常走完阶段6/7；
- `--level detail`/`assets` → 直接进入 `references/05_assets_and_audio.md`，
  用 `--macro-id` 只重新配音该大场景，再走阶段7；
- `--level video` → 直接进入 `references/06_scene_video_generation.md`，
  用 `--macro-id --micro-id` 只重新生成对应小场景视频，再重新跑
  `compose_macro_scene.py` 合成该大场景；
- `--global-entity` → 先确认新的定妆图/配音已生成（阶段6，或全新
  角色/地点属于阶段3范围），受影响的每个大场景都按 `assets` 级别
  走完阶段6/7。

全部处理完后，跑一次 `check_project_state.py` 确认：受影响的大场景
`status` 已经重新变回 `done`，其它未受影响的大场景状态没有被误改。
最后**必须重新跑一次阶段8**（`compose_final_video_v2.py`）产出新的
`video.mp4`——旧的已经在 Step 2 被删除，不会有人误用旧成片。

## 一个例子

用户说"macro_02 里角色 char_03 的第二句台词其实原文不是这么说的，
改一下"：

1. 打开 `macro_scene_02/scene_detail.yaml`，找到对应 `dialogue`
   block，改成从 `macro_scenes.yaml` 里 `macro_02.raw_text` 摘录的
   正确原文；
2. 跑 `invalidate.py <output_dir> --macro-id macro_02 --level detail`；
3. 跑 `check_scene_detail.py <output_dir> macro_02` 确认改过的对话
   通过子串校验；
4. 跑阶段6的 `synthesize_scene_audio.py --macro-id macro_02` 重新配音；
5. 跑阶段7的 `generate_scene_videos_v2.py --macro-id macro_02` +
   `compose_macro_scene.py macro_02` 重新生成视频+合成；
6. 跑阶段8的 `compose_final_video_v2.py` 产出新的 `video.mp4`。

不需要动其它任何大场景。

## 事后补建封面

封面（`global/assets/cover_bg.png`/`cover.png`）是阶段8最终拼接时
**新增在最前面**的一段独立片段，不改变、不依赖任何大场景内部的
文案/配音/画面产物。所以下面三类反馈**都不走上面 Step 0 的表、不需要
跑 `invalidate.py`**，哪怕这时 `video.mp4` 已经生成完毕：

1. **项目当初没开封面，现在想加**（最常见的场景，包括
   `video.mp4` 已经交付之后才提出）；
2. **封面已经生成过，只是想换标题文字/换布局**；
3. **封面已经生成过，想换一张背景图**（标题文字不变或跟着换）。

统一走这一套轻量流程：

```
1. 检查/更新 novel_project.json.cover：
   - 之前没有 cover 字段，或 enabled=false → 向用户确认
     title_text/duration_sec/layout（默认 title_text=null 用
     source_title，layout=center），写入 enabled=true；
   - 只改标题文字/布局 → 直接改对应字段，enabled 保持 true；
   - 只换背景图，标题不变 → cover 字段不用改。

2. 按需生成/更新素材（跳过不需要重新生成的那一步）：
   - global/assets/cover_bg.png 不存在，或用户要求换背景图
     → 走 references/02_global_assets.md Step A 重新生成
       cover_bg.png（这一步会调用生图 API，其它素材/大场景完全
       不受影响，characters.json/locations.json/script.md 都还在，
       随时可以补跑）；
   - global/assets/cover.png 不存在，或 cover_bg.png 刚被重新生成，
     或 title_text/layout 有变化
     → 跑 references/02_global_assets.md Step B
       （scripts/render_cover_title.py），本地操作，秒级完成，
       不调用任何外部 API；
   - 只改标题文字/布局、cover_bg.png 没变 → 只需要跑上面这个 Step B，
     不需要碰 Step A。

3. 直接重跑阶段8：
   python .claude/skills/novel-video-studio/scripts/compose_final_video_v2.py \
     <output_dir>
   macro_scenes.yaml 里各大场景仍是 done、macro_scene_XX.mp4 都还在
   磁盘上，脚本本身就是"读现成大场景视频 + 当前封面配置 → 重新合成"，
   天然支持覆盖重跑，不需要、也不应该对任何大场景状态做 invalidate。

4. 覆盖旧 video.mp4 前提醒用户一句：如果想保留旧版本对比，生成前
   先把当前 video.mp4 另存一份；否则直接覆盖生成新版本，新总时长
   应约等于"旧总时长 + 封面时长"（如果是新增封面）或与旧总时长一致
   （如果只是换标题/背景图，封面时长不变）。
```

这条路径不清空任何 `macro_scene_XX` 的状态，`check_project_state.py`
里各大场景 `status` 全程保持 `done` 不受影响。
