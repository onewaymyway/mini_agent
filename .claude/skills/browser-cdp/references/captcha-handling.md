# 验证码处理与反检测指南

本文档详细说明 browser-cdp skill 的验证码处理和反检测功能。

## 验证码类型支持

### 1. 滑块验证码（Slider Captcha）

**常见平台**：极验（Geetest）、腾讯防水墙、阿里云验证码

**检测特征**：
- 页面包含 `#slideBlock`、`.slide-block` 等元素
- 文本包含"滑动验证"、"拖拽滑块"等关键词
- URL 包含 `geetest`、`slide` 等标识

**处理方式**：
```python
# 自动处理
python src/core/browser_nav.py --tab <id> --goto "https://example.com" --handle-captcha

# 代码调用
from src.core.captcha_handler import CaptchaHandler
handler = CaptchaHandler(session)
result = await handler.handle_captcha()
```

**实现细节**：
1. 定位滑块元素
2. 计算滑动距离（通过图像对比或默认值）
3. 模拟人类滑动轨迹（缓动函数 + 随机扰动）
4. 执行按下、移动、释放操作

### 2. 点选验证码（Click Captcha）

**常见平台**：百度人机验证、部分极验点选模式

**检测特征**：
- 页面包含多个可点击元素
- 文本包含"点选验证"、"选择正确"等关键词

**处理方式**：
1. 获取指令文本（如"请点击太阳"）
2. 识别目标元素
3. 点击对应元素

**限制**：需要人工判断指令与元素的对应关系，当前实现为启发式匹配。

### 3. 文字验证码（Text Captcha）

**常见平台**：各类网站登录/注册页面

**处理方式**：
- 需要配置 OCR API
- 支持第三方 OCR 服务（百度 OCR、腾讯 OCR、阿里云 OCR）

**配置示例**：
```python
from src.core.captcha_handler import CaptchaHandler

# 定义 OCR 函数
def my_ocr_api(image_bytes: bytes) -> str:
    # 调用你的 OCR API
    return "识别结果"

handler = CaptchaHandler(session, ocr_api=my_ocr_api)
result = await handler.handle_captcha()
```

### 4. reCAPTCHA / hCaptcha

**处理方式**：
- 当前版本仅支持检测，无法自动处理
- 输出提示信息，建议用户使用专业服务（如 2Captcha、Anti-Captcha）

### 5. 短信/邮箱验证码

**处理方式**：
- 需要用户提供验证码
- 输出提示信息，等待用户输入

## 反检测模式（Stealth Mode）

### 启用方式

```bash
# 命令行
python src/core/browser_nav.py --tab <id> --goto "https://example.com" --stealth

# 代码调用
from src.core.stealth import StealthMode, StealthConfig
stealth = StealthMode(session, StealthConfig())
await stealth.apply()
```

### 反检测功能清单

| 功能 | 说明 | 默认状态 |
|-----|------|---------|
| 移除 webdriver | 隐藏 `navigator.webdriver` 属性 | ✅ 启用 |
| Chrome runtime | 模拟 `window.chrome` 对象 | ✅ 启用 |
| 权限模拟 | 模拟 `permissions.query` | ✅ 启用 |
| 语言模拟 | 设置 `navigator.languages` | ✅ 启用 |
| 平台模拟 | 设置 `navigator.platform` | ✅ 启用 |
| 插件模拟 | 模拟浏览器插件列表 | ✅ 启用 |
| 人类化鼠标 | 贝塞尔曲线轨迹 + 随机扰动 | ✅ 启用 |
| 人类化打字 | 随机延迟 + 偶尔停顿 | ✅ 启用 |
| 随机延迟 | 请求间隔随机化 | ✅ 启用 |
| User-Agent 轮换 | 随机选择 UA 字符串 | ✅ 启用 |

### 自定义配置

