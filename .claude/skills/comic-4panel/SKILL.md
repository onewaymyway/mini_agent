---
name: comic-4panel
description: 四格漫画全流程生成技能，从主题构思到分镜脚本再到一次性生成完整四格漫画图。当用户说"画个四格漫画"、"生成四格漫画"、"四格漫画"时使用。
triggers: 四格漫画, 4koma, comic, 漫画生成, 画漫画, 四格, 4panel, comic strip, 4格
---

# 四格漫画生成 (4-Panel Comic Generator)

## 概述

本 skill 用于生成完整的四格漫画（2×2 网格布局）。流程包括：主题构思 → 分镜脚本 → 一次性生成完整漫画图 → 用户选择交付。

**依赖**：
- `gen_image_with_text` skill（必须，通过 bash 调用 `gen_image.py gen` 生成图片）
- `Pillow`（可选，用于对白文字叠加）
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
| add_text_overlay | `true` | 是否同时生成对白叠加版 |
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
- 展示给用户确认后再进入 Step 3

### Step 3: 生成漫画

#### Prompt 构建规则

**中文 prompt 模板**（prompt_lang=zh，默认）：

```
一幅2×2网格布局的四格漫画。

第1格（左上）：[角色描述块_zh]，[表情_zh]，[动作_zh]，在[场景_zh]。[构图_zh]
第2格（右上）：[角色描述块_zh]，[表情_zh]，[动作_zh]，在[场景_zh]。[构图_zh]
第3格（左下）：[角色描述块_zh]，[表情_zh]，[动作_zh]，在[场景_zh]。[构图_zh]
第4格（右下）：[角色描述块_zh]，[表情_zh]，[动作_zh]，在[场景_zh]。[构图_zh]

[风格后缀_zh]
```

**英文 prompt 模板**（prompt_lang=en）：

```
A 4-panel comic strip in 2x2 grid layout.

Panel 1 (top-left): [CHARACTER_BLOCK_EN], [EXPRESSION_EN], [ACTION_EN], in [SCENE_EN]. [COMPOSITION_EN]
Panel 2 (top-right): [CHARACTER_BLOCK_EN], [EXPRESSION_EN], [ACTION_EN], in [SCENE_EN]. [COMPOSITION_EN]
Panel 3 (bottom-left): [CHARACTER_BLOCK_EN], [EXPRESSION_EN], [ACTION_EN], in [SCENE_EN]. [COMPOSITION_EN]
Panel 4 (bottom-right): [CHARACTER_BLOCK_EN], [EXPRESSION_EN], [ACTION_EN], in [SCENE_EN]. [COMPOSITION_EN]

[STYLE_SUFFIX_EN]
```

**关键原则**：
- `[角色描述块]` 每格完全相同 → 保证角色一致性
- 明确声明 2×2 网格布局 + 每格标注位置
- `[风格后缀]` 放在末尾统一风格

#### 风格后缀表

| style | 中文后缀 | 英文后缀 |
|---|---|---|
| anime | 动漫风格，四格漫画，所有格角色设计一致，漫画画风，格间白色边框，鲜艳色彩，高质量 | anime style, 4koma comic strip, consistent character design across all panels, manga art style, clean white borders between panels, vibrant colors, high quality |
| manga-bw | 黑白漫画风格，四格漫画，所有格角色设计一致，墨线画，网点纸，格间白色边框，高质量 | black and white manga style, 4koma comic strip, consistent character design across all panels, ink pen drawing, screentone, clean white borders between panels, high quality |
| chibi | Q版风格，四格漫画，超变形，可爱，大头小身，所有格角色设计一致，格间白色边框，高质量 | chibi style, 4koma comic strip, super deformed, cute, big head small body, consistent character design across all panels, clean white borders between panels, high quality |
| realistic | 半写实动漫风格，四格漫画，所有格角色设计一致，精细阴影，格间白色边框，高质量 | semi-realistic anime style, 4koma comic strip, consistent character design across all panels, detailed shading, clean white borders between panels, high quality |
| watercolor | 水彩插画风格，四格漫画，所有格角色设计一致，柔和色彩，颜料质感，格间白色边框，高质量 | watercolor illustration style, 4koma comic strip, consistent character design across all panels, soft colors, paint texture, clean white borders between panels, high quality |
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

### Step 4: 用户选择与交付

展示所有候选图路径，让用户选择：

```
🎬 四格漫画生成完成！共 N 张候选图：
  📷 候选1: <path>_v1.png
  📷 候选2: <path>_v2.png
  📝 对白版1: <path>_v1_text.png
  📝 对白版2: <path>_v2_text.png

请选择满意的版本（输入编号），或说"重新生成"获取更多候选图。
```

**重新生成**：用户说"重新生成"、"再来几张"、"更多"时，生成新候选图，编号递增（_v3, _v4...），旧候选保留。

## 对白文字叠加规范

