---
name: gen_image_with_text
description: 要用文字生成图片时，使用本 skill。支持 prompt 字符串或 prompt_file 文件两种方式。
triggers: 图片生成，生成图, gen_image, prompt_file
---

# Generate Image with Text

此 skill 允许你使用 `agnes_tools.py` 中的 `AgnesImageClient` 根据文本描述生成图片。

## 快速开始

```bash
# 设置 API 密钥并生成图片
AGNES_API_KEY="your-api-key" python .claude/skills/gen_image_with_text/gen_image.py gen "图片描述" --save-path output.png

# 或通过环境变量
export AGNES_API_KEY="your-api-key"  # Linux/Mac
$env:AGNES_API_KEY="your-api-key"   # Windows PowerShell
```

## 两种 Prompt 输入方式

`gen_image()` 函数支持两种 prompt 输入方式，**二者只需提供其一**：

| 方式 | 参数 | 适用场景 |
|------|------|----------|
| **直接传入字符串** | `prompt="描述文字"` | 简短 prompt，直接在代码或命令行中使用 |
| **从文件读取** | `prompt_file="prompt.txt"` | 长 prompt、需要版本管理、或批量生成时使用 |

### 函数调用示例

```python
from gen_image import gen_image

# 方式一：直接传入 prompt 字符串
result = gen_image(prompt="A beautiful sunset beach scene", size="1024x1024")

# 方式二：从文件读取 prompt
result = gen_image(prompt_file="prompts/my_prompt.txt", size="1024x1024")
```

### 命令行示例

```bash
# 方式一：直接传入 prompt 字符串
python gen_image.py gen "A beautiful sunset beach scene" --save-path output.png

# 方式二：从文件读取 prompt
python gen_image.py gen --prompt-file prompts/my_prompt.txt --save-path output.png
```

> **注意**：`prompt` 和 `--prompt-file` 互斥，不能同时指定。如果两者都为空，会返回错误。

## 功能

- **text-to-image**: 根据文字描述生成图片
- **image editing**: 基于现有图片进行修改

## 两种 Prompt 输入方式

`gen_image()` 函数支持两种 prompt 输入方式，**二者只需提供其一**：

| 方式 | 参数 | 适用场景 |
|------|------|----------|
| **直接传入字符串** | `prompt="描述文字"` | 简短 prompt，直接在代码或命令行中使用 |
| **从文件读取** | `prompt_file="prompt.txt"` | 长 prompt、需要版本管理、或批量生成时使用 |

### 函数调用示例

```python
from gen_image import gen_image

# 方式一：直接传入 prompt 字符串
result = gen_image(prompt="A beautiful sunset beach scene", size="1024x1024")

# 方式二：从文件读取 prompt
result = gen_image(prompt_file="prompts/my_prompt.txt", size="1024x1024")
```

### 命令行示例

```bash
# 方式一：直接传入 prompt 字符串
python gen_image.py gen "A beautiful sunset beach scene" --save-path output.png

# 方式二：从文件读取 prompt
python gen_image.py gen --prompt-file prompts/my_prompt.txt --save-path output.png
```

> **注意**：`prompt` 和 `--prompt-file` 互斥，不能同时指定。如果两者都为空，会返回错误。

## 命令行用法

### 生成图片（text-to-image）

```bash
# 方式一：直接传入 prompt 字符串
python .claude/skills/gen_image_with_text/gen_image.py gen "prompt"  --save-path output.png

# 方式二：从文件读取 prompt
python .claude/skills/gen_image_with_text/gen_image.py gen --prompt-file prompt.txt  --save-path output.png
```

> **注意**：`prompt` 和 `--prompt-file` 互斥，只能选其一。

### 编辑图片（image-to-image）

```bash
python .claude/skills/gen_image_with_text/gen_image.py edit "prompt" --image-path input.png [选项]
```

## 可用参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `prompt` | 图片描述文本（与 `--prompt-file` 二选一） | - |
| `--prompt-file` | 包含 prompt 文本的文件路径（与 `prompt` 二选一） | - |
| `--size` | 图片尺寸 | `1024x1024` |
| `--save-path` | 保存图片的路径 | 不保存 |
| `--image-path` | 输入图片路径（edit 模式必填） | - |
| `--format` | 响应格式：`url` 或 `b64_json` | `url` |

## 使用示例

### 生成一张日落海滩的图片
```bash
AGNES_API_KEY="sk-xxx" python .claude/skills/gen_image_with_text/gen_image.py \
  gen "A beautiful beach at sunset with orange sky and calm ocean waves" \
  --size 1024x1024 \
  --save-path ./output/sunset-beach.png
```

### 生成动漫角色（英文 prompt 效果更佳）
```bash
AGNES_API_KEY="sk-xxx" python .claude/skills/gen_image_with_text/gen_image.py \
  gen "Rem from Re:Zero, blue hair, white headband, maid outfit, futuristic spaceship cockpit, high quality anime art" \
  --save-path analyse_data/image_from_skill/leimu.png
```

### 基于现有图片修改
```bash
AGNES_API_KEY="sk-xxx" python .claude/skills/gen_image_with_text/gen_image.py \
  edit "Add a cat sitting on the rock" \
  --image-path ./input.png \
  --save-path ./output/edited.png
```

## API 密钥如何传递

代码从环境变量读取 `AGNES_API_KEY` 并传给 `AgnesImageClient`：

**读取流程**（`gen_image.py`）：
```python
# 1. 从系统环境变量获取 API Key
api_key = os.environ.get("AGNES_API_KEY")

# 2. 传给 AgnesImageClient 构造函数
client = AgnesImageClient(api_key=api_key)

# 3. client 使用此 key 在 headers 中发起请求
# headers = {"Authorization": f"Bearer {self.api_key}"}
```

**设置方式**：

**Windows PowerShell（会话生效）**：
```powershell
$env:AGNES_API_KEY="sk-your-api-key-here"
python .claude/skills/gen_image_with_text/gen_image.py gen "prompt"
```

**Windows Bash / Git Bash**：
```bash
AGNES_API_KEY="sk-your-api-key-here" python .claude/skills/gen_image_with_text/gen_image.py gen "prompt"
```

**永久设置（Windows）**：
1. 右键"此电脑" → 属性 → 高级系统设置 → 环境变量
2. 用户变量 → 新建 → 变量名：`AGNES_API_KEY`，变量值：你的 API Key
3. 重启终端生效

## 提示

1. **API 密钥**: 确保 `AGNES_API_KEY` 环境变量已正确设置
2. **Prompt 语言**: 英文 prompt 通常能获得更好的生成效果
3. **目录创建**: `--save-path` 指定的目录会自动创建
4. **尺寸选项**: 支持 `1024x1024`、`1024x768`、`768x1024` 等
5. **Prompt 编写建议**：
   - 使用英文描述获得更准确的结果
   - 包含角色特征（如：blue hair, white headband, maid outfit）
   - 描述场景背景（如：sunrise park, sunset roller coaster scene）
   - 添加质量修饰词（如：high quality anime art）
6. **Windows 环境**：使用 `$env:VAR_NAME="value"` 设置环境变量，或在命令前直接 `VAR=value`（通过 bash）
7. **验证 API 密钥**：如果生成失败，先确认 API Key 是否有效且未过期
