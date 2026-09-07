---
name: gen_image_with_text
description: 要用文字生成图片、编辑图片或多图合成时，使用本 skill。支持 prompt 字符串或 prompt_file 文件两种方式。
triggers: 图片生成，生成图, gen_image, prompt_file, 多图合成, 图生图
---

# Generate Image with Text

此 skill 允许你使用 `agnes_tools.py` 中的 `AgnesImageClient` 根据文本描述生成图片、编辑现有图片，或把多张参考图合成为一张新图。

底层模型：**Agnes Image 2.5 Flash**（`agnes-image-2.5-flash`）——Agnes AI 最新一代图像模型，图像生成、编辑、构图、细节呈现和提示词遵循等整体能力全面超过上一代 Agnes Image 2.1 Flash；请求/响应参数、支持尺寸、价格和计费方法与 2.1 Flash 完全一致，属于同接口下的直接升级。如需回退旧模型，构造 `AgnesImageClient(api_key=..., model="agnes-image-2.1-flash")` 即可。

## 快速开始

```bash
# 设置 API 密钥并生成图片
AGNES_API_KEY="your-api-key" python .claude/skills/gen_image_with_text/gen_image.py gen "图片描述" --save-path output.png

# 或通过环境变量
export AGNES_API_KEY="your-api-key"  # Linux/Mac
$env:AGNES_API_KEY="your-api-key"   # Windows PowerShell
```

## 功能

- **text-to-image（文生图）**：根据文字描述生成图片
- **image editing（图生图）**：基于现有图片进行修改
- **multi-image composition（多图合成）**：使用多张参考图像组合生成新图像（新增）

## 两种 Prompt 输入方式

`gen_image()` 函数支持两种 prompt 输入方式，**二者只需提供其一**：

| 方式 | 参数 | 适用场景 |
|------|------|----------|
| **直接传入字符串** | `prompt="描述文字"` | 简短 prompt，直接在代码或命令行中使用 |
| **从文件读取** | `prompt_file="prompt.txt"` | 长 prompt、需要版本管理、或批量生成时使用 |

> **注意**：`prompt` 和 `--prompt-file` 互斥，不能同时指定。如果两者都为空，会返回错误。

### 函数调用示例

```python
from gen_image import gen_image, edit_image

# 方式一：直接传入 prompt 字符串
result = gen_image(prompt="A beautiful sunset beach scene", size="1024x1024")

# 方式二：从文件读取 prompt
result = gen_image(prompt_file="prompts/my_prompt.txt", size="1024x1024")

# 方式三：推荐用档位式 size + ratio 获得可预期的输出尺寸
result = gen_image(prompt="A cinematic product hero image", size="2K", ratio="16:9")

# 多图合成：image_path 传列表即可
result = edit_image(
    image_path=["character-1.png", "character-2.png"],
    prompt="Combine the two characters into an intense fantasy battle scene",
)
```

## 命令行用法

### 生成图片（text-to-image）

```bash
# 方式一：直接传入 prompt 字符串
python .claude/skills/gen_image_with_text/gen_image.py gen "prompt" --save-path output.png

# 方式二：从文件读取 prompt
python .claude/skills/gen_image_with_text/gen_image.py gen --prompt-file prompt.txt --save-path output.png

# 方式三：档位式 size + ratio（推荐，输出尺寸可预期）
python .claude/skills/gen_image_with_text/gen_image.py gen "prompt" --size 2K --ratio 16:9 --save-path output.png
```

> **注意**：`prompt` 和 `--prompt-file` 互斥，只能选其一。

### 编辑图片（image-to-image）

```bash
python .claude/skills/gen_image_with_text/gen_image.py edit "prompt" --image-path input.png [选项]
```

### 多图合成（multi-image composition）

重复传 `--image-path` 即可传入多张参考图：

```bash
python .claude/skills/gen_image_with_text/gen_image.py edit \
  "Combine the two characters into an intense fantasy battle scene, dynamic lighting, cinematic composition" \
  --image-path character-1.png \
  --image-path character-2.png \
  --save-path output/battle-scene.png
```

## 可用参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `prompt` | 图片描述文本（与 `--prompt-file` 二选一） | - |
| `--prompt-file` | 包含 prompt 文本的文件路径（与 `prompt` 二选一） | - |
| `--size` | 图片尺寸，推荐档位式 `1K`/`2K`/`3K`/`4K`，也兼容 `1024x1024` 等精确尺寸 | `1024x1024` |
| `--ratio` | 与档位式 `--size` 配合使用的宽高比 | 不传则由 API 默认（`1:1`） |
| `--save-path` | 保存图片的路径 | 不保存 |
| `--image-path` | 输入图片路径（edit 模式必填，可重复传多次做多图合成） | - |
| `--format` | 响应格式：`url` 或 `b64_json` | `url` |

## 尺寸与宽高比

为了获得可预期的输出尺寸，建议将 `--size` 用档位值并配合 `--ratio` 使用：

