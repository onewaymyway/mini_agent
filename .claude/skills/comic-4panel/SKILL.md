---
name: comic-4panel
description: 四格漫画全流程生成技能，从主题构思到分镜脚本再到一次性生成完整四格漫画图（图文一体）。当用户说"画个四格漫画"、"生成四格漫画"、"四格漫画"时使用。
triggers: 四格漫画, 4koma, comic, 漫画生成, 画漫画, 四格, 4panel, comic strip, 4格
---

# 四格漫画生成 (4-Panel Comic Generator)

## 概述

本 skill 用于生成完整的四格漫画（2×2 网格布局）。流程包括：主题构思 → 分镜脚本 → 一次性生成完整漫画图（图文一体）→ 用户选择交付。

**核心理念**：对白文字直接由图片生成模型画入画面，无需后处理叠加。只需调用一次 `gen_image_with_text` 即可得到最终成品。

**依赖**：
- `gen_image_with_text` skill（必须，通过 bash 调用 `gen_image.py gen` 生成图片）
- `AGNES_API_KEY` 环境变量（必须）

## 输入规范

### 必需
- **theme**：漫画主题/关键词，如 "程序员日常"

### 可选
| 参数 | 默认值 | 说明 |
|---|---|---|
| style | `anime` | 画风：anime / manga-bw / chibi / realistic / watercolor / custom |
| style_suffix | `""` | 自定义风格后缀（style=custom 时使用） |
| characters | `""` | 角色描述 |
| source_work | `""` | **来源作品**（如"《Re:从零开始的异世界生活》"），直接影响模型对角色服装、发型、气质的理解，对生成效果至关重要 |
| output_dir | `./comic_output` | 输出目录 |
| filename | `comic_4panel_{timestamp}` | 输出文件名（不含扩展名） |
| language | `zh` | 对白语言：zh / ja / en |
| humor_type | `日常` | 幽默类型：日常 / 荒诞 / 吐槽 / 温馨 |
| num_candidates | `2` | 每次生成的候选图数量 |
| auto_mode | `false` | 快速模式，跳过中间确认 |
| prompt_lang | `zh` | prompt 语言：zh（优先中文）/ en（强制英文） |

## ⚠️ 产物文件强制保存规范（最高优先级）

**每个步骤完成后，必须立即将产物写入文件，不得仅停留在对话输出中。**

这是本 skill 的**铁律**：
1. **先生成内容，立刻写文件** — 不要等用户确认后再写，确认前就要写入
2. **文件写入是步骤完成的标志** — 没写入文件 = 步骤未完成，不能进入下一步
3. **每次重新生成都要覆盖/追加文件** — 确保产物文件始终是最新状态
4. **如果写入失败，必须报告错误并停止** — 不能跳过产物保存

**检查清单**：每完成一个步骤，在脑中过一遍："产物文件写了吗？" → 如果没写，立刻补上。

---

## 流程规范

### Step 1: 主题构思

将用户主题扩展为完整的漫画创作概念，输出格式：

```yaml
theme_concept:
  core_idea: "一句话核心创意"
  tone: "风格基调"
  setting: "场景设定"
  source_work: "来源作品（如《Re:从零开始的异世界生活》）"
  characters:
    - name: "角色名"
      visual_desc_zh: "中文视觉描述"
      visual_desc_en: "English visual description"
      personality: "性格特点"
  narrative_arc: "起：... → 承：... → 转：... → 合：..."
```

**要求**：
- 角色必须同时包含 `visual_desc_zh` 和 `visual_desc_en`
- 叙事弧线必须遵循起承转合结构
- **必须包含 `source_work` 字段**（来源作品），即使为空也要显式设置为空字符串
- 展示给用户确认后再进入 Step 2

**产物输出（强制）**：Step 1 完成后，**立即**写入 `output_dir/theme_concept.yaml`，然后才能展示给用户确认。

### Step 2: 分镜脚本

基于主题概念生成4格详细分镜，输出格式：

```yaml
panels:
  - id: 1
    title: "起 - 标题"
    scene_zh: "中文场景描述"
    scene_en: "English scene description"
    characters:
      - name: "角色名"
        expression_zh: "中文表情"
        expression_en: "English expression"
        action_zh: "中文动作"
        action_en: "English action"
        position: "画面位置"
    dialogue: "对白内容"（使用 language 指定的语言）
    narration: "旁白"（可选）
    composition_zh: "中文构图指示"
    composition_en: "English composition"
```

**要求**：
- 每格必须包含中英文版本的场景、表情、动作、构图
- 对白简短有力，符合角色性格
- 对白长度控制在 10-20 字以内（方便模型画入画面）
- 旁白控制在 5-10 字以内
- 展示给用户确认后再进入 Step 3

