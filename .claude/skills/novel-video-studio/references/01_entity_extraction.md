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
3. 每个角色新增/更新时判断/补充 `voice_profile`（音色描述：性别/年龄段
   /音色特点，如"青年女声，清亮活泼"，结合年龄性格身份推断，没有足够
   信息时给一个合理默认描述而不是留空）；
4. **立即把合并后的完整 `characters.json`/`locations.json` 覆盖写回
   磁盘**（不是追加），保证任何时刻磁盘都是最新完整状态，中途中断不
   丢已处理部分。

只收录**会被可视化到画面里**的角色（有具体外貌/动作描写）；地点只收录
**反复出现（至少 2 次）** 的。

`characters.json` 条目字段：
```json
{"id": "char_01", "names": ["林然", "小林"],
 "description_zh": "...", "description_en": "...(含 art_style)",
 "voice_profile": "青年男声，清朗略带江湖气",
 "first_appear": "第1章 第2段", "relations": ["char_02:挚友"],
 "asset_path": null, "face_reference_id": null}
```

`locations.json` 条目字段：
```json
{"id": "loc_01", "name": "青石客栈",
 "description_zh": "...", "description_en": "...(含 art_style)",
 "asset_path": null}
```

### Step 3：校验

```bash
python .claude/skills/novel-video-studio/scripts/check_entities.py <output_dir>
```

不通过（重复 id、角色缺 `voice_profile`）→ 回 Step 2 修复对应条目，
重新跑校验，直到通过才能进入阶段2。

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
`description_en` 非空；每个地点 `description_zh`/`description_en` 非空；
同名未合并的启发式提示（仅警告不阻断）。退出码非 0 时不允许交付下游。

**向用户展示**：识别出的角色/地点清单（数量+简要身份+声音设定一句话）、
校验通过结果。

## 已知限制

- 分块大小是经验值，超长章节实体密度很高时仍可能需要进一步细分；
- `voice_profile` 只是文字描述，具体映射到 TTS 音色由阶段4负责；
- 人脸参考照片机制未实现，`face_reference_id` 先占位。
