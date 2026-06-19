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
| output_dir | `./comic_output` | 输出目录 |
| filename | `comic_4panel_{timestamp}` | 输出文件名（不含扩展名） |
| language | `zh` | 对白语言：zh / ja / en |
| humor_type | `日常` | 幽默类型：日常 / 荒诞 / 吐槽 / 温馨 |
| num_candidates | `2` | 每次生成的候选图数量 |
| auto_mode | `false` | 快速模式，跳过中间确认 |
| prompt_lang | `zh` | prompt 语言：zh（优先中文）/ en（强制英文） |

## 流程规范

### Step 1: 主题构思

将用户主题扩展为完整的漫画创作概念，输出格式：

```yaml
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

**要求**：
- 角色必须同时包含 `visual_desc_zh` 和 `visual_desc_en`
- 叙事弧线必须遵循起承转合结构
- 展示给用户确认后再进入 Step 2

**产物输出**：Step 1 完成后，立即写入 `output_dir/theme_concept.yaml`

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

#### Prompt 构建规则

**核心原则**：在 prompt 中明确告诉模型每格的对白文字和旁白文字，让模型直接将文字画入画面。

**中文 prompt 模板**（prompt_lang=zh，默认）：

```
一幅2×2网格布局的四格漫画，格间有白色边框。

第1格（左上）：[角色描述块]，[表情]，[动作]，在[场景]。[构图]。对话框文字："[对白]"。旁白文字："[旁白]"。
第2格（右上）：[角色描述块]，[表情]，[动作]，在[场景]。[构图]。对话框文字："[对白]"。旁白文字："[旁白]"。
第3格（左下）：[角色描述块]，[表情]，[动作]，在[场景]。[构图]。对话框文字："[对白]"。旁白文字："[旁白]"。
第4格（右下）：[角色描述块]，[表情]，[动作]，在[场景]。[构图]。对话框文字："[对白]"。旁白文字："[旁白]"。

[风格后缀]
```

**英文 prompt 模板**（prompt_lang=en）：

```
A 4-panel comic strip in 2x2 grid layout with clean white borders between panels.

Panel 1 (top-left): [CHARACTER_BLOCK], [EXPRESSION], [ACTION], in [SCENE]. [COMPOSITION]. Speech bubble text: "[DIALOGUE]". Narration text: "[NARRATION]".
Panel 2 (top-right): [CHARACTER_BLOCK], [EXPRESSION], [ACTION], in [SCENE]. [COMPOSITION]. Speech bubble text: "[DIALOGUE]". Narration text: "[NARRATION]".
Panel 3 (bottom-left): [CHARACTER_BLOCK], [EXPRESSION], [ACTION], in [SCENE]. [COMPOSITION]. Speech bubble text: "[DIALOGUE]". Narration text: "[NARRATION]".
Panel 4 (bottom-right): [CHARACTER_BLOCK], [EXPRESSION], [ACTION], in [SCENE]. [COMPOSITION]. Speech bubble text: "[DIALOGUE]". Narration text: "[NARRATION]".

[STYLE_SUFFIX]
```

**关键原则**：
- `[角色描述块]` 每格完全相同 → 保证角色一致性
- 明确声明 2×2 网格布局 + 每格标注位置
- `[风格后缀]` 放在末尾统一风格
- 对白文字用引号包裹，明确标注为 "对话框文字" / "Speech bubble text"
- 旁白文字用引号包裹，明确标注为 "旁白文字" / "Narration text"

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

```bash
python .claude/skills/gen_image_with_text/gen_image.py gen "<prompt>" --size 1024x1024 --save-path <output_dir>/<filename>_v{i}.png
```

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
| Step 3 漫画生成 | `{filename}_v{i}.png` | PNG | 图文一体候选图（含对白文字） |
| Step 3 漫画生成 | `generation_log.json` | JSON | 生成日志（含 prompt、参数、时间） |
| 全部完成 | `README.md` | Markdown | 最终交付说明，汇总所有产物 |

### 产物文件内容规范

#### theme_concept.yaml

```yaml
# 主题构思产物
generated_at: "2026-06-19T10:30:00+08:00"
input_theme: "程序员日常"
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
2. **角色跨格不一致**：统一角色描述前缀 + 风格后缀中强调 `所有格角色设计一致` / `consistent character design across all panels`
3. **文字渲染效果差**：模型对文字渲染能力有限，对白尽量简短（10字以内）；支持 prompt_lang=en 切换英文 prompt
4. **API 限流**：串行调用 + 失败重试1次
5. **Pillow 不再需要**：文字由模型直接生成，无需后处理叠加
