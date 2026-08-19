# 网络空闲检测模块（network_idle_detector.py）

> 精细化网络状态监控，按 MIME 类型优先级区分关键/非关键请求。

---

## 1. 核心功能

| 功能 | 方法 | 说明 |
|------|------|------|
| 等待空闲 | `await detector.wait_for_idle()` | 持续检测直到网络空闲 |
| 获取状态 | `detector.get_idle_status()` | 实时获取空闲状态统计 |
| 等待特定请求 | `await detector.wait_for_request(pattern, timeout)` | 等待指定 URL 模式的请求完成 |
| 重置状态 | `detector.reset()` | 清空历史记录和请求计数 |
| 停止监听 | `await detector.stop()` | 停止 CDP 事件监听 |

---

## 2. MIME 类型优先级分类

| 分类 | MIME 类型/扩展名 | 优先级 |
|------|-----------------|--------|
| **关键** | text/html, application/json, text/css, application/javascript | P0 - 计入空闲判定 |
| **中等** | image/svg+xml, font/woff2, application/xml | P1 - 部分计入 |
| **非关键** | image/*, video/*, audio/*, font/*, application/octet-stream | P2 - 不计入 |

---

## 3. 配置选项

| 参数 | 默认 | 说明 |
|------|------|------|
| `idle_seconds` | 0.5s | 连续空闲判定阈值 |
| `timeout` | 30s | 整体超时时间 |
| `max_pending_requests` | 0 | 允许的最大 pending 数（0=严格） |
| `wait_critical_only` | True | 仅等待关键 MIME 类型空闲 |
| `exclude_patterns` | [] | 排除的 URL 模式列表 |
| `critical_mime_types` | set | 关键 MIME 类型集合 |
| `non_critical_extensions` | set | 非关键文件扩展名集合 |

---

## 4. 快速使用

```python
from src.core.network_idle_detector import (
    NetworkIdleConfig,
    NetworkIdleDetector,
    create_network_idle_detector,
    wait_for_network_idle,
)

# 方式一：工厂函数（推荐）
detector = create_network_idle_detector(session, idle_seconds=1.0, timeout=20)
result = await detector.wait_for_idle()

# 方式二：便捷函数
result = await wait_for_network_idle(session, idle_seconds=0.5, timeout=30)

# 自定义配置
config = NetworkIdleConfig(
    idle_seconds=1.0,
    timeout=20,
    max_pending_requests=5,
    exclude_patterns=["*.svg", "*.png"],
)
detector = NetworkIdleDetector(session, config)
```
