# Browser-CDP 网站抓取兼容性需求文档

> 生成时间：2026-08-16
> 目标：拓展 browser-cdp skill 对各类网站的抓取兼容性，覆盖更多领域

---

## 一、主流网站反爬机制分类与强度评估

### 1.1 反爬机制分类

| 机制类型 | 描述 | 强度等级 | 典型网站 |
|---------|------|---------|---------|
| **请求头验证** | 检查 UA、Accept-Language、Referer 等是否合规 | ⭐⭐ | 基础新闻网站 |
| **IP 频率限制** | 同一 IP 短时间内多次请求触发封锁 | ⭐⭐⭐ | 大部分商业网站 |
| **Cookie 验证** | 要求有效的会话 Cookie，拒绝无 Cookie 请求 | ⭐⭐⭐ | 电商、社交网站 |
| **动态渲染** | 内容通过 JavaScript 动态加载，纯 HTTP 无法获取 | ⭐⭐⭐⭐ | 现代 SPA 应用 |
| **行为验证码** | 极验、顶像、数美等滑块/点选验证码 | ⭐⭐⭐⭐⭐ | 阿里系、支付平台 |
| **设备指纹检测** | Canvas/WebGL/字体指纹识别唯一设备 | ⭐⭐⭐⭐⭐ | 小红书、抖音 |
| **TLS 指纹识别** | 识别 TLS 握手特征区分浏览器与爬虫 | ⭐⭐⭐⭐ | Cloudflare 防护网站 |
| **登录态依赖** | 必须登录才能访问核心数据 | ⭐⭐⭐⭐ | 淘宝、知乎 |
| **数据加密** | 接口参数/API响应加密（如AES/RSA） | ⭐⭐⭐⭐⭐ | 金融、政务平台 |
| **JS 混淆** | 关键逻辑加密/混淆，逆向难度大 | ⭐⭐⭐⭐⭐ | 高级防护网站 |

---

## 二、各类型网站反爬特点分析

### 2.1 电商平台

**典型网站：淘宝、京东、拼多多、当当**

| 特征 | 说明 |
|-----|------|
| **登录态强依赖** | 商品详情、搜索结果需登录，未登录返回空数据或跳转 |
| **设备指纹** | 阿里系使用自研设备指纹，识别模拟器/脚本 |
| **动态参数** | API 请求参数加密（如 tk、abtest 字段） |
| **行为风控** | 高频访问触发滑块验证码或弹出登录框 |
| **IP 限制** | 自动封禁异常 IP，需代理池配合 |

**兼容挑战：**
- 模拟登录流程复杂（手机验证码/扫码）
- Cookie 有效期短，需维护 Cookie 池
- 页面结构频繁变化，选择器易失效

### 2.2 社交媒体平台

**典型网站：微博、知乎、小红书、抖音、B站**

| 特征 | 说明 |
|-----|------|
| **Canvas 指纹** | 小红书、抖音重度依赖 Canvas/WebGL 指纹 |
| **字体反爬** | 微博使用自定义字体，需额外解析 |
| **行为分析** | 检测鼠标轨迹、滚动速度等人类行为特征 |
| **API 签名** | 接口请求需要时间戳+签名验证 |
| **登录墙** | 部分内容需登录后才能查看 |

**兼容挑战：**
- 设备指纹欺骗需要高级工具（如 puppeteer-extra-plugin-anonymize-ua）
- 行为模拟不够自然容易触发风控
- 部分内容在移动端APP而非网页

### 2.3 新闻/资讯网站

**典型网站：人民网、新华网、腾讯新闻、新浪新闻**

| 特征 | 说明 |
|-----|------|
| **反爬较轻** | 多数新闻网站反爬措施较基础 |
| **频率限制** | 主要依靠 IP 频率限制 |
| **结构化内容** | 文章内容结构相对固定，易于解析 |
| **部分加密** | 少数网站对评论接口有保护 |

