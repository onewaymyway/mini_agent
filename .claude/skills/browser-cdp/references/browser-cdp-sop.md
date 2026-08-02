# Browser CDP 技能标准操作流程 (SOP)

## 1. 环境准备

### 1.1 检查 Python 环境
```bash
cd .claude/skills/browser-cdp
python --version  # 确认 Python 可用
# 本环境使用 python（Anaconda），python3 不可用
```

### 1.2 安装依赖
```bash
cd .claude/skills/browser-cdp
pip install websocket-client requests pillow
```

> `pillow` 仅用于截图标注功能 (`--annotate` 选项)

## 2. 浏览器连接管理

### 2.1 启动专用实例（推荐）
```bash
cd .claude/skills/browser-cdp
python browser_launch.py --dedicated --name work --start-url "https://example.com"
```
输出示例：`--port 9333 --tab abc123`

### 2.2 管理专用实例
```bash
# 查看已创建的专用实例
python browser_launch.py --list-dedicated

# 关闭指定实例
python browser_launch.py --stop-dedicated work
```

### 2.3 检测系统中运行的调试浏览器
```bash
cd .claude/skills/browser-cdp
python browser_launch.py --list-running
```

### 2.4 连接已有浏览器（场景 B/C）
参考 `references/browser-launch-scenarios.md` 获取完整说明。

## 3. 页面导航与控制

### 3.1 打开新标签页
```bash
cd .claude/skills/browser-cdp
python browser_launch.py --new "https://example.com"
```

### 3.2 导航到指定 URL
```bash
cd .claude/skills/browser-cdp
python browser_nav.py --port 9333 --tab <tab-id> --goto "https://example.com"
```

### 3.3 前进/后退/刷新
```bash
# 前进
python browser_nav.py --port 9333 --tab <tab-id> --forward

# 后退
python browser_nav.py --port 9333 --tab <tab-id> --back

# 刷新
python browser_nav.py --port 9333 --tab <tab-id> --refresh
```

### 3.4 等待元素出现
```bash
cd .claude/skills/browser-cdp
python browser_nav.py --port 9333 --tab <tab-id> --wait-selector "#element-id"
```

## 4. 内容抓取

### 4.1 抓取纯文本（推荐用于 AI 分析）
```bash
cd .claude/skills/browser-cdp
python browser_extract.py --port 9333 --tab <tab-id> --mode text --max-chars 20000
```

### 4.2 抓取 HTML 原文
```bash
cd .claude/skills/browser-cdp
python browser_extract.py --port 9333 --tab <tab-id> --mode html
```

### 4.3 提取表单信息
```bash
cd .claude/skills/browser-cdp
python browser_extract.py --port 9333 --tab <tab-id> --mode forms
```

### 4.4 提取链接列表
```bash
cd .claude/skills/browser-cdp
python browser_extract.py --port 9333 --tab <tab-id> --mode links
```

### 4.5 保存内容到文件
```bash
cd .claude/skills/browser-cdp
python browser_extract.py --port 9333 --tab <tab-id> --mode text --save output.txt
```

## 5. 截图与标注

### 5.1 带编号标注的截图（用于看图操作）
```bash
cd .claude/skills/browser-cdp
python browser_screenshot.py --port 9333 --tab <tab-id> --out shot.png --annotate
```

### 5.2 整页截图
```bash
cd .claude/skills/browser-cdp
python browser_screenshot.py --port 9333 --tab <tab-id> --out full.png --full-page
```

### 5.3 元素级截图
```bash
cd .claude/skills/browser-cdp
python browser_screenshot.py --port 9333 --tab <tab-id> --out element.png --selector ".target-class"
```

## 6. 用户交互（模拟点击和输入）

### 6.1 模拟点击
```bash
cd .claude/skills/browser-cdp
python browser_input.py --port 9333 --tab <tab-id> --click "#button-id"
```

### 6.2 文字输入
```bash
cd .claude/skills/browser-cdp
python browser_input.py --port 9333 --tab <tab-id> --input "#search-box" --text "搜索关键词"
```

### 6.3 按键操作
```bash
cd .claude/skills/browser-cdp
python browser_input.py --port 9333 --tab <tab-id> --press "Enter"
```

### 6.4 滚动页面
```bash
cd .claude/skills/browser-cdp
python browser_input.py --port 9333 --tab <tab-id> --scroll 500
```

### 6.5 悬停操作
```bash
cd .claude/skills/browser-cdp
python browser_input.py --port 9333 --tab <tab-id> --hover "#menu-item"
```

## 7. 调试与监控

### 7.1 执行任意 JavaScript
```bash
cd .claude/skills/browser-cdp
python browser_console.py --port 9333 --tab <tab-id> --js "document.title"
```

### 7.2 读取 Console 日志
```bash
cd .claude/skills/browser-cdp
python browser_console.py --port 9333 --tab <tab-id> --console-log
```

### 7.3 抓取网络请求
```bash
cd .claude/skills/browser-cdp
python browser_console.py --port 9333 --tab <tab-id> --network-log
```

## 8. 协作模式（browser_watch.py）

用于判断用户是否已完成某个操作（URL/标题变化）：
```bash
cd .claude/skills/browser-cdp
python browser_watch.py --port 9333 --tab <tab-id> --wait-url-contains "success"
```

## 9. 路径规则（重要！）

⚠️ **所有脚本都必须先 `cd` 到 skill 目录再运行**（相对导入 `cdp_client`/`utils`），这意味着所有相对路径（包括 `./temp_data/`）都是**相对于 skill 目录**解析的，不是项目根目录。

## 10. 安全注意事项

- 不用于绕过验证码、自动登录他人账号、批量注册等滥用场景
- 涉及"提交""支付""删除""发送"等不可逆操作时，先用截图向用户确认，再执行
- headless spawn 模式默认使用独立 profile，不会读取用户真实 Chrome 的 cookie/密码
- 调试端口只在需要时开启，用完可以提示用户关闭该 Chrome 窗口恢复正常模式

## 11. 常见问题速查

| 问题 | 解决方案 |
|------|----------|
| 找不到 python 命令 | 加载 `python-env-detection` 子资源 |
| 登录态丢失 | 确保使用相同的 `--name` 参数 |
| 元素编号失效 | 页面变化后重新截图/扫描再操作 |
| SPA 路由跳转 | 使用 `browser_watch.py --wait-url-contains` 代替等待 load 事件 |

完整故障排查见 `references/troubleshooting.md`。