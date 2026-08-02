# Browser CDP 技能详细操作指南

## 1. 快速入门

### 1.1 启动浏览器
```bash
cd .claude/skills/browser-cdp
python browser_launch.py --dedicated --name work --start-url "https://example.com"
```
输出示例：`--port 9333 --tab abc123`（记住这两个参数）

### 1.2 导航到网页
```bash
cd .claude/skills/browser-cdp
python browser_nav.py --port 9333 --tab abc123 --goto "https://httpbin.org/html"
```

### 1.3 抓取内容
```bash
cd .claude/skills/browser-cdp
python browser_extract.py --port 9333 --tab abc123 --mode text --max-chars 50000
```

### 1.4 截图标注
```bash
cd .claude/skills/browser-cdp
python browser_screenshot.py --port 9333 --tab abc123 --out shot.png --annotate
```

## 2. 浏览器连接管理

### 2.1 专用实例模式（推荐）
这是最常用的模式，会启动一个独立的 Chrome 实例，不干扰用户的真实浏览器。

| 命令 | 说明 |
|------|------|
| `--dedicated --name <name>` | 启动专用实例，<name> 用于标识和复用 |
| `--list-dedicated` | 查看已创建的专用实例 |
| `--stop-dedicated <name>` | 关闭指定专用实例 |
| `--new "URL"` | 在现有专用实例中打开新标签页 |

**重要提示**：同一个任务内所有涉及浏览器的步骤都要用**同一个固定的实例名**（如 `--name zhihu_session`），不要每次调用都用不同的名字，否则登录态会丢失。

### 2.2 连接已有浏览器（场景 B/C）
如果你希望使用用户正在登录的真实浏览器窗口：

```bash
cd .claude/skills/browser-cdp
python browser_launch.py --connect --port 9222 --tab <tab-id>
```

参考 `references/browser-launch-scenarios.md` 获取完整说明。

### 2.3 Headless 模式（无 GUI）
在无图形界面的服务器环境中使用：

```bash
cd .claude/skills/browser-cdp
python browser_launch.py --dedicated --headless --name headless-work --start-url "https://example.com"
```

## 3. 页面导航与控制

### 3.1 基本导航
```bash
# 打开网址
python browser_nav.py --port 9333 --tab <id> --goto "https://example.com"

# 前进
python browser_nav.py --port 9333 --tab <id> --forward

# 后退
python browser_nav.py --port 9333 --tab <id> --back

# 刷新
python browser_nav.py --port 9333 --tab <id> --refresh
```

### 3.2 等待元素出现
当页面是动态加载时，需要等待特定元素出现后再继续操作：

```bash
python browser_nav.py --port 9333 --tab <id> --wait-selector ".loading-complete"
```
> 默认超时 30 秒，可用 `--timeout` 参数调整。

## 4. 内容抓取

### 4.1 文本抓取（推荐用于 AI 分析）
```bash
python browser_extract.py --port 9333 --tab <id> --mode text --max-chars 20000
```
- `--mode text`：提取正文文本，适合直接喂给模型分析
- `--max-chars`：最大字符数（默认 20000），大页面建议调高或设置 `--save out.txt` 保存文件

### 4.2 HTML 抓取
```bash
python browser_extract.py --port 9333 --tab <id> --mode html
```
获取完整的 HTML 源代码。

### 4.3 表单信息
```bash
python browser_extract.py --port 9333 --tab <id> --mode forms
```
返回页面中的所有表单字段信息（名称、类型、值等）。

### 4.4 链接列表
```bash
python browser_extract.py --port 9333 --tab <id> --mode links
```
返回页面中的所有超链接（URL 和显示文本）。

### 4.5 Meta 信息
```bash
python browser_extract.py --port 9333 --tab <id> --mode meta
```
返回页面的 title、description、keywords 等元数据。

## 5. 截图与标注

### 5.1 带编号标注的截图（看图操作必备）
```bash\python browser_screenshot.py --port 9333 --tab <id> --out shot.png --annotate
```
- 会在截图上用红色框标出可交互元素，并显示编号
- 配合 `browser_input.py` 使用，通过编号点击元素

### 5.2 整页截图
```bash
python browser_screenshot.py --port 9333 --tab <id> --out full.png --full-page
```
滚动整个页面并拼接成一张长图。

### 5.3 元素级截图
```bash
python browser_screenshot.py --port 9333 --tab <id> --out element.png --selector ".target-class"
```
只截取指定 CSS 选择器对应的元素区域。

## 6. 用户交互（模拟点击和输入）

### 6.1 看图点击（使用编号）
```bash
python browser_input.py --port 9333 --tab <id> --click 1
```
- 先运行 `browser_screenshot.py --annotate` 获取带编号的截图
- 根据截图中的编号点击对应元素（如点击编号为 1 的元素）

### 6.2 直接点击（使用 CSS 选择器）
```bash
python browser_input.py --port 9333 --tab <id> --click "#submit-button"
```

