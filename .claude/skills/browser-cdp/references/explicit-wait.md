# 显式等待模块（explicit_wait.py）

> 提供结构化显式等待能力，替代隐式等待（WebDriver.implicitly_wait）。

---

## 1. 核心功能

| 功能 | 方法 | 说明 |
|------|------|------|
| 条件等待 | `await wait.until(condition, timeout)` | 轮询直到条件满足 |
| 条件取反 | `await wait.until_not(condition, timeout)` | 轮询直到条件不再满足 |
| 元素可见 | `await wait.until_visible(selector, timeout)` | 等待元素可见（含 Shadow DOM） |
| 元素存在 | `await wait.until_present(selector, timeout)` | 等待元素存在于 DOM |
| 文本匹配 | `await wait.until_text(selector, text, exact)` | 等待元素包含指定文本 |
| 数量条件 | `await wait.until_count(selector, count, operator)` | 等待匹配元素数量满足条件 |
| URL匹配 | `await wait.until_url_matches(pattern, timeout)` | 等待URL匹配正则模式 |
| 内容稳定 | `await wait.until_stable(selector, check_times)` | 等待元素内容连续多次不变 |
| 等待网络空闲 | `await wait.wait_for_network_idle()` | 委托 NetworkIdleDetector |

---

## 2. Condition 复合条件

```python
from src.core.explicit_wait import Condition, ExplicitWait

cond = Condition("has_text", lambda: el.text == "OK") & \
       Condition("is_visible", lambda: el.is_visible)
result = await wait.until(cond, timeout=10)
```

---

## 3. 配置选项

| 参数 | 默认 | 说明 |
|------|------|------|
| `timeout` | 10s | 全局超时时间 |
| `poll_interval` | 0.2s | 轮询间隔 |
| `poll_backoff` | 0 | 指数退避步长 |
| `raise_on_timeout` | True | 超时时是否抛出异常 |
| `check_shadow_dom` | True | 是否进入 Shadow DOM 查找 |

---

## 4. 模块级便捷函数

```python
from src.core.explicit_wait import (
    wait_for_selector,   # 等待元素匹配选择器
    wait_for_visible,    # 等待元素可见
    wait_for_text,       # 等待页面出现指定文本
    wait_for_network_idle,
)

result = await wait_for_selector(session, "#submit-btn", timeout=15)
result = await wait_for_visible(session, ".loading-spinner", timeout=20)
result = await wait_for_text(session, "加载完成", timeout=30)
result = await wait_for_network_idle(session, idle_seconds=0.5, timeout=30)
```