**兼容挑战：**
- 相对容易抓取，适合做 baseline
- 需注意版权合规性

### 2.4 政府/政务网站

**典型网站：中国裁判文书网、政府公开数据平台**

| 特征 | 说明 |
|-----|------|
| **备案要求** | 部分网站要求访问者进行ICP备案 |
| **数据量巨大** | 如裁判文书网数据量达亿级 |
| **访问压力** | 爬虫可能导致正常用户无法访问 |
| **法律风险** | 未经授权抓取可能涉及法律风险 |

**兼容挑战：**
- 需严格遵守相关法律法规
- 建议通过官方API或数据开放平台获取
- 避免对网站造成过大压力

### 2.5 金融/数据平台

**典型网站：同花顺、东方财富、天眼查、企查查**

| 特征 | 说明 |
|-----|------|
| **强加密** | 接口参数和响应通常加密 |
| **严格风控** | 检测到爬虫立即封禁账号 |
| **行为检测** | 检测非人类操作模式 |
| **CAPTCHA** | 高频操作触发验证码 |

**兼容挑战：**
- 逆向成本高
- 需长期维护更新
- 法律风险较高

### 2.6 视频/直播平台

**典型网站：B站、抖音、快手、YouTube**

| 特征 | 说明 |
|-----|------|
| **流媒体保护** | 视频流有DRM保护 |
| **API签名** | 请求需要复杂签名 |
| **登录墙** | 大量内容需登录 |
| **地理限制** | 部分内容有地区限制 |

**兼容挑战：**
- 视频下载涉及版权问题
- API逆向难度大
- 建议仅抓取公开元数据

---

## 三、当前 browser-cdp skill 能力盘点

### 3.1 已实现功能

根据代码分析，当前 skill 具备：

| 能力 | 状态 | 备注 |
|-----|------|------|
| CDP 连接管理 | ✅ | 支持 Chrome DevTools Protocol |
| 页面导航 | ✅ | goto, reload, back/forward |
| 元素操作 | ✅ | click, fill, select |
| 截图 | ✅ | fullPage, viewport |
| 内容提取 | ✅ | innerHTML, text, attributes |
| Cookie 管理 | ✅ | get/set cookies |
| 多标签页 | ✅ | create/close/switch tabs |
| 等待策略 | ✅ | waitForSelector, waitForNetwork |
| 基础反检测 | ⚠️ | 部分插件支持 |

### 3.2 缺失/薄弱能力

| 缺失能力 | 强度 | 影响范围 | 优先级 |
|---------|------|---------|--------|
| **设备指纹伪装** | ⭐⭐⭐⭐⭐ | 小红书、抖音、支付类 | P0 |
| **高级验证码处理** | ⭐⭐⭐⭐ | 极验、顶像、数美 | P1 |
| **TLS 指纹伪装** | ⭐⭐⭐⭐ | Cloudflare 防护网站 | P1 |
| **代理IP集成** | ⭐⭐⭐ | IP封锁应对 | P1 |
| **行为模拟增强** | ⭐⭐⭐ | 风控检测 | P2 |
| **登录态自动化** | ⭐⭐⭐⭐ | 电商、社交 | P1 |
| **Cookie 池管理** | ⭐⭐⭐ | 会话维持 | P2 |
| **响应解密** | ⭐⭐⭐⭐⭐ | 金融、政务 | P3 |

---

## 四、兼容性扩展需求

### 4.1 P0 级需求（高优先级）

#### 4.1.1 设备指纹伪装模块

**目标网站：** 小红书、抖音、支付宝、微信支付

