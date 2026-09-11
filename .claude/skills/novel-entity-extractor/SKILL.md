---
name: novel-entity-extractor
description: 小说转视频 v2 流程的第一步。通读小说全文（支持长篇，自动分块），抽取全局角色（含声音设定 voice_profile）和反复出现的地点，写入 novel_output/<项目>/global/ 供后续 skill（novel-macro-scene-planner 及之后各步骤）使用。也支持"单点补抽取"模式，供 novel-scene-detail-planner 发现遗漏角色/地点时调用。当用户说"把这本小说做成视频"、"小说转视频"时，从本 skill 开始（取代 v1 的 novel-scene-planner）。
triggers: 小说转视频, 小说做视频, 角色提取, 全局角色库, voice_profile, novel entity extractor
---

# 小说全局角色/地点抽取 (Novel Entity Extractor)

## 概述

本 skill 是"小说转视频 v2"六段流程的第一步：

```
novel-entity-extractor（本 skill）
  → novel-macro-scene-planner
  → novel-scene-detail-planner
  → novel-asset-generator
  → novel-scene-video-generator
  → novel-video-composer
```

方案文档：`next_doc/novel_video_generator_plan_v2.md`（完整架构、目录结构、
文件契约，本 skill 只做其中第 2 节的工作）。

与 v1 `novel-scene-planner` 的区别：v1 把"抽取角色/地点"和"切分旁白
段落"揉在一个 skill 里，且只支持一次处理一段文本；本 skill **只做抽取**，
**支持长篇小说分块通读 + 增量合并**，且新增角色 `voice_profile`（声音
设定）字段，为后续 `novel-asset-generator` 的差异化配音做准备。

**依赖**：无外部 API/模型依赖，纯 Agent 推理 + 文件读写。

**依赖脚本**：`scripts/check_entities.py`——校验 `global/characters.json`/
`global/locations.json` 的 id 唯一性、字段完整性（含 `voice_profile` 非空），
每次写完必须跑，不通过不能交付给下游 skill。

## ⚠️ 产物文件强制保存规范（与其余 5 个 skill 一致）

1. 先生成内容，立刻写文件，不要等用户确认；
2. 文件写入是步骤完成的标志；
3. 长篇分块时，**每处理完一块就立即合并写盘一次**，不要攒到全文处理完
   才写——中途中断也不丢已处理部分的结果；
4. 写入失败必须报告错误并停止，不能跳过。

产物路径（`output_dir` 默认为 `./novel_output/{小说名}_{timestamp}`）：

```
novel_output/小说名_20260911/
├── novel_project.json        # 全局配置，最先写入（同 v1 Step 0）
└── global/
    ├── characters.json       # 本 skill 产物
    └── locations.json        # 本 skill 产物
```

## 两种运行模式

### 模式 A：全文抽取（首次运行，新建项目时用）