```python
from src.core.stealth import StealthConfig, StealthMode

config = StealthConfig(
    enable_webdriver_removal=True,
    enable_chrome_runtime=True,
    humanize_mouse=True,
    humanize_typing=True,
    random_delay_range=(0.2, 0.8)  # 调整延迟范围
)

stealth = StealthMode(session, config)
await stealth.apply()
```

## 常见反爬场景应对策略

### 场景 1：请求频率过高

**症状**：
- 返回 429 Too Many Requests
- 页面被重定向到验证页
- IP 被临时封禁

**应对**：
```bash
# 启用反检测 + 随机延迟
python src/core/browser_nav.py --tab <id> --goto "https://example.com" --stealth

# 在代码中控制请求间隔
import asyncio
from src.core.stealth import StealthMode

stealth = StealthMode(session)
await stealth.random_human_delay()  # 0.5-3 秒随机延迟
```

### 场景 2：指纹检测

**症状**：
- 页面加载异常
- 自动弹出验证码
- 请求被拦截

**应对**：
```bash
# 启用完整 stealth 模式
python src/core/browser_nav.py --tab <id> --goto "https://example.com" --stealth --handle-captcha
```

### 场景 3：IP 封禁

**症状**：
- 无法访问目标网站
- 返回 403 Forbidden
- 重定向到 IP 检测页面

**应对**：
- 使用代理池（需外部配置）
- 降低请求频率
- 轮换 IP

### 场景 4：JavaScript 检测

**症状**：
- 页面空白或加载失败
- 控制台输出检测脚本

**应对**：
```bash
# 等待 JS 执行完成
python src/core/browser_nav.py --tab <id> --goto "https://example.com" --wait-for networkidle

# 或使用稳定等待
python src/core/browser_nav.py --tab <id> --goto "https://example.com" --wait-for stable
```

### 场景 5：行为分析

**症状**：
- 触发验证码
- 账号被限制

**应对**：
```bash
# 启用人类化行为模拟
python src/core/browser_nav.py --tab <id> --goto "https://example.com" --stealth

# 在代码中使用人类化操作
from src.core.stealth import StealthMode
stealth = StealthMode(session)
await stealth.human_like_click(x, y)  # 人类化点击
await stealth.human_like_type("text")  # 人类化输入
await stealth.human_like_scroll(500)   # 人类化滚动
```

## 输出格式

### 验证码检测结果

成功时：
```
[ok] 验证码已处理: [成功] 类型=slider, 消息=滑块滑动距离: 280.0px
```

失败时：
```
[warn] 检测到 slider 验证码，需要手动处理
[info] 提示: 未找到滑块元素
```

### 反检测模式启用

```
[info] 已启用反检测模式
[info] Stealth 模式应用成功
```

## 限制与注意事项

1. **OCR 识别**：文字验证码需要配置外部 OCR API，当前版本不提供内置 OCR
2. **reCAPTCHA/hCaptcha**：无法自动处理，需要用户使用专业服务或手动完成
3. **图像对比**：滑块验证码的距离计算使用简化算法，复杂场景可能需要手动调整
4. **法律边界**：本功能仅用于自动化测试和合法数据抓取，不得用于绕过安全验证的恶意用途

## 扩展开发

### 添加新的验证码类型

1. 在 `CaptchaType` 枚举中添加新类型
2. 在 `SELECTORS` 字典中添加对应的 CSS 选择器
3. 在 `DETECTION_PATTERNS` 字典中添加文本匹配模式
4. 实现 `_handle_<type>` 方法
5. 在 `handle_captcha` 方法中注册新处理器

### 集成 OCR API

```python
from src.core.captcha_handler import CaptchaHandler

# 百度 OCR
def baidu_ocr(image_bytes: bytes) -> str:
    # 调用百度 OCR API
    pass

# 腾讯 OCR
def tencent_ocr(image_bytes: bytes) -> str:
    # 调用腾讯 OCR API
    pass

handler = CaptchaHandler(session, ocr_api=baidu_ocr)
```