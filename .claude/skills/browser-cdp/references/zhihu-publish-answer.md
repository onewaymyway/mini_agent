---
name: zhihu-publish-answer
skill: browser-cdp
script: zhihu_publish_answer.py
description: 知乎问题回答发布自动化脚本，通过已登录的浏览器实例在知乎问题下撰写并发布回答。
triggers: 知乎发布, 知乎写回答, zhihu publish answer, 发布知乎回答, 知乎问答发布
platforms: windows, macos, linux, pc
---

# 知乎回答发布自动化脚本 (`zhihu_publish_answer.py`)

## 用途

通过 CDP 控制已登录的知乎浏览器实例，在指定知乎问题下撰写并发布回答。

支持两种输入模式：
- **纯文本模式**：直接传入回答内容字符串
- **文档模式**：传入包含回答内容的文件路径（如 .txt、.md 文件）

## 使用示例

```bash
cd .claude/skills/browser-cdp

# 前提：先运行 launch_zhihu_logged_in.py 启动已登录的知乎浏览器（端口 9336）

# 基本用法：直接提供回答内容
python zhihu_publish_answer.py "https://www.zhihu.com/question/123456" "这是我的回答内容"

# 从文档读取回答内容
python zhihu_publish_answer.py "https://www.zhihu.com/question/123456" ./answer.txt

# 自定义调试端口（如果使用了其他端口）
python zhihu_publish_answer.py --port 9336 "https://www.zhihu.com/question/123456" "回答内容"

# 干跑模式：只填写内容不实际发布，用于预览检查
python zhihu_publish_answer.py --dry-run "https://www.zhihu.com/question/123456" "回答内容"

# 跳过确认提示（适合自动化场景）
python zhihu_publish_answer.py --no-confirm "https://www.zhihu.com/question/123456" "回答内容"
```

## 参数说明

| 参数 | 说明 |
|------|------|
| `question_url` | 必填：知乎问题链接（如 https://www.zhihu.com/question/123） |
| `answer` | 必填：回答内容文本，或包含回答内容的文件路径 |
| `--port PORT` | 调试端口（默认 9336），需与 launch_zhihu_logged_in.py 一致 |
| `--dry-run` | 仅填写内容，不点击发布按钮，用于预览检查 |
| `--no-confirm` | 跳过发布前的确认提示，自动执行发布 |

## 前置条件

1. **启动已登录的知乎浏览器**：必须先运行 `launch_zhihu_logged_in.py` 启动浏览器并完成知乎登录
   ```bash
   python launch_zhihu_logged_in.py
   ```
   - 该脚本会使用端口 `9336` 和用户数据目录 `temp_data/zhihu_logged_in_profile`
   - 浏览器窗口保持打开状态，不要关闭

2. **保持登录态**：浏览器实例不能关闭，否则登录态会丢失。如需重启，需重新登录知乎。

## 工作流程

1. **检测浏览器连接**：检查端口 9336 是否有运行的 Chrome/Edge 实例，没有则尝试启动
2. **导航到问题页**：打开指定的知乎问题链接
3. **验证登录态**：通过页面 DOM 检测用户是否已登录知乎，未登录则退出
4. **点击写回答**：找到并点击问题页面上的"写回答"按钮
5. **等待编辑器加载**：等待富文本编辑器（DraftJS）完全加载
6. **填充回答内容**：通过 Input.insertText 逐行填入内容，支持多行换行
7. **内容校验**：读取编辑器内容确认已正确填入
8. **（可选）干跑模式**：如果设置了 --dry-run，跳过发布步骤
9. **确认发布**：显示预览并要求用户确认（除非设置 --no-confirm）
10. **点击发布**：找到并点击"发布回答"按钮
11. **等待发布完成**：监控 URL 变化或成功提示 toast，判断发布结果

## 技术要点

### 登录态检测
通过查询页面元素判断登录状态：
- 存在用户头像且无登录按钮 → 已登录
- 存在登录按钮或跳转到登录页 → 未登录

### 写回答按钮定位
支持多种选择器兼容知乎不同版本的问题页结构：
- `.QuestionAnswer-WriteBtn`, `.WriteAnswerButton`
- `button.Button--primary.Button--blue`
- `a[href*="/answer/edit"]`
- 按文本匹配"写回答"

### 编辑器内容填充
知乎使用 DraftJS 富文本编辑器，直接设置 innerHTML 会导致内容丢失。采用双策略：
1. **首选**：`Input.insertText` + `dispatchKeyEvent(Enter)` 模拟真实键盘输入，触发框架事件
2. **兜底**：`document.execCommand('insertText')`

多行内容按行拆分，每行之间发送 Enter 键实现换行。

### 发布按钮定位
支持多种选择器：
- `.PublishAnswerButton`, `.QuestionAnswer-PublishBtn`
- `button.Button--primary.Button--blue`
- `AnswerForm button[type="submit"]`
- 按文本匹配"发布回答"或"发布"

### 发布完成判断
两种方式判断发布成功：
1. **URL 跳转**：页面从 `/question/...` 跳转到 `/answer/...`
2. **成功提示**：检测到包含"发布"字样的 Toast 通知

## 错误处理

- 浏览器端口不可用：尝试启动新实例，失败则退出
- 未登录知乎：检测失败后退出，提示用户手动登录
- 找不到写回答按钮：退出，建议检查页面结构
- 编辑器填充失败：退出，建议重试
- 发布按钮不可点击：退出，建议检查页面状态

## 注意事项

⚠️ **重要**：本脚本不负责知乎登录，必须提前通过 `launch_zhihu_logged_in.py` 启动已登录的浏览器实例。

⚠️ **安全**：涉及"发布"等不可逆操作，默认要求用户确认。可使用 `--no-confirm` 跳过确认，但需谨慎使用。

⚠️ **富文本限制**：当前版本仅支持纯文本内容。如需插入图片、引用等富文本格式，需扩展脚本支持。

⚠️ **知乎页面变更**：知乎 UI 可能随时调整，选择器可能需要更新。如遇元素找不到，可检查浏览器控制台是否有 JS 错误。

## 依赖脚本

- `launch_zhihu_logged_in.py` — 启动已登录知乎的浏览器实例（配套脚本）
- `cdp_client.py` — CDP 连接和 JS 执行底层库
- `browser_launch.py` — 浏览器实例管理（复用其中的端口检测逻辑）

## 文件结构

```
.claude/skills/browser-cdp/
├── zhihu_publish_answer.py      # 主脚本
├── zhihu_publish_answer_skill.md # 本文档（SKILL.md 子资源）
├── launch_zhihu_logged_in.py    # 配套：启动已登录浏览器
├── temp_data/
│   └── zhihu_logged_in_profile/ # 知乎登录态数据目录
└── references/
    └── zhihu-publish-answer.md  # 本资源文件
```

## 与 zhihu_hot.py / zhihu_search.py 的区别

| 特性 | zhihu_publish_answer | zhihu_hot | zhihu_search |
|------|---------------------|-----------|--------------|
| 用途 | 发布回答到问题 | 抓取热榜 | 搜索知乎内容 |
| 登录要求 | **必须已登录** | 发现页免登录，热榜需登录 | 通常无需登录 |
| 操作类型 | 写（发布） | 读（抓取） | 读（搜索） |
| 交互方式 | 填写表单、点击提交 | 解析页面内容 | 搜索查询 |
