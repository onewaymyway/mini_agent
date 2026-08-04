# 快速开始指南

## 5 分钟掌握网页操作

### 第一步：启动浏览器

```bash
cd .claude/skills/browser-cdp
python src/core/browser_launch.py --dedicated --name web_op --start-url "https://example.com"
```

输出示例：
```
[ok] 浏览器实例已启动: web_op
[info] 调试端口: 9333
[info] 首个 Tab ID: ABCDEF1234567890
```

### 第二步：导航到目标页面

```bash
python src/core/browser_nav.py --port 9333 --tab ABCDEF1234567890 --goto "https://example.com/page" --wait-for networkidle
```

### 第三步：截图并标注元素

```bash
python src/core/browser_screenshot.py --port 9333 --tab ABCDEF1234567890 --out shot.png --annotate
```

截图会保存为 `shot.png`，同时生成 `shot.elements.json` 记录元素编号。

### 第四步：交互操作

```bash
# 点击编号为 3 的元素
python src/core/browser_input.py --port 9333 --tab ABCDEF1234567890 --click-index 3

# 在编号为 5 的元素输入文本
python src/core/browser_input.py --port 9333 --tab ABCDEF1234567890 --type-index 5 --text "hello world"

# 按 Enter 键
python src/core/browser_input.py --port 9333 --tab ABCDEF1234567890 --key Enter
```

### 第五步：提取数据

```bash
# 提取页面文本内容
python src/core/browser_extract.py --port 9333 --tab ABCDEF1234567890 --mode text --save content.txt

# 提取可交互元素列表
python src/core/browser_extract.py --port 9333 --tab ABCDEF1234567890 --mode elements --save elements.json

# 提取所有链接
python src/core/browser_extract.py --port 9333 --tab ABCDEF1234567890 --mode links --save links.json
```

### 第六步：关闭浏览器

```bash
python src/core/browser_launch.py --stop-dedicated web_op
```

## 完整示例：登录并抓取数据

```bash
# 1. 启动浏览器
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

## 常用命令速查表

| 操作 | 命令 |
|------|------|
| 启动浏览器 | `browser_launch.py --dedicated --name <name> --start-url <url>` |
| 导航 | `browser_nav.py --port <port> --tab <id> --goto <url>` |
| 后退 | `browser_nav.py --port <port> --tab <id> --back` |
| 前进 | `browser_nav.py --port <port> --tab <id> --forward` |
| 刷新 | `browser_nav.py --port <port> --tab <id> --reload` |
| 点击 | `browser_input.py --port <port> --tab <id> --click-index <n>` |
| 输入 | `browser_input.py --port <port> --tab <id> --type-index <n> --text <text>` |
| 按键 | `browser_input.py --port <port> --tab <id> --key <key>` |
| 截图 | `browser_screenshot.py --port <port> --tab <id> --out <file>` |
| 提取文本 | `browser_extract.py --port <port> --tab <id> --mode text` |
| 提取元素 | `browser_extract.py --port <port> --tab <id> --mode elements` |
| 关闭浏览器 | `browser_launch.py --stop-dedicated <name>` |

## 注意事项

1. **路径规则**：所有脚本必须先 `cd` 到 browser-cdp skill 目录再运行
2. **端口获取**：启动浏览器后会输出调试端口，记录该端口号
3. **Tab ID**：启动后会输出首个 Tab ID，后续操作使用此 ID
4. **登录态**：使用 `--dedicated --name <固定名称>` 保持登录态
5. **超时设置**：大页面建议增加 `--timeout` 参数