**产物输出**：Step 2 完成后，立即写入 `output_dir/storyboard.yaml`

### Step 3: 生成漫画（图文一体）

#### Step 3a: 构建并保存 Prompt

基于 `storyboard.yaml` 中的分镜数据和 `theme_concept.yaml` 中的 `source_work`，按照以下模板构建完整 prompt，**先保存到文件，再展示给用户确认**。

**核心原则**：
- **prompt 开头先放角色设定信息**（来源作品 + 角色外观描述），确保模型理解角色身份
- 每格用**完整段落**描述，不再用占位符拼接
- 每格包含：画面场景 + 角色状态 + 屏幕/道具内容 + 对白气泡文字
- 屏幕上的文字（代码、报错信息等）也要在 prompt 中写明，让模型画入画面
- **对话框必须明确指明是哪个角色的**，格式为：`[角色名]的对话框文字："[对白内容]"`，这样模型能准确将文字画在对应角色的气泡中

**中文 prompt 模板**（prompt_lang=zh，默认）：

```
【角色设定】
来自《[来源作品]》的[角色名]，[服装描述]，[外貌特征描述]。

一幅2×2网格布局的四格漫画，格间有白色边框，所有格角色设计一致。

【第1格 · 左上】
[场景描述]。[角色名]穿着[服装]，[表情描述]，[动作描述]。[屏幕/道具内容描述，含文字]。[角色名]的对话框文字："[对白]"。

【第2格 · 右上】
[场景描述]。[角色名]穿着[服装]，[表情描述]，[动作描述]。[屏幕/道具内容描述，含文字]。[角色名]的对话框文字："[对白]"。

【第3格 · 左下】
[场景描述]。[角色名]穿着[服装]，[表情描述]，[动作描述]。[屏幕/道具内容描述，含文字]。[角色名]的对话框文字："[对白]"。

【第4格 · 右下】
[场景描述]。[角色名]穿着[服装]，[表情描述]，[动作描述]。[屏幕/道具内容描述，含文字]。[角色名]的对话框文字："[对白]"。

[风格后缀]
```

**英文 prompt 模板**（prompt_lang=en）：

```
[CHARACTER SETTING]
[Character Name] from "[SOURCE_WORK]", wearing [outfit], [appearance description].

A 4-panel comic strip in 2x2 grid layout with clean white borders between panels, consistent character design across all panels.

[Panel 1 · Top-left]
[Scene description]. [Character Name] wearing [outfit], [expression], [action]. [Screen/prop description with text]. [Character Name]'s speech bubble text: "[DIALOGUE]".

[Panel 2 · Top-right]
[Scene description]. [Character Name] wearing [outfit], [expression], [action]. [Screen/prop description with text]. [Character Name]'s speech bubble text: "[DIALOGUE]".

[Panel 3 · Bottom-left]
[Scene description]. [Character Name] wearing [outfit], [expression], [action]. [Screen/prop description with text]. [Character Name]'s speech bubble text: "[DIALOGUE]".

[Panel 4 · Bottom-right]
[Scene description]. [Character Name] wearing [outfit], [expression], [action]. [Screen/prop description with text]. [Character Name]'s speech bubble text: "[DIALOGUE]".

[STYLE_SUFFIX]
```

**实际示例**（基于程序员笑话 × 异世界女仆 × 太空船脚本，来源作品：《Re:从零开始的异世界生活》）：

```
【角色设定】
来自《Re:从零开始的异世界生活》的蕾姆，蓝白色女仆装，蓝色长发，头发遮住右眼，认真负责的性格。

一幅2×2网格布局的四格漫画，格间有白色边框，所有格角色设计一致。

【第1格 · 左上】
未来太空飞船的控制室，红色警报灯疯狂闪烁，多个屏幕显示错误信息。蕾姆穿着蓝白色女仆装，蓝色长发，一脸认真地盯着控制台屏幕。屏幕上显示红色警告文字："Critical Error: Life Support System Failure!"。蕾姆的对话框文字："又崩了……是哪个魔法回路写的代码？"

【第2格 · 右上】
蕾姆站在控制台前，打开系统日志界面。屏幕上满是乱码和程序员注释，可见文字："// TODO: fix this later (100年前的程序员写的)"、"// it works, don't touch it"、"// I have no idea why this works"。蕾姆面无表情，眼神死寂。蕾姆的对话框文字："……这是'祖传代码'吗？"

【第3格 · 左下】
蕾姆坐在控制台前认真敲代码，女仆围裙飘起，身后有冰系魔法的光效。屏幕上显示代码："# remove legacy curse"、"system.reboot(force=True)"。飞船剧烈抖动，背景有震动线条。蕾姆的对话框文字："冰系魔法（注释掉旧代码）发动！"

【第4格 · 右下】
太空船控制室灯光全部变成粉色，背景音乐自动播放的氛围。屏幕显示："System stable. Personality module activated: Maid Mode."。蕾姆愣在原地，一脸无语。蕾姆的对话框文字："……我只是想修bug，不是重写世界观啊。"

动漫风格，四格漫画，所有格角色设计一致，漫画画风，格间白色边框，鲜艳色彩，高质量，清晰的文字
```

