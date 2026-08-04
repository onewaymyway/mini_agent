---
name: browser-web-operation
description: 通用网页操作技能，支持任意网站的页面导航、元素交互、表单提交、数据提取、截图分析等核心功能。当用户说"帮我操作网页"、"填写表单"、"提交数据"、"抓取网页内容"、"自动化网页操作"时使用。
triggers: 网页操作, 表单提交, 网页自动化, 页面交互, 数据抓取, web operation, form submit, page automation
platforms: windows, macos, linux, pc
resources:
  - id: quick-start
    path: references/quick-start.md
    description: 快速开始指南：5分钟掌握网页操作核心命令
    triggers: 快速开始, 快速上手, quick start
  - id: element-interaction
    path: references/element-interaction.md
    description: 元素交互详解：点击、输入、选择、悬停、拖拽等操作
    triggers: 点击, 输入, 选择, 悬停, 拖拽, click, type, select
  - id: form-automation
    path: references/form-automation.md
    description: 表单自动化：多步骤表单、文件上传、表单验证、状态保存
    triggers: 表单, 提交, 上传文件, form, upload, submit
  - id: data-extraction
    path: references/data-extraction.md
    description: 数据提取：表格、列表、JSON、API 响应提取
    triggers: 提取数据, 抓取, 表格, 列表, extract, scrape
  - id: screenshot-analysis
    path: references/screenshot-analysis.md
    description: 截图分析：编号标注、元素定位、视觉验证
    triggers: 截图, 标注, 编号, screenshot, annotate
  - id: workflow-patterns
    path: references/workflow-patterns.md
    description: 工作流模式：多步骤流程、条件分支、错误处理
    triggers: 工作流, 多步骤, 流程, workflow, multi-step
  - id: troubleshooting
    path: references/troubleshooting.md
    description: 故障排查指南：常见错误、超时处理、调试技巧
    triggers: 错误, 故障, 排查, troubleshooting, error, timeout
---

# Browser Web Operation Skill

通用网页操作技能，基于 browser-cdp 核心能力，提供开箱即用的网页自动化解决方案。

## 核心能力

| 能力 | 说明 | 命令 |
|------|------|------|
| 页面导航 | 打开 URL、前进后退、刷新 | `browser_nav.py` |
| 元素交互 | 点击、输入、选择、悬停 | `browser_input.py` |
| 表单提交 | 填写表单、上传文件、提交 | `browser_form.py` |
| 数据提取 | HTML/文本/表格/链接提取 | `browser_extract.py` |
| 截图分析 | 编号标注、元素定位 | `browser_screenshot.py` |
| 多标签页 | 标签管理、批量操作 | `browser_tabs.py` |
| 文件下载 | 下载监听、进度监控 | `browser_download.py` |

## 快速开始

### 1. 启动浏览器

```bash
cd .claude/skills/browser-cdp
python src/core/browser_launch.py --dedicated --name web_op --start-url "https://example.com"
```

### 2. 导航到目标页面

```bash
python src/core/browser_nav.py --port 9333 --tab <id> --goto "https://example.com/page" --wait-for networkidle
```

### 3. 截图并标注元素

```bash
python src/core/browser_screenshot.py --port 9333 --tab <id> --out shot.png --annotate
```

### 4. 交互操作

```bash
# 点击元素
python src/core/browser_input.py --port 9333 --tab <id> --click-index 3

# 输入文本
python src/core/browser_input.py --port 9333 --tab <id> --type-index 5 --text "hello"

# 按键
python src/core/browser_input.py --port 9333 --tab <id> --key Enter
```

### 5. 提取数据

```bash
# 提取文本内容
python src/core/browser_extract.py --port 9333 --tab <id> --mode text --save content.txt

# 提取可交互元素
python src/core/browser_extract.py --port 9333 --tab <id> --mode elements --save elements.json

# 提取链接
python src/core/browser_extract.py --port 9333 --tab <id> --mode links --save links.json
```

### 6. 关闭浏览器

```bash
python src/core/browser_launch.py --stop-dedicated web_op
```