#### Step 0：配置（同 v1 Step 0，一次性问清楚，不阻塞）

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
  "orientation": "landscape",
  "aspect_ratio": "16:9"
}
```

`transition_mode` 新增字段（v2），可选 `cut`（默认，硬切）/`fade`（大场景
间黑场淡入淡出），本 skill 阶段先按默认值写入，用户明确要转场效果时
再改成 `fade`（也可以在后面的 `novel-video-composer` 阶段再问一次）。

#### Step 1：确定全曲统一美术风格

同 v1 Step 1，结合题材/情绪基调/时代背景提炼一段英文风格描述，回填
`novel_project.json.art_style`。

#### Step 2：分块通读 + 抽取 + 增量合并

**长篇处理规则**：全文按章节（优先）或固定字数（无明显章节时，约
3000-5000 字一块）切分成若干块，**依次**处理，不要求一次性把全文塞进
上下文：

对每一块：
1. 通读本块，识别人物和地点；
2. **同当前 `global/characters.json`/`global/locations.json` 已有条目做
   合并去重**（同名/别名归并到同一 id；本块新出现的、库里没有的才新建
   id，命名规则 `char_NN`/`loc_NN` 按当前库里最大编号递增，不要从头
   重新编号，避免下游已引用的 id 失效）；
3. 每个角色新增/更新时，同时判断/补充 `voice_profile`（音色描述，覆盖
   性别、年龄段、音色特点，如"青年女声，清亮活泼"/"老者音，苍劲沙哑"，
   结合人物的年龄、性格、身份推断，没有足够信息时给一个合理的默认
   描述而不是留空）；
4. 立即把合并后的完整 `characters.json`/`locations.json` 写回磁盘（覆盖
   写，不是追加，保证任何时刻磁盘上都是最新完整状态）。

只收录**会被可视化到画面里**的角色（有具体外貌/动作描写）；地点只收录
**反复出现（至少 2 次）** 的。收录标准与 v1 一致。

`characters.json`：
```json
{
  "characters": [
    {"id": "char_01", "names": ["林然", "小林"],
     "description_zh": "...", "description_en": "...(含 art_style)",
     "voice_profile": "青年男声，清朗略带江湖气",
     "first_appear": "第1章 第2段", "relations": ["char_02:挚友"],
     "asset_path": null, "face_reference_id": null}
  ]
}
```

`locations.json`：
```json
{
  "locations": [
    {"id": "loc_01", "name": "青石客栈",
     "description_zh": "...", "description_en": "...(含 art_style)",
     "asset_path": null}
  ]
}
```

#### Step 3：校验

全文处理完后跑：

```bash
python .claude/skills/novel-entity-extractor/scripts/check_entities.py \
  <output_dir>
```

不通过（有重复 id、有角色缺 `voice_profile`）则回 Step 2 修复对应条目，
重新跑校验，直到通过才能交付给下游 `novel-macro-scene-planner`。

### 模式 B：单点补抽取（供 novel-scene-detail-planner 回调）

`novel-scene-detail-planner` 在规划某个大场景时，如果发现引用了
`global/characters.json`/`global/locations.json` 里不存在的角色/地点，
会以"补抽取"方式调用本 skill：

- 输入：**只给该大场景的 `raw_text` 片段**（不是全文），以及提示"这段
  文字里提到了但全局库没有的角色/地点是什么"；
- 流程：只对这一小段文本做 Step 2 同样的抽取+合并+写回逻辑（合并对象
  仍是完整的 `global/characters.json`/`global/locations.json`，只是本次
  处理范围缩小到一个片段）；
- 完成后同样跑 `check_entities.py` 校验，通过后把新增的角色/地点 id
  返回给调用方（`novel-scene-detail-planner` 会拿这些新 id 去触发
  `novel-asset-generator` 补生成对应素材）。

这个模式**不需要重新跑全文**，只处理指定片段，保证长篇小说的增量维护
成本可控。

## 校验脚本说明 `check_entities.py`

检查：
1. `characters.json`/`locations.json` 内部 id 无重复；
2. 每个角色是否都有非空 `voice_profile`、`description_zh`、
   `description_en`；
3. 每个地点是否都有非空 `description_zh`、`description_en`；
4. 简单的同名未合并启发式检查（`names` 列表间若有重叠或高度相似字符串，
   提示可能需要人工核对是否漏合并）——只是警告（`warnings`），不阻断。

退出码非 0（`ok: false`）时不允许交付给下游 skill。

**Agent 需向用户展示的中间内容**：识别出的角色/地点清单（数量+简要
身份+声音设定一句话）、校验通过结果。

**产物**：`novel_project.json`（含 `art_style`/`transition_mode` 等配置）、
`global/characters.json`、`global/locations.json`（全部强制落盘，校验
通过）。

## 已知限制

- 分块大小（章节 or 3000-5000 字）是经验值，超长章节内部如果实体密度
  很高，仍可能需要 Agent 结合上下文进一步细分，脚本不做强制分块；
- `voice_profile` 目前只是文字描述，具体映射到 CosyVoice 参考音频 /
  edge-tts 内置音色名，由 `novel-asset-generator` 负责，本 skill 不关心
  底层 TTS 引擎细节；
- 人脸参考照片机制同 v1，仍未实现，`face_reference_id` 先占位。

## 下一步

完成本 skill 后，进入 `novel-macro-scene-planner`（按剧情把全文切成
大场景），输入即为本 skill 产出的 `<output_dir>` 整个目录（含
`novel_project.json` 和 `global/`）。