**技术路线：**
```python
# 使用 puppeteer-extra-plugin-stealth
# 或 playwright-extra with stealth plugin

from playwright.async_api import async_playwright
import asyncio

async def setup_stealth_browser():
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(
        headless=False,
        args=[
            '--disable-blink-features=AutomationControlled',
            '--disable-features=IsolateOrigins,site-per-process',
        ]
    )
    # 应用 stealth 插件消除 automation 标志
    context = await browser.new_context(
        viewport={'width': 1920, 'height': 1080},
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) ...'
    )
    page = await context.new_page()
    return page
```

**需要扩展的 CDP 命令：**
- `Runtime.evaluate` - 注入 stealth 脚本
- `Page.enable` + `Page.addScriptToEvaluateOnNewDocument` - 持久化脚本

#### 4.1.2 高级验证码处理模块

**目标网站：** 极验、顶像、数美、阿里云盾

**技术路线：**
```python
# 方案1: OCR识别（适合简单验证码）
from PIL import Image
import pytesseract

def ocr_captcha(image_path):
    img = Image.open(image_path)
    return pytesseract.image_to_string(img)

# 方案2: 深度学习模型（适合复杂验证码）
# 使用 trained model 识别点选/滑块验证码

# 方案3: 第三方打码平台API
# 接入打码平台如 超级鹰、鹰眼
```

**需要扩展的 CDP 命令：**
- `Page.captureScreenshot` - 截取验证码
- `Input.dispatchMouseEvent` - 模拟拖拽/点击

---

### 4.2 P1 级需求（中优先级）

#### 4.2.1 TLS 指纹伪装模块

**目标网站：** Cloudflare 防护网站

**技术路线：**
```python
# 使用 curl_cffi 或 requests-tls 库
# 模拟真实 Chrome TLS 握手特征

import curl_cffi.requests as requests

session = requests.Session(
    impersonate='chrome124'
)
response = session.get('https://target-site.com')
```

**CDP 扩展：**
- 需要修改 TLS 握手配置
- 或使用中间代理注入 TLS 伪装

#### 4.2.2 代理 IP 集成模块

**目标网站：** IP封锁严格的网站

**技术路线：**
```python
# 集成代理池
proxy_config = {
    'server': 'http://proxy.example.com:8080',
    'username': 'user',
    'password': 'pass'
}

browser = await playwright.chromium.launch(proxy=proxy_config)
```

**需要扩展的配置项：**
- 代理池管理
- IP轮换策略
- 代理健康检查

#### 4.2.3 行为模拟增强模块

**目标网站：** 行为检测严格的网站

**技术路线：**
```python
# 模拟人类行为模式
import random
import asyncio

async def human_like_action(page, action):
    # 随机延时
    await asyncio.sleep(random.uniform(0.5, 2.0))
    # 随机鼠标移动
    await page.mouse.move(
        random.randint(100, 800),
        random.randint(100, 600)
    )
    # 执行操作
    await action()
```

**需要扩展的 CDP 命令：**
- `Input.dispatchMouseEvent` - 鼠标事件
- `Input.dispatchKeyEvent` - 键盘事件

---

### 4.3 P2 级需求（低优先级）

#### 4.3.1 登录态自动化模块

**目标网站：** 需要登录的网站

**技术路线：**
```python
# 保存/恢复登录状态
context = await browser.new_context(
    storage_state='cookies.json'  # 保存的登录状态
)
```

**需要扩展的 CDP 命令：**
- `Storage.setCookies` - 设置 Cookie
- `Storage.clearCookies` - 清除 Cookie

#### 4.3.2 Cookie 池管理模块

**目标网站：** Cookie 有效期短的网站

**技术路线：**
```python
class CookiePool:
    def __init__(self):
        self.cookies = []
        self.index = 0
    
    def get_next_cookie(self):
        cookie = self.cookies[self.index % len(self.cookies)]
        self.index += 1
        return cookie
```

---

## 五、分阶段实施计划

### 阶段一：基础兼容（1-2周）

**目标：** 覆盖 70% 常规网站