## 典型使用场景

### 场景 1：填写并提交表单

```bash
# 1. 打开表单页面
python src/core/browser_nav.py --port 9333 --tab <id> --goto "https://example.com/form" --wait-for stable

# 2. 截图查看表单结构
python src/core/browser_screenshot.py --port 9333 --tab <id> --out shot.png --annotate

# 3. 填写表单字段
python src/core/browser_input.py --port 9333 --tab <id> --type-index 1 --text "John"
python src/core/browser_input.py --port 9333 --tab <id> --type-index 2 --text "john@example.com"
python src/core/browser_input.py --port 9333 --tab <id> --type-index 3 --text "Hello World"

# 4. 提交表单
python src/core/browser_input.py --port 9333 --tab <id> --click-index 4

# 5. 等待提交完成
python src/core/browser_nav.py --port 9333 --tab <id> --wait-selector ".success-message" --timeout 10
```

### 场景 2：搜索并提取结果

```bash
# 1. 打开搜索页面
python src/core/browser_nav.py --port 9333 --tab <id> --goto "https://example.com/search" --wait-for networkidle

# 2. 输入搜索关键词
python src/core/browser_input.py --port 9333 --tab <id> --type-selector "input.search-box" --text "关键词"
python src/core/browser_input.py --port 9333 --tab <id> --key Enter

# 3. 等待结果加载
python src/core/browser_nav.py --port 9333 --tab <id> --wait-selector ".results" --timeout 10

# 4. 提取搜索结果
python src/core/browser_extract.py --port 9333 --tab <id> --mode elements --save results.json
```

### 场景 3：登录并抓取数据

```bash
# 1. 启动浏览器（保持登录态）
python src/core/browser_launch.py --dedicated --name login_session --start-url "https://example.com/login"

# 2. 填写登录表单
python src/core/browser_input.py --port 9333 --tab <id> --type-selector "input[name='username']" --text "myuser"
python src/core/browser_input.py --port 9333 --tab <id> --type-selector "input[name='password']" --text "mypass"
python src/core/browser_input.py --port 9333 --tab <id> --click-selector "button[type='submit']"

# 3. 等待登录完成
python src/core/browser_nav.py --port 9333 --tab <id> --wait-selector ".user-profile" --timeout 10

# 4. 导航到目标页面
python src/core/browser_nav.py --port 9333 --tab <id> --goto "https://example.com/dashboard" --wait-for networkidle

# 5. 提取数据
python src/core/browser_extract.py --port 9333 --tab <id> --mode text --save dashboard.txt

# 6. 截图保存
python src/core/browser_screenshot.py --port 9333 --tab <id> --out dashboard.png --full-page

# 7. 关闭浏览器
python src/core/browser_launch.py --stop-dedicated login_session
```

### 场景 4：文件下载

```bash
# 1. 启动下载监听
python src/core/browser_download.py --port 9333 --tab <id> --start-listener

# 2. 触发下载
python src/core/browser_input.py --port 9333 --tab <id> --click-selector "a.download"

# 3. 等待下载完成
python src/core/browser_download.py --port 9333 --tab <id> --wait --timeout 60

# 4. 查看下载状态
python src/core/browser_download.py --port 9333 --tab <id> --list
```

## 高级功能

### 反检测模式

```bash
python src/core/browser_nav.py --port 9333 --tab <id> --goto "https://example.com" --stealth --handle-captcha
```

### 智能等待策略

| 策略 | 适用场景 | 命令 |
|------|---------|------|
| `networkidle` | 动态加载页面 | `--wait-for networkidle` |
| `stable` | 内容渲染页面 | `--wait-for stable` |
| `selector` | 等待特定元素 | `--wait-selector "#element"` |
| `ajax` | AJAX 加载页面 | `--wait-for ajax` |

### 多标签页管理

