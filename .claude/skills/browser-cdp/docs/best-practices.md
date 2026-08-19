# 网站操作最佳实践

**版本**: 1.0.0  
**创建日期**: 2026-08-07  
**状态**: 草案  
**适用范围**: browser-cdp skill 网站抓取与浏览最佳实践

---

## 1. 页面加载最佳实践

### 1.1 智能等待策略

```python
# 推荐：使用智能等待
from browser_cdp.core.wait import SmartWait

wait = SmartWait(timeout=30)
await wait.until_visible(".search-result")
await wait.until_network_idle()

# 避免：固定延迟
import time
time.sleep(5)  # 不推荐
```

### 1.2 超时处理

| 场景 | 超时设置 | 处理方式 |
|------|----------|----------|
| 首页加载 | 30s | 重试 2 次 |
| 搜索查询 | 15s | 重试 1 次 |
| 详情页 | 20s | 重试 1 次 |
| 动态内容 | 10s | 等待 networkidle |

### 1.3 错误恢复

```python
# 推荐：使用重试装饰器
from browser_cdp.reliability.retry import retry_on_failure

@retry_on_failure(max_retries=3, delay=2)
async def search(query: str):
    # 搜索逻辑
    pass
```

---

## 2. 元素定位最佳实践

### 2.1 定位策略优先级

1. **CSS 选择器** - 首选，性能最好
2. **XPath** - 复杂结构时使用
3. **文本匹配** - 按钮、链接等
4. **属性匹配** - data-* 属性
5. **相对定位** - 列表项、卡片

### 2.2 动态元素处理

```python
# 懒加载内容
await page.wait_for_selector(".lazy-loaded", state="visible")

# 滚动加载
await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
await page.wait_for_timeout(1000)
```

---

## 3. 反检测最佳实践

### 3.1 请求头策略

```python
# 推荐：使用随机 UA 池
from browser_cdp.core.headers import RandomUA

ua = RandomUA()
headers = ua.get_headers()
```

### 3.2 行为模拟

| 操作 | 建议延迟 | 说明 |
|------|----------|------|
| 鼠标移动 | 100-500ms 随机 | 模拟人类移动 |
| 点击间隔 | 200-800ms 随机 | 避免规律性 |
| 输入速度 | 50-150ms/字符 | 模拟打字速度 |
| 页面滚动 | 200-600ms | 自然滚动 |

### 3.3 验证码处理

```python
# 推荐：集成验证码识别服务
from browser_cdp.core.captcha import CaptchaHandler

handler = CaptchaHandler(service="third_party_api")
result = await handler.solve(image_url)
```

---

## 4. 性能优化最佳实践

### 4.1 浏览器实例管理

```python
# 推荐：使用浏览器池
from browser_cdp.core.pool import BrowserPool

pool = BrowserPool(max_instances=5)
async with pool.acquire() as browser:
    # 使用浏览器
    pass
```

### 4.2 并发控制

```python
# 推荐：使用信号量控制并发
import asyncio

semaphore = asyncio.Semaphore(10)  # 最多 10 个并发

async def fetch_with_limit(url):
    async with semaphore:
        return await fetch(url)
```

### 4.3 内存管理

```python
# 及时释放资源
async with page:
    # 使用页面
    pass
# 页面自动关闭
```

---

## 5. 数据提取最佳实践

### 5.1 结构化数据优先

```python
# 优先提取 JSON-LD / Microdata
from browser_cdp.core.extractor import StructuredDataExtractor

extractor = StructuredDataExtractor()
data = await extractor.extract(page, schema="Product")
```

### 5.2 数据验证

```python
# 提取后验证数据质量
from browser_cdp.core.validator import DataValidator

validator = DataValidator()
validated_data = validator.validate(data, schema=SCHEMA)
```

---

## 6. 稳定性最佳实践

### 6.1 连接管理

```python
# 使用长连接，避免频繁重连
from browser_cdp.core.connection import ConnectionManager

manager = ConnectionManager(reconnect=True, max_retries=3)
async with manager.connect() as ws:
    # 使用连接
    pass
```

### 6.2 异常处理

```python
# 统一异常处理
from browser_cdp.core.exceptions import BrowserError

try:
    await page.goto(url)
except BrowserError as e:
    logger.error(f"浏览器错误: {e}")
    # 记录错误，继续执行
```

---

## 7. 安全合规最佳实践

### 7.1 数据保护

- 不存储敏感信息（密码、身份证等）
- 加密存储 Cookie 和 Session
- 定期清理临时数据

### 7.2 合规要求

- 遵守 robots.txt
- 控制请求频率
- 尊重网站服务条款

---

## 8. 评估与改进最佳实践

### 8.1 定期评估

| 评估类型 | 频率 | 范围 |
|----------|------|------|
| 全量评估 | 每周 | 所有 P0/P1 网站 |
| 增量评估 | 每日 | 新增/变更网站 |
| 专项评估 | 按需 | 特定场景 |

### 8.2 持续改进

1. 记录每次评估结果
2. 分析薄弱环节
3. 制定改进计划
4. 跟踪改进效果
5. 更新最佳实践文档

---

## 9. 常见问题与解决方案

### 9.1 页面加载超时

**问题**: 页面加载时间超过预期  
**解决**: 
- 检查网络连接
- 增加超时时间
- 使用智能等待策略

### 9.2 元素定位失败

**问题**: 无法定位目标元素  
**解决**:
- 检查元素是否已加载
- 使用更精确的选择器
- 尝试滚动到元素可见

### 9.3 反爬检测

**问题**: 触发反爬机制  
**解决**:
- 降低请求频率
- 轮换 User-Agent
- 使用代理池

---

## 10. 参考文档

- [evaluation-standards-v2.md](./evaluation-standards-v2.md) - 评估标准
- [anti-detection.md](../references/anti-detection.md) - 反检测指南
- [smart-wait.md](../references/smart-wait.md) - 智能等待策略
- [error-retry-strategy.md](./error-retry-strategy.md) - 错误重试策略

---

*本文件为最佳实践指南，请随 skill 演进持续更新*