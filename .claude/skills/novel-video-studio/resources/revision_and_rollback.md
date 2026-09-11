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

## Step 1：修改源头内容

按反馈类型手动编辑对应文件：
- 大场景边界/原文范围 → 编辑 `macro_scenes.yaml`；
- 小场景文案/引用/镜头描述 → 编辑对应 `macro_scene_XX/scene_detail.yaml`；
- 角色/地点设定（外形描述、`voice_profile`）→ 编辑
  `global/characters.json`/`locations.json`。

编辑之后**不要直接去跑生成脚本**，先执行 Step 2。

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

- `--level macro` → 回 `resources/03_scene_detail_planning.md` 重新
  规划该大场景，然后正常走完阶段4/5；
- `--level detail`/`assets` → 直接进入 `resources/04_assets_and_audio.md`，
  用 `--macro-id` 只重新配音该大场景，再走阶段5；
- `--level video` → 直接进入 `resources/05_scene_video_generation.md`，
  用 `--macro-id --micro-id` 只重新生成对应小场景视频，再重新跑
  `compose_macro_scene.py` 合成该大场景；
- `--global-entity` → 先确认新的定妆图/配音已生成（阶段4），受影响的
  每个大场景都按 `assets` 级别走完阶段4/5。

全部处理完后，跑一次 `check_project_state.py` 确认：受影响的大场景
`status` 已经重新变回 `done`，其它未受影响的大场景状态没有被误改。
最后**必须重新跑一次阶段6**（`compose_final_video_v2.py`）产出新的
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
4. 跑阶段4的 `synthesize_scene_audio.py --macro-id macro_02` 重新配音；
5. 跑阶段5的 `generate_scene_videos_v2.py --macro-id macro_02` +
   `compose_macro_scene.py macro_02` 重新生成视频+合成；
6. 跑阶段6的 `compose_final_video_v2.py` 产出新的 `video.mp4`。

不需要动其它任何大场景。
