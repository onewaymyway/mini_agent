# 故障排查指南

## 连接问题

### 错误：`Connection refused`

**原因**：浏览器未启动或 CDP 端口不匹配

**解决步骤**：
```bash
# 1. 检查是否有浏览器在运行
python src/core/browser_launch.py --list-running

# 2. 如果没有，启动新实例
python src/core/browser_launch.py --dedicated --name my_session --start-url "about:blank"

# 3. 获取正确的端口和 tab ID
python src/core/browser_tabs.py --port 9333 --list
```

### 错误：`Tab not found`

**原因**：tab ID 无效或已关闭

**解决**：
```bash
# 重新获取 tab 列表
python src/core/browser_tabs.py --port 9333 --list

# 使用新的 tab ID 重试
```

## 元素定位问题

### 错误：`Element not found`

**原因**：元素索引过期（页面已变化）

**解决**：
```bash
# 1. 重新截图并标注
python src/core/browser_screenshot.py --port 9333 --tab <id> --out shot.png --annotate

# 2. 重新扫描元素
python src/core/browser_extract.py --port 9333 --tab <id> --mode elements

# 3. 使用新的索引操作
python src/core/browser_input.py --port 9333 --tab <id> --click-index <new_index>
```

### 元素点击无响应

**可能原因**：
- 元素被其他元素遮挡
- 元素需要滚动才能可见
- JavaScript 事件未触发

**解决**：
```bash
# 尝试滚动到元素位置
python src/core/browser_input.py --port 9333 --tab <id> --scroll-to-index <index>

# 或使用 selector 方式
python src/core/browser_input.py --port 9333 --tab <id> --click-selector "button.submit-btn"
```

## 超时问题

### 导航超时

**原因**：网络慢、页面复杂、SPA 路由

**解决**：
```bash
# 增加超时时间（默认 15s，可设为 30-60s）
python src/core/browser_nav.py --port 9333 --tab <id> --goto "URL" --timeout 30

# 使用智能等待策略
python src/core/browser_nav.py --port 9333 --tab <id> --goto "URL" --wait-for networkidle
```

### 截图超时

**原因**：页面未完全加载、大页面、CDP 连接问题

**解决**：
```bash
# 1. 先确保页面就绪
python src/core/browser_nav.py --port 9333 --tab <id> --wait-for networkidle --timeout 30

# 2. 增加截图超时
python src/core/browser_screenshot.py --port 9333 --tab <id> --out shot.png --timeout 90

# 3. 如仍超时，尝试可视区域截图（比整页快）
python src/core/browser_screenshot.py --port 9333 --tab <id> --out shot.png
```

## 验证码问题

### 检测到验证码

**解决选项**：
```bash
# 选项 1：自动处理（支持简单验证码）
python src/core/browser_nav.py --port 9333 --tab <id> --goto "URL" --handle-captcha

# 选项 2：截图后手动处理
python src/core/browser_screenshot.py --port 9333 --tab <id> --out captcha.png
# 人工查看 captcha.png 并输入
python src/core/browser_input.py --port 9333 --tab <id> --type-selector "input.captcha" --text "手动输入"

# 选项 3：使用反检测模式
python src/core/browser_nav.py --port 9333 --tab <id> --goto "URL" --stealth
```

## 性能优化

### 加速页面加载

```bash
# 禁用图片加载（仅文本提取时）
python src/core/browser_nav.py --port 9333 --tab <id> --goto "URL" --no-wait-load

# 使用 networkidle 而非 stable（更快）
python src/core/browser_nav.py --port 9333 --tab <id> --goto "URL" --wait-for networkidle
```

### 批量操作优化

```bash
# 批量导航时复用同一浏览器实例
python src/core/browser_tabs.py --port 9333 --batch-goto "url1,url2,url3"

# 批量截图时指定输出目录
python src/core/browser_tabs.py --port 9333 --batch-screenshot --out-dir ./screenshots
```

## 调试技巧

### 启用详细日志

```bash
# 设置环境变量
export PYTHONPATH="E:\codes\mini_claude_code\.claude\skills\browser-cdp"
python -m src.core.browser_nav --goto "URL" --verbose
```

### 检查 CDP 连接

```bash
# 测试连接
python -c "
from src.core.cdp_client import CDPClient
client = CDPClient('127.0.0.1', 9333)
print(client.get_version())
client.close()
"
```

### 查看浏览器控制台日志

```bash
python src/core/browser_console.py --port 9333 --tab <id> --follow
```

## 常见场景速查

| 场景 | 命令 |
|------|------|
| 页面卡住 | `browser_nav.py --reload` |
| 元素找不到 | 重新截图 + 扫描 |
| 登录态丢失 | 使用 `--dedicated --name` 保持会话 |
| 需要等待 | `--wait-for networkidle` |
| 批量操作 | 使用 `browser_tabs.py --batch-*` |
| 调试问题 | `browser_console.py --follow` |