当 `add_text_overlay=true` 时，为每张候选图生成对白叠加版。

### 气泡位置规则
- 每格画面分为4个象限，对白气泡优先放在角色面部的对角象限
- 单角色格：气泡放在角色上方或下方
- 双角色格：各角色对白分别放在各自上方
- 旁白文字：画面顶部，半透明黑色背景条

### 字体规范
- 中文：微软雅黑（msyh.ttc），字号 16-20px
- 日文：MS Gothic（msgothic.ttc），字号 16-20px
- 英文：Arial，字号 14-18px

### 实现方式

Agent 通过 Python 一行命令调用 PIL 叠加文字：

```python
from PIL import Image, ImageDraw, ImageFont
import os

def add_dialogue_overlay(image_path, panels_dialogue, output_path, lang="zh"):
    """在四格漫画图上叠加对白文字。
    
    Args:
        image_path: 原始漫画图路径
        panels_dialogue: 列表，每项为 dict，包含 dialogue 和 narration
            [{"dialogue": "对白", "narration": "旁白"}, ...]
        output_path: 输出路径
        lang: 对白语言
    """
    img = Image.open(image_path)
    w, h = img.size
    pw, ph = w // 2, h // 2  # 每格尺寸
    draw = ImageDraw.Draw(img)
    
    # 字体选择
    font_paths = {
        "zh": "C:/Windows/Fonts/msyh.ttc",
        "ja": "C:/Windows/Fonts/msgothic.ttc",
        "en": "C:/Windows/Fonts/arial.ttf",
    }
    font_path = font_paths.get(lang, font_paths["zh"])
    font = ImageFont.truetype(font_path, 18)
    small_font = ImageFont.truetype(font_path, 14)
    
    positions = [
        (pw * 0 + 10, ph * 0 + 10),   # 第1格 左上
        (pw * 1 + 10, ph * 0 + 10),   # 第2格 右上
        (pw * 0 + 10, ph * 1 + 10),   # 第3格 左下
        (pw * 1 + 10, ph * 1 + 10),   # 第4格 右下
    ]
    
    for i, pd in enumerate(panels_dialogue):
        px, py = positions[i]
        # 旁白
        if pd.get("narration"):
            text = pd["narration"]
            bbox = draw.textbbox((0, 0), text, font=small_font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            draw.rectangle([px, py, px + tw + 10, py + th + 6], fill=(0, 0, 0, 128))
            draw.text((px + 5, py + 3), text, fill="white", font=small_font)
            py += th + 12
        # 对白
        if pd.get("dialogue"):
            text = pd["dialogue"]
            bbox = draw.textbbox((0, 0), text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            # 气泡背景
            bx, by = px, py + ph - th - 40
            draw.rounded_rectangle(
                [bx, by, bx + tw + 16, by + th + 12],
                radius=8, fill="white", outline="black", width=2
            )
            draw.text((bx + 8, by + 6), text, fill="black", font=font)
    
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path)
```

叠加版命名：`{filename}_v{i}_text.png`，原图保留。

**后续扩展**：支持用户自定义气泡样式、字体、字号、位置等参数。

## 产物文件规范

每个流程完成后，必须在 `output_dir` 下输出对应的产物文件，便于用户查看、追溯和重新生成。

### 产物文件清单

| 流程 | 产物文件 | 格式 | 说明 |
|---|---|---|---|
| Step 1 主题构思 | `theme_concept.yaml` | YAML | 主题概念完整数据 |
| Step 2 分镜脚本 | `storyboard.yaml` | YAML | 四格分镜详细脚本 |
| Step 3 漫画生成 | `{filename}_v{i}.png` | PNG | 纯画面候选图 |
| Step 3 漫画生成 | `{filename}_v{i}_text.png` | PNG | 对白叠加版候选图（可选） |
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
      "text_overlay_file": "comic_4panel_v1_text.png",
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
| `{filename}_v1.png` | 候选图1（纯画面） |
| `{filename}_v1_text.png` | 候选图1（对白叠加） |
| `generation_log.json` | 生成日志 |

## 使用说明

- 查看分镜脚本：打开 `storyboard.yaml`
- 重新生成：告知 AI 需要调整的主题或分镜
```

### 产物输出时机

1. **Step 1 完成后**：立即写入 `theme_concept.yaml`
2. **Step 2 完成后**：立即写入 `storyboard.yaml`
3. **Step 3 每张图生成后**：追加写入 `generation_log.json`，同时生成对白叠加版（如开启）
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
3. **中文 prompt 效果不佳**：支持 prompt_lang=en 切换英文；也可中英混合（布局声明用英文，内容描述用中文）
4. **API 限流**：串行调用 + 失败重试1次
5. **对白叠加遮挡画面**：同时保留纯画面版，用户可选择；气泡位置遵循避让角色面部规则
6. **Pillow 未安装**：add_text_overlay 功能需要 Pillow，未安装时跳过叠加步骤并提示用户