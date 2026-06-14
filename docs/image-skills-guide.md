# 图片技能使用指南

本文档说明 mini-agent 中处理图片相关的两个核心技能：`ask_image`（图片识别）和 `gen_image_with_text`（图片生成）。

---

## 一、ask_image - 图片信息提取

### 功能说明

`ask_image` 技能允许你从图片中读取信息或对图片进行问答。

**重要**：当需要读取图片时，**请始终使用 `ask_image` 技能**，而不要尝试用 `read_file` 工具直接读取图片文件（这只会返回二进制数据或空结果）。

### 触发方式

该技能在以下情况下会自动激活：
- 提到「图片」、「读图」、「图片分析」等关键词
- 提供图片路径并要求分析内容

### 使用方法

当你需要分析图片时，直接描述你的需求：

```
帮我看看这张图片里是什么：E:/codes/images/example.png
```

Agent 会自动调用 `ask_image` 技能来处理。

### 最佳实践

**✅ 正确做法**
- 直接用自然语言描述你想要从图片中了解的信息
- 使用绝对路径（推荐正斜杠格式 `/`）
- 问题描述尽量具体明确

**❌ 错误做法**
- 不要使用 `read_file` 工具直接读取图片文件
- 不要使用相对路径（可能导致路径解析问题）
- 不要试图自己处理图片的 base64 编码

### 示例场景

```
# 分析代码截图中的错误信息
帮我看看这个截图里有什么错误：E:/codes/project/screenshot.png

# 识别图片中的角色
这个图片里的是什么角色，来自哪个作品？路径：/images/character.png

# 数据分析图表解读
这张图表显示了什么趋势？：E:/data/charts/sales_2025.png
```

### 常见问题

**Q: 为什么会遇到 `UnicodeEncodeError: 'gbk' codec can't encode character`？**

A: 这是 Windows 命令行编码问题，不影响功能。可设置 `chcp 65001` 切换为 UTF-8 编码，或直接忽略该警告。

**Q: 为什么会看到 `InsecureRequestWarning`？**

A: 这是本地 HTTPS 请求的 SSL 警告，不影响功能，可忽略。

---

## 二、gen_image_with_text - 文本生成图片

### 功能说明

该技能允许你根据文本描述生成图片，支持两种模式：
- **text-to-image**：从纯文本描述生成图片
- **image-to-image**：基于现有图片进行修改

### API 密钥配置

使用前需要设置 `AGNES_API_KEY` 环境变量：

**Windows PowerShell（当前会话）**：
```powershell
$env:AGNES_API_KEY="sk-your-api-key-here"
```

**Windows Bash / Git Bash**：
```bash
export AGNES_API_KEY="sk-your-api-key-here"
```

**永久设置（Windows）**：
1. 右键"此电脑" → 属性 → 高级系统配置 → 环境变量
2. 用户变量 → 新建 → 变量名：`AGNES_API_KEY`，变量值：你的 API Key
3. 重启终端生效

### 使用方法

当你需要生成图片时，直接描述你想要的内容：

```
生成一张日落海滩的风景图
```

或更详细的描述：

```
生成一张动漫风格的图片：一个蓝发少女站在未来主义的飞船驾驶舱内，高质量动画艺术风格
```

### 参数选项

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `prompt` | 图片描述文本（必填） | - |
| `--size` | 图片尺寸 | `1024x1024` |
| `--save-path` | 保存图片的路径 | 不保存 |
| `--format` | 响应格式（`url` 或 `b64_json`） | `url` |

### 示例场景

**生成风景图**：
```
生成一张日落海滩的图片，橙色天空和宁静的海浪
```

**生成动漫角色**（英文 prompt 效果更佳）：
```
生成动漫角色图片：Rem from Re:Zero, blue hair, white headband, maid outfit, futuristic spaceship cockpit, high quality anime art
```

**编辑现有图片**：
```
在这张图片上添加一只猫坐在石头上：E:/input.png
```

### 提示

1. **英文 Prompt**：英文描述通常能获得更准确的生成结果
2. **详细描述**：包含角色特征（如：blue hair, white headband, maid outfit）和场景背景
3. **质量修饰**：添加质量修饰词（如：high quality anime art, photorealistic, 8k）
4. **尺寸选择**：支持 `1024x1024`（正方形）、`1024x768`（横向）、`768x1024`（纵向）
5. **API 验证**：如果生成失败，先确认 API Key 是否有效且未过期

---

## 三、技能激活与管理

### 查看图片相关技能状态

```bash
/skills                              # 列出所有技能及状态
/skill info ask_image                # 查看图片识别技能详情
/skill info gen_image_with_text      # 查看图片生成技能详情
```

### 手动激活技能

```bash
/skill on ask_image                  # 激活图片识别技能
/skill on gen_image_with_text        # 激活图片生成技能
```

### 技能触发词

这些技能会自动根据关键词激活：

| 技能 | 触发词 |
|------|--------|
| `ask_image` | 图片，读图，图片信息提取，ask_image，图 |
| `gen_image_with_text` | 图片生成，生成图 |

---

## 四、与其他工具的配合

### 保存生成的图片

生成图片后，可以使用文件操作工具管理输出：

```bash
# 生成并保存到指定路径
生成一张星空夜景的图片，保存到 E:/output/starry_night.png
```

### 分析生成的图片

生成图片后可以立即分析验证：

```bash
# 先生成图片
生成一张卡通风格的猫的图片

# 然后分析生成的结果
帮我看看刚生成的这张图片效果如何：E:/output/cat.png
```

---

## 五、技术实现说明

### ask_image 实现

- 核心文件：`.claude/skills/ask_image/vision_tools.py`
- 功能：通过 Vision API 将图片编码为 base64 并发送给 LLM 进行问答
- 调用方式：`Bash` 工具执行 `ask_image.py` 脚本

### gen_image_with_text 实现

- 核心文件：`.claude/skills/gen_image_with_text/agnes_tools.py`
- 功能：`AgnesImageClient` 类封装 Agnes Image API 调用
- 调用方式：`Bash` 工具执行 `gen_image.py` 脚本

---

## 六、故障排查

| 问题 | 可能原因 | 解决方案 |
|------|----------|----------|
| `UnicodeEncodeError` | Windows 编码问题 | 设置 `chcp 65001` 或忽略 |
| `InsecureRequestWarning` | SSL 验证警告 | 可安全忽略 |
| 图片生成失败 | API Key 无效 | 检查 `AGNES_API_KEY` 环境变量 |
| 读图返回空结果 | 使用了 read_file | 改用 `ask_image` 技能 |
| 路径解析错误 | 使用了相对路径 | 改用绝对路径 |

---

*最后更新：2026-06*