**产物输出**：构建完成后，立即写入 `output_dir/prompt_used.txt`

#### Step 3b: 用户确认 Prompt

展示 `prompt_used.txt` 的内容给用户，询问是否满意。用户可以说：
- "可以" / "生成" → 进入 Step 3c
- "修改" → 回到 Step 2 调整分镜，或直接在 Step 3a 调整 prompt

#### Step 3c: 调用模型生成

用户确认后，读取 `prompt_used.txt` 的内容，调用图片生成模型。

#### 风格后缀表

| style | 中文后缀 | 英文后缀 |
|---|---|---|
| anime | 动漫风格，四格漫画，所有格角色设计一致，漫画画风，格间白色边框，鲜艳色彩，高质量，清晰的文字 | anime style, 4koma comic strip, consistent character design across all panels, manga art style, clean white borders between panels, vibrant colors, high quality, clear text |
| manga-bw | 黑白漫画风格，四格漫画，所有格角色设计一致，墨线画，网点纸，格间白色边框，高质量，清晰的文字 | black and white manga style, 4koma comic strip, consistent character design across all panels, ink pen drawing, screentone, clean white borders between panels, high quality, clear text |
| chibi | Q版风格，四格漫画，超变形，可爱，大头小身，所有格角色设计一致，格间白色边框，高质量，清晰的文字 | chibi style, 4koma comic strip, super deformed, cute, big head small body, consistent character design across all panels, clean white borders between panels, high quality, clear text |
| realistic | 半写实动漫风格，四格漫画，所有格角色设计一致，精细阴影，格间白色边框，高质量，清晰的文字 | semi-realistic anime style, 4koma comic strip, consistent character design across all panels, detailed shading, clean white borders between panels, high quality, clear text |
| watercolor | 水彩插画风格，四格漫画，所有格角色设计一致，柔和色彩，颜料质感，格间白色边框，高质量，清晰的文字 | watercolor illustration style, 4koma comic strip, consistent character design across all panels, soft colors, paint texture, clean white borders between panels, high quality, clear text |
| custom | 用户通过 style_suffix 参数提供 | 用户通过 style_suffix 参数提供 |

#### 多候选生成

- 默认生成 `num_candidates=2` 张候选图
- 每张使用相同 prompt，利用模型随机性产生不同结果
- 候选图命名：`{filename}_v1.png`, `{filename}_v2.png`, ...
- 串行生成，失败重试1次

#### 调用方式

**使用 prompt 文件模式**（推荐，避免命令行参数过长）：

```bash
python .claude/skills/gen_image_with_text/gen_image.py gen --prompt-file <output_dir>/prompt_used.txt --size 1024x1024 --save-path <output_dir>/<filename>_v{i}.png
```

> 说明：`--prompt-file` 参数读取 `prompt_used.txt` 中的完整 prompt 内容，无需在命令行中转义引号或换行符。

**产物输出**：每张图生成后，追加写入 `output_dir/generation_log.json`

### Step 4: 用户选择与交付

展示所有候选图路径，让用户选择：

```
🎬 四格漫画生成完成！共 N 张候选图：
  📷 候选1: <path>_v1.png
  📷 候选2: <path>_v2.png

请选择满意的版本（输入编号），或说"重新生成"获取更多候选图。
```

**重新生成**：用户说"重新生成"、"再来几张"、"更多"时，生成新候选图，编号递增（_v3, _v4...），旧候选保留。

**产物输出**：全部完成后，生成 `output_dir/README.md` 交付说明

## 产物文件规范

每个流程完成后，必须在 `output_dir` 下输出对应的产物文件，便于用户查看、追溯和重新生成。

### 产物文件清单

