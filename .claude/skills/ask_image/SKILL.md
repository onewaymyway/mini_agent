---
name: ask_image
description: 要从图片中读取信息，或者对图片进行问答时，使用本 skill。
triggers: 图片信息提取，读图
---
# ask_image - 图片问答 Skill

基于 `vision_tools.py` 实现的图片问答功能。

## 用法

**直接调用**：
```bash
python .claude/skills/ask_image/ask_image.py <图片路径> <prompt>
```

**通过 Bash 工具调用**（推荐）：
```bash
python .claude/skills/ask_image/ask_image.py "<绝对路径>" "<问题>"
```

## 示例

```bash
python .claude/skills/ask_image/ask_image.py "test.jpg" "详细描述这张图片"
python .claude/skills/ask_image/ask_image.py "screenshot.png" "截图里有什么错误信息？"
python .claude/skills/ask_image/ask_image.py "E:/codes/agnes_code/analyse_data/image_from_skill/leimu.png" "这个图片里的是什么角色，来自哪个作品？"
```

## 最佳实践

### ✅ 正确做法
- 直接用 Bash 工具调用 ask_image.py，将问题作为 prompt 参数传入
- 使用绝对路径（正斜杠格式）
- 问题描述尽量具体明确

### ❌ 错误做法
- **不要直接用 Read 工具读取图片文件** - 这只会返回二进制数据或空结果
- 不要使用相对路径
- 不要试图自己处理图片的 base64 编码

## 常见问题

**Q: 为什么会遇到 `UnicodeEncodeError: 'gbk' codec can't encode character`？**
A: 这是 Windows 命令行编码问题，不影响功能，可忽略。也可设置 `chcp 65001` 切换为 UTF-8 编码。

**Q: 为什么会看到 `InsecureRequestWarning`？**
A: 这是本地 HTTPS 请求的 SSL 警告，不影响功能，可忽略。