```bash
# 列出所有标签页
python src/core/browser_tabs.py --port 9333 --list

# 批量导航
python src/core/browser_tabs.py --port 9333 --batch-goto "url1,url2,url3"

# 批量截图
python src/core/browser_tabs.py --port 9333 --batch-screenshot --out-dir ./screenshots

# 批量提取
python src/core/browser_tabs.py --port 9333 --batch-extract --mode text --out-dir ./extracted
```

### 复杂表单处理

```bash
# 文件上传
python src/core/browser_form.py --port 9333 --tab <id> --upload-file --fill-selector "input[type='file']" --file "/path/to/file.pdf"

# 批量填写表单
python src/core/browser_form.py --port 9333 --tab <id> --fill-form form_def.json

# 保存/恢复表单状态
python src/core/browser_form.py --port 9333 --tab <id> --save-form --out saved.json
python src/core/browser_form.py --port 9333 --tab <id> --restore-form --in saved.json
```

## 配置指南

### 浏览器实例管理

```bash
# 查看已创建的实例
python src/core/browser_launch.py --list-dedicated

# 停止实例
python src/core/browser_launch.py --stop-dedicated <name>

# 查看运行的浏览器
python src/core/browser_launch.py --list-running
```

### 登录态持久化

使用 `--dedicated --name <固定名称>` 保持登录态：

```bash
# 第一次使用，手动登录
python src/core/browser_launch.py --dedicated --name my_session --start-url "https://example.com/login"

# 后续使用，自动复用登录态
python src/core/browser_launch.py --dedicated --name my_session
python src/core/browser_nav.py --port 9333 --tab <id> --goto "https://example.com/protected"
```

## 安全与边界

- 不用于绕过验证码、自动登录他人账号、批量注册等滥用场景
- 涉及"提交""支付""删除""发送"等不可逆操作时，先用截图向用户确认
- 遵守目标网站的 robots.txt 和服务条款
- 控制请求频率，避免对目标服务器造成压力

## 常见坑

- `Page.captureScreenshot` 的 `clip` 坐标是 CSS 像素，不需要额外乘 DPR
- 页面是 SPA 时不要死等 load 事件，用 `--wait-for networkidle` 判断路由跳转
- 元素编号依赖当次 DOM 扫描顺序，页面有明显变化后务必先重新截图/扫描再操作
- 所有脚本必须先 `cd` 到 browser-cdp skill 目录再运行

## 错误处理

### 常见错误及解决方案

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| `Connection refused` | 浏览器未启动或端口错误 | 先运行 `browser_launch.py --dedicated` |
| `Tab not found` | tab ID 无效 | 运行 `browser_tabs.py --list` 获取有效 ID |
| `Element not found` | 元素索引过期 | 重新截图/扫描，页面变化后索引会失效 |
| `Timeout` | 网络慢或页面复杂 | 增加 `--timeout` 参数（默认 15s） |
| `Screenshot timeout` | 页面未就绪 | 先执行 `--wait-for networkidle` 再截图 |
| `Captcha detected` | 触发验证码 | 使用 `--handle-captcha` 或手动处理 |

### 重试模式

```bash
# 带重试的导航（最多 3 次）
for i in 1 2 3; do
    if python src/core/browser_nav.py --port 9333 --tab <id> --goto "URL" --wait-for networkidle; then
        echo "[ok] 成功"
        break
    fi
    echo "[warn] 第 $i 次失败，2 秒后重试..."
    sleep 2
done
```

### 状态检查

```bash
# 检查浏览器状态
python src/core/browser_launch.py --list-running

# 检查 tab 列表
python src/core/browser_tabs.py --port 9333 --list

# 检查当前页面状态
python src/core/browser_extract.py --port 9333 --tab <id> --mode meta
```

## 依赖

本 skill 依赖 browser-cdp skill，无需额外安装依赖。

```bash
pip install websocket-client requests pillow
```

## 参考文档

- [快速开始](references/quick-start.md)
- [元素交互](references/element-interaction.md)
- [表单自动化](references/form-automation.md)
- [数据提取](references/data-extraction.md)
- [截图分析](references/screenshot-analysis.md)
- [工作流模式](references/workflow-patterns.md)