| 流程 | 产物文件 | 格式 | 说明 |
|---|---|---|---|
| Step 1 主题构思 | `theme_concept.yaml` | YAML | 主题概念完整数据 |
| Step 2 分镜脚本 | `storyboard.yaml` | YAML | 四格分镜详细脚本 |
| Step 3 漫画生成 | `prompt_used.txt` | TXT | 实际用于调用图片生成模型的完整 prompt 内容 |
| Step 3 漫画生成 | `{filename}_v{i}.png` | PNG | 图文一体候选图（含对白文字） |
| Step 3 漫画生成 | `generation_log.json` | JSON | 生成日志（含 prompt、参数、时间） |
| 全部完成 | `README.md` | Markdown | 最终交付说明，汇总所有产物 |

### 产物文件内容规范

#### theme_concept.yaml

```yaml
# 主题构思产物
generated_at: "2026-06-19T10:30:00+08:00"
input_theme: "程序员日常"
source_work: "《Re:从零开始的异世界生活》"
theme_concept:
  core_idea: "一句话核心创意"
  tone: "风格基调"
  setting: "场景设定"
  characters:
    - name: "角色名"
      visual_desc_zh: "中文视觉描述"
      visual_desc_en: "English visual description"
      personality: "性格特点"
  narrative_arc: "起：... → 承：... → 转：... → 合：..."
```

#### storyboard.yaml

```yaml
# 分镜脚本产物
generated_at: "2026-06-19T10:35:00+08:00"
theme_concept_ref: "theme_concept.yaml"
panels:
  - id: 1
    title: "起 - 标题"
    scene_zh: "中文场景描述"
    scene_en: "English scene description"
    characters:
      - name: "角色名"
        expression_zh: "中文表情"
        expression_en: "English expression"
        action_zh: "中文动作"
        action_en: "English action"
        position: "画面位置"
    dialogue: "对白内容"
    narration: "旁白"
    composition_zh: "中文构图指示"
    composition_en: "English composition"
  # ... 共4格
```

#### generation_log.json

```json
{
  "generated_at": "2026-06-19T10:40:00+08:00",
  "style": "anime",
  "language": "zh",
  "prompt_used": "一幅2×2网格布局的四格漫画...",
  "candidates": [
    {
      "version": 1,
      "file": "comic_4panel_v1.png",
      "prompt_lang": "zh",
      "size": "1024x1024",
      "generated_at": "2026-06-19T10:40:30+08:00"
    }
  ]
}
```

#### README.md（最终交付）

```markdown
# 四格漫画交付

- **主题**：{input_theme}
- **来源作品**：{source_work}
- **风格**：{style}
- **生成时间**：{generated_at}

## 产物清单

| 文件 | 说明 |
|---|---|
| `theme_concept.yaml` | 主题构思 |
| `storyboard.yaml` | 分镜脚本 |
| `{filename}_v1.png` | 候选图1（图文一体） |
| `generation_log.json` | 生成日志 |

## 使用说明

- 查看分镜脚本：打开 `storyboard.yaml`
- 重新生成：告知 AI 需要调整的主题或分镜
```

### 产物输出时机

1. **Step 1 完成后**：立即写入 `theme_concept.yaml`
2. **Step 2 完成后**：立即写入 `storyboard.yaml`
3. **Step 3 每张图生成后**：追加写入 `generation_log.json`
4. **全部完成后**：生成 `README.md` 交付说明

## 用户交互规范

### 分步确认模式（默认）
- Step 1 完成后暂停，等用户确认主题构思
- Step 2 完成后暂停，等用户确认分镜脚本
- Step 3-4 连续执行，展示候选图供选择

### 快速模式
- 用户说"直接生成"、"不用确认"、auto_mode=true 时启用
- 跳过 Step 1/2 的确认，直接生成

### 重新生成
- 用户说"重新生成"、"再来几张"、"更多"时，生成新候选图
- 编号递增，旧候选保留
- 可指定重新生成某一步（如"换个分镜"回到 Step 2）

## 常见问题与陷阱

1. **模型不理解2×2布局**：prompt 中必须明确声明网格布局 + 每格标注位置；多候选生成提高成功率
2. **角色跨格不一致**：prompt 开头统一角色设定 + 风格后缀中强调 `所有格角色设计一致` / `consistent character design across all panels`
3. **来源作品缺失导致角色形象偏差**：务必在 theme_concept.yaml 中填写 `source_work`，prompt 开头会引用此信息
4. **对话框未指明角色导致文字错位**：prompt 中必须使用 `[角色名]的对话框文字："[对白]"` 格式，明确指定每个气泡属于哪个角色
5. **文字渲染效果差**：模型对文字渲染能力有限，对白尽量简短（10字以内）；支持 prompt_lang=en 切换英文 prompt
6. **API 限流**：串行调用 + 失败重试1次
7. **Pillow 不再需要**：文字由模型直接生成，无需后处理叠加