| 任务 | 内容 | 预计时间 |
|-----|------|---------|
| T1 | 设备指纹伪装模块开发 | 3天 |
| T2 | 代理IP集成模块开发 | 2天 |
| T3 | 行为模拟增强模块开发 | 2天 |
| T4 | 基础测试验证 | 3天 |

### 阶段二：进阶兼容（2-3周）

**目标：** 覆盖 85% 中等难度网站

| 任务 | 内容 | 预计时间 |
|-----|------|---------|
| T5 | 验证码处理模块开发 | 4天 |
| T6 | 登录态自动化模块开发 | 3天 |
| T7 | Cookie池管理模块开发 | 2天 |
| T8 | 深度测试验证 | 4天 |

### 阶段三：高级兼容（持续迭代）

**目标：** 覆盖 95% 高难度网站

| 任务 | 内容 | 预计时间 |
|-----|------|---------|
| T9 | TLS指纹伪装模块开发 | 5天 |
| T10 | 响应解密模块开发 | 5天 |
| T11 | 专项优化（按网站） | 持续 |
| T12 | 性能与稳定性优化 | 3天 |

---

## 六、技术选型建议

### 6.1 浏览器自动化框架

| 框架 | 优势 | 劣势 | 推荐度 |
|-----|------|------|--------|
| **Playwright** | 跨浏览器、API完善、原生stealth支持 | 学习成本略高 | ⭐⭐⭐⭐⭐ |
| **Puppeteer** | 生态成熟、文档丰富 | 仅Chrome | ⭐⭐⭐⭐ |
| **Selenium** | 最成熟、语言支持多 | 性能较差 | ⭐⭐⭐ |
| **CDP 原生** | 完全控制、高性能 | 开发成本高 | ⭐⭐⭐ |

**建议：** 保持 CDP 原生基础上，增加 Playwright 作为高级封装层

### 6.2 反检测工具

| 工具 | 功能 | 推荐度 |
|-----|------|--------|
| **puppeteer-extra-plugin-stealth** | 消除自动化标志 | ⭐⭐⭐⭐⭐ |
| **curl_cffi** | TLS指纹伪装 | ⭐⭐⭐⭐ |
| **fake-useragent** | 随机UA生成 | ⭐⭐⭐⭐ |
| **undetected-chromedriver** | Chrome反检测 | ⭐⭐⭐ |

---

## 七、风险评估

### 7.1 技术风险

| 风险 | 影响 | 应对措施 |
|-----|------|---------|
| 网站频繁更新结构 | 爬虫失效 | 建立监控机制，及时更新 |
| 反爬技术升级 | 能力不足 | 持续跟进最新技术 |
| 法律合规风险 | 业务受限 | 遵守 robots.txt，控制频率 |

### 7.2 资源风险

| 风险 | 影响 | 应对措施 |
|-----|------|---------|
| 代理IP成本 | 预算超支 | 选择性价比高的代理服务商 |
| 计算资源消耗 | 性能瓶颈 | 优化并发策略 |
| 维护成本 | 人力投入 | 建立自动化测试体系 |

---

## 八、附录

### 8.1 参考资源

1. [Cloudflare Anti-Bot Guide](https://developers.cloudflare.com/bots/)
2. [Playwright Stealth Plugin](https://github.com/berstend/puppeteer-extra)
3. [Reverse Engineering CAPTCHA](https://blog.csdn.net/category_12345.html)
4. [Device Fingerprinting Deep Dive](https://datadome.co/)

### 8.2 术语表

| 术语 | 解释 |
|-----|------|
| CDP | Chrome DevTools Protocol，浏览器调试协议 |
| SPA | Single Page Application，单页应用 |
| TLS | Transport Layer Security，传输层安全协议 |
| OCR | Optical Character Recognition，光学字符识别 |
| CAPTCHA | 全自动区分计算机和人类的图灵测试 |

---

> 本文档为 browser-cdp skill 兼容性扩展的基础依据，后续开发应以此为指导方向。