- 推荐 `size` 值：`1K`、`2K`、`3K`、`4K`
- 支持的 `ratio` 值：`1:1`、`3:4`、`4:3`、`16:9`、`9:16`、`2:3`、`3:2`、`21:9`
- 也兼容 `1024x1024`、`1024x768`、`768x1024` 等历史精确尺寸写法，但不受原生支持的精确尺寸（比如 `1920x1080`、`2560x1440` 这类标准显示器分辨率）可能会被服务端标准化到最接近的档位（例如 16:9 的 `1K` 会映射成 `1312x736`）。如果确实需要 `1920x1080`/`2560x1440` 这类素材，建议请求 `size=2K, ratio=16:9`，再在下游裁剪/缩放到最终画布。

### 输出尺寸参考（档位 × 宽高比）

| Ratio  | 1K          | 2K          | 3K          | 4K          |
| ------ | ----------- | ----------- | ----------- | ----------- |
| `1:1`  | `1024x1024` | `2048x2048` | `3072x3072` | `4096x4096` |
| `3:4`  | `864x1152`  | `1728x2304` | `2592x3456` | `3456x4608` |
| `4:3`  | `1152x864`  | `2304x1728` | `3456x2592` | `4608x3456` |
| `16:9` | `1312x736`  | `2624x1472` | `3936x2208` | `5248x2944` |
| `9:16` | `736x1312`  | `1472x2624` | `2208x3936` | `2944x5248` |
| `2:3`  | `832x1248`  | `1664x2496` | `2496x3744` | `3328x4992` |
| `3:2`  | `1248x832`  | `2496x1664` | `3744x2496` | `4992x3328` |
| `21:9` | `1568x672`  | `3136x1344` | `4704x2016` | `6272x2688` |

## 使用示例

### 生成一张日落海滩的图片
```bash
AGNES_API_KEY="sk-xxx" python .claude/skills/gen_image_with_text/gen_image.py \
  gen "A beautiful beach at sunset with orange sky and calm ocean waves" \
  --size 1024x1024 \
  --save-path ./output/sunset-beach.png
```

### 生成 16:9 的桌面壁纸级素材（档位 + ratio）
```bash
AGNES_API_KEY="sk-xxx" python .claude/skills/gen_image_with_text/gen_image.py \
  gen "A cinematic product hero image for a desktop monitor wallpaper, clean lighting, high detail" \
  --size 2K --ratio 16:9 \
  --save-path ./output/wallpaper.png
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

### 多图合成
```bash
AGNES_API_KEY="sk-xxx" python .claude/skills/gen_image_with_text/gen_image.py \
  edit "Combine the two characters into an intense fantasy battle scene, dynamic lighting, detailed background, cinematic composition" \
  --image-path character-1.png \
  --image-path character-2.png \
  --save-path ./output/battle-scene.png
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

## 定价

`agnes-image-2.1-flash` 和 `agnes-image-2.5-flash` 的价格及计费方法相同——当前所有输出分辨率档位和输入参考图片均免费（前 3 张输入参考图片按刊例价本就不额外收费，第 4 张起原价 `$0.003/张`，目前也是 `$0`）。

## 常见错误与故障排除

1. **`response_format` 不能放在请求体顶层**：需要 URL/Base64 输出时，必须放进 `extra_body.response_format`（`agnes_tools.py` 已内置正确写法，直接用即可，无需手动拼请求体）。
2. **图生图 / 多图合成不需要 `tags: ["img2img"]`**：只需在 `extra_body.image` 中提供输入图像。
3. **输入图像 URL 无法访问**：请使用公共 HTTPS 图像 URL，不需要登录/cookie/私有请求头；无法公开访问时改用本地文件路径（`agnes_tools.py` 会自动转换成 Data URI）。
4. **请求超时**：根据提示词复杂度、图像尺寸和服务器负载，生成可能需要数秒到几十秒，官方建议客户端超时设置为 `60s - 360s`（`agnes_tools.py` 默认 `timeout=1800`，留了较大余量，一般不需要调整）。
5. **图生图 / 多图合成缺少输入图片**：`edit` 模式必须至少传一个 `--image-path`；多图合成重复传该参数即可。

## 提示

1. **API 密钥**：确保 `AGNES_API_KEY` 环境变量已正确设置
2. **Prompt 语言**：英文 prompt 通常能获得更好的生成效果
3. **目录创建**：`--save-path` 指定的目录会自动创建
4. **尺寸选项**：优先用 `1K`/`2K`/`3K`/`4K` 档位 + `ratio`；也支持 `1024x1024`、`1024x768`、`768x1024` 等精确尺寸写法
5. **Prompt 编写建议**：
   - 使用英文描述获得更准确的结果
   - 包含角色特征（如：blue hair, white headband, maid outfit）
   - 描述场景背景（如：sunrise park, sunset roller coaster scene）
   - 添加质量修饰词（如：high quality anime art）
   - 图生图：说明 [改变要求] + [新风格/场景] + [需要添加或移除的元素] + [需要保留的元素]（例如"...同时保留原始街道布局、相机角度和主要建筑形状"），Agnes Image 2.5 Flash 对"构图保留"做了专门优化
   - 多图合成：说明每张参考图的角色（如"第一张图作为主要角色，第二张图作为产品参考"），以及最终图像应如何组合这些信息
6. **Windows 环境**：使用 `$env:VAR_NAME="value"` 设置环境变量，或在命令前直接 `VAR=value`（通过 bash）
7. **验证 API 密钥**：如果生成失败，先确认 API Key 是否有效且未过期