### 6.3 文字输入
```bash
python browser_input.py --port 9333 --tab <id> --input "#search-box" --text "搜索关键词"
```

### 6.4 按键操作
```bash
# 按下 Enter 键
python browser_input.py --port 9333 --tab <id> --press "Enter"

# 按下 Tab 键
python browser_input.py --port 9333 --tab <id> --press "Tab"
```

### 6.5 滚动页面
```bash
# 向下滚动 500 像素
python browser_input.py --port 9333 --tab <id> --scroll 500

# 滚动到页面底部
python browser_input.py --port 9333 --tab <id> --scroll bottom
```

### 6.6 悬停操作
```bash
python browser_input.py --port 9333 --tab <id> --hover "#menu-item"
```
模拟鼠标悬停效果，常用于触发下拉菜单。

## 7. 调试与监控

### 7.1 执行任意 JavaScript
```bash
# 获取页面标题
python browser_console.py --port 9333 --tab <id> --js "document.title"

# 执行复杂脚本
python browser_console.py --port 9333 --tab <id> --js "console.log('Hello'); document.body.innerHTML.substring(0, 100)"
```

### 7.2 读取 Console 日志
```bash
python browser_console.py --port 9333 --tab <id> --console-log
```
获取页面所有的 console.log、error、warn 等消息。

### 7.3 抓取网络请求
```bash
python browser_console.py --port 9333 --tab <id> --network-log
```
获取页面加载过程中的所有网络请求（URL、方法、状态码等）。

## 8. 协作模式（browser_watch.py）

在协作场景中，轮询判断用户是否已完成某个操作：

```bash
# 等待 URL 包含 "success"
python browser_watch.py --port 9333 --tab <id> --wait-url-contains "success"

# 等待标题变化
python browser_watch.py --port 9333 --tab <id> --wait-title-changes
```

## 9. 路径规则（极易踩坑！）

⚠️ **所有脚本都必须先 `cd` 到 skill 目录再运行**（相对导入 `cdp_client`/`utils`），这意味着所有相对路径（包括 `./temp_data/`）都是**相对于 skill 目录**解析的，不是项目根目录。

错误示例：
```bash
cd /project/root
python .claude/skills/browser-cdp/browser_launch.py ...  # ❌ 错误！相对路径会错
```

正确做法：
```bash
cd .claude/skills/browser-cdp
python browser_launch.py ...  # ✅ 正确
```

或者使用绝对路径指定工作目录：
```bash
cd /project/root
python -c "import os; os.chdir('.claude/skills/browser-cdp'); import browser_launch; browser_launch.cmd_dedicated(...)"
```

## 10. 安全注意事项

- 不用于绕过验证码、自动登录他人账号、批量注册等滥用场景
- 涉及"提交""支付""删除""发送"等不可逆操作时，先用截图向用户确认，再执行
- headless spawn 模式默认使用独立 profile，不会读取用户真实 Chrome 的 cookie/密码，纯抓取公开页面场景优先用这个模式，减少对用户账号的接触面
- 调试端口只在需要时开启，用完可以提示用户关闭该 Chrome 窗口恢复正常模式

## 11. 常见坑与解决方案

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| Python 命令找不到 | 环境配置问题 | 加载 `python-env-detection` 子资源检测 |
| 登录态丢失 | 使用了不同的 `--name` | 确保同一任务使用固定的 `--name` |
| 元素编号失效 | DOM 结构发生变化 | 页面变化后重新截图/扫描再操作 |
| SPA 路由跳转不生效 | 死等 load 事件 | 用 `browser_watch.py --wait-url-contains` 判断路由跳转 |
| 截图编号不对 | 页面内容变化 | 先截图扫描，再操作，不要跨多次调用用旧编号 |
| 路径错误 | 不在 skill 目录下运行 | 始终 `cd .claude/skills/browser-cdp` 后再运行脚本 |

完整故障排查见 `references/troubleshooting.md`。

## 12. 完整工作流示例

### 示例：知乎内容抓取工作流

```bash
# 1. 启动专用浏览器（第一次用，手动登录）
cd .claude/skills/browser-cdp
python browser_launch.py --dedicated --name zhihu_session --start-url "https://www.zhihu.com"

# 2. 搜索知乎内容
cd .claude/skills/browser-cdp
python zhihu_search.py --name zhihu_session --query "人工智能"

# 3. 抓取某个回答详情
cd .claude/skills/browser-cdp
python browser_nav.py --port 9333 --tab <tab-id> --goto "https://www.zhihu.com/question/xxx/answer/yyy"
python browser_extract.py --port 9333 --tab <tab-id> --mode text --save answer.txt

# 4. 关闭浏览器
cd .claude/skills/browser-cdp
python browser_launch.py --stop-dedicated zhihu_session
```

参考 `references/workflows.md` 获取更多工作流示例。